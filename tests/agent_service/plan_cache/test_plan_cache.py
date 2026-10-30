"""PlanCache with fakes for Haiku, Voyage, Chroma and the Transaction Service (no network)."""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_service.agent.query_agent import AgentAnswer
from agent_service.memory.session_store import InMemorySessionStore, SessionEntry
from agent_service.plan_cache.chroma_connection import PlanStoreUnavailableError
from agent_service.plan_cache.chroma_plan_store import PlanMatch
from agent_service.plan_cache.embedding_provider import EmbeddingUnavailableError
from agent_service.plan_cache.plan_cache import CacheLookup, PlanCache, found_reply
from agent_service.plan_cache.plan_cache_metrics import PlanCacheOutcome as Outcome
from agent_service.plan_cache.query_template_extractor import (
    ExtractedQuery,
    TemplateExtractionError,
)
from agent_service.plan_cache.slot_substitutor import StoredPlanTemplate
from agent_service.plan_execution.plan_executor import PlanExecutor, PlanResultPage
from shared.clients.transaction_service_client import TransactionServiceError
from shared.clock import FixedClock
from shared.contracts import (
    ChargebackSummary,
    PaginatedResult,
    QueryPlan,
    SearchableEntity,
    SearchRequest,
    TransactionSummary,
)

CLOCK = FixedClock(datetime(2026, 10, 29, 15, tzinfo=UTC))
SESSION = "session-1"
CUSTOMER_TEMPLATE = "chargebacks for customer {customer_id} this month"
VECTORS = {
    CUSTOMER_TEMPLATE: [1.0, 0.0, 0.0],
    "chargebacks of customer {customer_id} this month": [0.98, 0.2, 0.0],  # ~0.98 similar
    "chargebacks for customer {customer_id} over {amount} this month": [0.97, 0.24, 0.0],
    "refunds over {amount}": [0.0, 0.0, 1.0],
}


def chargeback_plan(customer_id: str) -> QueryPlan:
    return QueryPlan.model_validate(
        {
            "steps": [
                {
                    "tool_name": "search_chargebacks",
                    "filter": {
                        "kind": "group",
                        "children": [
                            {
                                "kind": "condition",
                                "field": "customer_id",
                                "operator": "eq",
                                "value": customer_id,
                            },
                            {
                                "kind": "condition",
                                "field": "opened_at",
                                "operator": "between",
                                "value": {"relative": "this_month"},
                            },
                        ],
                    },
                }
            ]
        }
    )


def extracted(template: str, slots: dict[str, str] | None = None, self_contained: bool = True):
    return ExtractedQuery(
        template_text=template, slot_values=slots or {}, is_self_contained=self_contained
    )


def agent_answer(plan: QueryPlan | None, **overrides: Any) -> AgentAnswer:
    fields: dict[str, Any] = {
        "reply": "Customer 10001641 has 2 chargebacks this month.",
        "query_plan": plan,
        "result_page": {
            "items": [],
            "page": 1,
            "page_size": 25,
            "total_count": 2,
            "total_pages": 1,
        },
        "result_entity": SearchableEntity.CHARGEBACKS,
        "claude_calls": 2,
        "tool_calls": 1,
        "reached_round_limit": False,
    }
    return AgentAnswer(**(fields | overrides))


class FakeExtractor:
    def __init__(self) -> None:
        self.next: ExtractedQuery | Exception = extracted(CUSTOMER_TEMPLATE)
        self.seen_entries: list[Sequence[SessionEntry]] = []

    async def extract(
        self, question: str, previous_entries: Sequence[SessionEntry]
    ) -> ExtractedQuery:
        self.seen_entries.append(previous_entries)
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


class FakeEmbeddings:
    failure: Exception | None = None

    async def embed(self, text: str) -> list[float]:
        if self.failure is not None:
            raise self.failure
        return VECTORS[text]


class InMemoryPlanStore:
    def __init__(self) -> None:
        self.records: dict[str, tuple[StoredPlanTemplate, list[float]]] = {}
        self.hits: list[str] = []
        self.failure: Exception | None = None

    async def nearest(self, embedding: list[float]) -> PlanMatch | None:
        if self.failure is not None:
            raise self.failure
        if not self.records:
            return None
        template, vector = max(
            self.records.values(), key=lambda record: cosine(embedding, record[1])
        )
        return PlanMatch(template, cosine(embedding, vector))

    async def add(self, template: StoredPlanTemplate, embedding: list[float]) -> None:
        self.records[template.template_id] = (template, embedding)

    async def record_hit(self, template_id: str, now: datetime) -> None:
        self.hits.append(template_id)


class RecordingMetrics:
    def __init__(self) -> None:
        self.outcomes: list[Outcome] = []

    async def record(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)


class FakeTransactionService:
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []
        self.failure: Exception | None = None

    async def search_transactions(
        self, request: SearchRequest
    ) -> PaginatedResult[TransactionSummary]:
        raise AssertionError("not used")

    async def search_chargebacks(
        self, request: SearchRequest
    ) -> PaginatedResult[ChargebackSummary]:
        if self.failure is not None:
            raise self.failure
        self.requests.append(request)
        return PaginatedResult[ChargebackSummary](
            items=[], page=request.page, page_size=request.page_size, total_count=3, total_pages=1
        )


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.hypot(*a) * math.hypot(*b))


