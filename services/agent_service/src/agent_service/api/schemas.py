"""Request and response bodies of the agent service API (what the React UI uses)."""

from datetime import datetime
from typing import Literal

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
    plan_source: Literal["agent", "cache"] = "agent"  # "cache": a stored plan, no Opus call


class ExecutePlanRequest(ContractModel):
    query_plan: QueryPlan
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class PlanPageResponse(ContractModel):
    result_entity: SearchableEntity
    result_page: ResultPage


class PlanCacheHits(ContractModel):
    exact: int  # same normalized template text: what a Redis exact-key cache would catch
    similar: int  # similarity >= PLAN_HIT_THRESHOLD, different wording


class PlanCacheMisses(ContractModel):
    no_templates: int
    below_threshold: int
    follow_up: int
    slot_mismatch: int  # the question's slots differ from the stored template's
    invalid_plan: int
    unavailable: int  # Voyage, Haiku or Chroma could not be used


class PlanCacheLearned(ContractModel):
    stored: int
    skipped_similar: int  # a template at least this similar was already stored
    skipped_unsuccessful: int  # no successful plan, or a follow-up question
    skipped_unslottable: int  # the plan couldn't be made reusable safely


class TemplateUsage(ContractModel):
    template_text: str
    hit_count: int
    last_used_at: datetime | None


class PlanCacheMetricsResponse(ContractModel):
    enabled: bool  # PLAN_CACHE_ENABLED and a VOYAGE_API_KEY
    store_available: bool  # False: Chroma unreachable; counts are this process's only
    since: datetime | None
    lookups: int
    hits: PlanCacheHits
    misses: PlanCacheMisses
    learned: PlanCacheLearned
    hit_rate: float  # hits / lookups
    exact_match_rate: float  # exact hits / lookups: decides whether Redis is worth adding
    top_templates: list[TemplateUsage]
