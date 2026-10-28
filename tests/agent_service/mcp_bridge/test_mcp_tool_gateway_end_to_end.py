"""Agent gateway -> MCP server -> Transaction Service -> Postgres, all over real HTTP.

Both servers run under uvicorn on free ports. Requires the seeded docker-compose Postgres.
"""

from collections.abc import Iterator

import pytest
from filter_test_helpers import condition
from pydantic import SecretStr
from server_test_helpers import running_uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from agent_service.mcp_bridge.mcp_tool_gateway import (
    McpServerUnavailableError,
    McpToolGateway,
)
from mcp_server.main import create_app as create_mcp_app
from mcp_server.settings import McpServerSettings
from shared.config import DatabaseSettings
from transaction_service.main import create_app as create_transaction_service_app
from transaction_service.settings import TransactionServiceSettings

pytestmark = pytest.mark.integration

SERVICE_TOKEN = SecretStr("service-token-0123456789abcdef")
MCP_TOKEN = SecretStr("mcp-token-0123456789abcdef")


@pytest.fixture(scope="module")
def mcp_url() -> Iterator[str]:
    database_url = DatabaseSettings().postgres_dsn_readonly
    try:
        with create_engine(str(database_url)).connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")
    service_app = create_transaction_service_app(
        TransactionServiceSettings(
            postgres_dsn_readonly=database_url,
            auth_enabled=True,
            transaction_service_api_token=SERVICE_TOKEN,
        )
    )
    with running_uvicorn(service_app) as service_url:
        mcp_app = create_mcp_app(
            McpServerSettings(
                auth_enabled=True,
                mcp_api_token=MCP_TOKEN,
                transaction_service_url=service_url,  # type: ignore[arg-type]
                transaction_service_api_token=SERVICE_TOKEN,
            )
        )
        with running_uvicorn(mcp_app) as mcp_base_url:
            yield f"{mcp_base_url}/mcp"


async def test_discovers_the_five_tools_over_http(mcp_url: str) -> None:
    async with McpToolGateway.connect_to(mcp_url, MCP_TOKEN) as gateway:
        assert [tool["name"] for tool in gateway.tool_definitions] == [
            "get_transaction_detail",
            "list_filterable_fields",
            "search_chargebacks",
            "search_transactions",
            "validate_filters",
        ]


async def test_runs_a_search_through_the_whole_chain(mcp_url: str) -> None:
    async with McpToolGateway.connect_to(mcp_url, MCP_TOKEN) as gateway:
        outcome = await gateway.call_tool(
            "search_transactions",
            {"filter": condition("has_chargeback", "exists", True), "page_size": 5},
        )
    assert outcome.is_error is False
    assert outcome.structured_content is not None
    assert outcome.structured_content["total_count"] == 15


async def test_validation_problems_come_back_for_claude(mcp_url: str) -> None:
    async with McpToolGateway.connect_to(mcp_url, MCP_TOKEN) as gateway:
        outcome = await gateway.call_tool(
            "search_transactions", {"filter": condition("cust_id", "eq", "C-1")}
        )
    assert outcome.is_error is True
    assert "FILTER_VALIDATION_FAILED" in outcome.text
    assert "customer_id" in outcome.text


async def test_wrong_token_fails_at_startup(mcp_url: str) -> None:
    with pytest.raises(McpServerUnavailableError):
        async with McpToolGateway.connect_to(mcp_url, SecretStr("wrong-token-0123456789")):
            pass
