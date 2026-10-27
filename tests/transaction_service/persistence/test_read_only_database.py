"""Integration tests for the service's read-only database access.

Requires the seeded docker-compose Postgres (`make up migrate seed`).
"""

from collections.abc import AsyncIterator

import pytest
from database_test_helpers import connect_or_skip
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from shared.config import DatabaseSettings
from transaction_service.persistence.database import (
    ReadOnlyDatabase,
    WritableDatabaseAccountError,
)

pytestmark = pytest.mark.integration

INSERT_TRANSACTION = text(
    "INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type,"
    " transaction_date) VALUES (gen_random_uuid(), 'C-1', 1, 'USD', 'PURCHASE', now())"
)


@pytest.fixture
async def database_opened_with_owner_account() -> AsyncIterator[ReadOnlyDatabase]:
    """Simulates a misconfigured service that was given the writable owner account."""
    database = await connect_or_skip(str(DatabaseSettings().postgres_dsn_owner))
    yield database
    await database.dispose()


async def test_reads_data(readonly_database: ReadOnlyDatabase) -> None:
    async with readonly_database.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM transactions"))).scalar_one()
    assert count == 100


async def test_sessions_run_in_a_read_only_transaction(
    readonly_database: ReadOnlyDatabase,
) -> None:
    async with readonly_database.session() as session:
        mode = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
    assert mode == "on"


async def test_startup_check_passes_for_the_read_only_account(
    readonly_database: ReadOnlyDatabase,
) -> None:
    await readonly_database.ensure_cannot_write()


async def test_startup_check_rejects_a_writable_account(
    database_opened_with_owner_account: ReadOnlyDatabase,
) -> None:
    with pytest.raises(WritableDatabaseAccountError, match=r"can write to: .*transactions"):
        await database_opened_with_owner_account.ensure_cannot_write()


async def test_writes_fail_even_with_a_writable_account(
    database_opened_with_owner_account: ReadOnlyDatabase,
) -> None:
    async with database_opened_with_owner_account.session() as session:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await session.execute(INSERT_TRANSACTION)
    async with database_opened_with_owner_account.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM transactions"))).scalar_one()
    assert count == 100
