"""McpToolGateway against an in-memory test MCP server, and end to end over HTTP."""

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, SecretStr

from agent_service.mcp_bridge.mcp_tool_gateway import (
    McpServerUnavailableError,
    McpToolGateway,
)


class Greeting(BaseModel):
    message: str


def build_test_tool_server() -> MCPServer:
    server: MCPServer = MCPServer(name="test-tools")

    @server.tool(description="Greet someone.")
    def greet(name: str) -> Greeting:
        return Greeting(message=f"hello {name}")

    @server.tool(description="Always fails the way our tools report problems.")
    def fail() -> str:
        raise ToolError('{"error": "FILTER_VALIDATION_FAILED", "issues": []}')

    return server


def in_memory_gateway() -> McpToolGateway:
    server = build_test_tool_server()
    return McpToolGateway(lambda: Client(server))


async def test_discovers_tools_on_connect() -> None:
    async with in_memory_gateway() as gateway:
        assert [tool["name"] for tool in gateway.tool_definitions] == ["fail", "greet"]
        assert gateway.tool_names == {"fail", "greet"}


async def test_successful_call_returns_structured_content() -> None:
    async with in_memory_gateway() as gateway:
        outcome = await gateway.call_tool("greet", {"name": "Sam"})
    assert outcome.is_error is False
    assert outcome.structured_content == {"message": "hello Sam"}
    assert "hello Sam" in outcome.text


async def test_tool_error_is_an_outcome_not_an_exception() -> None:
    async with in_memory_gateway() as gateway:
        outcome = await gateway.call_tool("fail", {})
    assert outcome.is_error is True
    assert outcome.structured_content is None
    assert "FILTER_VALIDATION_FAILED" in outcome.text


async def test_unknown_tool_is_rejected_without_calling_the_server() -> None:
    async with in_memory_gateway() as gateway:
        outcome = await gateway.call_tool("drop_tables", {})
    assert outcome.is_error is True
    assert "unknown tool 'drop_tables'" in outcome.text
    assert "available: fail, greet" in outcome.text


async def test_invalid_arguments_are_an_error_outcome() -> None:
    async with in_memory_gateway() as gateway:
        outcome = await gateway.call_tool("greet", {"name": 42, "extra": True})
    assert outcome.is_error is True


async def test_calling_before_connecting_is_a_programming_error() -> None:
    with pytest.raises(RuntimeError, match="not connected"):
        await in_memory_gateway().call_tool("greet", {"name": "Sam"})


async def test_connecting_to_nothing_fails_at_startup() -> None:
    with pytest.raises(McpServerUnavailableError, match="could not connect to the MCP server"):
        async with McpToolGateway.connect_to(
            "http://127.0.0.1:9/mcp", None, tool_call_timeout_seconds=2
        ):
            pass


def test_connect_to_needs_no_token_when_auth_is_off() -> None:
    McpToolGateway.connect_to("http://localhost:8100/mcp", None)
    McpToolGateway.connect_to("http://localhost:8100/mcp", SecretStr("x" * 16))
