"""Integration tests for paginated search. Requires the seeded docker-compose Postgres."""

import math
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from filter_test_helpers import condition, group
from pydantic import BaseModel
from sqlalchemy import text

from shared.clock import SystemClock
from shared.contracts import PaginatedResult, SearchRequest
from transaction_service.filtering.business_calendar import BusinessCalendar, DateRange
from transaction_service.filtering.condition_resolver import ConditionResolver
from transaction_service.filtering.field_types import FieldValueCoercer
from transaction_service.filtering.filter_compiler import FilterCompiler
from transaction_service.filtering.filter_validator import FilterValidator
from transaction_service.persistence.database import ReadOnlyDatabase
from transaction_service.repositories.search_definitions import (
    CHARGEBACK_SEARCH,
    TRANSACTION_SEARCH,
)
from transaction_service.repositories.searchable_repository import (
    SearchableRepository,
    SearchQueryDefinition,
)

pytestmark = pytest.mark.integration

NEW_YORK = ZoneInfo("America/New_York")
CALENDAR = BusinessCalendar(NEW_YORK, SystemClock())
VALIDATOR = FilterValidator(ConditionResolver(FieldValueCoercer(NEW_YORK), CALENDAR))
CURRENT_YEAR = CALENDAR.current_year_range()


async def search[ItemT: BaseModel](
    database: ReadOnlyDatabase,
    definition: SearchQueryDefinition[ItemT],
    scope: DateRange = CURRENT_YEAR,
    **request_fields: Any,
) -> PaginatedResult[ItemT]:
    validated = VALIDATOR.validate(
        SearchRequest.model_validate(request_fields), definition.registry
    )
    async with database.session() as session:
        return await SearchableRepository(definition, FilterCompiler()).search(
            session, validated, scope
        )


async def count_by_sql(database: ReadOnlyDatabase, sql: str) -> int:
    async with database.session() as session:
        return int((await session.execute(text(sql))).scalar_one())


async def test_unfiltered_search_counts_every_row(readonly_database: ReadOnlyDatabase) -> None:
    result = await search(readonly_database, TRANSACTION_SEARCH)
    assert result.total_count == 100
    assert result.total_pages == 4
    assert len(result.items) == 25


async def test_paging_is_stable_with_no_gaps_or_overlaps(
    readonly_database: ReadOnlyDatabase,
) -> None:
    pages = [
        await search(readonly_database, TRANSACTION_SEARCH, page=page, page_size=30)
        for page in range(1, 5)
    ]
    ids = [item.transaction_id for page in pages for item in page.items]
    assert [len(page.items) for page in pages] == [30, 30, 30, 10]
    assert len(set(ids)) == 100
    dates = [item.transaction_date for page in pages for item in page.items]
    assert dates == sorted(dates, reverse=True)


async def test_page_past_the_end_is_empty_but_keeps_the_total(
    readonly_database: ReadOnlyDatabase,
) -> None:
    result = await search(readonly_database, TRANSACTION_SEARCH, page=9)
    assert result.items == []
    assert result.total_count == 100


async def test_one_to_many_filter_counts_each_transaction_once(
    readonly_database: ReadOnlyDatabase,
) -> None:
    result = await search(
        readonly_database,
        TRANSACTION_SEARCH,
        filter=condition("has_refund", "exists", True),
        page_size=100,
    )
    expected = await count_by_sql(
        readonly_database,
        "SELECT count(DISTINCT p.transaction_id) FROM refunds r "
        "JOIN payments p ON p.payment_id = r.original_payment_id",
    )
    assert result.total_count == expected == len(result.items)
    assert len({item.transaction_id for item in result.items}) == expected
    assert all(item.has_refund for item in result.items)


async def test_filter_on_a_displayed_relation_joins_it_once(
    readonly_database: ReadOnlyDatabase,
) -> None:
    result = await search(
        readonly_database,
        TRANSACTION_SEARCH,
        filter=group(
            condition("payment_status", "eq", "CAPTURED"),
            condition("has_chargeback", "exists", True),
        ),
        page_size=100,
    )
    assert result.total_count == await count_by_sql(
        readonly_database,
        "SELECT count(*) FROM transactions t JOIN payments p USING (transaction_id) "
        "JOIN chargebacks c USING (transaction_id) WHERE p.payment_status = 'CAPTURED'",
    )
    assert all(item.has_chargeback and item.chargeback_status for item in result.items)


async def test_result_columns_follow_the_data(readonly_database: ReadOnlyDatabase) -> None:
    result = await search(readonly_database, TRANSACTION_SEARCH, page_size=100)
    ach_rows = [item for item in result.items if item.payment_method == "ACH"]
    assert ach_rows
    assert all(item.card_brand is None for item in ach_rows)
    assert sum(item.has_chargeback for item in result.items) == 15
    assert all(item.merchant_name for item in result.items)


async def test_sort_on_a_relation_puts_empty_values_last(
    readonly_database: ReadOnlyDatabase,
) -> None:
    result = await search(
        readonly_database,
        TRANSACTION_SEARCH,
        sort={"field": "chargeback_opened_at", "direction": "asc"},
        page_size=100,
    )
    flags = [item.has_chargeback for item in result.items]
    assert flags == [True] * 15 + [False] * 85


async def test_sort_ascending_by_amount(readonly_database: ReadOnlyDatabase) -> None:
    result = await search(
        readonly_database, TRANSACTION_SEARCH, sort={"field": "amount", "direction": "asc"}
    )
    amounts = [item.amount for item in result.items]
    assert amounts == sorted(amounts)


async def test_scope_limits_both_the_count_and_the_page(
    readonly_database: ReadOnlyDatabase,
) -> None:
    last_year = DateRange(
        datetime(CURRENT_YEAR.start.year - 1, 1, 1, tzinfo=UTC), CURRENT_YEAR.start
    )
    result = await search(readonly_database, TRANSACTION_SEARCH, scope=last_year)
    assert (result.total_count, result.items) == (0, [])


async def test_chargeback_search_returns_the_latest_event(
    readonly_database: ReadOnlyDatabase,
) -> None:
    result = await search(readonly_database, CHARGEBACK_SEARCH, page_size=100)
    assert result.total_count == 15
    async with readonly_database.session() as session:
        latest_by_chargeback = dict(
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT ON (chargeback_id) chargeback_id, event_type "
                        "FROM chargeback_events ORDER BY chargeback_id, event_sequence DESC"
                    )
                )
            ).all()
        )
    for item in result.items:
        assert item.latest_event_type == latest_by_chargeback[item.chargeback_id]
        assert item.currency
    opened = [item.opened_at for item in result.items]
    assert opened == sorted(opened, reverse=True)


async def test_total_pages_rounds_up(readonly_database: ReadOnlyDatabase) -> None:
    result = await search(readonly_database, CHARGEBACK_SEARCH, page_size=4)
    assert result.total_pages == math.ceil(15 / 4)


def test_definition_must_cover_every_result_field() -> None:
    with pytest.raises(ValueError, match="result columns missing for: amount"):
        SearchQueryDefinition(
            registry=TRANSACTION_SEARCH.registry,
            item_model=TRANSACTION_SEARCH.item_model,
            result_columns={
                name: column
                for name, column in TRANSACTION_SEARCH.result_columns.items()
                if name != "amount"
            },
        )
