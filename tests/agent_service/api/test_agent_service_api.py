"""The agent service HTTP API with a scripted Claude, an in-memory MCP server and the real
Transaction Service in-process (seeded Postgres)."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from agent_test_helpers import ScriptedClaude, final_answer, reply_with, tool_use
from filter_test_helpers import condition
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from agent_service.llm.claude_client import (
    ClaudeRefusalError,
    ClaudeReply,
    ClaudeUnavailableError,
)
from agent_service.main import PlanCacheParts, create_app
from agent_service.mcp_bridge.mcp_tool_gateway import McpServerUnavailableError
from agent_service.memory.session_store import SessionEntry
from agent_service.plan_cache.chroma_plan_store import PlanMatch
from agent_service.plan_cache.query_template_extractor import ExtractedQuery
from agent_service.plan_cache.slot_substitutor import StoredPlanTemplate
from agent_service.settings import AgentServiceSettings
from mcp_server.main import create_mcp_server
from mcp_server.settings import McpServerSettings
from shared.config import DatabaseSettings
from transaction_service.main import create_app as create_transaction_service_app
from transaction_service.settings import TransactionServiceSettings

pytestmark = pytest.mark.integration

SERVICE_TOKEN = SecretStr("service-token-0123456789abcdef")
AGENT_TOKEN = "agent-token-0123456789abcdef"
AUTHORIZED = {"Authorization": f"Bearer {AGENT_TOKEN}"}
CHARGEBACK_SEARCH = {"filter": condition("has_chargeback", "exists", True), "page_size": 5}


def agent_settings(auth_enabled: bool = True, **overrides: Any) -> AgentServiceSettings:
    """Explicit settings, so a local .env can't change the tests."""
    return AgentServiceSettings(
        auth_enabled=auth_enabled,
        agent_api_token=SecretStr(AGENT_TOKEN) if auth_enabled else None,
        mcp_api_token=SecretStr("mcp-token-0123456789abcdef") if auth_enabled else None,
        transaction_service_url="http://transaction-service",  # type: ignore[arg-type]
        transaction_service_api_token=SERVICE_TOKEN,
        anthropic_api_key=None,
        # No real Chroma: the plan cache's counters and templates stay untouched.
        chroma_url="http://127.0.0.1:9",  # type: ignore[arg-type]
        chroma_timeout_seconds=1.0,
        **overrides,
    )


@asynccontextmanager
async def agent_api(
    claude_replies: Sequence[ClaudeReply | Exception],
    auth_enabled: bool = True,
    plan_cache_parts: PlanCacheParts | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, ScriptedClaude]]:
    service_app = create_transaction_service_app(
        TransactionServiceSettings(
            postgres_dsn_readonly=DatabaseSettings().postgres_dsn_readonly,
            auth_enabled=True,
            transaction_service_api_token=SERVICE_TOKEN,
        )
    )
    service_transport = httpx.ASGITransport(app=service_app)
    mcp_server = create_mcp_server(
        McpServerSettings(
            auth_enabled=False,
            mcp_api_token=None,
            transaction_service_url="http://transaction-service",  # type: ignore[arg-type]
            transaction_service_api_token=SERVICE_TOKEN,
        ),
        transaction_service_transport=service_transport,
    )
    claude = ScriptedClaude(claude_replies)
    app = create_app(
        agent_settings(auth_enabled),
        claude=claude,
        mcp_client_factory=lambda: Client(mcp_server),
        transaction_service_transport=service_transport,
        plan_cache_parts=plan_cache_parts,
    )
    try:
        async with (
            service_app.router.lifespan_context(service_app),
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://agent"
            ) as client,
        ):
            yield client, claude
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")


def searches_then_answers(text: str = "15 transactions have chargebacks.") -> list[ClaudeReply]:
    return [reply_with(tool_use("search_transactions", CHARGEBACK_SEARCH)), final_answer(text)]


async def chat(client: httpx.AsyncClient, message: str, **fields: Any) -> httpx.Response:
    return await client.post("/api/chat", json={"message": message, **fields}, headers=AUTHORIZED)


# --- Health, auth, CORS ---


async def test_health_needs_no_token() -> None:
    async with agent_api([final_answer("unused")]) as (client, _):
        response = await client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_api_requires_the_token_while_auth_is_enabled() -> None:
    async with agent_api([final_answer("hi")]) as (client, _):
        response = await client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


async def test_api_is_open_when_auth_is_disabled() -> None:
    async with agent_api([final_answer("hi")], auth_enabled=False) as (client, _):
        response = await client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 200


