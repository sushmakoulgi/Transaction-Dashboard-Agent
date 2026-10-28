"""Integration test: first-run setup migrates and never replaces existing data."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from transaction_database.seed.sample_data_seeder import SeedSettings
from transaction_database.setup import ALEMBIC_INI, set_up_database

pytestmark = pytest.mark.integration


def test_alembic_config_is_found() -> None:
    assert ALEMBIC_INI.is_file()


def test_setup_keeps_existing_data() -> None:
    settings = SeedSettings()
    engine = create_engine(str(settings.postgres_dsn_owner))
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text("SELECT array_agg(transaction_id ORDER BY transaction_id) FROM transactions")
            ).scalar_one()
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")
    if not before:
        pytest.skip("needs a seeded database (make up migrate seed)")

    assert set_up_database(settings) is False

    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT array_agg(transaction_id ORDER BY transaction_id) FROM transactions")
        ).scalar_one()
    engine.dispose()
    assert after == before
