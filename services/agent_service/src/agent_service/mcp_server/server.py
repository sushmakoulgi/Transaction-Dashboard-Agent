"""The MCP server exposing the Transaction Service as tools, run inside the agent service.

The agent connects to it in memory (`mcp.Client(server)`): there is no MCP endpoint, port,
URL or token, so nothing outside the agent process can reach the tools. Its calls to the
Transaction Service still send TRANSACTION_SERVICE_API_TOKEN.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import SecretStr

from agent_service.mcp_server.tools import register_tools
from shared.clients.transaction_service_client import TransactionServiceClient

SERVER_INSTRUCTIONS = (
    "Read-only search over payment transactions and chargebacks. Searches take a structured "
    "filter (never SQL). Only the current calendar year (America/New_York) can be searched."
)
DEFAULT_TRANSACTION_SERVICE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class McpServerResources:
    """Created when the server starts and shared by every tool call."""

    transaction_service: TransactionServiceClient


def create_mcp_server(
    transaction_service_url: str,
    transaction_service_api_token: SecretStr | None,
    transaction_service_timeout_seconds: float = DEFAULT_TRANSACTION_SERVICE_TIMEOUT_SECONDS,
    transaction_service_transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer[McpServerResources]:
    """`transaction_service_transport` lets tests route calls to an in-process service."""

    @asynccontextmanager
    async def lifespan(_: MCPServer[McpServerResources]) -> AsyncIterator[McpServerResources]:
        async with TransactionServiceClient(
            transaction_service_url,
            transaction_service_api_token,
            timeout_seconds=transaction_service_timeout_seconds,
            transport=transaction_service_transport,
        ) as transaction_service:
            yield McpServerResources(transaction_service=transaction_service)

    server: MCPServer[McpServerResources] = MCPServer(
        name="transaction-dashboard",
        title="Transaction Dashboard",
        instructions=SERVER_INSTRUCTIONS,
        version="0.1.0",
        lifespan=lifespan,
    )
    register_tools(server)
    return server
