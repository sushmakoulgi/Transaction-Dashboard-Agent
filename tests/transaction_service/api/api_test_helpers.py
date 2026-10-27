"""Constants and helpers for the Transaction Service API tests."""

from typing import Any

import httpx
from pydantic import SecretStr

from transaction_service.settings import TransactionServiceSettings

API_TOKEN = "test-token-0123456789abcdef"
AUTHORIZED = {"Authorization": f"Bearer {API_TOKEN}"}


def settings_for(database_url: str, auth_enabled: bool = True) -> TransactionServiceSettings:
    """Explicit settings, so a local AUTH_ENABLED in .env can't change the tests."""
    return TransactionServiceSettings(
        postgres_dsn_readonly=database_url,  # type: ignore[arg-type]
        auth_enabled=auth_enabled,
        transaction_service_api_token=SecretStr(API_TOKEN) if auth_enabled else None,
    )


async def post_search(
    client: httpx.AsyncClient, entity: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(f"/api/v1/{entity}/search", json=body, headers=AUTHORIZED)
