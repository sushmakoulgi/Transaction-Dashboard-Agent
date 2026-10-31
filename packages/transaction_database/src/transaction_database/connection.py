"""Database engines that can sign in with IAM auth tokens (Amazon Aurora / RDS).

With DATABASE_IAM_AUTH=true the database URL has no password. A `do_connect` hook signs a
fresh token for every new physical connection, so pooled connections outlive the token's
15 minutes (it is only checked at login). Signing is local: no AWS call per connection.
"""

from typing import Any

import boto3
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shared.config import DatabaseIamAuthSettings

DEFAULT_POSTGRES_PORT = 5432


def database_engine(
    database_url: str, iam_auth: DatabaseIamAuthSettings | None = None, **options: Any
) -> Engine:
    engine = create_engine(database_url, **options)
    if iam_auth is not None and iam_auth.database_iam_auth:
        sign_in_with_iam_tokens(engine, make_url(database_url), iam_auth)
    return engine


def async_database_engine(
    database_url: str, iam_auth: DatabaseIamAuthSettings | None = None, **options: Any
) -> AsyncEngine:
    engine = create_async_engine(database_url, **options)
    if iam_auth is not None and iam_auth.database_iam_auth:
        sign_in_with_iam_tokens(engine.sync_engine, make_url(database_url), iam_auth)
    return engine


def sign_in_with_iam_tokens(engine: Engine, url: URL, iam_auth: DatabaseIamAuthSettings) -> None:
    if url.host is None or url.username is None:
        raise ValueError("an IAM-auth database URL needs a host and a user name")
    if url.password is not None:
        raise ValueError("an IAM-auth database URL must not contain a password")
    host, port, user = url.host, url.port or DEFAULT_POSTGRES_PORT, url.username
    rds = boto3.Session(
        profile_name=iam_auth.database_aws_profile, region_name=iam_auth.database_aws_region
    ).client("rds")

    @event.listens_for(engine, "do_connect")
    def add_token(_dialect: Any, _record: Any, _args: Any, params: dict[str, Any]) -> None:
        params["password"] = rds.generate_db_auth_token(DBHostname=host, Port=port, DBUsername=user)
        params.setdefault("sslmode", "require")  # IAM auth needs SSL; the URL may ask for more
