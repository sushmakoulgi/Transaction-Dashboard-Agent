from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.exc import OperationalError

from shared.config import DatabaseSettings


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
