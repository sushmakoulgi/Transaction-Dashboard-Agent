from decimal import Decimal
from typing import Any

import pytest
from filter_test_helpers import build_validator, condition, group, utc

from shared.contracts import FilterIssueCode, Operator, SearchRequest
from transaction_service.filtering.entity_field_registries import (
    CHARGEBACK_FIELD_REGISTRY,
    TRANSACTION_FIELD_REGISTRY,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.filter_validator import (
    FilterValidationFailedError,
    ValidatedSearch,
)
from transaction_service.filtering.resolved_filter import (
    Comparison,
    Membership,
    NullCheck,
    Predicate,
    Range,
    RelatedRowCheck,
    ResolvedCondition,
    ResolvedGroup,
    TextContains,
)

VALIDATOR = build_validator()


def validate(
    filter_tree: dict[str, Any] | None = None,
    registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY,
    **request_fields: Any,
) -> ValidatedSearch:
    request = SearchRequest.model_validate({"filter": filter_tree, **request_fields})
    return VALIDATOR.validate(request, registry)


def predicate_of(field: str, operator: str, value: Any = None) -> Predicate:
    resolved = validate(condition(field, operator, value)).filter
    assert isinstance(resolved, ResolvedCondition)
    assert resolved.field.name == field
    return resolved.predicate


def issues_of(
    filter_tree: dict[str, Any] | None = None,
    registry: FieldRegistry = TRANSACTION_FIELD_REGISTRY,
    **request_fields: Any,
) -> list[tuple[str, FilterIssueCode]]:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(filter_tree, registry, **request_fields)
    return [(issue.path, issue.code) for issue in error.value.issues]


# --- The five use cases ---


def test_use_case_1_transactions_on_a_date_cover_the_whole_new_york_day() -> None:
    assert predicate_of("transaction_date", "eq", "2026-10-15") == Range(
        utc(2026, 10, 15, 4), utc(2026, 10, 16, 4), end_is_exclusive=True
    )


def test_use_case_2_transactions_with_chargebacks_this_month() -> None:
    assert predicate_of("chargeback_opened_at", "between", {"relative": "this_month"}) == Range(
        utc(2026, 10, 1, 4), utc(2026, 11, 1, 4), end_is_exclusive=True
    )


def test_use_case_3_and_4_by_customer_and_payment() -> None:
    assert predicate_of("customer_id", "eq", "10001641") == Comparison(Operator.EQUALS, "10001641")
    payment_id = "K7Q2M9XA4B1Z"
    predicate = predicate_of("payment_id", "eq", payment_id)
    assert isinstance(predicate, Comparison)
    assert str(predicate.value) == payment_id


def test_use_case_5_customer_chargebacks_this_month() -> None:
    validated = validate(
        group(
            condition("customer_id", "eq", "10001641"),
            condition("opened_at", "between", {"relative": "this_month"}),
        ),
        registry=CHARGEBACK_FIELD_REGISTRY,
    )
    assert isinstance(validated.filter, ResolvedGroup)
    customer, opened_at = validated.filter.children
    assert isinstance(customer, ResolvedCondition) and isinstance(opened_at, ResolvedCondition)
    assert opened_at.predicate == Range(
        utc(2026, 10, 1, 4), utc(2026, 11, 1, 4), end_is_exclusive=True
    )


# --- Resolved predicates ---


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("gt", Comparison(Operator.GREATER_THAN_OR_EQUAL, utc(2026, 10, 16, 4))),
        ("gte", Comparison(Operator.GREATER_THAN_OR_EQUAL, utc(2026, 10, 15, 4))),
        ("lt", Comparison(Operator.LESS_THAN, utc(2026, 10, 15, 4))),
        ("lte", Comparison(Operator.LESS_THAN, utc(2026, 10, 16, 4))),
        ("neq", Range(utc(2026, 10, 15, 4), utc(2026, 10, 16, 4), True, negate=True)),
    ],
)
def test_comparing_with_a_date_compares_with_the_whole_day(
    operator: str, expected: Predicate
) -> None:
    assert predicate_of("transaction_date", operator, "2026-10-15") == expected


def test_comparing_with_a_date_time_uses_the_exact_moment() -> None:
    assert predicate_of("transaction_date", "gt", "2026-10-15T09:30:00") == Comparison(
        Operator.GREATER_THAN, utc(2026, 10, 15, 13, 30)
    )


