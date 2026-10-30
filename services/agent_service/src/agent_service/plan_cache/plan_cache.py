"""Answers a question from a stored plan when one fits, so no Opus call is made.

try_answer (before the agent):
  extract the template (Haiku) -> follow-up? miss
  embed it (Voyage) and find the nearest stored template (Chroma)
  hit when the text is the same (exact) or similarity >= the threshold (similar),
  the slot names are exactly the stored ones, and the filled plan is valid
  -> run the plan's search for page 1 and reply with a fixed sentence.

learn (after the agent answered a miss, as a background task):
  store the agent's plan only for a self-contained question whose plan has a search
  and whose best similarity was below the threshold (a new kind of question).

Every outcome is counted in `PlanCacheMetrics`. The cache never fails a question: any
problem is a miss, and the agent answers as it would without the cache.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_service.agent.query_agent import AgentAnswer
from agent_service.memory.session_store import SessionEntry, SessionStore
from agent_service.plan_cache.chroma_connection import PlanStoreUnavailableError
from agent_service.plan_cache.chroma_plan_store import PlanMatch
from agent_service.plan_cache.embedding_provider import EmbeddingError, EmbeddingProvider
from agent_service.plan_cache.plan_cache_metrics import PlanCacheOutcome
from agent_service.plan_cache.query_template_extractor import (
    ExtractedQuery,
    TemplateExtractionError,
)
from agent_service.plan_cache.slot_substitutor import (
    PlanTemplateFillError,
    StoredPlanTemplate,
    UnslottablePlanError,
    fill_plan_template,
    template_plan,
)
from agent_service.plan_execution.plan_executor import (
    PlanExecutor,
    PlanHasNoSearchError,
    PlanResultPage,
)
from shared.clients.transaction_service_client import (
    TransactionServiceError,
    TransactionServiceUnavailableError,
)
from shared.clock import Clock
from shared.contracts import QueryPlan, SearchableEntity
from shared.contracts.search_request import DEFAULT_PAGE_SIZE

logger = logging.getLogger(__name__)

ENTITY_NOUNS: dict[SearchableEntity, tuple[str, str]] = {
    SearchableEntity.TRANSACTIONS: ("transaction", "transactions"),
    SearchableEntity.CHARGEBACKS: ("chargeback", "chargebacks"),
}


class TemplateExtractor(Protocol):
    async def extract(
        self, question: str, previous_entries: Sequence[SessionEntry]
    ) -> ExtractedQuery: ...


class PlanTemplateStore(Protocol):
    async def nearest(self, embedding: list[float]) -> PlanMatch | None: ...
    async def add(self, template: StoredPlanTemplate, embedding: list[float]) -> None: ...
    async def record_hit(self, template_id: str, now: datetime) -> None: ...


class OutcomeRecorder(Protocol):
    async def record(self, outcome: PlanCacheOutcome) -> None: ...


@dataclass(frozen=True)
class CacheLookup:
    """What a lookup found out, for learning from the agent's answer after a miss."""

    extracted: ExtractedQuery | None = None
    embedding: list[float] | None = None
    best_similarity: float | None = None  # None: nothing was stored yet


@dataclass(frozen=True)
class CachedAnswer:
    reply: str
    query_plan: QueryPlan
    page: PlanResultPage
    template_text: str
    similarity: float


