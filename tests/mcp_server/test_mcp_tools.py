"""The MCP tools, called through the SDK's in-memory client, against the real Transaction
Service app running in-process on the seeded database."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from filter_test_helpers import condition, group
from mcp import Client
from mcp.types import CallToolResult
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from mcp_server.main import create_mcp_server
from mcp_server.settings import McpServerSettings
from shared.config import DatabaseSettings
from transaction_service.main import create_app
from transaction_service.settings import TransactionServiceSettings

pytestmark = pytest.mark.integration

SERVICE_TOKEN = SecretStr("service-token-0123456789abcdef")
MCP_TOKEN = SecretStr("mcp-token-0123456789abcdef")
TOOL_NAMES = {
    "search_transactions",
    "search_chargebacks",
    "get_transaction_detail",
    "list_filterable_fields",
    "validate_filters",
}


@asynccontextmanager
async def connected_client() -> AsyncIterator[Client]:
    """Opened inside each test: the MCP client's task group must enter and exit in one task."""
    service_app = create_app(
        TransactionServiceSettings(
            postgres_dsn_readonly=DatabaseSettings().postgres_dsn_readonly,
            auth_enabled=True,
            transaction_service_api_token=SERVICE_TOKEN,
        )
    )
    server = create_mcp_server(
        McpServerSettings(
            auth_enabled=True,
            mcp_api_token=MCP_TOKEN,
            transaction_service_url="http://transaction-service",  # type: ignore[arg-type]
            transaction_service_api_token=SERVICE_TOKEN,
        ),
        transaction_service_transport=httpx.ASGITransport(app=service_app),
    )
    try:
        async with service_app.router.lifespan_context(service_app), Client(server) as client:
            yield client
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")


async def call(client: Client, tool_name: str, **arguments: Any) -> CallToolResult:
    return await client.call_tool(tool_name, arguments)


def structured(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result.structured_content


def error_body(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is True
    text: str = result.content[0].text  # type: ignore[union-attr]
    # The SDK prefixes the message with "Error executing tool <name>: ".
    assert text.startswith("Error executing tool ") and text.index("{") > 0
    body: dict[str, Any] = json.loads(text[text.index("{") :])
    return body


async def test_lists_the_five_read_only_tools() -> None:
    async with connected_client() as mcp_client:
        tools = (await mcp_client.list_tools()).tools
        assert {tool.name for tool in tools} == TOOL_NAMES
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.idempotent_hint is True
            assert tool.output_schema is not None
            assert tool.description and len(tool.description) > 100


async def test_search_transactions() -> None:
    async with connected_client() as mcp_client:
        page = structured(
            await call(
                mcp_client,
                "search_transactions",
                filter=condition("has_chargeback", "exists", True),
                page_size=5,
            )
        )
        assert (page["total_count"], page["total_pages"], len(page["items"])) == (15, 3, 5)
        assert all(item["has_chargeback"] for item in page["items"])


async def test_search_chargebacks_this_month() -> None:
    async with connected_client() as mcp_client:
        page = structured(
            await call(
                mcp_client,
                "search_chargebacks",
                filter=condition("opened_at", "between", {"relative": "this_month"}),
            )
        )
        assert page["total_count"] >= 1
        assert all(item["latest_event_type"] for item in page["items"])


async def test_search_then_drill_down() -> None:
    async with connected_client() as mcp_client:
        page = structured(await call(mcp_client, "search_transactions", page_size=1))
        transaction_id = page["items"][0]["transaction_id"]
        detail = structured(
            await call(mcp_client, "get_transaction_detail", transaction_id=transaction_id)
        )
        assert detail["transaction_id"] == transaction_id
        assert detail["payment"]["payment_id"] == page["items"][0]["payment_id"]


async def test_list_filterable_fields() -> None:
    async with connected_client() as mcp_client:
        result = structured(await call(mcp_client, "list_filterable_fields", entity="transactions"))
        fields = {field["name"]: field for field in result["fields"]}
        assert result["entity"] == "transactions"
        assert "VISA" in fields["card_brand"]["allowed_values"]


async def test_validate_filters_reports_problems_without_an_error() -> None:
    async with connected_client() as mcp_client:
        valid = structured(
            await call(
                mcp_client,
                "validate_filters",
                entity="chargebacks",
                filter=condition("customer_id", "eq", "10001641"),
            )
        )
        assert valid == {"is_valid": True, "issues": []}
        invalid = structured(
            await call(
                mcp_client,
                "validate_filters",
                entity="transactions",
                filter=condition("transaction_date", "eq", "2001-01-01"),
            )
        )
        assert invalid["is_valid"] is False
        assert invalid["issues"][0]["code"] == "DATE_OUT_OF_RANGE"


async def test_validate_filters_reports_shape_problems_too() -> None:
    async with connected_client() as mcp_client:
        too_deep = group(group(group(group(condition("amount", "gt", 1)))))
        result = structured(
            await call(mcp_client, "validate_filters", entity="transactions", filter=too_deep)
        )
        assert result["is_valid"] is False
        assert {issue["code"] for issue in result["issues"]} == {"INVALID_REQUEST_SHAPE"}


async def test_invalid_filter_returns_every_issue_for_the_llm() -> None:
    async with connected_client() as mcp_client:
        body = error_body(
            await call(
                mcp_client,
                "search_transactions",
                filter=group(
                    condition("cust_id", "eq", "C-1"), condition("payment_status", "eq", "SETTLED")
                ),
            )
        )
        assert body["error"] == "FILTER_VALIDATION_FAILED"
        assert [(issue["path"], issue["code"]) for issue in body["issues"]] == [
            ("filter.children[0].field", "UNKNOWN_FIELD"),
            ("filter.children[1].value", "INVALID_VALUE"),
        ]
        assert "customer_id" in body["issues"][0]["suggestions"]
        assert "CAPTURED" in body["issues"][1]["suggestions"]


async def test_too_deep_filter_is_a_shape_error() -> None:
    async with connected_client() as mcp_client:
        body = error_body(
            await call(
                mcp_client,
                "search_transactions",
                filter=group(group(group(group(condition("amount", "gt", 1))))),
            )
        )
        assert body["error"] == "INVALID_REQUEST"
        assert "maximum is 3" in body["issues"][0]["message"]


async def test_arguments_that_break_the_schema_are_rejected() -> None:
    async with connected_client() as mcp_client:
        result = await call(mcp_client, "search_transactions", page_size=1000)
        assert result.is_error is True
        result = await call(
            mcp_client, "search_transactions", filter=condition("amount; DROP TABLE x", "eq", 1)
        )
        assert result.is_error is True


async def test_unknown_transaction_is_reported() -> None:
    async with connected_client() as mcp_client:
        body = error_body(
            await call(mcp_client, "get_transaction_detail", transaction_id="999999999999999")
        )
        assert body["error"] == "TRANSACTION_NOT_FOUND"


async def test_unreachable_transaction_service_is_reported() -> None:
    server = create_mcp_server(
        McpServerSettings(
            auth_enabled=True,
            mcp_api_token=MCP_TOKEN,
            transaction_service_url="http://127.0.0.1:9",  # type: ignore[arg-type]  # nothing there
            transaction_service_api_token=SERVICE_TOKEN,
            transaction_service_timeout_seconds=1,
        )
    )
    async with Client(server) as client:
        body = error_body(await call(client, "list_filterable_fields", entity="transactions"))
    assert body["error"] == "TRANSACTION_SERVICE_UNAVAILABLE"
