"""The agent service API used by the React UI."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status

from agent_service.api.dependencies import AgentServiceContainer, get_container
from agent_service.api.schemas import (
    ChatRequest,
    ChatResponse,
    ExecutePlanRequest,
    PlanPageResponse,
    ResultPage,
)
from agent_service.memory.session_store import SessionEntry
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


def to_result_page(entity: SearchableEntity, page: dict[str, Any]) -> ResultPage:
    """The agent's untyped page (from the MCP tool) as the typed page for its entity."""
    if entity is SearchableEntity.TRANSACTIONS:
        return PaginatedResult[TransactionSummary].model_validate(page)
    return PaginatedResult[ChargebackSummary].model_validate(page)
