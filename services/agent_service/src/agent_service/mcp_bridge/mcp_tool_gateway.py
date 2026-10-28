"""The agent's connection to the MCP server: discovers the tools once and runs tool calls.

Open it once at startup and close it at shutdown, in the same task (the MCP client runs a
task group that must be entered and exited together):

    async with McpToolGateway.connect_to(url, token) as gateway:
        gateway.tool_definitions     # for Claude
        await gateway.call_tool(...)  # never raises for tool or connection failures
"""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx2
from anthropic.types.beta import BetaToolParam
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr

from agent_service.mcp_bridge.tool_definition_mapper import to_anthropic_tools

DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 30.0


class McpServerUnavailableError(RuntimeError):
    """The MCP server could not be reached (or rejected the token) when connecting."""


@dataclass(frozen=True)
class ToolCallOutcome:
    is_error: bool
    text: str  # what the tool said: a result summary, or the error message
    structured_content: dict[str, Any] | None = None  # the typed result on success


class McpToolGateway:
    def __init__(
        self,
        client_factory: Callable[[], Client],
        tool_call_timeout_seconds: float = DEFAULT_TOOL_CALL_TIMEOUT_SECONDS,
    ) -> None:
        """`client_factory` builds an unopened MCP client (tests pass an in-memory one)."""
        self._client_factory = client_factory
        self._tool_call_timeout_seconds = tool_call_timeout_seconds
        self._exit_stack = AsyncExitStack()
        self._client: Client | None = None
        self._tool_definitions: tuple[BetaToolParam, ...] = ()

    @classmethod
    def connect_to(
        cls,
        mcp_server_url: str,
        mcp_api_token: SecretStr | None,
        tool_call_timeout_seconds: float = DEFAULT_TOOL_CALL_TIMEOUT_SECONDS,
    ) -> Self:
        def http_client_factory() -> Client:
            headers = (
                {"Authorization": f"Bearer {mcp_api_token.get_secret_value()}"}
                if mcp_api_token is not None
                else {}
            )
            http_client = httpx2.AsyncClient(
                headers=headers, timeout=httpx2.Timeout(tool_call_timeout_seconds)
            )
            return Client(
                streamable_http_client(mcp_server_url, http_client=http_client),
                read_timeout_seconds=tool_call_timeout_seconds,
            )

        return cls(http_client_factory, tool_call_timeout_seconds)

    @property
    def tool_definitions(self) -> tuple[BetaToolParam, ...]:
        """The tools for Claude, sorted by name (stable for prompt caching)."""
        return self._tool_definitions

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(tool["name"] for tool in self._tool_definitions)

    async def __aenter__(self) -> Self:
        """Connects and discovers the tools. Raises `McpServerUnavailableError` if it can't."""
        try:
            client = await self._exit_stack.enter_async_context(self._client_factory())
            listing = await client.list_tools()
        except Exception as error:  # the SDK wraps failures in nested exception groups
            await self._exit_stack.aclose()
            raise McpServerUnavailableError(
                f"could not connect to the MCP server or list its tools ({describe(error)})"
            ) from error
        self._client = client
        self._tool_definitions = to_anthropic_tools(listing.tools)
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client = None
        await self._exit_stack.aclose()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Runs one tool. Failures come back as `is_error=True` outcomes, never exceptions,
        so the agent can hand them to Claude."""
        if self._client is None:
            raise RuntimeError("McpToolGateway is not connected; use it as `async with`")
        if tool_name not in self.tool_names:
            return ToolCallOutcome(
                is_error=True,
                text=f"unknown tool '{tool_name}'; available: {', '.join(sorted(self.tool_names))}",
            )
        try:
            result = await self._client.call_tool(
                tool_name, arguments, read_timeout_seconds=self._tool_call_timeout_seconds
            )
        except Exception as error:  # transport failures must not crash the turn
            return ToolCallOutcome(
                is_error=True,
                text=f"the tool server could not be reached ({describe(error)}); try again shortly",
            )
        return to_outcome(result)


def to_outcome(result: CallToolResult) -> ToolCallOutcome:
    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
    return ToolCallOutcome(
        is_error=bool(result.is_error),
        text=text,
        structured_content=None if result.is_error else result.structured_content,
    )


def describe(error: BaseException) -> str:
    """The innermost error's type and message, unwrapping exception groups."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return f"{type(error).__name__}: {error}"
