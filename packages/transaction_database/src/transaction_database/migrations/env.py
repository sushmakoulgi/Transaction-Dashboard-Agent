from logging.config import fileConfig

from alembic import context

from shared.config import DatabaseSettings
from transaction_database.connection import database_engine
from transaction_database.orm_models import Base

if context.config.config_file_name is not None:
    # Keep loggers that already exist (e.g. db-setup's own) instead of silencing them.
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def owner_database_url() -> str:
    return str(DatabaseSettings().postgres_dsn_owner)  # values come from the environment


def run_migrations_offline() -> None:
    context.configure(
        url=owner_database_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = DatabaseSettings()
    engine = database_engine(str(settings.postgres_dsn_owner), settings)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
