from typing import Any

import pytest
from pydantic import ValidationError

from shared.contracts import DetailStep, QueryPlan, SearchableEntity, SearchStep

CUSTOMER_FILTER = {"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1"}


def plan(*steps: dict[str, Any]) -> QueryPlan:
    return QueryPlan.model_validate({"steps": list(steps)})


def test_search_then_detail_plan() -> None:
    transaction_id = "999999999999999"
    query_plan = plan(
        {"tool_name": "search_chargebacks", "filter": CUSTOMER_FILTER},
        {"tool_name": "get_transaction_detail", "transaction_id": str(transaction_id)},
    )
    search, detail = query_plan.steps
    assert isinstance(search, SearchStep)
    assert search.entity is SearchableEntity.CHARGEBACKS
    assert isinstance(detail, DetailStep)
    assert detail.transaction_id == transaction_id
    assert query_plan.last_search is search


def test_last_search_is_the_most_recent_search() -> None:
    query_plan = plan(
        {"tool_name": "search_transactions"},
        {"tool_name": "search_chargebacks", "filter": CUSTOMER_FILTER},
    )
    assert query_plan.last_search is query_plan.steps[1]
    assert (
        plan(
            {"tool_name": "get_transaction_detail", "transaction_id": "999999999999999"}
        ).last_search
        is None
    )


@pytest.mark.parametrize(
    "step_with_result_data",
    [
        {"tool_name": "search_transactions", "items": [{"transaction_id": "x"}]},
        {"tool_name": "search_transactions", "total_count": 15},
        {"tool_name": "search_transactions", "page": 2},
        {"tool_name": "get_transaction_detail", "transaction_id": "999999999999999", "payment": {}},
    ],
)
def test_steps_have_no_place_for_results_or_paging(step_with_result_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        plan(step_with_result_data)


def test_rejects_unknown_tools_empty_and_oversized_plans() -> None:
    with pytest.raises(ValidationError):
        plan({"tool_name": "drop_tables"})
    with pytest.raises(ValidationError):
        QueryPlan.model_validate({"steps": []})
    with pytest.raises(ValidationError):
        plan(*[{"tool_name": "search_transactions"}] * 11)


def test_step_filters_are_validated_like_any_search() -> None:
    with pytest.raises(ValidationError):
        plan(
            {"tool_name": "search_transactions", "filter": {**CUSTOMER_FILTER, "field": "x; DROP"}}
        )


def test_plan_round_trips_through_json() -> None:
    query_plan = plan({"tool_name": "search_transactions", "filter": CUSTOMER_FILTER})
    assert QueryPlan.model_validate_json(query_plan.model_dump_json()) == query_plan
