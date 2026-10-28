"""PlanExecutor with a fake client (unit) and against the real stack (end to end)."""

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from agent_test_helpers import ScriptedClaude, final_answer, reply_with, tool_use
from filter_test_helpers import condition
from mcp import Client
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from agent_service.agent.query_agent import QueryAgent
from agent_service.mcp_bridge.mcp_tool_gateway import McpToolGateway
from agent_service.memory.session_store import InMemorySessionStore
from agent_service.plan_execution.plan_executor import (
    InvalidPageRequestError,
    PlanExecutor,
    PlanHasNoSearchError,
)
from mcp_server.main import create_mcp_server
from mcp_server.settings import McpServerSettings
from shared.clients.transaction_service_client import TransactionServiceClient
from shared.clock import SystemClock
from shared.config import DatabaseSettings
from shared.contracts import (
    ChargebackSummary,
    PaginatedResult,
    QueryPlan,
    SearchableEntity,
    SearchRequest,
    TransactionSummary,
)
from transaction_service.main import create_app
from transaction_service.settings import TransactionServiceSettings

SERVICE_TOKEN = SecretStr("service-token-0123456789abcdef")
CUSTOMER_FILTER = condition("customer_id", "eq", "C-1004")


def plan(*steps: dict[str, Any]) -> QueryPlan:
    return QueryPlan.model_validate({"steps": list(steps)})


class RecordingSearchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, SearchRequest]] = []

    async def search_transactions(
        self, request: SearchRequest
    ) -> PaginatedResult[TransactionSummary]:
        self.calls.append(("transactions", request))
        return PaginatedResult[TransactionSummary](
            items=[], page=request.page, page_size=request.page_size, total_count=0
        )

    async def search_chargebacks(
        self, request: SearchRequest
    ) -> PaginatedResult[ChargebackSummary]:
        self.calls.append(("chargebacks", request))
        return PaginatedResult[ChargebackSummary](
            items=[], page=request.page, page_size=request.page_size, total_count=0
        )


# --- Unit ---


async def test_reruns_the_last_search_with_the_requested_page() -> None:
    client = RecordingSearchClient()
    page = await PlanExecutor(client).execute(
        plan(
            {"tool_name": "search_transactions"},
            {
                "tool_name": "search_chargebacks",
                "filter": CUSTOMER_FILTER,
                "sort": {"field": "opened_at", "direction": "asc"},
            },
            {"tool_name": "get_transaction_detail", "transaction_id": str(uuid.uuid4())},
        ),
        page=3,
        page_size=10,
    )
    ((entity, request),) = client.calls
    assert entity == "chargebacks"
    assert page.entity is SearchableEntity.CHARGEBACKS
    assert (request.page, request.page_size) == (3, 10)
    assert request.filter is not None and request.filter.model_dump()["value"] == "C-1004"
    assert request.sort is not None and request.sort.direction == "asc"


async def test_plan_without_a_search_cannot_be_paged() -> None:
    detail_only = plan({"tool_name": "get_transaction_detail", "transaction_id": str(uuid.uuid4())})
    with pytest.raises(PlanHasNoSearchError):
        await PlanExecutor(RecordingSearchClient()).execute(detail_only, page=1, page_size=25)


@pytest.mark.parametrize(("page", "page_size"), [(0, 25), (1, 0), (1, 101)])
async def test_out_of_range_paging_is_rejected(page: int, page_size: int) -> None:
    client = RecordingSearchClient()
    with pytest.raises(InvalidPageRequestError):
        await PlanExecutor(client).execute(
            plan({"tool_name": "search_transactions"}), page=page, page_size=page_size
        )
    assert client.calls == []


# --- End to end: the agent's page 1 equals the executor's page 1 ---


@pytest.mark.integration
async def test_rerunning_the_agents_plan_returns_the_same_page() -> None:
    service_app = create_app(
        TransactionServiceSettings(
            postgres_dsn_readonly=DatabaseSettings().postgres_dsn_readonly,
            auth_enabled=True,
            transaction_service_api_token=SERVICE_TOKEN,
        )
    )
    mcp_server = create_mcp_server(
        McpServerSettings(
            auth_enabled=False,
            mcp_api_token=None,
            transaction_service_url="http://transaction-service",  # type: ignore[arg-type]
            transaction_service_api_token=SERVICE_TOKEN,
        ),
        transaction_service_transport=httpx.ASGITransport(app=service_app),
    )
    claude = ScriptedClaude(
        [
            reply_with(
                tool_use(
                    "search_transactions",
                    {"filter": condition("has_chargeback", "exists", True), "page_size": 5},
                )
            ),
            final_answer("15 transactions have chargebacks."),
        ]
    )
    try:
        async with (
            service_app.router.lifespan_context(service_app),
            McpToolGateway(lambda: Client(mcp_server)) as gateway,
            TransactionServiceClient(
                "http://transaction-service",
                SERVICE_TOKEN,
                transport=httpx.ASGITransport(app=service_app),
            ) as service_client,
        ):
            agent = QueryAgent(
                claude,
                gateway,
                InMemorySessionStore(10, timedelta(hours=2), SystemClock()),
                SystemClock(),
                ZoneInfo("America/New_York"),
                max_tool_rounds=8,
                tool_result_preview_rows=10,
            )
            answer = await agent.answer("session-1", "Which transactions have chargebacks?")
            assert answer.query_plan is not None and answer.result_page is not None

            executor = PlanExecutor(service_client)
            first_page = await executor.execute(answer.query_plan, page=1, page_size=5)
            assert first_page.result.model_dump(mode="json") == answer.result_page

            pages = [first_page] + [
                await executor.execute(answer.query_plan, page=number, page_size=5)
                for number in (2, 3)
            ]
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")

    assert first_page.entity is SearchableEntity.TRANSACTIONS
    ids = [item.transaction_id for page in pages for item in page.result.items]
    assert len(ids) == len(set(ids)) == first_page.result.total_count == 15