def test_between_dates_includes_the_whole_end_day() -> None:
    assert predicate_of("transaction_date", "between", ["2026-10-01", "2026-10-15"]) == Range(
        utc(2026, 10, 1, 4), utc(2026, 10, 16, 4), end_is_exclusive=True
    )


def test_between_date_times_includes_the_end_moment() -> None:
    assert predicate_of(
        "transaction_date", "between", ["2026-10-01T09:00:00", "2026-10-01T17:00:00"]
    ) == Range(utc(2026, 10, 1, 13), utc(2026, 10, 1, 21), end_is_exclusive=False)


def test_amount_between_is_inclusive_and_exact() -> None:
    assert predicate_of("amount", "between", [100, "500.50"]) == Range(
        Decimal("100"), Decimal("500.50"), end_is_exclusive=False
    )


def test_in_list_converts_every_value() -> None:
    assert predicate_of("payment_status", "not_in", ["captured", "FAILED"]) == Membership(
        ("CAPTURED", "FAILED"), negate=True
    )


def test_contains_null_and_exists_predicates() -> None:
    assert predicate_of("merchant_name", "contains", "harbor") == TextContains("harbor")
    assert predicate_of("captured_at", "is_null", False) == NullCheck(is_null=False)
    assert predicate_of("has_refund", "exists") == RelatedRowCheck(should_exist=True)
    assert predicate_of("has_chargeback", "exists", False) == RelatedRowCheck(should_exist=False)


def test_group_structure_is_preserved() -> None:
    validated = validate(
        group(
            condition("customer_id", "eq", "10001641"),
            group(
                condition("has_refund", "exists", True),
                condition("amount", "gt", 500),
                logic="or",
                negate=True,
            ),
        )
    )
    assert isinstance(validated.filter, ResolvedGroup)
    inner = validated.filter.children[1]
    assert isinstance(inner, ResolvedGroup)
    assert (inner.logic, inner.negate, len(inner.children)) == ("or", True, 2)


def test_default_and_explicit_sort() -> None:
    default = validate()
    assert (default.sort_field.name, default.sort_direction) == ("transaction_date", "desc")
    explicit = validate(sort={"field": "amount", "direction": "asc"}, page=2, page_size=10)
    assert (explicit.sort_field.name, explicit.sort_direction) == ("amount", "asc")
    assert (explicit.page, explicit.page_size) == (2, 10)


# --- Issues ---


def test_unknown_field_is_reported_with_suggestions() -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(condition("cust_id", "eq", "10001641"))
    (issue,) = error.value.issues
    assert issue.path == "filter.field"
    assert issue.code is FilterIssueCode.UNKNOWN_FIELD
    assert "customer_id" in issue.suggestions


def test_operator_not_allowed_lists_the_allowed_operators() -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(condition("payment_status", "gt", "CAPTURED"))
    (issue,) = error.value.issues
    assert (issue.path, issue.code) == ("filter.operator", FilterIssueCode.OPERATOR_NOT_ALLOWED)
    assert set(issue.suggestions) == {"eq", "neq", "in", "not_in"}


def test_invalid_enum_value_suggests_allowed_values() -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(condition("payment_status", "eq", "SETTLED"))
    (issue,) = error.value.issues
    assert (issue.path, issue.code) == ("filter.value", FilterIssueCode.INVALID_VALUE)
    assert "CAPTURED" in issue.suggestions


@pytest.mark.parametrize(
    ("field", "operator", "value", "expected_code"),
    [
        ("customer_id", "eq", ["C-1", "C-2"], FilterIssueCode.INVALID_VALUE_COUNT),
        ("customer_id", "in", "C-1", FilterIssueCode.INVALID_VALUE_COUNT),
        ("amount", "between", [1, 2, 3], FilterIssueCode.INVALID_VALUE_COUNT),
        ("amount", "between", 5, FilterIssueCode.INVALID_VALUE_COUNT),
        ("customer_id", "eq", None, FilterIssueCode.INVALID_VALUE),
        ("amount", "gt", {"relative": "this_month"}, FilterIssueCode.INVALID_VALUE),
        ("amount", "between", [500, 100], FilterIssueCode.INVALID_VALUE),
        (
            "transaction_date",
            "between",
            ["2026-10-15", "2026-10-01"],
            FilterIssueCode.INVALID_VALUE,
        ),
        ("has_refund", "exists", "maybe", FilterIssueCode.INVALID_VALUE),
    ],
)
def test_value_shape_problems(
    field: str, operator: str, value: Any, expected_code: FilterIssueCode
) -> None:
    assert issues_of(condition(field, operator, value)) == [("filter.value", expected_code)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_id", "12345"),
        ("transaction_id", "3f2b8c1e-9d4a-4e6b-8f1a-2c3d4e5f6a7b"),
        ("payment_id", "k7q2m9xa4b1z"),
        ("payment_id", "K7Q2M9XA4B1"),
        ("customer_id", "C-1004"),
    ],
)
def test_identifiers_in_the_wrong_format_are_rejected(field: str, value: str) -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(condition(field, "eq", value))
    (issue,) = error.value.issues
    assert (issue.path, issue.code) == ("filter.value", FilterIssueCode.INVALID_VALUE)
    assert "expected" in issue.message


