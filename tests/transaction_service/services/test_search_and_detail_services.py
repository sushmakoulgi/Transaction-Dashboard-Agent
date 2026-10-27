"""Integration tests for SearchService and TransactionDetailService.

Requires the seeded docker-compose Postgres. The seed is relative to today, so the services
use the real clock; a clock set to last year checks the current-year scope.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from filter_test_helpers import NEW_YORK, condition, group
from sqlalchemy import text

from shared.clock import Clock, FixedClock, SystemClock
from shared.contracts import FilterIssueCode, SearchableEntity, SearchRequest
from transaction_service.filtering.business_calendar import BusinessCalendar
from transaction_service.filtering.condition_resolver import ConditionResolver
from transaction_service.filtering.field_types import FieldValueCoercer
from transaction_service.filtering.filter_compiler import FilterCompiler
from transaction_service.filtering.filter_validator import (
    FilterValidationFailedError,
    FilterValidator,
)
from transaction_service.persistence.database import ReadOnlyDatabase
from transaction_service.services.search_service import SearchService
from transaction_service.services.transaction_detail_service import (
    TransactionDetailService,
    TransactionNotFoundError,
)

pytestmark = pytest.mark.integration

ONE_YEAR_AGO_CLOCK = FixedClock(datetime(datetime.now(UTC).year - 1, 6, 15, 16, tzinfo=UTC))


def build_search_service(database: ReadOnlyDatabase, clock: Clock) -> SearchService:
    calendar = BusinessCalendar(NEW_YORK, clock)
    return SearchService(
        database,
        FilterValidator(ConditionResolver(FieldValueCoercer(NEW_YORK), calendar)),
        calendar,
        FilterCompiler(),
    )


def build_detail_service(database: ReadOnlyDatabase, clock: Clock) -> TransactionDetailService:
    return TransactionDetailService(database, BusinessCalendar(NEW_YORK, clock))


def request_with(**fields: Any) -> SearchRequest:
    return SearchRequest.model_validate(fields)


async def scalar(database: ReadOnlyDatabase, sql: str, **params: Any) -> Any:
    async with database.session() as session:
        return (await session.execute(text(sql), params)).scalar_one()


# --- SearchService ---


async def test_searches_transactions_for_a_customer(readonly_database: ReadOnlyDatabase) -> None:
    customer_id = await scalar(
        readonly_database,
        "SELECT customer_id FROM transactions GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
    )
    result = await build_search_service(readonly_database, SystemClock()).search_transactions(
        request_with(filter=condition("customer_id", "eq", customer_id))
    )
    assert result.total_count == await scalar(
        readonly_database,
        "SELECT count(*) FROM transactions WHERE customer_id = :customer_id",
        customer_id=customer_id,
    )
    assert {item.customer_id for item in result.items} == {customer_id}


async def test_searches_a_customers_chargebacks_this_month(
    readonly_database: ReadOnlyDatabase,
) -> None:
    customer_id = await scalar(
        readonly_database, "SELECT customer_id FROM chargebacks ORDER BY opened_at DESC LIMIT 1"
    )
    result = await build_search_service(readonly_database, SystemClock()).search_chargebacks(
        request_with(
            filter=group(
                condition("customer_id", "eq", customer_id),
                condition("opened_at", "between", {"relative": "this_month"}),
            )
        )
    )
    assert result.total_count >= 1
    assert {item.customer_id for item in result.items} == {customer_id}


async def test_search_without_a_date_filter_is_limited_to_the_current_year(
    readonly_database: ReadOnlyDatabase,
) -> None:
    # All seeded data is in the current year, so a service living one year ago sees nothing.
    last_year_service = build_search_service(readonly_database, ONE_YEAR_AGO_CLOCK)
    transactions = await last_year_service.search_transactions(SearchRequest())
    chargebacks = await last_year_service.search_chargebacks(SearchRequest())
    assert (transactions.total_count, chargebacks.total_count) == (0, 0)


async def test_invalid_search_raises_with_every_issue(readonly_database: ReadOnlyDatabase) -> None:
    service = build_search_service(readonly_database, SystemClock())
    with pytest.raises(FilterValidationFailedError) as error:
        await service.search_transactions(
            request_with(
                filter=group(condition("cust_id", "eq", "C-1"), condition("amount", "gt", "lots"))
            )
        )
    assert [issue.code for issue in error.value.issues] == [
        FilterIssueCode.UNKNOWN_FIELD,
        FilterIssueCode.INVALID_VALUE,
    ]


def test_validate_returns_issues_without_running_a_query(
    readonly_database: ReadOnlyDatabase,
) -> None:
    service = build_search_service(readonly_database, SystemClock())
    assert service.validate(SearchableEntity.TRANSACTIONS, SearchRequest()) == []
    issues = service.validate(
        SearchableEntity.CHARGEBACKS, request_with(sort={"field": "reason_code"})
    )
    assert [issue.code for issue in issues] == [FilterIssueCode.FIELD_NOT_SORTABLE]


def test_describes_the_fields_of_each_entity(readonly_database: ReadOnlyDatabase) -> None:
    service = build_search_service(readonly_database, SystemClock())
    transaction_fields = {
        field.name for field in service.describe_fields(SearchableEntity.TRANSACTIONS)
    }
    chargeback_fields = {
        field.name for field in service.describe_fields(SearchableEntity.CHARGEBACKS)
    }
    assert {"customer_id", "card_brand", "has_refund"} <= transaction_fields
    assert {"opened_at", "chargeback_event_type"} <= chargeback_fields


# --- TransactionDetailService ---


async def test_detail_of_a_transaction_with_two_refunds(
    readonly_database: ReadOnlyDatabase,
) -> None:
    service = build_detail_service(readonly_database, SystemClock())
    refunded_transaction_id = await scalar(
        readonly_database,
        "SELECT p.transaction_id FROM refunds r JOIN payments p "
        "ON p.payment_id = r.original_payment_id GROUP BY 1 HAVING count(*) = 2 LIMIT 1",
    )
    detail = await service.get_transaction_detail(refunded_transaction_id)
    assert detail.transaction_id == refunded_transaction_id
    assert len(detail.refunds) == 2
    assert detail.refunds[0].refunded_at <= detail.refunds[1].refunded_at
    assert detail.payment.payment_status == "CAPTURED"
    assert "merchant_name" in detail.merchant_details
    assert "channel" in detail.metadata


async def test_detail_includes_the_chargeback_event_timeline(
    readonly_database: ReadOnlyDatabase,
) -> None:
    service = build_detail_service(readonly_database, SystemClock())
    transaction_id = await scalar(
        readonly_database,
        "SELECT c.transaction_id FROM chargebacks c JOIN chargeback_events e USING (chargeback_id) "
        "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
    )
    detail = await service.get_transaction_detail(transaction_id)
    assert detail.chargeback is not None
    sequences = [event.event_sequence for event in detail.chargeback.events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert detail.chargeback.events[0].event_type == "OPENED"
    assert len(sequences) == 4


async def test_detail_without_refunds_or_chargeback(readonly_database: ReadOnlyDatabase) -> None:
    service = build_detail_service(readonly_database, SystemClock())
    transaction_id = await scalar(
        readonly_database,
        "SELECT t.transaction_id FROM transactions t JOIN payments p USING (transaction_id) "
        "WHERE NOT EXISTS (SELECT 1 FROM chargebacks c WHERE c.transaction_id = t.transaction_id) "
        "AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.original_payment_id = p.payment_id) "
        "LIMIT 1",
    )
    detail = await service.get_transaction_detail(transaction_id)
    assert (detail.chargeback, detail.refunds) == (None, ())


async def test_unknown_transaction_is_not_found(readonly_database: ReadOnlyDatabase) -> None:
    service = build_detail_service(readonly_database, SystemClock())
    with pytest.raises(TransactionNotFoundError):
        await service.get_transaction_detail(uuid.uuid4())


async def test_transaction_outside_the_current_year_is_not_found(
    readonly_database: ReadOnlyDatabase,
) -> None:
    transaction_id = await scalar(
        readonly_database, "SELECT transaction_id FROM transactions LIMIT 1"
    )
    await build_detail_service(readonly_database, SystemClock()).get_transaction_detail(
        transaction_id
    )
    with pytest.raises(TransactionNotFoundError):
        await build_detail_service(readonly_database, ONE_YEAR_AGO_CLOCK).get_transaction_detail(
            transaction_id
        )


async def test_detail_serializes_to_json(readonly_database: ReadOnlyDatabase) -> None:
    transaction_id = await scalar(
        readonly_database, "SELECT transaction_id FROM transactions LIMIT 1"
    )
    detail = await build_detail_service(readonly_database, SystemClock()).get_transaction_detail(
        transaction_id
    )
    assert detail.model_dump(mode="json")["transaction_id"] == str(transaction_id)
