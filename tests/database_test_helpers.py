"""Database helpers shared by the integration tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from transaction_service.persistence.database import ReadOnlyDatabase


async def connect_or_skip(database_url: str) -> ReadOnlyDatabase:
    """A connected ReadOnlyDatabase; skips the test when Postgres isn't running."""
    database = ReadOnlyDatabase(database_url)
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
    except OperationalError as error:
        await database.dispose()
        pytest.skip(f"Postgres is not reachable: {error.orig}")
    return database
