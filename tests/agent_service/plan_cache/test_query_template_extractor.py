"""Unit tests for QueryTemplateExtractor with a fake Anthropic client (no real Claude calls)."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from agent_service.memory.session_store import SessionEntry
from agent_service.plan_cache.query_template_extractor import (
    ExtractedQuery,
    QueryTemplateExtractor,
    TemplateExtractionError,
    TemplateSlot,
    _ExtractionOutput,
    normalize_template,
)
from shared.contracts import QueryPlan


def output(template: str, slots: dict[str, str] | None = None, self_contained: bool = True):
    return _ExtractionOutput(
        template_text=template,
        slots=[TemplateSlot(name=name, value=value) for name, value in (slots or {}).items()],
        is_self_contained=self_contained,
    )


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **request: Any) -> Any:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def extractor_returning(result: Any) -> tuple[QueryTemplateExtractor, FakeMessages]:
    messages = FakeMessages(result)
    client = SimpleNamespace(messages=messages)
    return QueryTemplateExtractor(client, model="claude-haiku-5-5", effort="low"), messages  # type: ignore[arg-type]


def reply(parsed: _ExtractionOutput | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)


async def test_extracts_a_normalized_template_with_slots() -> None:
    extractor, messages = extractor_returning(
        reply(
            output(
                "Transactions for customer {customer_id} THIS month?", {"customer_id": "10001641"}
            )
        )
    )

    extracted = await extractor.extract("Show transactions for customer 10001641 this month?", [])

    assert extracted == ExtractedQuery(
        template_text="transactions for customer {customer_id} this month",
        slot_values={"customer_id": "10001641"},
        is_self_contained=True,
    )
    (request,) = messages.calls
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_format"] is _ExtractionOutput
    assert request["output_config"] == {"effort": "low"}
    assert "relative date words" in request["system"]
    assert request["messages"] == [
        {
            "role": "user",
            "content": "<current_question>Show transactions for customer 10001641 this month?"
            "</current_question>",
        }
    ]


async def test_earlier_questions_and_plans_are_sent_for_follow_up_detection() -> None:
    extractor, messages = extractor_returning(
        reply(output("does it have a refund", self_contained=False))
    )
    earlier = SessionEntry(
        user_question="Find the transaction for payment T74TMQW2350Y",
        query_plan=QueryPlan.model_validate(
            {
                "steps": [
                    {"tool_name": "get_transaction_detail", "transaction_id": "856786156334238"}
                ]
            }
        ),
    )

    extracted = await extractor.extract("does it have a refund?", [earlier])

    assert extracted.is_self_contained is False
    content = messages.calls[0]["messages"][0]["content"]
    assert "<previous_questions>" in content and "T74TMQW2350Y" in content
    assert "856786156334238" in content  # the plan, never results


async def test_slot_values_match_the_question_ignoring_case() -> None:
    extractor, _ = extractor_returning(
        reply(output("{merchant_name} chargebacks", {"merchant_name": "Rogers"}))
    )
    extracted = await extractor.extract("rogers chargebacks", [])
    assert extracted.slot_values == {"merchant_name": "Rogers"}


@pytest.mark.parametrize(
    ("parsed", "reason"),
    [
        (output("transactions for customer {customer_id}", {"customer_id": "10009999"}), "contain"),
        (output("transactions for customer {customer_id}"), "different slots"),
        (output("transactions for customer", {"customer_id": "10001641"}), "different slots"),
        (
            _ExtractionOutput(
                template_text="{customer_id}",
                slots=[
                    TemplateSlot(name="customer_id", value="10001641"),
                    TemplateSlot(name="customer_id", value="10001641"),
                ],
                is_self_contained=True,
            ),
            "twice",
        ),
    ],
)
async def test_answers_that_do_not_fit_the_question_are_rejected(
    parsed: _ExtractionOutput, reason: str
) -> None:
    extractor, _ = extractor_returning(reply(parsed))
    with pytest.raises(TemplateExtractionError, match=reason):
        await extractor.extract("Transactions for customer 10001641", [])


@pytest.mark.parametrize(
    "response",
    [reply(None), reply(output("x"), stop_reason="max_tokens"), reply(None, stop_reason="refusal")],
)
async def test_unusable_replies_are_errors(response: SimpleNamespace) -> None:
    extractor, _ = extractor_returning(response)
    with pytest.raises(TemplateExtractionError):
        await extractor.extract("x", [])


async def test_api_failures_become_extraction_errors() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    extractor, _ = extractor_returning(anthropic.APIConnectionError(request=request))
    with pytest.raises(TemplateExtractionError, match="call failed"):
        await extractor.extract("x", [])


def test_slot_names_are_restricted() -> None:
    with pytest.raises(ValueError):
        TemplateSlot(name="Customer ID", value="1")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("  Chargebacks   opened THIS month?! ", "chargebacks opened this month"),
        ("transactions for {customer_id}.", "transactions for {customer_id}"),
    ],
)
def test_normalize_template(raw: str, normalized: str) -> None:
    assert normalize_template(raw) == normalized
