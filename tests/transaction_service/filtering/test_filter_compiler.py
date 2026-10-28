from typing import Any

from filter_test_helpers import build_validator, condition, group
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from shared.contracts import SearchRequest
from transaction_service.filtering.entity_field_registries import (
    CHARGEBACK_FIELD_REGISTRY,
    TRANSACTION_CHARGEBACK,
    TRANSACTION_FIELD_REGISTRY,
    TRANSACTION_PAYMENT,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.filter_compiler import (
    CompiledFilter,
    FilterCompiler,
    escape_like,
    join_required_relations,
)

VALIDATOR = build_validator()
COMPILER = FilterCompiler()


def compile_filter(
    filter_tree: dict[str, Any], registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY
) -> CompiledFilter:
    validated = VALIDATOR.validate(SearchRequest.model_validate({"filter": filter_tree}), registry)
    assert validated.filter is not None
    return COMPILER.compile(validated.filter)


def sql_and_params(
    filter_tree: dict[str, Any], registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY
) -> tuple[str, dict[str, Any]]:
    compiled = compile_filter(filter_tree, registry)
    statement = join_required_relations(
        select(registry.root_entity), compiled.required_relations
    ).where(compiled.where_clause)
    rendered = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    return " ".join(str(rendered).split()), dict(rendered.params)


def sql_of(
    filter_tree: dict[str, Any], registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY
) -> str:
    return sql_and_params(filter_tree, registry)[0]


SAME_REFUND_CONDITIONS = (
    condition("refund_status", "eq", "COMPLETED"),
    condition("refunded_at", "between", {"relative": "this_month"}),
)


# --- Safety ---


def test_values_are_bound_parameters_never_sql_text() -> None:
    hostile_value = "ATT'; DROP TABLE transactions; --"
    sql, params = sql_and_params(condition("merchant_name", "eq", hostile_value))
    assert "DROP TABLE" not in sql
    assert hostile_value in params.values()


def test_json_key_comes_from_the_registry_as_a_bound_parameter() -> None:
    sql, params = sql_and_params(condition("card_brand", "eq", "VISA"))
    assert "payments.payment_details ->> %(payment_details_1)s" in sql
    assert params["payment_details_1"] == "card_brand"


def test_contains_escapes_like_wildcards() -> None:
    sql, params = sql_and_params(condition("merchant_name", "contains", "50%_off"))
    assert "ILIKE %(param_1)s::VARCHAR ESCAPE '\\'" in sql
    assert params["param_1"] == "%50\\%\\_off%"


def test_escape_like_escapes_the_escape_character_first() -> None:
    assert escape_like("a\\b%c_d") == "a\\\\b\\%c\\_d"


# --- Joins ---


def test_root_only_filters_need_no_join() -> None:
    compiled = compile_filter(condition("customer_id", "eq", "10001641"))
    assert compiled.required_relations == ()
    assert "JOIN" not in sql_of(condition("customer_id", "eq", "10001641"))


def test_each_one_to_one_relation_is_joined_once() -> None:
    compiled = compile_filter(
        group(
            condition("payment_status", "eq", "CAPTURED"),
            condition("card_brand", "eq", "VISA"),
            condition("chargeback_status", "eq", "OPEN"),
        )
    )
    assert compiled.required_relations == (TRANSACTION_PAYMENT, TRANSACTION_CHARGEBACK)


def test_refund_filters_join_the_payment_they_are_reached_through() -> None:
    compiled = compile_filter(condition("has_refund", "exists", True))
    assert compiled.required_relations == (TRANSACTION_PAYMENT,)


# --- One-to-many relations ---


def test_and_conditions_on_refunds_must_match_the_same_refund() -> None:
    sql = sql_of(group(*SAME_REFUND_CONDITIONS, condition("customer_id", "eq", "10001641")))
    assert sql.count("EXISTS") == 1
    assert (
        "EXISTS (SELECT 1 FROM refunds WHERE refunds.original_payment_id = payments.payment_id "
        "AND refunds.refund_status = %(refund_status_1)s::VARCHAR "
        "AND refunds.refunded_at >= %(refunded_at_1)s::TIMESTAMP WITH TIME ZONE "
        "AND refunds.refunded_at < %(refunded_at_2)s::TIMESTAMP WITH TIME ZONE)"
    ) in sql


def test_or_conditions_on_refunds_each_get_their_own_exists() -> None:
    assert sql_of(group(*SAME_REFUND_CONDITIONS, logic="or")).count("EXISTS") == 2


def test_conditions_in_different_nested_groups_are_not_merged() -> None:
    first, second = SAME_REFUND_CONDITIONS
    assert sql_of(group(first, group(second))).count("EXISTS") == 2


def test_exists_checks_are_never_merged_with_value_conditions() -> None:
    sql = sql_of(group(condition("has_refund", "exists", True), SAME_REFUND_CONDITIONS[0]))
    assert sql.count("EXISTS") == 2


def test_subquery_correlates_to_the_outer_row() -> None:
    sql = sql_of(condition("chargeback_event_type", "eq", "WON"), CHARGEBACK_FIELD_REGISTRY)
    assert (
        "EXISTS (SELECT 1 FROM chargeback_events "
        "WHERE chargeback_events.chargeback_id = chargebacks.chargeback_id"
    ) in sql
    refund_sql = sql_of(condition("has_refund", "exists", True))
    assert "FROM refunds WHERE" in refund_sql  # payments comes from the outer query


def test_missing_related_rows() -> None:
    assert "NOT (EXISTS (SELECT 1 FROM refunds" in sql_of(condition("has_refund", "exists", False))
    assert "WHERE chargebacks.chargeback_id IS NULL" in sql_of(
        condition("has_chargeback", "exists", False)
    )


# --- Operators and negation ---


def test_ranges_with_exclusive_and_inclusive_ends() -> None:
    date_sql = sql_of(condition("transaction_date", "eq", "2026-10-15"))
    assert "transactions.transaction_date >= " in date_sql
    assert "transactions.transaction_date < " in date_sql
    amount_sql = sql_of(condition("amount", "between", [100, 500]))
    assert "transactions.amount >= " in amount_sql
    assert "transactions.amount <= " in amount_sql


def test_negation_includes_rows_where_the_field_is_empty() -> None:
    assert "chargebacks.chargeback_status IS DISTINCT FROM" in sql_of(
        condition("chargeback_status", "neq", "WON")
    )
    assert "payments.payment_method IS NULL OR (payments.payment_method NOT IN" in sql_of(
        condition("payment_method", "not_in", ["ACH"])
    )
    assert "WHERE NOT coalesce((payments.payment_details ->>" in sql_of(
        group(condition("card_brand", "eq", "AMEX"), negate=True)
    )
    assert "NOT coalesce(transactions.transaction_date >=" in sql_of(
        condition("transaction_date", "neq", "2026-10-15")
    )


def test_null_checks() -> None:
    assert "payments.captured_at IS NULL" in sql_of(condition("captured_at", "is_null", True))
    assert "payments.captured_at IS NOT NULL" in sql_of(condition("captured_at", "is_null", False))
