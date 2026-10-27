"""An httpx client for the Transaction Service, running its real lifespan in-process."""

from collections.abc import AsyncIterator

import httpx
import pytest
from api_test_helpers import settings_for
from sqlalchemy.exc import OperationalError

from shared.config import DatabaseSettings
from transaction_service.main import create_app


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
