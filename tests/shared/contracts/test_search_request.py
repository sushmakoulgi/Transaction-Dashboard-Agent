from typing import Any

import pytest
from pydantic import ValidationError

from shared.contracts import (
    FilterCondition,
    FilterGroup,
    Operator,
    RelativeDatePeriod,
    RelativeDateValue,
    SearchRequest,
)


def condition(field: str = "customer_id", operator: str = "eq", value: Any = "C-1004") -> dict:
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def group(*children: dict, logic: str = "and", negate: bool = False) -> dict:
    return {"kind": "group", "logic": logic, "negate": negate, "children": list(children)}


def nested_groups(depth: int) -> dict:
    node = condition()
    for _ in range(depth):
        node = group(node)
    return node


def test_parses_nested_and_or_not_filter_with_relative_date() -> None:
    request = SearchRequest.model_validate(
        {
            "filter": group(
                condition("customer_id", "eq", "C-1004"),
                condition("transaction_date", "between", {"relative": "this_month"}),
                group(
                    condition("has_refund", "exists", True),
                    condition("amount", "gt", 500),
                    logic="or",
                ),
                group(condition("card_brand", "eq", "AMEX"), negate=True),
            ),
            "page_size": 50,
        }
    )

    assert isinstance(request.filter, FilterGroup)
    date_condition, or_group, not_group = request.filter.children[1:]
    assert isinstance(date_condition, FilterCondition)
    assert date_condition.value == RelativeDateValue(relative=RelativeDatePeriod.THIS_MONTH)
    assert isinstance(or_group, FilterGroup)
    assert or_group.logic == "or"
    assert isinstance(not_group, FilterGroup)
    assert not_group.negate is True
    assert request.page == 1
    assert request.page_size == 50


def test_single_condition_is_a_valid_filter() -> None:
    request = SearchRequest.model_validate({"filter": condition()})
    assert request.filter == FilterCondition(
        field="customer_id", operator=Operator.EQUALS, value="C-1004"
    )


def test_empty_request_means_no_filter_and_default_paging() -> None:
    request = SearchRequest()
    assert (request.filter, request.sort, request.page, request.page_size) == (None, None, 1, 25)


@pytest.mark.parametrize(
    "field_name",
    [
        "customer_id; DROP TABLE transactions",
        "customer_id) OR (1=1",
        "payment_details->>'card_brand'",
        "Customer_ID",
        "1customer",
        "",
        "a" * 65,
    ],
)
def test_rejects_field_names_that_are_not_plain_snake_case(field_name: str) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(field=field_name)})


def test_rejects_unknown_keys_anywhere_in_the_request() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(), "raw_sql": "SELECT 1"})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": {**condition(), "where": "1=1"}})


def test_rejects_unknown_operator_and_missing_kind() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(operator="like")})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(
            {"filter": {"field": "customer_id", "operator": "eq", "value": "C-1"}}
        )


def test_rejects_empty_group() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": group()})


def test_values_are_not_coerced() -> None:
    request = SearchRequest.model_validate({"filter": condition("amount", "gt", "500")})
    assert isinstance(request.filter, FilterCondition)
    assert request.filter.value == "500"

    request = SearchRequest.model_validate({"filter": condition("has_refund", "exists", True)})
    assert isinstance(request.filter, FilterCondition)
    assert request.filter.value is True


def test_rejects_object_values_other_than_relative_dates() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(value={"$ne": None})})


@pytest.mark.parametrize(
    "relative_date",
    [{}, {"relative": "this_month", "last_n_days": 7}, {"last_n_days": 0}, {"relative": "soon"}],
)
def test_relative_date_needs_exactly_one_valid_period(relative_date: dict) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(
            {"filter": condition("transaction_date", "between", relative_date)}
        )


def test_list_values_are_limited_to_fifty() -> None:
    SearchRequest.model_validate({"filter": condition(operator="in", value=["C-1"] * 50)})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(operator="in", value=["C-1"] * 51)})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filter": condition(operator="in", value=[])})


def test_groups_nest_at_most_three_deep() -> None:
    SearchRequest.model_validate({"filter": nested_groups(depth=3)})
    with pytest.raises(ValidationError, match="maximum is 3"):
        SearchRequest.model_validate({"filter": nested_groups(depth=4)})


def test_filter_has_at_most_twenty_conditions_in_total() -> None:
    ten_conditions = group(*[condition() for _ in range(10)])
    SearchRequest.model_validate({"filter": group(ten_conditions, ten_conditions)})
    with pytest.raises(ValidationError, match="maximum is 20"):
        SearchRequest.model_validate({"filter": group(ten_conditions, ten_conditions, condition())})


@pytest.mark.parametrize("paging", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_rejects_out_of_range_paging(paging: dict) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(paging)


def test_rejects_unsafe_sort_field_and_unknown_direction() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"sort": {"field": "amount desc; --"}})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"sort": {"field": "amount", "direction": "sideways"}})


def test_json_schema_is_recursive_for_tool_definitions() -> None:
    schema = SearchRequest.model_json_schema()
    assert {"FilterCondition", "FilterGroup", "RelativeDateValue"} <= set(schema["$defs"])
    group_children = schema["$defs"]["FilterGroup"]["properties"]["children"]["items"]
    assert {"$ref": "#/$defs/FilterGroup"} in group_children["oneOf"]