async def test_plan_cache_metrics_are_served_even_without_chroma() -> None:
    async with agent_api([]) as (client, _):
        assert (await client.get("/api/metrics/plan-cache")).status_code == 401
        response = await client.get("/api/metrics/plan-cache", headers=AUTHORIZED)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False  # PLAN_CACHE_ENABLED is off by default
    assert body["store_available"] is False  # Chroma unreachable: nothing stored to show
    assert body["lookups"] == 0 and body["exact_match_rate"] == 0.0
    assert body["hits"] == {"exact": 0, "similar": 0}
    assert body["top_templates"] == []


class OneTemplateExtractor:
    async def extract(
        self, question: str, previous_entries: Sequence[SessionEntry]
    ) -> ExtractedQuery:
        return ExtractedQuery(
            template_text="transactions with chargebacks", slot_values={}, is_self_contained=True
        )


class ConstantEmbeddings:
    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


class InMemoryPlanStore:
    def __init__(self) -> None:
        self.templates: list[StoredPlanTemplate] = []

    async def nearest(self, embedding: list[float]) -> PlanMatch | None:
        return PlanMatch(self.templates[-1], 1.0) if self.templates else None

    async def add(self, template: StoredPlanTemplate, embedding: list[float]) -> None:
        self.templates.append(template)

    async def record_hit(self, template_id: str, now: Any) -> None:
        pass


async def test_a_repeated_question_is_answered_from_the_plan_cache_without_claude() -> None:
    store = InMemoryPlanStore()
    parts = PlanCacheParts(OneTemplateExtractor(), ConstantEmbeddings(), store)
    async with agent_api(searches_then_answers(), plan_cache_parts=parts) as (client, claude):
        first = (await chat(client, "Which transactions have chargebacks?")).json()
        # The plan was learned in the background, after the first response.
        assert [template.template_text for template in store.templates] == [
            "transactions with chargebacks"
        ]
        second = (
            await chat(
                client, "Which transactions have chargebacks?", session_id=first["session_id"]
            )
        ).json()
        metrics = (await client.get("/api/metrics/plan-cache", headers=AUTHORIZED)).json()
        entries = (
            await client.get(f"/api/sessions/{first['session_id']}/entries", headers=AUTHORIZED)
        ).json()

    assert first["plan_source"] == "agent"
    assert second["plan_source"] == "cache"
    assert len(claude.requests) == 2  # both from the first question: none for the second
    assert second["reply"] == "Found 15 transactions."
    assert second["query_plan"] == first["query_plan"]
    assert second["result_page"]["total_count"] == 15
    assert len(entries) == 2  # the cached answer is in the session like any other
    assert metrics["enabled"] is True
    assert (metrics["lookups"], metrics["hits"]["exact"]) == (2, 1)
    assert metrics["misses"]["no_templates"] == 1 and metrics["learned"]["stored"] == 1


async def test_cors_allows_the_ui_origin_only() -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    async with agent_api([final_answer("unused")]) as (client, _):
        allowed = await client.options(
            "/api/chat", headers={**preflight, "Origin": "http://localhost:5173"}
        )
        refused = await client.options(
            "/api/chat", headers={**preflight, "Origin": "https://evil.example"}
        )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in refused.headers


async def test_refuses_to_start_without_the_mcp_server() -> None:
    app = create_app(
        agent_settings(mcp_server_url="http://127.0.0.1:9/mcp", mcp_startup_wait_seconds=0),
        claude=ScriptedClaude([]),
    )
    with pytest.raises(McpServerUnavailableError):
        async with app.router.lifespan_context(app):
            pass


async def test_waits_for_an_mcp_server_that_starts_later() -> None:
    mcp_server = create_mcp_server(
        McpServerSettings(
            auth_enabled=False, mcp_api_token=None, transaction_service_api_token=None
        )
    )
    attempts: list[int] = []

    def mcp_client_factory() -> Client:
        attempts.append(1)
        if len(attempts) == 1:  # first attempt: nothing listening yet
            return Client(streamable_http_client("http://127.0.0.1:9/mcp"))
        return Client(mcp_server)

    app = create_app(
        agent_settings(mcp_startup_wait_seconds=10),
        claude=ScriptedClaude([]),
        mcp_client_factory=mcp_client_factory,
    )
    async with app.router.lifespan_context(app):
        pass
    assert len(attempts) == 2


# --- Chat, sessions, paging, detail ---


async def test_chat_answers_with_plan_and_typed_first_page() -> None:
    async with agent_api(searches_then_answers()) as (client, _):
        response = await chat(client, "Which transactions have chargebacks?")
    assert response.status_code == 200
    body = response.json()
    assert len(body["session_id"]) == 32
    assert body["reply"] == "15 transactions have chargebacks."
    assert body["result_entity"] == "transactions"
    assert body["query_plan"]["steps"][0]["tool_name"] == "search_transactions"
    page = body["result_page"]
    assert (page["total_count"], page["total_pages"], len(page["items"])) == (15, 3, 5)
    assert all(item["has_chargeback"] for item in page["items"])
    assert body["reached_round_limit"] is False


