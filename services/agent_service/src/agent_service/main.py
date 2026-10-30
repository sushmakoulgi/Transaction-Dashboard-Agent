"""Agent service app factory.

Run with: uvicorn agent_service.main:create_app --factory --port 8000
Needs the MCP server (and through it the Transaction Service) running, and Claude credentials.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp import Client

from agent_service.agent.query_agent import ClaudeMessenger, QueryAgent
from agent_service.api.dependencies import AgentServiceContainer
from agent_service.api.error_handlers import register_error_handlers
from agent_service.api.routes import router
from agent_service.llm.claude_client import ClaudeClient
from agent_service.mcp_bridge.mcp_tool_gateway import McpServerUnavailableError, McpToolGateway
from agent_service.memory.session_store import InMemorySessionStore
from agent_service.plan_cache.chroma_connection import ChromaConnection
from agent_service.plan_cache.chroma_plan_store import ChromaPlanStore
from agent_service.plan_cache.plan_cache_metrics import PlanCacheMetrics
from agent_service.plan_execution.plan_executor import PlanExecutor
from agent_service.settings import AgentServiceSettings
from shared.clients.transaction_service_client import TransactionServiceClient
from shared.clock import Clock, SystemClock
from shared.contracts import HealthStatus
from shared.http.bearer_auth import bearer_token_dependencies

logger = logging.getLogger(__name__)
MCP_CONNECT_RETRY_INTERVAL_SECONDS = 1.0


async def connect_waiting_for_mcp_server(
    resources: AsyncExitStack, new_gateway: Callable[[], McpToolGateway], wait_seconds: float
) -> McpToolGateway:
    """Connects, retrying until `wait_seconds` have passed (the MCP server may still be
    starting, e.g. when VS Code launches every service at once). Then raises
    `McpServerUnavailableError`."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return await resources.enter_async_context(new_gateway())
        except McpServerUnavailableError:
            if time.monotonic() >= deadline:
                raise
            logger.info("MCP server not reachable yet; retrying")
            await asyncio.sleep(MCP_CONNECT_RETRY_INTERVAL_SECONDS)


def create_app(
    settings: AgentServiceSettings | None = None,
    clock: Clock | None = None,
    claude: ClaudeMessenger | None = None,
    mcp_client_factory: Callable[[], Client] | None = None,
    transaction_service_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """The optional arguments let tests swap in a scripted Claude, an in-memory MCP server
    and an in-process Transaction Service; production leaves them unset."""
    service_settings = settings or AgentServiceSettings()  # values from the environment
    service_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:

            def new_gateway() -> McpToolGateway:
                if mcp_client_factory is not None:
                    return McpToolGateway(
                        mcp_client_factory, service_settings.mcp_tool_call_timeout_seconds
                    )
                return McpToolGateway.connect_to(
                    str(service_settings.mcp_server_url),
                    service_settings.mcp_api_token,
                    service_settings.mcp_tool_call_timeout_seconds,
                )

            gateway = await connect_waiting_for_mcp_server(
                resources, new_gateway, service_settings.mcp_startup_wait_seconds
            )
            transaction_service = await resources.enter_async_context(
                TransactionServiceClient(
                    str(service_settings.transaction_service_url),
                    service_settings.transaction_service_api_token,
                    transport=transaction_service_transport,
                )
            )
            claude_messenger = claude
            if claude_messenger is None:
                claude_client = ClaudeClient(service_settings)
                resources.push_async_callback(claude_client.aclose)
                claude_messenger = claude_client
            sessions = InMemorySessionStore(
                service_settings.session_context_question_limit,
                timedelta(minutes=service_settings.session_idle_ttl_minutes),
                service_clock,
            )
            # Opened on first use, never at startup: the agent doesn't wait for Chroma.
            chroma = ChromaConnection(
                service_settings.chroma_url, service_settings.chroma_timeout_seconds
            )
            app.state.container = AgentServiceContainer(
                query_agent=QueryAgent(
                    claude_messenger,
                    gateway,
                    sessions,
                    service_clock,
                    ZoneInfo(service_settings.business_timezone),
                    service_settings.max_tool_rounds,
                    service_settings.tool_result_preview_rows,
                ),
                plan_executor=PlanExecutor(transaction_service),
                sessions=sessions,
                transaction_service=transaction_service,
                plan_store=ChromaPlanStore(chroma),
                plan_cache_metrics=PlanCacheMetrics(chroma, service_clock),
                plan_cache_active=service_settings.plan_cache_active,
            )
            yield

    app = FastAPI(
        title="Agent Service",
        description="Answers natural-language questions about transactions using Claude.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=service_settings.cors_allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_error_handlers(app)
    app.include_router(
        router,
        dependencies=bearer_token_dependencies(
            service_settings.auth_enabled, service_settings.agent_api_token, "Agent service"
        ),
    )

    @app.get("/health", response_model=HealthStatus, tags=["health"])
    async def health() -> JSONResponse:
        """No token needed. The app only starts once the MCP server is reachable."""
        return JSONResponse(content={"status": "ok"})

    return app
