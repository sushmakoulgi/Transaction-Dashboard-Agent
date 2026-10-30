"""The agent service API used by the React UI."""

import contextlib
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status

from agent_service.api.dependencies import AgentServiceContainer, get_container
from agent_service.api.schemas import (
    ChatRequest,
    ChatResponse,
    ExecutePlanRequest,
    PlanCacheHits,
    PlanCacheLearned,
    PlanCacheMetricsResponse,
    PlanCacheMisses,
    PlanPageResponse,
    ResultPage,
    TemplateUsage,
)
from agent_service.memory.session_store import SessionEntry
from agent_service.plan_cache.chroma_connection import PlanStoreUnavailableError
from agent_service.plan_cache.plan_cache_metrics import PlanCacheMetricsSnapshot
from agent_service.plan_cache.plan_cache_metrics import PlanCacheOutcome as Outcome
from shared.contracts import (
    ChargebackSummary,
    ErrorResponse,
    PaginatedResult,
    SearchableEntity,
    TransactionDetail,
    TransactionSummary,
)
from shared.contracts.identifiers import TRANSACTION_ID_PATTERN

Container = Annotated[AgentServiceContainer, Depends(get_container)]
ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request"},
    502: {"model": ErrorResponse, "description": "Claude returned an unusable reply"},
    503: {"model": ErrorResponse, "description": "Claude or a backing service is unavailable"},
}

router = APIRouter(prefix="/api")
TOP_TEMPLATES_SHOWN = 10


@router.post("/chat", response_model=ChatResponse, responses=ERRORS)
async def chat(request: ChatRequest, container: Container) -> ChatResponse:
    """Answer a question. Omit session_id to start a new session; send it back to continue."""
    session_id = request.session_id or uuid.uuid4().hex
    answer = await container.query_agent.answer(session_id, request.message)
    result_page = (
        to_result_page(answer.result_entity, answer.result_page)
        if answer.result_entity is not None and answer.result_page is not None
        else None
    )
    return ChatResponse(
        session_id=session_id,
        reply=answer.reply,
        query_plan=answer.query_plan,
        result_entity=answer.result_entity,
        result_page=result_page,
        reached_round_limit=answer.reached_round_limit,
    )


@router.post("/plans/execute", response_model=PlanPageResponse, responses=ERRORS)
async def execute_plan(request: ExecutePlanRequest, container: Container) -> PlanPageResponse:
    """Another page of a chat answer's results, without calling Claude."""
    page = await container.plan_executor.execute(
        request.query_plan, request.page, request.page_size
    )
    return PlanPageResponse(result_entity=page.entity, result_page=page.result)


@router.get("/transactions/{transaction_id}", response_model=TransactionDetail, responses=ERRORS)
async def get_transaction_detail(
    transaction_id: Annotated[str, Path(pattern=TRANSACTION_ID_PATTERN)], container: Container
) -> TransactionDetail:
    """Everything about one transaction, for the detail view."""
    return await container.transaction_service.get_transaction_detail(transaction_id)


@router.get("/sessions/{session_id}/entries", response_model=list[SessionEntry], responses=ERRORS)
async def get_session_entries(session_id: str, container: Container) -> list[SessionEntry]:
    """What the session remembers: questions and their query plans only."""
    return await container.sessions.load_recent_entries(session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def delete_session(session_id: str, container: Container) -> Response:
    """Forget a session (for example, a 'new conversation' button)."""
    await container.sessions.delete_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/metrics/plan-cache", response_model=PlanCacheMetricsResponse)
async def plan_cache_metrics(container: Container) -> PlanCacheMetricsResponse:
    """Plan cache hits (exact vs similar), misses by reason and what was learned, since the
    first recorded question. Available even while the cache is off or Chroma is down."""
    snapshot = await container.plan_cache_metrics.snapshot()
    top_templates: list[TemplateUsage] = []
    # Without the template list, the counters are still worth showing.
    with contextlib.suppress(PlanStoreUnavailableError):
        if snapshot.store_available:
            top_templates = [
                TemplateUsage(
                    template_text=template.template_text,
                    hit_count=template.hit_count,
                    last_used_at=template.last_used_at,
                )
                for template in await container.plan_store.top_templates(TOP_TEMPLATES_SHOWN)
            ]
    return to_metrics_response(snapshot, container.plan_cache_active, top_templates)


def to_metrics_response(
    snapshot: PlanCacheMetricsSnapshot, enabled: bool, top_templates: list[TemplateUsage]
) -> PlanCacheMetricsResponse:
    def count(outcome: Outcome) -> int:
        return snapshot.counts[outcome]

    return PlanCacheMetricsResponse(
        enabled=enabled,
        store_available=snapshot.store_available,
        since=snapshot.since,
        lookups=snapshot.lookups,
        hits=PlanCacheHits(exact=count(Outcome.HIT_EXACT), similar=count(Outcome.HIT_SIMILAR)),
        misses=PlanCacheMisses(
            no_templates=count(Outcome.MISS_NO_TEMPLATES),
            below_threshold=count(Outcome.MISS_BELOW_THRESHOLD),
            follow_up=count(Outcome.MISS_FOLLOW_UP),
            missing_slot=count(Outcome.MISS_MISSING_SLOT),
            invalid_plan=count(Outcome.MISS_INVALID_PLAN),
            unavailable=count(Outcome.MISS_UNAVAILABLE),
        ),
        learned=PlanCacheLearned(
            stored=count(Outcome.STORED),
            skipped_similar=count(Outcome.SKIPPED_SIMILAR),
            skipped_unsuccessful=count(Outcome.SKIPPED_UNSUCCESSFUL),
            skipped_unslottable=count(Outcome.SKIPPED_UNSLOTTABLE),
        ),
        hit_rate=snapshot.hit_rate,
        exact_match_rate=snapshot.exact_match_rate,
        top_templates=top_templates,
    )


def to_result_page(entity: SearchableEntity, page: dict[str, Any]) -> ResultPage:
    """The agent's untyped page (from the MCP tool) as the typed page for its entity."""
    if entity is SearchableEntity.TRANSACTIONS:
        return PaginatedResult[TransactionSummary].model_validate(page)
    return PaginatedResult[ChargebackSummary].model_validate(page)
