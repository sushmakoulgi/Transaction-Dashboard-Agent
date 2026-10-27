"""Integration tests for the Transaction Service HTTP API.

The app runs with its real lifespan (startup write check, service wiring) against the seeded
docker-compose Postgres. Requests go through httpx without a network socket.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from filter_test_helpers import condition, group
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from shared.config import DatabaseSettings
from transaction_service.main import create_app
from transaction_service.persistence.database import WritableDatabaseAccountError
from transaction_service.settings import TransactionServiceSettings

pytestmark = pytest.mark.integration

API_TOKEN = "test-token-0123456789abcdef"
AUTHORIZED = {"Authorization": f"Bearer {API_TOKEN}"}


def settings_for(database_url: str) -> TransactionServiceSettings:
    return TransactionServiceSettings(
        postgres_dsn_readonly=database_url,  # type: ignore[arg-type]
        transaction_service_api_token=SecretStr(API_TOKEN),
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings_for(str(DatabaseSettings().postgres_dsn_readonly)))
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://transaction-service"
            ) as http_client:
                yield http_client
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")


async def post_search(
    client: httpx.AsyncClient, entity: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(f"/api/v1/{entity}/search", json=body, headers=AUTHORIZED)


# --- Startup and auth ---


async def test_refuses_to_start_with_a_writable_database_account() -> None:
    app = create_app(settings_for(str(DatabaseSettings().postgres_dsn_owner)))
    try:
        with pytest.raises(WritableDatabaseAccountError):
            async with app.router.lifespan_context(app):
                pass
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")


async def test_health_needs_no_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": f"Basic {API_TOKEN}"}],
)
async def test_api_rejects_missing_or_wrong_tokens(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/transactions/search", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_no_route_accepts_write_methods(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    methods = {method for operations in schema["paths"].values() for method in operations}
    assert methods <= {"get", "post"}
    post_paths = {path for path, operations in schema["paths"].items() if "post" in operations}
    assert post_paths == {
        "/api/v1/transactions/search",
        "/api/v1/chargebacks/search",
        "/api/v1/{entity}/filters/validate",
    }
    response = await client.delete("/api/v1/transactions/search", headers=AUTHORIZED)
    assert response.status_code == 405


# --- Search ---


async def test_search_transactions(client: httpx.AsyncClient) -> None:
    response = await post_search(client, "transactions", {"page_size": 10})
    assert response.status_code == 200
    body = response.json()
    assert (body["total_count"], body["total_pages"], len(body["items"])) == (100, 10, 10)
    assert {"transaction_id", "payment_status", "has_refund"} <= set(body["items"][0])


async def test_search_chargebacks_this_month(client: httpx.AsyncClient) -> None:
    response = await post_search(
        client,
        "chargebacks",
        {"filter": condition("opened_at", "between", {"relative": "this_month"})},
    )
    assert response.status_code == 200
    assert response.json()["total_count"] >= 1


async def test_invalid_filter_returns_every_issue(client: httpx.AsyncClient) -> None:
    response = await post_search(
        client,
        "transactions",
        {
            "filter": group(
                condition("cust_id", "eq", "C-1"), condition("payment_status", "eq", "SETTLED")
            )
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "FILTER_VALIDATION_FAILED"
    assert [(issue["path"], issue["code"]) for issue in body["issues"]] == [
        ("filter.children[0].field", "UNKNOWN_FIELD"),
        ("filter.children[1].value", "INVALID_VALUE"),
    ]
    assert "customer_id" in body["issues"][0]["suggestions"]


@pytest.mark.parametrize(
    "body",
    [
        {"raw_sql": "SELECT 1"},
        {"filter": condition("customer_id; DROP TABLE transactions", "eq", "x")},
        {"page_size": 1000},
        {"filter": group(group(group(group(condition("amount", "gt", 1)))))},
    ],
)
async def test_malformed_requests_return_shape_issues(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await post_search(client, "transactions", body)
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "INVALID_REQUEST"
    assert payload["issues"]
    assert {issue["code"] for issue in payload["issues"]} == {"INVALID_REQUEST_SHAPE"}


# --- Detail ---


async def test_transaction_detail(client: httpx.AsyncClient) -> None:
    first = (await post_search(client, "transactions", {"page_size": 1})).json()["items"][0]
    response = await client.get(
        f"/api/v1/transactions/{first['transaction_id']}", headers=AUTHORIZED
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["transaction_id"] == first["transaction_id"]
    assert detail["payment"]["payment_id"] == first["payment_id"]


async def test_unknown_transaction_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/transactions/{uuid.uuid4()}", headers=AUTHORIZED)
    assert response.status_code == 404
    assert response.json()["error"] == "TRANSACTION_NOT_FOUND"


async def test_malformed_transaction_id_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/transactions/not-a-uuid", headers=AUTHORIZED)
    assert response.status_code == 422
    assert response.json()["issues"][0]["path"] == "path.transaction_id"


# --- Metadata ---


async def test_lists_filterable_fields(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/chargebacks/filterable-fields", headers=AUTHORIZED)
    assert response.status_code == 200
    names = {field["name"] for field in response.json()}
    assert {"opened_at", "chargeback_event_type"} <= names


async def test_unknown_entity_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/payments/filterable-fields", headers=AUTHORIZED)
    assert response.status_code == 422


async def test_validate_filters(client: httpx.AsyncClient) -> None:
    valid = await client.post(
        "/api/v1/transactions/filters/validate",
        json={"filter": condition("customer_id", "eq", "C-1004")},
        headers=AUTHORIZED,
    )
    assert valid.json() == {"is_valid": True, "issues": []}
    invalid = await client.post(
        "/api/v1/transactions/filters/validate",
        json={"filter": condition("transaction_date", "eq", "2001-01-01")},
        headers=AUTHORIZED,
    )
    assert invalid.status_code == 200
    body = invalid.json()
    assert body["is_valid"] is False
    assert body["issues"][0]["code"] == "DATE_OUT_OF_RANGE"
