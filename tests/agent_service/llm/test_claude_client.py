"""ClaudeClient with the real Anthropic SDK over a mock HTTP transport (no network calls)."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from agent_service.llm.claude_client import (
    REFUSAL_FALLBACK_BETA,
    ClaudeClient,
    ClaudeConfigurationError,
    ClaudeRefusalError,
    ClaudeReplyTruncatedError,
    ClaudeUnavailableError,
    ClaudeUnexpectedStopError,
)
from agent_service.settings import ClaudeSettings

SYSTEM = [{"type": "text", "text": "You answer questions about transactions."}]
MESSAGES = [{"role": "user", "content": "Transactions for 10001641?"}]
TOOLS = [
    {
        "name": "search_transactions",
        "description": "Search transactions.",
        "input_schema": {"type": "object", "properties": {"filter": {"type": "object"}}},
    }
]


def message_body(stop_reason: str, content: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
        },
        **extra,
    }


class RecordingTransport(httpx2.MockTransport):
    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []

        def record_and_respond(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            return respond(request)

        super().__init__(record_and_respond)

    @property
    def last_body(self) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[-1].content)
        return body


def client_with(transport: RecordingTransport, **settings_overrides: Any) -> ClaudeClient:
    settings = ClaudeSettings(anthropic_api_key=None, claude_max_retries=0, **settings_overrides)
    sdk_client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
    )
    return ClaudeClient(settings, sdk_client)


def responding(status_code: int, body: dict[str, Any]) -> RecordingTransport:
    return RecordingTransport(lambda _: httpx2.Response(status_code, json=body))


async def create(client: ClaudeClient) -> Any:
    return await client.create_message(SYSTEM, MESSAGES, TOOLS)  # type: ignore[arg-type]


# --- The request ---


async def test_sends_the_configured_model_effort_and_auto_tool_choice() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport))
    body = transport.last_body
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 16000
    assert body["output_config"] == {"effort": "medium"}
    assert body["tool_choice"] == {"type": "auto"}
    assert "cache_control" not in body  # prompt caching is not used
    assert body["tools"][0]["name"] == "search_transactions"
    assert body["system"] == SYSTEM
    assert body["messages"] == MESSAGES


async def test_leaves_thinking_and_sampling_to_the_model() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport))
    body = transport.last_body
    for removed_or_unneeded in ("thinking", "temperature", "top_p", "top_k"):
        assert removed_or_unneeded not in body


async def test_opts_into_refusal_fallbacks_by_default() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport))
    assert transport.last_body["fallbacks"] == "default"
    assert REFUSAL_FALLBACK_BETA in transport.requests[-1].headers["anthropic-beta"]


async def test_fallbacks_can_be_turned_off() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport, use_refusal_fallbacks=False))
    assert "fallbacks" not in transport.last_body
    assert REFUSAL_FALLBACK_BETA not in transport.requests[-1].headers.get("anthropic-beta", "")


async def test_settings_override_model_and_effort() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport, planner_model="claude-sonnet-5-5", planner_effort="low"))
    assert transport.last_body["model"] == "claude-sonnet-5-5"
    assert transport.last_body["output_config"] == {"effort": "low"}


# --- Replies ---


async def test_tool_use_reply_keeps_every_content_block_unmodified() -> None:
    transport = responding(
        200,
        message_body(
            "tool_use",
            [
                {"type": "thinking", "thinking": "", "signature": "sig-abc"},
                {"type": "text", "text": "Let me look that up."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search_transactions",
                    "input": {"filter": {"field": "customer_id"}},
                },
            ],
        ),
    )
    reply = await create(client_with(transport))
    assert reply.wants_tools
    assert [block.type for block in reply.content] == ["thinking", "text", "tool_use"]
    assert reply.content[0].signature == "sig-abc"
    (tool_use,) = reply.tool_use_requests
    assert (tool_use.tool_use_id, tool_use.tool_name) == ("toolu_1", "search_transactions")
    assert tool_use.tool_input == {"filter": {"field": "customer_id"}}
    assert reply.text == "Let me look that up."
    assert (reply.input_tokens, reply.output_tokens, reply.cache_read_input_tokens) == (
        120,
        30,
        100,
    )


async def test_final_answer_reply() -> None:
    transport = responding(
        200, message_body("end_turn", [{"type": "text", "text": "10001641 has 7 transactions."}])
    )
    reply = await create(client_with(transport))
    assert not reply.wants_tools
    assert reply.text == "10001641 has 7 transactions."


async def test_refusal_is_raised_with_its_category() -> None:
    transport = responding(
        200,
        message_body(
            "refusal",
            [],
            stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
        ),
    )
    with pytest.raises(ClaudeRefusalError) as error:
        await create(client_with(transport))
    assert (error.value.category, error.value.explanation) == ("cyber", "declined")


async def test_truncated_reply_is_raised() -> None:
    transport = responding(200, message_body("max_tokens", [{"type": "text", "text": "Half"}]))
    with pytest.raises(ClaudeReplyTruncatedError):
        await create(client_with(transport))


async def test_unexpected_stop_reason_is_raised() -> None:
    transport = responding(200, message_body("pause_turn", []))
    with pytest.raises(ClaudeUnexpectedStopError, match="pause_turn"):
        await create(client_with(transport))


# --- Errors ---


@pytest.mark.parametrize(
    ("status_code", "error_type", "expected"),
    [
        (429, "rate_limit_error", ClaudeUnavailableError),
        (529, "overloaded_error", ClaudeUnavailableError),
        (500, "api_error", ClaudeUnavailableError),
        (401, "authentication_error", ClaudeConfigurationError),
        (403, "permission_error", ClaudeConfigurationError),
        (404, "not_found_error", ClaudeConfigurationError),
        (400, "invalid_request_error", ClaudeConfigurationError),
    ],
)
async def test_api_errors_are_mapped(
    status_code: int, error_type: str, expected: type[Exception]
) -> None:
    transport = responding(
        status_code, {"type": "error", "error": {"type": error_type, "message": "nope"}}
    )
    with pytest.raises(expected):
        await create(client_with(transport))


async def test_network_failure_is_unavailable() -> None:
    def refuse_connection(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(ClaudeUnavailableError):
        await create(client_with(RecordingTransport(refuse_connection)))


async def test_uses_api_version_2026_10_21_with_a_bearer_key() -> None:
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    await create(client_with(transport))
    request = transport.requests[-1]
    assert request.url.path == "/v1/messages"
    assert request.headers["anthropic-version"] == "2026-10-21"
    assert request.headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize("blank_key", ["", "   "])
def test_blank_api_key_lets_the_sdk_resolve_credentials(blank_key: str) -> None:
    assert ClaudeSettings(anthropic_api_key=blank_key).anthropic_api_key is None  # type: ignore[arg-type]


async def test_missing_credentials_are_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(variable, raising=False)
    transport = responding(200, message_body("end_turn", [{"type": "text", "text": "Hi"}]))
    sdk_client = anthropic.AsyncAnthropic(
        max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(transport=transport)
    )
    client = ClaudeClient(ClaudeSettings(anthropic_api_key=None), sdk_client)
    with pytest.raises(ClaudeConfigurationError, match="set ANTHROPIC_API_KEY"):
        await create(client)
    assert transport.requests == []
