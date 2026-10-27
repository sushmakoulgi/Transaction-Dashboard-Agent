"""MCP server exposing the Transaction Service as tools over Streamable HTTP.

Run with: uvicorn mcp_server.main:create_app --factory --port 8100
Clients connect to http://localhost:8100/mcp with `Authorization: Bearer $MCP_API_TOKEN`.

The server is stateless and answers in JSON: every tool call is a self-contained
request/response, so no session state is kept between calls.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from mcp_server.settings import McpServerSettings
from shared.clients.transaction_service_client import TransactionServiceClient
from shared.contracts import HealthStatus
from shared.http.bearer_auth import BearerTokenMiddleware

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
SERVER_INSTRUCTIONS = (
    "Read-only search over payment transactions and chargebacks. Searches take a structured "
    "filter (never SQL). Only the current calendar year (America/New_York) can be searched."
)


@dataclass(frozen=True)
class McpServerResources:
    """Created when the server starts and shared by every tool call."""

    transaction_service: TransactionServiceClient


def create_mcp_server(settings: McpServerSettings) -> MCPServer[McpServerResources]:
    @asynccontextmanager
    async def lifespan(_: MCPServer[McpServerResources]) -> AsyncIterator[McpServerResources]:
        async with TransactionServiceClient(
            str(settings.transaction_service_url),
            settings.transaction_service_api_token,
            timeout_seconds=settings.transaction_service_timeout_seconds,
        ) as transaction_service:
            yield McpServerResources(transaction_service=transaction_service)

    server: MCPServer[McpServerResources] = MCPServer(
        name="transaction-dashboard",
        title="Transaction Dashboard",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
        lifespan=lifespan,
    )

    # Placeholder until the real tools arrive in P1b-3.
    @server.tool(description="Check that the MCP server is reachable. Returns 'pong'.")
    def ping() -> str:
        return "pong"

    @server.custom_route(HEALTH_PATH, methods=["GET"])  # type: ignore[untyped-decorator]  # SDK
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(HealthStatus(status="ok").model_dump())

    return server


def create_app(settings: McpServerSettings | None = None) -> ASGIApp:
    server_settings = settings or McpServerSettings()  # values from the environment
    mcp_app = create_mcp_server(server_settings).streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True
    )
    return BearerTokenMiddleware(mcp_app, server_settings.mcp_api_token, exempt_paths={HEALTH_PATH})
