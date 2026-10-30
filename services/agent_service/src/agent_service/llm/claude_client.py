"""The one place the agent talks to Claude.

- Model, effort and token limits come from `ClaudeSettings`; thinking is left at the model's
  default (always on for Claude Opus 5.5), and only `tool_choice: auto` is used.
- Prompt caching is not used (no `cache_control`).
- Refusals are opted into server-side fallbacks (`fallbacks: "default"`), and every
  `stop_reason` is checked before the content is used.
- SDK errors are mapped to a few agent-level errors, most specific first.
- The reply keeps Claude's content blocks unmodified, so they can be appended to the turn
  as-is (thinking blocks must not be edited when sent back).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types.beta import (
    BetaContentBlock,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolParam,
)

from agent_service.settings import ClaudeSettings

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
ACCEPTED_STOP_REASONS = frozenset({"end_turn", "tool_use", "stop_sequence"})
MISSING_CREDENTIALS_HINT = (
    "no usable Claude credentials: set ANTHROPIC_API_KEY in .env or in the environment"
)


@dataclass(frozen=True)
class ToolUseRequest:
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ClaudeReply:
    content: list[BetaContentBlock]  # append unmodified to the turn's messages
    stop_reason: str
    text: str
    tool_use_requests: tuple[ToolUseRequest, ...]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int

    @property
    def wants_tools(self) -> bool:
        return self.stop_reason == "tool_use" and bool(self.tool_use_requests)


class ClaudeError(Exception):
    """Base for every failure the agent can get from Claude."""


class ClaudeRefusalError(ClaudeError):
    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(f"Claude declined the request (category: {category or 'unspecified'})")
        self.category = category
        self.explanation = explanation


class ClaudeReplyTruncatedError(ClaudeError):
    """The reply hit max_tokens; raising beats acting on a half-finished answer."""


class ClaudeUnexpectedStopError(ClaudeError):
    def __init__(self, stop_reason: str | None) -> None:
        super().__init__(f"unexpected stop_reason {stop_reason!r}")
        self.stop_reason = stop_reason


class ClaudeUnavailableError(ClaudeError):
    """Rate limited, overloaded, server error or network failure, after the SDK's retries."""


class ClaudeConfigurationError(ClaudeError):
    """A request Claude will never accept as sent: bad key, model, permissions or request."""


class ClaudeClient:
    def __init__(
        self, settings: ClaudeSettings, anthropic_client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        self._settings = settings
        self._anthropic = anthropic_client or anthropic.AsyncAnthropic(
            api_key=(
                settings.anthropic_api_key.get_secret_value()
                if settings.anthropic_api_key is not None
                else None
            ),
            timeout=settings.claude_request_timeout_seconds,
            max_retries=settings.claude_max_retries,
        )

    async def create_message(
        self,
        system: Sequence[BetaTextBlockParam],
        messages: Sequence[BetaMessageParam],
        tools: Sequence[BetaToolParam],
    ) -> ClaudeReply:
        """One Messages API call. Raises a `ClaudeError` subclass on any failure."""
        try:
            response = await self._anthropic.beta.messages.create(
                **self._request_options(),
                model=self._settings.planner_model,
                max_tokens=self._settings.planner_max_tokens,
                system=list(system),
                messages=list(messages),
                tools=list(tools),
                tool_choice={"type": "auto"},
                output_config={"effort": self._settings.planner_effort},
            )
        except anthropic.APIError as error:
            raise to_claude_error(error) from error
        except anthropic.AnthropicError as error:  # e.g. CredentialsError: not an HTTP failure
            raise ClaudeConfigurationError(f"{MISSING_CREDENTIALS_HINT} ({error})") from error
        except TypeError as error:
            # The SDK raises TypeError when it finds no credentials at all.
            if "authentication method" not in str(error):
                raise
            raise ClaudeConfigurationError(MISSING_CREDENTIALS_HINT) from error
        return to_reply(response)

    async def aclose(self) -> None:
        await self._anthropic.close()

    def _request_options(self) -> dict[str, Any]:
        if not self._settings.use_refusal_fallbacks:
            return {}
        return {"betas": [REFUSAL_FALLBACK_BETA], "fallbacks": "default"}


def to_reply(response: BetaMessage) -> ClaudeReply:
    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        raise ClaudeRefusalError(
            category=getattr(stop_details, "category", None),
            explanation=getattr(stop_details, "explanation", None),
        )
    if response.stop_reason == "max_tokens":
        raise ClaudeReplyTruncatedError("Claude's reply was cut off at max_tokens")
    if response.stop_reason not in ACCEPTED_STOP_REASONS:
        raise ClaudeUnexpectedStopError(response.stop_reason)
    usage = response.usage
    return ClaudeReply(
        content=list(response.content),
        stop_reason=response.stop_reason,
        text="".join(block.text for block in response.content if block.type == "text"),
        tool_use_requests=tuple(
            ToolUseRequest(tool_use_id=block.id, tool_name=block.name, tool_input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        ),
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
    )


def to_claude_error(error: anthropic.APIError) -> ClaudeError:
    """Most specific first: retryable-later failures vs. requests that will never succeed."""
    if isinstance(error, anthropic.RateLimitError):
        return ClaudeUnavailableError("Claude is rate limiting requests; try again shortly")
    if isinstance(
        error,
        anthropic.AuthenticationError
        | anthropic.PermissionDeniedError
        | anthropic.NotFoundError
        | anthropic.BadRequestError
        | anthropic.UnprocessableEntityError,
    ):
        return ClaudeConfigurationError(f"Claude rejected the request: {error.message}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return ClaudeUnavailableError(f"Claude is unavailable ({error.status_code})")
        return ClaudeConfigurationError(f"Claude rejected the request: {error.message}")
    if isinstance(error, anthropic.APIConnectionError):  # includes APITimeoutError
        return ClaudeUnavailableError("could not reach Claude; try again shortly")
    return ClaudeError(str(error))
