"""Integration tests: the app_readonly role can read but never change data.

Requires the docker-compose Postgres with migrations applied (`make up migrate`).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from shared.config import DatabaseSettings

pytestmark = pytest.mark.integration


@pytest.fixture
def readonly_connection() -> Iterator[Connection]:
    engine = create_engine(str(DatabaseSettings().postgres_dsn_readonly))
    try:
        connection = engine.connect()
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")
    with connection:
        yield connection
    engine.dispose()


def test_can_select_from_every_table(readonly_connection: Connection) -> None:
    for table_name in ("transactions", "payments", "refunds", "chargebacks", "chargeback_events"):
        readonly_connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one()


def test_insert_is_rejected(readonly_connection: Connection) -> None:
    with pytest.raises(DBAPIError, match="read-only transaction"):
        readonly_connection.execute(
            text(
                "INSERT INTO transactions (transaction_id, customer_id, amount, currency,"
                " transaction_type, transaction_date)"
                " VALUES (gen_random_uuid(), 'C-1', 1, 'USD', 'PURCHASE', now())"
            )
        )


def test_write_is_rejected_even_after_turning_off_read_only_mode(
    readonly_connection: Connection,
) -> None:
    readonly_connection.execute(text("SET default_transaction_read_only = off"))
    readonly_connection.commit()
    with pytest.raises(DBAPIError, match="permission denied"):
        readonly_connection.execute(text("DELETE FROM transactions"))


def test_long_running_statement_is_cancelled(readonly_connection: Connection) -> None:
    assert readonly_connection.execute(text("SHOW statement_timeout")).scalar_one() == "5s"
    with pytest.raises(DBAPIError, match="statement timeout"):
        readonly_connection.execute(text("SELECT pg_sleep(6)"))
