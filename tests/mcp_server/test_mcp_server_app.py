"""The MCP server running under real uvicorn on a free local port, reached by a real MCP client."""

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from mcp_server.main import create_app
from mcp_server.settings import McpServerSettings

MCP_TOKEN = "mcp-test-token-0123456789abcdef"


def free_local_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    settings = McpServerSettings(
        mcp_api_token=SecretStr(MCP_TOKEN),
        transaction_service_api_token=SecretStr("unused-in-these-tests-0123"),
    )
    port = free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("MCP server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def mcp_client(server_url: str, token: str = MCP_TOKEN) -> Client:
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(f"{server_url}/mcp", http_client=http_client))


def test_health_needs_no_token(server_url: str) -> None:
    response = httpx.get(f"{server_url}/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": MCP_TOKEN}]
)
def test_mcp_endpoint_rejects_missing_or_wrong_tokens(
    server_url: str, headers: dict[str, str]
) -> None:
    response = httpx.post(f"{server_url}/mcp", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"


def test_rejects_requests_for_a_foreign_host_name(server_url: str) -> None:
    # DNS-rebinding protection: a page on evil.example must not reach the local server.
    response = httpx.post(
        f"{server_url}/mcp",
        json={},
        headers={"Authorization": f"Bearer {MCP_TOKEN}", "Host": "evil.example"},
    )
    assert response.status_code in (400, 403, 421)


async def test_real_mcp_client_lists_and_calls_tools(server_url: str) -> None:
    async with mcp_client(server_url) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["ping"]
        result = await client.call_tool("ping")
    assert result.is_error is False
    assert result.content[0].text == "pong"  # type: ignore[union-attr]


async def test_real_mcp_client_with_a_wrong_token_cannot_connect(server_url: str) -> None:
    # The SDK reports the server's 401 as an MCPError inside nested exception groups.
    with pytest.raises(ExceptionGroup) as error:
        async with mcp_client(server_url, token="wrong-token") as client:
            await client.list_tools()
    mcp_errors, _ = error.value.split(MCPError)
    assert mcp_errors is not None
