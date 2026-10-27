"""End-to-end checks of the five use cases (plus OR / NOT / nested filters) through the HTTP API.

Each test compares the exact set of IDs the API returns with hand-written SQL over the seeded
database. Test inputs (dates, customers, payments) are picked from the data, so the tests keep
working after a re-seed on another day.
"""

from typing import Any

import httpx
import pytest
from api_test_helpers import post_search
from filter_test_helpers import condition, group
from sqlalchemy import text

from transaction_service.persistence.database import ReadOnlyDatabase

pytestmark = pytest.mark.integration

NEW_YORK_DATE = "(transaction_date AT TIME ZONE 'America/New_York')::date"
OPENED_THIS_MONTH = (
    "date_trunc('month', opened_at AT TIME ZONE 'America/New_York') "
    "= date_trunc('month', now() AT TIME ZONE 'America/New_York')"
)


async def ids_from_api(
    client: httpx.AsyncClient, entity: str, id_field: str, **request: Any
) -> list[str]:
    """Every matching ID, collected page by page in the order the API returns them."""
    collected: list[str] = []
    page = 1
    while True:
        response = await post_search(client, entity, {**request, "page": page, "page_size": 20})
        assert response.status_code == 200, response.text
        body = response.json()
        collected.extend(item[id_field] for item in body["items"])
        if page >= body["total_pages"]:
            assert len(collected) == body["total_count"]
            return collected
        page += 1


async def ids_from_sql(database: ReadOnlyDatabase, sql: str, **params: Any) -> list[str]:
    async with database.session() as session:
        return [str(row[0]) for row in (await session.execute(text(sql), params)).all()]


async def first_value(database: ReadOnlyDatabase, sql: str) -> Any:
    async with database.session() as session:
        return (await session.execute(text(sql))).scalar_one()


async def assert_same_transactions(
    client: httpx.AsyncClient,
    database: ReadOnlyDatabase,
    filter_tree: dict[str, Any],
    expected_sql: str,
    **params: Any,
) -> list[str]:
    api_ids = await ids_from_api(client, "transactions", "transaction_id", filter=filter_tree)
    assert len(api_ids) == len(set(api_ids)), "duplicate rows"
    expected = await ids_from_sql(database, expected_sql, **params)
    assert set(api_ids) == set(expected)
    assert api_ids, "the filter should match something in the seed data"
    return api_ids


# --- Use case 1: transactions for a given date ---