def test_identifiers_in_the_right_format_are_accepted() -> None:
    assert predicate_of("transaction_id", "eq", "482910375610294") == Comparison(
        Operator.EQUALS, "482910375610294"
    )
    assert predicate_of("payment_id", "in", ["K7Q2M9XA4B1Z", "X7JLXWJKSOVZ"]) == Membership(
        ("K7Q2M9XA4B1Z", "X7JLXWJKSOVZ")
    )


def test_contains_accepts_a_partial_identifier() -> None:
    assert isinstance(predicate_of("transaction_id", "contains", "4829"), TextContains)


@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("eq", "2025-12-31"),
        ("gt", "2027-01-01T00:00:00"),
        ("between", ["2025-12-01", "2026-01-15"]),
    ],
)
def test_explicit_dates_outside_the_current_year_are_rejected(operator: str, value: Any) -> None:
    issues = issues_of(condition("transaction_date", operator, value))
    assert {code for _, code in issues} == {FilterIssueCode.DATE_OUT_OF_RANGE}


def test_relative_date_reaching_into_last_year_is_rejected() -> None:
    assert issues_of(condition("transaction_date", "between", {"last_n_days": 366})) == [
        ("filter.value", FilterIssueCode.DATE_OUT_OF_RANGE)
    ]


def test_problems_at_both_ends_of_a_range_are_reported_together() -> None:
    assert issues_of(condition("amount", "between", ["abc", True])) == [
        ("filter.value[0]", FilterIssueCode.INVALID_VALUE),
        ("filter.value[1]", FilterIssueCode.INVALID_VALUE),
    ]


def test_every_bad_list_item_is_reported() -> None:
    assert issues_of(condition("payment_status", "in", ["SETTLED", "CAPTURED", "PENDING"])) == [
        ("filter.value[0]", FilterIssueCode.INVALID_VALUE),
        ("filter.value[2]", FilterIssueCode.INVALID_VALUE),
    ]


def test_all_problems_across_the_tree_and_sort_are_reported_in_one_pass() -> None:
    issues = issues_of(
        group(
            condition("cust_id", "eq", "10001641"),
            group(
                condition("amount", "contains", "5"),
                condition("payment_status", "eq", "SETTLED"),
                logic="or",
            ),
            condition("transaction_date", "eq", "2025-06-01"),
        ),
        sort={"field": "customer_id"},
    )
    assert issues == [
        ("filter.children[0].field", FilterIssueCode.UNKNOWN_FIELD),
        ("filter.children[1].children[0].operator", FilterIssueCode.OPERATOR_NOT_ALLOWED),
        ("filter.children[1].children[1].value", FilterIssueCode.INVALID_VALUE),
        ("filter.children[2].value", FilterIssueCode.DATE_OUT_OF_RANGE),
        ("sort.field", FilterIssueCode.FIELD_NOT_SORTABLE),
    ]


def test_unsortable_field_suggests_sortable_fields() -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(sort={"field": "customer_id"})
    (issue,) = error.value.issues
    assert set(issue.suggestions) == {"amount", "transaction_date", "chargeback_opened_at"}


def test_unknown_sort_field_is_reported() -> None:
    assert issues_of(sort={"field": "amout"}) == [("sort.field", FilterIssueCode.UNKNOWN_FIELD)]


def test_issue_messages_name_the_field() -> None:
    with pytest.raises(FilterValidationFailedError) as error:
        validate(condition("amount", "gt", "lots"))
    assert error.value.issues[0].message == "amount: expected a number, got 'lots'"
