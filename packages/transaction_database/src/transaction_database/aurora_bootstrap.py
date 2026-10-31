"""One-time setup of an Amazon Aurora PostgreSQL cluster with IAM-only sign-in (e.g. express
configuration): `python -m transaction_database.aurora_bootstrap` (`make aurora-bootstrap`).

The Aurora counterpart of database/initdb/01_create_application_roles.sh. Signs in as the
cluster's admin (DATABASE_ADMIN_USER, `postgres`) with an IAM token, then creates the
database named in POSTGRES_DSN_OWNER and the two application roles:
- app_owner    — owns the schema; used by migrations and the seed script.
- app_readonly — SELECT only, read-only transactions, 5s statement timeout; used by services.
Both get `rds_iam` instead of a password, so they sign in with IAM tokens only. Safe to repeat.

Needs DATABASE_IAM_AUTH=true and the POSTGRES_DSN_* URLs pointing at the cluster (no password).
"""

import logging

from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from shared.config import DatabaseSettings
from transaction_database.connection import database_engine

logger = logging.getLogger("transaction_database.aurora_bootstrap")  # not __main__

# Roles are cluster-wide. Granting app_owner to the admin lets it hand objects to app_owner
# (PostgreSQL 16+ gives a role's creator only ADMIN OPTION, not membership).
CREATE_ROLES = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner') THEN
        CREATE ROLE app_owner LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_readonly') THEN
        CREATE ROLE app_readonly LOGIN;
    END IF;
END
$$;
GRANT rds_iam TO app_owner, app_readonly;
GRANT app_owner TO CURRENT_USER;
ALTER ROLE app_readonly SET default_transaction_read_only = on;
ALTER ROLE app_readonly SET statement_timeout = '5s';
"""

GRANT_ACCESS = """
GRANT CONNECT ON DATABASE {database} TO app_owner, app_readonly;
ALTER SCHEMA public OWNER TO app_owner;
GRANT USAGE ON SCHEMA public TO app_readonly;
-- Every table app_owner creates later is automatically readable (and only readable).
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO app_readonly;
"""


class AuroraBootstrapSettings(DatabaseSettings):
    database_admin_user: str = "postgres"  # an express cluster's IAM-enabled admin


def admin_engine(settings: AuroraBootstrapSettings, database: str) -> Engine:
    url = make_url(str(settings.postgres_dsn_owner)).set(
        username=settings.database_admin_user, database=database
    )
    return database_engine(
        url.render_as_string(hide_password=False), settings, isolation_level="AUTOCOMMIT"
    )


def bootstrap(settings: AuroraBootstrapSettings) -> None:
    if not settings.database_iam_auth:
        raise SystemExit("DATABASE_IAM_AUTH must be true: this sets up an IAM-auth Aurora cluster")
    database = make_url(str(settings.postgres_dsn_owner)).database
    if not database or not database.isidentifier():
        raise SystemExit(f"POSTGRES_DSN_OWNER needs a plain database name, not {database!r}")

    engine = admin_engine(settings, "postgres")
    try:
        with engine.connect() as connection:
            connection.execute(text(CREATE_ROLES))
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            ).first()
            if exists:
                logger.info("Database %s already exists", database)
            else:
                connection.execute(text(f'CREATE DATABASE "{database}"'))
                logger.info("Created database %s", database)
    finally:
        engine.dispose()

    engine = admin_engine(settings, database)
    try:
        with engine.connect() as connection:
            connection.execute(text(GRANT_ACCESS.format(database=f'"{database}"')))
    finally:
        engine.dispose()
    logger.info("Roles app_owner and app_readonly are ready (IAM sign-in only)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    bootstrap(AuroraBootstrapSettings())  # values come from the environment


if __name__ == "__main__":
    main()
