"""First-run database setup: `python -m transaction_database.setup`.

Applies every migration (safe to repeat), then loads the sample data only when there are no
transactions yet, so restarting the stack never replaces existing data. Used by the
`db-setup` container in docker-compose; `make seed` still replaces the data on purpose.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select

from transaction_database.orm_models import Transaction
from transaction_database.seed.sample_data_seeder import SeedSettings, seed_sample_data

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

logger = logging.getLogger("transaction_database.setup")  # not __main__ under `python -m`


def apply_migrations() -> None:
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


def has_transactions(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(select(func.count()).select_from(Transaction)).scalar_one())


def set_up_database(settings: SeedSettings) -> bool:
    """Migrates, then seeds an empty database. Returns whether sample data was loaded."""
    apply_migrations()
    engine = create_engine(str(settings.postgres_dsn_owner))
    try:
        if has_transactions(engine):
            logger.info("Database already has data; not seeding")
            return False
        seed_sample_data(engine, settings)
        return True
    finally:
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # alembic.ini sets the root logger to WARNING; keep this package's progress messages.
    logging.getLogger("transaction_database").setLevel(logging.INFO)
    set_up_database(SeedSettings())  # values come from the environment


if __name__ == "__main__":
    main()
