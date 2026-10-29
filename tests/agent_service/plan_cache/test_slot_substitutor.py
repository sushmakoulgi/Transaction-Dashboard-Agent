"""Plan <-> plan template: slots out on learning, slots back in on a cache hit."""

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_service.plan_cache.slot_substitutor import (
    PlanTemplateFillError,
    UnslottablePlanError,
    fill_plan_template,
    template_id_of,
    template_plan,
)
from shared.contracts import QueryPlan

NOW = datetime(2026, 10, 28, 20, tzinfo=UTC)


def condition(field: str, operator: str, value: Any) -> dict[str, Any]:
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def search(filter_node: dict[str, Any], tool: str = "search_transactions") -> QueryPlan:
    return QueryPlan.model_validate({"steps": [{"tool_name": tool, "filter": filter_node}]})


def chargebacks_for(customer_id: str) -> QueryPlan:
    return search(
        {
            "kind": "group",
            "logic": "and",
            "children": [
                condition("customer_id", "eq", customer_id),
                condition("opened_at", "between", {"relative": "this_month"}),
            ],
        },
        tool="search_chargebacks",
    )


def test_round_trip_gives_back_the_same_plan() -> None:
    plan = chargebacks_for("10001641")
    template = template_plan(
        "chargebacks for customer {customer_id} this month",
        plan,
        {"customer_id": "10001641"},
        "What chargebacks did customer 10001641 have this month?",
        NOW,
    )

    assert template.template_id == template_id_of(
        "chargebacks for customer {customer_id} this month"
    )
    assert template.slot_names == ("customer_id",)
    first_child = template.plan_template["steps"][0]["filter"]["children"][0]
    assert first_child["value"] == {"$slot": "customer_id", "as": "text"}
    assert template.created_at == NOW and template.hit_count == 0
    assert fill_plan_template(template, {"customer_id": "10001641"}) == plan
    # ...and reused for another customer, with "this month" untouched
    assert fill_plan_template(template, {"customer_id": "10033317"}) == chargebacks_for("10033317")


def test_case_as_the_plan_wrote_it_is_reapplied() -> None:
    plan = search(condition("merchant_name", "contains", "Rogers"))
    template = template_plan(
        "{merchant_name} transactions",
        plan,
        {"merchant_name": "rogers"},
        "rogers transactions",
        NOW,
    )
    child = template.plan_template["steps"][0]["filter"]
    assert child["value"] == {"$slot": "merchant_name", "as": "title"}
    filled = fill_plan_template(template, {"merchant_name": "bell"})
    assert filled == search(condition("merchant_name", "contains", "Bell"))


def test_amounts_and_day_counts_are_numbers() -> None:
    plan = search(
        {
            "kind": "group",
            "children": [
                condition("amount", "gt", 500),
                condition("transaction_date", "between", {"last_n_days": 7}),
            ],
        }
    )
    template = template_plan(
        "transactions over {amount} in the last {days} days",
        plan,
        {"amount": "$500", "days": "7"},
        "transactions over $500 in the last 7 days",
        NOW,
    )
    filled = fill_plan_template(template, {"amount": "CA$1,250.50", "days": "30"})
    group = filled.steps[0].model_dump(mode="json", exclude_none=True)["filter"]  # type: ignore[union-attr]
    assert group["children"][0]["value"] == 1250.5
    assert group["children"][1]["value"] == {"last_n_days": 30}


def test_list_values_and_detail_steps_are_slotted() -> None:
    plan = QueryPlan.model_validate(
        {
            "steps": [
                {
                    "tool_name": "search_transactions",
                    "filter": condition("card_brand", "in", ["VISA", "AMEX"]),
                },
                {"tool_name": "get_transaction_detail", "transaction_id": "856786156334238"},
            ]
        }
    )
    template = template_plan(
        "{card_brand} or {card_brand_2} transactions and details of {transaction_id}",
        plan,
        {"card_brand": "visa", "card_brand_2": "Amex", "transaction_id": "856786156334238"},
        "visa or Amex transactions and details of 856786156334238",
        NOW,
    )
    filled = fill_plan_template(
        template,
        {"card_brand": "mastercard", "card_brand_2": "visa", "transaction_id": "442045004778231"},
    )
    dumped = filled.model_dump(mode="json")
    assert dumped["steps"][0]["filter"]["value"] == ["MASTERCARD", "VISA"]
    assert dumped["steps"][1]["transaction_id"] == "442045004778231"


def test_a_slot_the_plan_does_not_use_is_refused() -> None:
    with pytest.raises(UnslottablePlanError, match="not found"):
        template_plan(
            "transactions over {amount}",
            search(condition("amount", "gt", 400)),  # Claude used another number
            {"amount": "$500"},
            "transactions over $500",
            NOW,
        )


@pytest.mark.parametrize(
    ("question", "plan"),
    [
        ("rogers chargebacks", search(condition("merchant_name", "contains", "Rogers"))),
        ("captured payments", search(condition("payment_status", "eq", "CAPTURED"))),
        ("transactions over $500", search(condition("amount", "gt", 500))),
        ("under review chargebacks", search(condition("chargeback_status", "eq", "UNDER_REVIEW"))),
    ],
)
def test_a_question_value_left_in_the_plan_is_refused(question: str, plan: QueryPlan) -> None:
    # Haiku made no slot, so "bell chargebacks" could later match and run the Rogers search.
    with pytest.raises(UnslottablePlanError, match="not slots"):
        template_plan(question, plan, {}, question, NOW)


def test_values_the_question_does_not_mention_may_stay_in_the_plan() -> None:
    # "with a chargeback" became has_chargeback exists true; "this month" is a relative date.
    plan = search(
        {
            "kind": "group",
            "children": [
                condition("has_chargeback", "exists", True),
                condition("transaction_date", "between", {"relative": "this_month"}),
            ],
        }
    )
    question = "transactions with a chargeback this month"
    template = template_plan(question, plan, {}, question, NOW)
    assert fill_plan_template(template, {}) == plan


def test_missing_or_invalid_slot_values_cannot_fill_a_template() -> None:
    template = template_plan(
        "chargebacks for customer {customer_id} this month",
        chargebacks_for("10001641"),
        {"customer_id": "10001641"},
        "chargebacks for customer 10001641 this month",
        NOW,
    )
    with pytest.raises(PlanTemplateFillError, match="no value"):
        fill_plan_template(template, {})

    detail = template_plan(
        "details of {transaction_id}",
        QueryPlan.model_validate(
            {
                "steps": [
                    {"tool_name": "get_transaction_detail", "transaction_id": "856786156334238"}
                ]
            }
        ),
        {"transaction_id": "856786156334238"},
        "details of 856786156334238",
        NOW,
    )
    with pytest.raises(PlanTemplateFillError, match="invalid"):
        fill_plan_template(detail, {"transaction_id": "12345"})  # not 15 digits

    amount = template_plan(
        "transactions over {amount}",
        search(condition("amount", "gt", 500)),
        {"amount": "500"},
        "transactions over 500",
        NOW,
    )
    with pytest.raises(PlanTemplateFillError, match="not a number"):
        fill_plan_template(amount, {"amount": "lots"})