async def test_follow_up_in_the_same_session_carries_context() -> None:
    replies = [*searches_then_answers(), final_answer("Here are their refunds.")]
    async with agent_api(replies) as (client, claude):
        first = (await chat(client, "Which transactions have chargebacks?")).json()
        await chat(client, "Of those, which had refunds?", session_id=first["session_id"])
        entries = (
            await client.get(f"/api/sessions/{first['session_id']}/entries", headers=AUTHORIZED)
        ).json()
    follow_up_opening = str(claude.requests[-1][0]["content"])
    assert "Which transactions have chargebacks?" in follow_up_opening
    assert "<current_question>Of those, which had refunds?</current_question>" in follow_up_opening
    assert [entry["user_question"] for entry in entries] == [
        "Which transactions have chargebacks?",
        "Of those, which had refunds?",
    ]
    assert set(entries[0]) == {"user_question", "query_plan"}
    assert entries[1]["query_plan"] is None


async def test_delete_session_forgets_it() -> None:
    async with agent_api([final_answer("hi")]) as (client, _):
        session_id = (await chat(client, "hello")).json()["session_id"]
        deleted = await client.delete(f"/api/sessions/{session_id}", headers=AUTHORIZED)
        entries = await client.get(f"/api/sessions/{session_id}/entries", headers=AUTHORIZED)
    assert deleted.status_code == 204
    assert entries.json() == []


async def test_execute_plan_returns_other_pages_without_claude() -> None:
    async with agent_api(searches_then_answers()) as (client, claude):
        plan = (await chat(client, "Which transactions have chargebacks?")).json()["query_plan"]
        claude_calls_after_chat = len(claude.requests)
        pages = [
            (
                await client.post(
                    "/api/plans/execute",
                    json={"query_plan": plan, "page": page, "page_size": 5},
                    headers=AUTHORIZED,
                )
            ).json()
            for page in (1, 2, 3)
        ]
    assert len(claude.requests) == claude_calls_after_chat  # paging never calls Claude
    ids = [item["transaction_id"] for page in pages for item in page["result_page"]["items"]]
    assert len(ids) == len(set(ids)) == 15
    assert {page["result_entity"] for page in pages} == {"transactions"}


async def test_transaction_detail_and_not_found() -> None:
    async with agent_api(searches_then_answers()) as (client, _):
        first_item = (await chat(client, "Chargebacks?")).json()["result_page"]["items"][0]
        detail = await client.get(
            f"/api/transactions/{first_item['transaction_id']}", headers=AUTHORIZED
        )
        missing = await client.get("/api/transactions/999999999999999", headers=AUTHORIZED)
    assert detail.status_code == 200
    assert detail.json()["chargeback"]["events"]
    assert (missing.status_code, missing.json()["error"]) == (404, "TRANSACTION_NOT_FOUND")


# --- Errors ---


@pytest.mark.parametrize(
    ("claude_reply", "expected_status", "expected_error"),
    [
        (ClaudeUnavailableError("overloaded"), 503, "CLAUDE_UNAVAILABLE"),
        (ClaudeRefusalError("cyber", "declined"), 422, "CLAUDE_DECLINED"),
    ],
)
async def test_claude_failures_are_mapped(
    claude_reply: Exception, expected_status: int, expected_error: str
) -> None:
    async with agent_api([claude_reply]) as (client, _):
        response = await chat(client, "Which transactions have chargebacks?")
        session_id = response.json().get("session_id")
    assert (response.status_code, response.json()["error"]) == (expected_status, expected_error)
    assert session_id is None


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "x" * 2001},
        {"message": "hi", "session_id": "../etc"},
        {"message": "hi", "unexpected": True},
    ],
)
async def test_invalid_chat_requests(body: dict[str, Any]) -> None:
    async with agent_api([final_answer("unused")]) as (client, claude):
        response = await client.post("/api/chat", json=body, headers=AUTHORIZED)
    assert (response.status_code, response.json()["error"]) == (422, "INVALID_REQUEST")
    assert claude.requests == []


async def test_execute_plan_errors() -> None:
    detail_only = {
        "steps": [{"tool_name": "get_transaction_detail", "transaction_id": "999999999999999"}]
    }
    search_plan = {"steps": [{"tool_name": "search_transactions"}]}
    async with agent_api([final_answer("unused")]) as (client, _):
        no_search = await client.post(
            "/api/plans/execute", json={"query_plan": detail_only}, headers=AUTHORIZED
        )
        too_big = await client.post(
            "/api/plans/execute",
            json={"query_plan": search_plan, "page_size": 1000},
            headers=AUTHORIZED,
        )
    assert (no_search.status_code, no_search.json()["error"]) == (422, "PLAN_HAS_NO_SEARCH")
    assert (too_big.status_code, too_big.json()["error"]) == (422, "INVALID_REQUEST")
