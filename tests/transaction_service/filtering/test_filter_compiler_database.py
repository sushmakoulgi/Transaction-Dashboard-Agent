"""Integration tests: compiled filters return the same rows as hand-written SQL.

Requires the seeded docker-compose Postgres (`make up migrate seed`). The seed is relative to
today, so these tests use the real clock.
"""

from typing import Any

import pytest
from filter_test_helpers import build_validator, condition, group
from sqlalchemy import Connection, inspect, select, text

from shared.clock import SystemClock
from shared.contracts import SearchRequest
from transaction_service.filtering.entity_field_registries import (
    CHARGEBACK_FIELD_REGISTRY,
    TRANSACTION_FIELD_REGISTRY,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.filter_compiler import FilterCompiler, join_required_relations

pytestmark = pytest.mark.integration

VALIDATOR = build_validator(SystemClock())
CURRENT_MONTH_IN_NEW_YORK = (
    "date_trunc('month', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'"
)
NEXT_MONTH_IN_NEW_YORK = (
    "(date_trunc('month', now() AT TIME ZONE 'America/New_York') + interval '1 month')"
    " AT TIME ZONE 'America/New_York'"
)


def matching_ids(
    connection: Connection,
    filter_tree: dict[str, Any],
    registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY,
) -> list[Any]:
    validated = VALIDATOR.validate(SearchRequest.model_validate({"filter": filter_tree}), registry)
    assert validated.filter is not None
    compiled = FilterCompiler().compile(validated.filter)
    primary_key = inspect(registry.root_entity).primary_key[0]
    statement = join_required_relations(
        select(primary_key).select_from(registry.root_entity), compiled.required_relations
    ).where(compiled.where_clause)
    return list(connection.execute(statement).scalars())


def count_by_sql(connection: Connection, sql: str, **params: Any) -> int:
    return int(connection.execute(text(sql), params).scalar_one())


@pytest.fixture
def busiest_customer(readonly_connection: Connection) -> str:
    return str(
        readonly_connection.execute(
            text("SELECT customer_id FROM transactions GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
        ).scalar_one()
    )


def test_transactions_for_a_customer(
    readonly_connection: Connection, busiest_customer: str
) -> None:
    ids = matching_ids(readonly_connection, condition("customer_id", "eq", busiest_customer))
    assert len(ids) == count_by_sql(
        readonly_connection,
        "SELECT count(*) FROM transactions WHERE customer_id = :customer_id",
        customer_id=busiest_customer,
    )
    assert len(ids) > 0


def test_transactions_with_chargebacks_opened_this_month(readonly_connection: Connection) -> None:
    ids = matching_ids(
        readonly_connection,
        condition("chargeback_opened_at", "between", {"relative": "this_month"}),
    )
    assert len(ids) == count_by_sql(
        readonly_connection,
        f"SELECT count(*) FROM chargebacks WHERE opened_at >= {CURRENT_MONTH_IN_NEW_YORK} "
        f"AND opened_at < {NEXT_MONTH_IN_NEW_YORK}",
    )
    assert len(ids) > 0


def test_customer_chargebacks_this_month(readonly_connection: Connection) -> None:
    customer_id = readonly_connection.execute(
        text("SELECT customer_id FROM chargebacks ORDER BY opened_at DESC LIMIT 1")
    ).scalar_one()
    ids = matching_ids(
        readonly_connection,
        group(
            condition("customer_id", "eq", customer_id),
            condition("opened_at", "between", {"relative": "this_month"}),
        ),
        CHARGEBACK_FIELD_REGISTRY,
    )
    assert len(ids) == count_by_sql(
        readonly_connection,
        f"SELECT count(*) FROM chargebacks WHERE customer_id = :customer_id "
        f"AND opened_at >= {CURRENT_MONTH_IN_NEW_YORK} AND opened_at < {NEXT_MONTH_IN_NEW_YORK}",
        customer_id=customer_id,
    )


def test_one_to_many_filters_never_duplicate_rows(readonly_connection: Connection) -> None:
    ids = matching_ids(readonly_connection, condition("has_refund", "exists", True))
    assert len(ids) == len(set(ids))
    assert len(ids) == count_by_sql(
        readonly_connection,
        "SELECT count(DISTINCT p.transaction_id) FROM refunds r "
        "JOIN payments p ON p.payment_id = r.original_payment_id",
    )


def test_anded_refund_conditions_match_the_same_refund(readonly_connection: Connection) -> None:
    ids = matching_ids(
        readonly_connection,
        group(
            condition("refund_status", "eq", "COMPLETED"),
            condition("refund_status", "neq", "PENDING"),
            condition("refunded_at", "between", {"relative": "this_year"}),
        ),
    )
    assert len(ids) == count_by_sql(
        readonly_connection,
        "SELECT count(DISTINCT p.transaction_id) FROM refunds r "
        "JOIN payments p ON p.payment_id = r.original_payment_id "
        "WHERE r.refund_status = 'COMPLETED'",
    )


def test_conflicting_anded_conditions_on_one_refund_match_nothing(
    readonly_connection: Connection,
) -> None:
    # Some payments have one COMPLETED and one PENDING refund, but no refund is both.
    ids = matching_ids(
        readonly_connection,
        group(
            condition("refund_status", "eq", "COMPLETED"),
            condition("refund_status", "eq", "PENDING"),
        ),
    )
    assert ids == []


def test_not_amex_includes_payments_without_a_card(readonly_connection: Connection) -> None:
    not_amex = matching_ids(
        readonly_connection, group(condition("card_brand", "eq", "AMEX"), negate=True)
    )
    amex = matching_ids(readonly_connection, condition("card_brand", "eq", "AMEX"))
    total = count_by_sql(readonly_connection, "SELECT count(*) FROM transactions")
    assert len(not_amex) + len(amex) == total
    ach_count = count_by_sql(
        readonly_connection, "SELECT count(*) FROM payments WHERE payment_method = 'ACH'"
    )
    assert ach_count > 0


def test_chargebacks_with_an_event_type(readonly_connection: Connection) -> None:
    ids = matching_ids(
        readonly_connection,
        condition("chargeback_event_type", "in", ["WON", "LOST"]),
        CHARGEBACK_FIELD_REGISTRY,
    )
    assert len(ids) == count_by_sql(
        readonly_connection,
        "SELECT count(DISTINCT chargeback_id) FROM chargeback_events "
        "WHERE event_type IN ('WON', 'LOST')",
    )


def test_contains_on_a_json_field(readonly_connection: Connection) -> None:
    ids = matching_ids(readonly_connection, condition("merchant_name", "contains", "harbor"))
    assert len(ids) == count_by_sql(
        readonly_connection,
        "SELECT count(*) FROM transactions WHERE merchant_details ->> 'merchant_name' "
        "ILIKE '%harbor%'",
    )
    assert len(ids) > 0


def test_amount_between_is_inclusive(readonly_connection: Connection) -> None:
    ids = matching_ids(readonly_connection, condition("amount", "between", [100, 500]))
    assert len(ids) == count_by_sql(
        readonly_connection, "SELECT count(*) FROM transactions WHERE amount BETWEEN 100 AND 500"
    )