class Harness:
    def __init__(self, threshold: float = 0.90) -> None:
        self.extractor = FakeExtractor()
        self.embeddings = FakeEmbeddings()
        self.store = InMemoryPlanStore()
        self.metrics = RecordingMetrics()
        self.sessions = InMemorySessionStore(10, timedelta(hours=1), CLOCK)
        self.service = FakeTransactionService()
        self.cache = PlanCache(
            self.extractor,
            self.embeddings,
            self.store,
            self.metrics,
            self.sessions,
            PlanExecutor(self.service),
            CLOCK,
            threshold,
        )

    async def ask(self, question: str, template: ExtractedQuery) -> tuple[Any, CacheLookup]:
        self.extractor.next = template
        return await self.cache.try_answer(SESSION, question)

    async def learn_customer_plan(self) -> None:
        question = "Chargebacks for customer 10001641 this month?"
        cached, lookup = await self.ask(
            question, extracted(CUSTOMER_TEMPLATE, {"customer_id": "10001641"})
        )
        assert cached is None
        plan = chargeback_plan("10001641")
        # On a miss the agent answers and saves the turn to the session.
        await self.sessions.append_entry(
            SESSION, SessionEntry(user_question=question, query_plan=plan)
        )
        await self.cache.learn(lookup, question, agent_answer(plan))


@pytest.fixture
def harness() -> Harness:
    return Harness()


async def test_first_question_misses_and_is_learned_then_reused_for_another_customer(
    harness: Harness,
) -> None:
    await harness.learn_customer_plan()
    assert harness.metrics.outcomes == [Outcome.MISS_NO_TEMPLATES, Outcome.STORED]
    assert len(harness.store.records) == 1

    question = "chargebacks for customer 10033317 this month"
    cached, _ = await harness.ask(
        question, extracted(CUSTOMER_TEMPLATE, {"customer_id": "10033317"})
    )

    assert cached is not None
    assert cached.query_plan == chargeback_plan("10033317")
    assert cached.reply == "Found 3 chargebacks."
    assert cached.page.entity is SearchableEntity.CHARGEBACKS
    (request,) = harness.service.requests
    assert request.page == 1 and request.filter is not None
    assert "10033317" in request.filter.model_dump_json()
    assert harness.metrics.outcomes[-1] is Outcome.HIT_EXACT
    assert harness.store.hits == list(harness.store.records)
    entries = await harness.sessions.load_recent_entries(SESSION)
    assert entries[-1] == SessionEntry(user_question=question, query_plan=cached.query_plan)


async def test_different_wording_above_the_threshold_is_a_similar_hit(harness: Harness) -> None:
    await harness.learn_customer_plan()
    cached, lookup = await harness.ask(
        "chargebacks of customer 10033317 this month",
        extracted("chargebacks of customer {customer_id} this month", {"customer_id": "10033317"}),
    )
    assert cached is not None
    assert harness.metrics.outcomes[-1] is Outcome.HIT_SIMILAR
    assert lookup.best_similarity == pytest.approx(cached.similarity)
    assert 0.9 < cached.similarity < 1.0


async def test_below_the_threshold_misses_and_the_new_shape_is_learned(harness: Harness) -> None:
    await harness.learn_customer_plan()
    question = "refunds over $500"
    cached, lookup = await harness.ask(
        question, extracted("refunds over {amount}", {"amount": "$500"})
    )
    assert cached is None
    assert harness.metrics.outcomes[-1] is Outcome.MISS_BELOW_THRESHOLD

    refunds = QueryPlan.model_validate(
        {
            "steps": [
                {
                    "tool_name": "search_transactions",
                    "filter": {
                        "kind": "condition",
                        "field": "amount",
                        "operator": "gt",
                        "value": 500,
                    },
                }
            ]
        }
    )
    await harness.cache.learn(
        lookup, question, agent_answer(refunds, result_entity=SearchableEntity.TRANSACTIONS)
    )
    assert harness.metrics.outcomes[-1] is Outcome.STORED
    assert len(harness.store.records) == 2


async def test_an_extra_slot_never_reuses_a_plan_that_would_drop_it(harness: Harness) -> None:
    await harness.learn_customer_plan()
    question = "chargebacks for customer 10033317 over $500 this month"
    cached, lookup = await harness.ask(
        question,
        extracted(
            "chargebacks for customer {customer_id} over {amount} this month",
            {"customer_id": "10033317", "amount": "$500"},
        ),
    )
    assert cached is None
    assert harness.metrics.outcomes[-1] is Outcome.MISS_SLOT_MISMATCH
    assert harness.service.requests == []

    # Near-duplicates are not stored again (similarity >= threshold).
    await harness.cache.learn(lookup, question, agent_answer(chargeback_plan("10033317")))
    assert harness.metrics.outcomes[-1] is Outcome.SKIPPED_SIMILAR
    assert len(harness.store.records) == 1


