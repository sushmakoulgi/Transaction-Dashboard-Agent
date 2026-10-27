"""Validated, current-year-scoped search for each searchable entity."""

from pydantic import BaseModel

from shared.contracts import (
    ChargebackSummary,
    FilterableFieldDescription,
    FilterValidationIssue,
    PaginatedResult,
    SearchableEntity,
    SearchRequest,
    TransactionSummary,
)
from transaction_service.filtering.business_calendar import BusinessCalendar
from transaction_service.filtering.entity_field_registries import FIELD_REGISTRY_BY_ENTITY
from transaction_service.filtering.filter_compiler import FilterCompiler
from transaction_service.filtering.filter_validator import (
    FilterValidationFailedError,
    FilterValidator,
)
from transaction_service.persistence.database import ReadOnlyDatabase
from transaction_service.repositories.search_definitions import (
    CHARGEBACK_SEARCH,
    TRANSACTION_SEARCH,
)
from transaction_service.repositories.searchable_repository import (
    SearchableRepository,
    SearchQueryDefinition,
)


class SearchService:
    def __init__(
        self,
        database: ReadOnlyDatabase,
        validator: FilterValidator,
        calendar: BusinessCalendar,
        compiler: FilterCompiler,
    ) -> None:
        self._database = database
        self._validator = validator
        self._calendar = calendar
        self._compiler = compiler

    async def search_transactions(
        self, request: SearchRequest
    ) -> PaginatedResult[TransactionSummary]:
        return await self._search(TRANSACTION_SEARCH, request)

    async def search_chargebacks(
        self, request: SearchRequest
    ) -> PaginatedResult[ChargebackSummary]:
        return await self._search(CHARGEBACK_SEARCH, request)

    def validate(
        self, entity: SearchableEntity, request: SearchRequest
    ) -> list[FilterValidationIssue]:
        """Every problem in the request; an empty list means it is valid."""
        try:
            self._validator.validate(request, FIELD_REGISTRY_BY_ENTITY[entity])
        except FilterValidationFailedError as error:
            return error.issues
        return []

    def describe_fields(self, entity: SearchableEntity) -> list[FilterableFieldDescription]:
        return FIELD_REGISTRY_BY_ENTITY[entity].describe_fields()

    async def _search[ItemT: BaseModel](
        self, definition: SearchQueryDefinition[ItemT], request: SearchRequest
    ) -> PaginatedResult[ItemT]:
        """Raises `FilterValidationFailedError` with every problem in the request."""
        validated = self._validator.validate(request, definition.registry)
        async with self._database.session() as session:
            return await SearchableRepository(definition, self._compiler).search(
                session, validated, self._calendar.current_year_range()
            )