class PlanCache:
    def __init__(
        self,
        extractor: TemplateExtractor,
        embeddings: EmbeddingProvider,
        store: PlanTemplateStore,
        metrics: OutcomeRecorder,
        sessions: SessionStore,
        executor: PlanExecutor,
        clock: Clock,
        hit_threshold: float,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._extractor = extractor
        self._embeddings = embeddings
        self._store = store
        self._metrics = metrics
        self._sessions = sessions
        self._executor = executor
        self._clock = clock
        self._hit_threshold = hit_threshold
        self._page_size = page_size

    async def try_answer(
        self, session_id: str, question: str
    ) -> tuple[CachedAnswer | None, CacheLookup]:
        """A cached answer (saved to the session like an agent answer), or None and what
        the lookup found. Never raises."""
        try:
            return await self._try_answer(session_id, question.strip())
        except Exception:  # the cache must never fail a question
            logger.exception("plan cache lookup failed; the agent answers")
            await self._metrics.record(PlanCacheOutcome.MISS_UNAVAILABLE)
            return None, CacheLookup()

    async def learn(self, lookup: CacheLookup, question: str, answer: AgentAnswer) -> None:
        """Stores the agent's plan when it is reusable and new. Never raises."""
        try:
            outcome = await self._learn(lookup, question.strip(), answer)
        except Exception:
            logger.exception("plan cache could not learn from the agent's answer")
            return
        if outcome is not None:
            await self._metrics.record(outcome)

    async def _try_answer(
        self, session_id: str, question: str
    ) -> tuple[CachedAnswer | None, CacheLookup]:
        previous_entries = await self._sessions.load_recent_entries(session_id)
        try:
            extracted = await self._extractor.extract(question, previous_entries)
        except TemplateExtractionError as error:
            return await self._miss(PlanCacheOutcome.MISS_UNAVAILABLE, CacheLookup(), error)
        if not extracted.is_self_contained:
            return await self._miss(PlanCacheOutcome.MISS_FOLLOW_UP, CacheLookup(extracted))
        try:
            embedding = await self._embeddings.embed(extracted.template_text)
            match = await self._store.nearest(embedding)
        except (EmbeddingError, PlanStoreUnavailableError) as error:
            return await self._miss(
                PlanCacheOutcome.MISS_UNAVAILABLE, CacheLookup(extracted), error
            )
        if match is None:
            return await self._miss(
                PlanCacheOutcome.MISS_NO_TEMPLATES, CacheLookup(extracted, embedding)
            )

        lookup = CacheLookup(extracted, embedding, match.similarity)
        is_exact = match.template.template_text == extracted.template_text
        if not is_exact and match.similarity < self._hit_threshold:
            return await self._miss(PlanCacheOutcome.MISS_BELOW_THRESHOLD, lookup)
        # Exactly the stored slots: an extra value (say an amount) would be silently dropped.
        if set(match.template.slot_names) != set(extracted.slot_values):
            return await self._miss(PlanCacheOutcome.MISS_SLOT_MISMATCH, lookup)
        try:
            plan = fill_plan_template(match.template, extracted.slot_values)
            page = await self._executor.execute(plan, 1, self._page_size)
        except (PlanTemplateFillError, PlanHasNoSearchError, TransactionServiceError) as error:
            return await self._miss(PlanCacheOutcome.MISS_INVALID_PLAN, lookup, error)
        except TransactionServiceUnavailableError as error:
            return await self._miss(PlanCacheOutcome.MISS_UNAVAILABLE, lookup, error)

        await self._metrics.record(
            PlanCacheOutcome.HIT_EXACT if is_exact else PlanCacheOutcome.HIT_SIMILAR
        )
        await self._count_hit(match.template.template_id)
        await self._sessions.append_entry(
            session_id, SessionEntry(user_question=question, query_plan=plan)
        )
        answer = CachedAnswer(
            reply=found_reply(page),
            query_plan=plan,
            page=page,
            template_text=match.template.template_text,
            similarity=match.similarity,
        )
        return answer, lookup

    async def _learn(
        self, lookup: CacheLookup, question: str, answer: AgentAnswer
    ) -> PlanCacheOutcome | None:
        extracted, embedding = lookup.extracted, lookup.embedding
        if extracted is None:
            return None  # the cache couldn't be used for this question at all
        plan = answer.query_plan
        if (
            not extracted.is_self_contained
            or plan is None
            or plan.last_search is None
            or answer.result_page is None
            or answer.reached_round_limit
        ):
            return PlanCacheOutcome.SKIPPED_UNSUCCESSFUL
        if embedding is None:
            return None  # Voyage or Chroma was unavailable
        if lookup.best_similarity is not None and lookup.best_similarity >= self._hit_threshold:
            return PlanCacheOutcome.SKIPPED_SIMILAR
        try:
            template = template_plan(
                extracted.template_text, plan, extracted.slot_values, question, self._clock.now()
            )
        except UnslottablePlanError as error:
            logger.info("plan not stored: %s", error)
            return PlanCacheOutcome.SKIPPED_UNSLOTTABLE
        await self._store.add(template, embedding)
        return PlanCacheOutcome.STORED

    async def _miss(
        self,
        outcome: PlanCacheOutcome,
        lookup: CacheLookup,
        error: Exception | None = None,
    ) -> tuple[None, CacheLookup]:
        if error is not None:
            logger.info("plan cache miss (%s): %s", outcome.value, error)
        await self._metrics.record(outcome)
        return None, lookup

    async def _count_hit(self, template_id: str) -> None:
        try:
            await self._store.record_hit(template_id, self._clock.now())
        except PlanStoreUnavailableError as error:
            logger.warning("could not count a template hit: %s", error)


def found_reply(page: PlanResultPage) -> str:
    singular, plural = ENTITY_NOUNS[page.entity]
    total = page.result.total_count
    if total == 0:
        return f"No {plural} matched."
    return f"Found {total:,} {singular if total == 1 else plural}."
