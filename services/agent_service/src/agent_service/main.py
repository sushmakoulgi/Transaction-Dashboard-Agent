"""Agent service app factory.

Run with: uvicorn agent_service.main:create_app --factory --port 8000
Needs the Transaction Service running, and Claude credentials. The MCP tools run inside this
process (agent_service.mcp_server) and are reached in memory: no MCP endpoint exists.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

import anthropic
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
from agent_service.mcp_bridge.mcp_tool_gateway import McpToolGateway
from agent_service.mcp_server.server import create_mcp_server
from agent_service.memory.session_store import InMemorySessionStore
from agent_service.plan_cache.chroma_connection import ChromaConnection
from agent_service.plan_cache.chroma_plan_store import ChromaPlanStore
from agent_service.plan_cache.embedding_provider import EmbeddingProvider, VoyageEmbeddingProvider
from agent_service.plan_cache.plan_cache import PlanCache, PlanTemplateStore, TemplateExtractor
from agent_service.plan_cache.plan_cache_metrics import PlanCacheMetrics
from agent_service.plan_cache.query_template_extractor import QueryTemplateExtractor
from agent_service.plan_execution.plan_executor import PlanExecutor
from agent_service.settings import AgentServiceSettings
from shared.clients.transaction_service_client import TransactionServiceClient
from shared.clock import Clock, SystemClock
from shared.contracts import HealthStatus
from shared.http.bearer_auth import bearer_token_dependencies


@dataclass(frozen=True)
class PlanCacheParts:
    """The plan cache's outside services; tests pass fakes (which turns the cache on)."""

    extractor: TemplateExtractor
    embeddings: EmbeddingProvider
    store: PlanTemplateStore


async def production_plan_cache_parts(
    settings: AgentServiceSettings, chroma: ChromaConnection, resources: AsyncExitStack
) -> PlanCacheParts | None:
    """Haiku, Voyage and Chroma, or None while the plan cache is off (or has no Voyage key)."""
    if not settings.plan_cache_active or settings.voyage_api_key is None:
        return None
    haiku = anthropic.AsyncAnthropic(
        api_key=(
            settings.anthropic_api_key.get_secret_value()
            if settings.anthropic_api_key is not None
            else None
        ),
        timeout=settings.claude_request_timeout_seconds,
        max_retries=settings.claude_max_retries,
    )
    resources.push_async_callback(haiku.close)
    voyage = VoyageEmbeddingProvider(
        settings.voyage_api_key,
        settings.embedding_model,
        base_url=str(settings.voyage_api_url),
        timeout_seconds=settings.embedding_timeout_seconds,
        max_retries=settings.embedding_max_retries,
    )
    resources.push_async_callback(voyage.aclose)
    return PlanCacheParts(
        extractor=QueryTemplateExtractor(
            haiku,
            settings.template_extractor_model,
            settings.template_extractor_effort,
            settings.template_extractor_max_tokens,
        ),
        embeddings=voyage,
        store=ChromaPlanStore(chroma),
    )


def create_app(
    settings: AgentServiceSettings | None = None,
    clock: Clock | None = None,
    claude: ClaudeMessenger | None = None,
    transaction_service_transport: httpx.AsyncBaseTransport | None = None,
    plan_cache_parts: PlanCacheParts | None = None,
) -> FastAPI:
    """The optional arguments let tests swap in a scripted Claude, an in-process Transaction
    Service (also used by the MCP tools) and plan cache fakes; production leaves them unset."""
    service_settings = settings or AgentServiceSettings()  # values from the environment
    service_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:
            mcp_server = create_mcp_server(
                str(service_settings.transaction_service_url),
                service_settings.transaction_service_api_token,
                transaction_service_transport=transaction_service_transport,
            )
            gateway = await resources.enter_async_context(
                McpToolGateway(
                    lambda: Client(mcp_server), service_settings.mcp_tool_call_timeout_seconds
                )
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
            plan_store = ChromaPlanStore(chroma)
            plan_cache_metrics = PlanCacheMetrics(chroma, service_clock)
            plan_executor = PlanExecutor(transaction_service)
            parts = plan_cache_parts or await production_plan_cache_parts(
                service_settings, chroma, resources
            )
            plan_cache = (
                PlanCache(
                    parts.extractor,
                    parts.embeddings,
                    parts.store,
                    plan_cache_metrics,
                    sessions,
                    plan_executor,
                    service_clock,
                    service_settings.plan_hit_threshold,
                )
                if parts is not None
                else None
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
                plan_executor=plan_executor,
                sessions=sessions,
                transaction_service=transaction_service,
                plan_store=plan_store,
                plan_cache_metrics=plan_cache_metrics,
                plan_cache=plan_cache,
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