async def test_follow_ups_go_to_the_agent_and_are_not_learned(harness: Harness) -> None:
    await harness.learn_customer_plan()
    cached, lookup = await harness.ask(
        "does it have a refund?", extracted("does it have a refund", self_contained=False)
    )
    assert cached is None
    assert harness.metrics.outcomes[-1] is Outcome.MISS_FOLLOW_UP
    assert harness.extractor.seen_entries[-1]  # the session's earlier question was sent
    await harness.cache.learn(
        lookup, "does it have a refund?", agent_answer(chargeback_plan("10001641"))
    )
    assert harness.metrics.outcomes[-1] is Outcome.SKIPPED_UNSUCCESSFUL


@pytest.mark.parametrize("broken", ["extractor", "embeddings", "store"])
async def test_an_unavailable_service_is_a_miss_and_nothing_is_learned(
    harness: Harness, broken: str
) -> None:
    if broken == "extractor":
        harness.extractor.next = TemplateExtractionError("Haiku down")
    elif broken == "embeddings":
        harness.embeddings.failure = EmbeddingUnavailableError("Voyage down")
    else:
        harness.store.failure = PlanStoreUnavailableError("Chroma down")
    if broken != "extractor":
        harness.extractor.next = extracted(CUSTOMER_TEMPLATE, {"customer_id": "10001641"})

    cached, lookup = await harness.cache.try_answer(
        SESSION, "chargebacks for customer 10001641 this month"
    )
    assert cached is None
    assert harness.metrics.outcomes == [Outcome.MISS_UNAVAILABLE]

    await harness.cache.learn(
        lookup,
        "chargebacks for customer 10001641 this month",
        agent_answer(chargeback_plan("10001641")),
    )
    assert harness.metrics.outcomes == [Outcome.MISS_UNAVAILABLE]  # nothing learned
    assert harness.store.records == {}


async def test_a_plan_the_service_rejects_is_a_miss(harness: Harness) -> None:
    await harness.learn_customer_plan()
    harness.service.failure = TransactionServiceError(422, "FILTER_VALIDATION_FAILED", "bad filter")
    cached, _ = await harness.ask(
        "chargebacks for customer 10033317 this month",
        extracted(CUSTOMER_TEMPLATE, {"customer_id": "10033317"}),
    )
    assert cached is None
    assert harness.metrics.outcomes[-1] is Outcome.MISS_INVALID_PLAN
    # Only the first question's entry: the agent saves this one when it answers.
    assert len(await harness.sessions.load_recent_entries(SESSION)) == 1


async def test_unexpected_errors_never_fail_the_question(harness: Harness) -> None:
    harness.extractor.next = RuntimeError("bug")  # not a TemplateExtractionError
    cached, lookup = await harness.cache.try_answer(SESSION, "anything")
    assert (cached, lookup) == (None, CacheLookup())
    assert harness.metrics.outcomes == [Outcome.MISS_UNAVAILABLE]


@pytest.mark.parametrize(
    "answer",
    [
        agent_answer(None, result_page=None, result_entity=None),  # no search ran
        agent_answer(chargeback_plan("10001641"), reached_round_limit=True),
        agent_answer(
            QueryPlan.model_validate(
                {
                    "steps": [
                        {"tool_name": "get_transaction_detail", "transaction_id": "856786156334238"}
                    ]
                }
            ),
            result_page=None,
            result_entity=None,
        ),
    ],
)
async def test_unsuccessful_or_detail_only_answers_are_not_learned(answer: AgentAnswer) -> None:
    harness = Harness()
    question = "chargebacks for customer 10001641 this month"
    _, lookup = await harness.ask(
        question, extracted(CUSTOMER_TEMPLATE, {"customer_id": "10001641"})
    )
    await harness.cache.learn(lookup, question, answer)
    assert harness.metrics.outcomes[-1] is Outcome.SKIPPED_UNSUCCESSFUL
    assert harness.store.records == {}


async def test_plans_with_question_values_left_in_are_not_learned(harness: Harness) -> None:
    question = "chargebacks for customer 10001641 this month"
    _, lookup = await harness.ask(question, extracted(CUSTOMER_TEMPLATE))  # Haiku missed the slot
    await harness.cache.learn(lookup, question, agent_answer(chargeback_plan("10001641")))
    assert harness.metrics.outcomes[-1] is Outcome.SKIPPED_UNSLOTTABLE
    assert harness.store.records == {}


@pytest.mark.parametrize(
    ("total", "reply"),
    [
        (0, "No chargebacks matched."),
        (1, "Found 1 chargeback."),
        (1234, "Found 1,234 chargebacks."),
    ],
)
def test_found_reply(total: int, reply: str) -> None:
    page = PlanResultPage(
        entity=SearchableEntity.CHARGEBACKS,
        result=PaginatedResult[ChargebackSummary](
            items=[], page=1, page_size=25, total_count=total, total_pages=math.ceil(total / 25)
        ),
    )
    assert found_reply(page) == reply
