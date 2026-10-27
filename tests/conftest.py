from collections.abc import AsyncIterator, Iterator

import pytest
from database_test_helpers import connect_or_skip
from sqlalchemy import Connection, create_engine
from sqlalchemy.exc import OperationalError

from shared.config import DatabaseSettings
from transaction_service.persistence.database import ReadOnlyDatabase


@pytest.fixture
def readonly_connection() -> Iterator[Connection]:
    """A connection as app_readonly; skips the test when Postgres isn't running."""
    engine = create_engine(str(DatabaseSettings().postgres_dsn_readonly))
    try:
        connection = engine.connect()
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")
    with connection:
        yield connection
    engine.dispose()


@pytest.fixture
async def readonly_database() -> AsyncIterator[ReadOnlyDatabase]:
    """The service's ReadOnlyDatabase as app_readonly; skips when Postgres isn't running."""
    database = await connect_or_skip(str(DatabaseSettings().postgres_dsn_readonly))
    yield database
    await database.dispose()
