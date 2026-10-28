"""Request and response bodies of the agent service API (what the React UI uses)."""

from pydantic import Field

from agent_service.memory.session_store import MAX_QUESTION_LENGTH, SESSION_ID_PATTERN
from shared.contracts import (
    ChargebackSummary,
    PaginatedResult,
    QueryPlan,
    SearchableEntity,
    TransactionSummary,
)
from shared.contracts.contract_model import ContractModel
from shared.contracts.search_request import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

ResultPage = PaginatedResult[TransactionSummary] | PaginatedResult[ChargebackSummary]


class ChatRequest(ContractModel):
    message: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN.pattern)


class ChatResponse(ContractModel):
    session_id: str  # send it back with the next question to keep the context
    reply: str
    query_plan: QueryPlan | None  # send it to /plans/execute to page through the results
    result_entity: SearchableEntity | None
    result_page: ResultPage | None
    reached_round_limit: bool


class ExecutePlanRequest(ContractModel):
    query_plan: QueryPlan
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class PlanPageResponse(ContractModel):
    result_entity: SearchableEntity
    result_page: ResultPage
