"""Re-runs a recorded query plan for any page, straight against the Transaction Service.

This is how the UI pages through results: no Claude call and no MCP hop, so paging is free
and always shows the same query. Only the plan's last search drives the table; detail steps
are not re-run (the UI opens details through the detail endpoint).
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from shared.contracts import (
    ChargebackSummary,
    PaginatedResult,
    QueryPlan,
    SearchableEntity,
    SearchRequest,
    TransactionSummary,
)


class TransactionSearchClient(Protocol):
    """The part of `TransactionServiceClient` the executor needs."""

    async def search_transactions(
        self, request: SearchRequest
    ) -> PaginatedResult[TransactionSummary]: ...

    async def search_chargebacks(
        self, request: SearchRequest
    ) -> PaginatedResult[ChargebackSummary]: ...


class PlanHasNoSearchError(ValueError):
    pass


class InvalidPageRequestError(ValueError):
    pass


@dataclass(frozen=True)
class PlanResultPage:
    entity: SearchableEntity
    result: PaginatedResult[TransactionSummary] | PaginatedResult[ChargebackSummary]


class PlanExecutor:
    def __init__(self, transaction_service: TransactionSearchClient) -> None:
        self._transaction_service = transaction_service

    async def execute(self, plan: QueryPlan, page: int, page_size: int) -> PlanResultPage:
        """Raises `PlanHasNoSearchError`, `InvalidPageRequestError`, or the client's
        `TransactionServiceError` / `TransactionServiceUnavailableError`."""
        search = plan.last_search
        if search is None:
            raise PlanHasNoSearchError("the query plan has no search to show a page of")
        try:
            request = SearchRequest(
                filter=search.filter, sort=search.sort, page=page, page_size=page_size
            )
        except ValidationError as error:
            raise InvalidPageRequestError(
                "; ".join(str(detail["msg"]) for detail in error.errors())
            ) from None
        if search.entity is SearchableEntity.TRANSACTIONS:
            transactions = await self._transaction_service.search_transactions(request)
            return PlanResultPage(entity=search.entity, result=transactions)
        chargebacks = await self._transaction_service.search_chargebacks(request)
        return PlanResultPage(entity=search.entity, result=chargebacks)
