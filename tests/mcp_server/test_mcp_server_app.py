"""The MCP server running under real uvicorn on a free local port, reached by a real MCP client."""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from server_test_helpers import running_uvicorn

from mcp_server.main import create_app
from mcp_server.settings import McpServerSettings

MCP_TOKEN = "mcp-test-token-0123456789abcdef"


def mcp_settings(auth_enabled: bool) -> McpServerSettings:
    """Explicit settings, so a local AUTH_ENABLED in .env can't change the tests."""
    if not auth_enabled:
        return McpServerSettings(
            auth_enabled=False, mcp_api_token=None, transaction_service_api_token=None
        )
    return McpServerSettings(
        auth_enabled=True,
        mcp_api_token=SecretStr(MCP_TOKEN),
        transaction_service_api_token=SecretStr("unused-in-these-tests-0123"),
    )


@contextmanager
def running_server(settings: McpServerSettings) -> Iterator[str]:
    with running_uvicorn(create_app(settings)) as url:
        yield url


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    with running_server(mcp_settings(auth_enabled=True)) as url:
        yield url


@pytest.fixture(scope="module")
def server_url_without_auth() -> Iterator[str]:
    with running_server(mcp_settings(auth_enabled=False)) as url:
        yield url


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


async def test_real_mcp_client_lists_the_tools(server_url: str) -> None:
    async with mcp_client(server_url) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "search_transactions",
        "search_chargebacks",
        "get_transaction_detail",
        "list_filterable_fields",
        "validate_filters",
    }


async def test_real_mcp_client_with_a_wrong_token_cannot_connect(server_url: str) -> None:
    # The SDK reports the server's 401 as an MCPError inside nested exception groups.
    with pytest.raises(ExceptionGroup) as error:
        async with mcp_client(server_url, token="wrong-token") as client:
            await client.list_tools()
    mcp_errors, _ = error.value.split(MCPError)
    assert mcp_errors is not None


# --- AUTH_ENABLED=false ---


async def test_without_auth_any_client_can_list_tools(server_url_without_auth: str) -> None:
    response = httpx.post(f"{server_url_without_auth}/mcp", json={})
    assert response.status_code != 401
    async with Client(streamable_http_client(f"{server_url_without_auth}/mcp")) as client:
        tools = await client.list_tools()
    assert len(tools.tools) == 5


def test_mcp_settings_need_tokens_only_while_auth_is_enabled() -> None:
    with pytest.raises(ValueError, match="MCP_API_TOKEN is required"):
        McpServerSettings(
            auth_enabled=True,
            mcp_api_token=None,
            transaction_service_api_token=SecretStr("x" * 16),
        )
    mcp_settings(auth_enabled=False)  # no tokens needed