async def test_use_case_1_transactions_on_a_date(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    busiest_day = await first_value(
        readonly_database,
        f"SELECT {NEW_YORK_DATE} FROM transactions GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
    )
    await assert_same_transactions(
        client,
        readonly_database,
        condition("transaction_date", "eq", busiest_day.isoformat()),
        f"SELECT transaction_id FROM transactions WHERE {NEW_YORK_DATE} = :day",
        day=busiest_day,
    )


# --- Use case 2: transactions with chargebacks this month ---


async def test_use_case_2_transactions_with_chargebacks_this_month(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    await assert_same_transactions(
        client,
        readonly_database,
        condition("chargeback_opened_at", "between", {"relative": "this_month"}),
        f"SELECT transaction_id FROM chargebacks WHERE {OPENED_THIS_MONTH}",
    )


# --- Use case 3: transactions for a customer ---


async def test_use_case_3_transactions_for_a_customer(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    customer_id = await first_value(
        readonly_database,
        "SELECT customer_id FROM transactions GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
    )
    await assert_same_transactions(
        client,
        readonly_database,
        condition("customer_id", "eq", customer_id),
        "SELECT transaction_id FROM transactions WHERE customer_id = :customer_id",
        customer_id=customer_id,
    )


# --- Use case 4: transactions for a payment ID ---


async def test_use_case_4_transaction_for_a_payment(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    payment_id = await first_value(
        readonly_database, "SELECT payment_id FROM payments ORDER BY payment_id LIMIT 1"
    )
    ids = await assert_same_transactions(
        client,
        readonly_database,
        condition("payment_id", "eq", str(payment_id)),
        "SELECT transaction_id FROM payments WHERE payment_id = :payment_id",
        payment_id=payment_id,
    )
    assert len(ids) == 1


# --- Use case 5: a customer's chargebacks this month ---


async def test_use_case_5_customer_chargebacks_this_month(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    customer_id = await first_value(
        readonly_database,
        f"SELECT customer_id FROM chargebacks WHERE {OPENED_THIS_MONTH} "
        "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
    )
    api_ids = await ids_from_api(
        client,
        "chargebacks",
        "chargeback_id",
        filter=group(
            condition("customer_id", "eq", customer_id),
            condition("opened_at", "between", {"relative": "this_month"}),
        ),
    )
    expected = await ids_from_sql(
        readonly_database,
        f"SELECT chargeback_id FROM chargebacks WHERE customer_id = :customer_id "
        f"AND {OPENED_THIS_MONTH}",
        customer_id=customer_id,
    )
    assert set(api_ids) == set(expected)
    assert api_ids


# --- OR, NOT and nested groups ---


async def test_or_returns_the_union(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    await assert_same_transactions(
        client,
        readonly_database,
        group(
            condition("payment_status", "eq", "FAILED"),
            condition("amount", "gt", 1200),
            logic="or",
        ),
        "SELECT t.transaction_id FROM transactions t JOIN payments p USING (transaction_id) "
        "WHERE p.payment_status = 'FAILED' OR t.amount > 1200",
    )


async def test_not_returns_the_complement_including_empty_fields(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    await assert_same_transactions(
        client,
        readonly_database,
        group(condition("card_brand", "in", ["VISA", "MASTERCARD"]), negate=True),
        "SELECT t.transaction_id FROM transactions t JOIN payments p USING (transaction_id) "
        "WHERE p.payment_details ->> 'card_brand' IS NULL "
        "OR p.payment_details ->> 'card_brand' NOT IN ('VISA', 'MASTERCARD')",
    )


async def test_nested_and_or_not_filter(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    # Transactions this year, either refunded or over 500, not on a captured payment.
    await assert_same_transactions(
        client,
        readonly_database,
        group(
            condition("transaction_date", "between", {"relative": "this_year"}),
            group(
                condition("has_refund", "exists", True),
                condition("amount", "gt", 500),
                logic="or",
            ),
            group(condition("payment_status", "eq", "CAPTURED"), negate=True),
        ),
        "SELECT t.transaction_id FROM transactions t JOIN payments p USING (transaction_id) "
        "WHERE (EXISTS (SELECT 1 FROM refunds r WHERE r.original_payment_id = p.payment_id) "
        "OR t.amount > 500) AND p.payment_status <> 'CAPTURED'",
    )


async def test_refund_conditions_combined_with_and_match_one_refund(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    await assert_same_transactions(
        client,
        readonly_database,
        group(
            condition("refund_status", "eq", "COMPLETED"),
            condition("refunded_at", "between", {"relative": "this_year"}),
        ),
        "SELECT DISTINCT p.transaction_id FROM refunds r "
        "JOIN payments p ON p.payment_id = r.original_payment_id "
        "WHERE r.refund_status = 'COMPLETED'",
    )


async def test_chargebacks_with_an_event_of_a_type(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    api_ids = await ids_from_api(
        client,
        "chargebacks",
        "chargeback_id",
        filter=condition("chargeback_event_type", "in", ["WON", "LOST"]),
    )
    expected = await ids_from_sql(
        readonly_database,
        "SELECT DISTINCT chargeback_id FROM chargeback_events WHERE event_type IN ('WON', 'LOST')",
    )
    assert set(api_ids) == set(expected)
    assert api_ids


# --- Ordering across pages ---


async def test_sorted_results_keep_their_order_across_pages(
    client: httpx.AsyncClient, readonly_database: ReadOnlyDatabase
) -> None:
    api_ids = await ids_from_api(
        client, "transactions", "transaction_id", sort={"field": "amount", "direction": "asc"}
    )
    expected = await ids_from_sql(
        readonly_database, "SELECT transaction_id FROM transactions ORDER BY amount, transaction_id"
    )
    assert api_ids == expected
