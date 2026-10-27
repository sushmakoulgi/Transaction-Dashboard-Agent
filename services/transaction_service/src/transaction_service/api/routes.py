"""Read-only HTTP API. Searches use POST only so the filter tree can travel as a JSON body."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from shared.contracts import (
    ChargebackSummary,
    ErrorResponse,
    FilterableFieldDescription,
    FilterValidationResult,
    PaginatedResult,
    SearchableEntity,
    SearchRequest,
    TransactionDetail,
    TransactionSummary,
)
from transaction_service.api.dependencies import (
    get_search_service,
    get_transaction_detail_service,
)
from transaction_service.services.search_service import SearchService
from transaction_service.services.transaction_detail_service import TransactionDetailService

SearchServiceDependency = Annotated[SearchService, Depends(get_search_service)]
TransactionDetailServiceDependency = Annotated[
    TransactionDetailService, Depends(get_transaction_detail_service)
]

INVALID_SEARCH_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorResponse, "description": "Invalid filter, sort or request shape"}
}

router = APIRouter(prefix="/api/v1")


@router.post(
    "/transactions/search",
    response_model=PaginatedResult[TransactionSummary],
    responses=INVALID_SEARCH_RESPONSES,
)
async def search_transactions(
    request: SearchRequest, search_service: SearchServiceDependency
) -> PaginatedResult[TransactionSummary]:
    return await search_service.search_transactions(request)


@router.post(
    "/chargebacks/search",
    response_model=PaginatedResult[ChargebackSummary],
    responses=INVALID_SEARCH_RESPONSES,
)
async def search_chargebacks(
    request: SearchRequest, search_service: SearchServiceDependency
) -> PaginatedResult[ChargebackSummary]:
    return await search_service.search_chargebacks(request)


# Must be registered before /transactions/{transaction_id}, which would otherwise match
# /transactions/filterable-fields and reject "filterable-fields" as a UUID.
@router.get("/{entity}/filterable-fields", response_model=list[FilterableFieldDescription])
async def list_filterable_fields(
    entity: SearchableEntity, search_service: SearchServiceDependency
) -> list[FilterableFieldDescription]:
    return search_service.describe_fields(entity)


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionDetail,
    responses={404: {"model": ErrorResponse, "description": "Unknown or outside current year"}},
)
async def get_transaction_detail(
    transaction_id: uuid.UUID, detail_service: TransactionDetailServiceDependency
) -> TransactionDetail:
    return await detail_service.get_transaction_detail(transaction_id)


@router.post(
    "/{entity}/filters/validate",
    response_model=FilterValidationResult,
    responses={422: {"model": ErrorResponse, "description": "Malformed request shape"}},
)
async def validate_filters(
    entity: SearchableEntity, request: SearchRequest, search_service: SearchServiceDependency
) -> FilterValidationResult:
    issues = search_service.validate(entity, request)
    return FilterValidationResult(is_valid=not issues, issues=tuple(issues))
