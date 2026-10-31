"""IAM-auth engines: every new connection gets a fresh token and SSL (no AWS call: signing
is local, with fake credentials)."""

from typing import Any

import pytest
from sqlalchemy import Engine

from shared.config import DatabaseIamAuthSettings
from transaction_database.connection import async_database_engine, database_engine

AURORA_URL = "postgresql+psycopg://app_readonly@db.example.us-east-1.rds.amazonaws.com/transactions"


@pytest.fixture(autouse=True)
def fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLEEXAMPLE00")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "example-secret-key-example-secret-key00")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def iam_auth(enabled: bool = True) -> DatabaseIamAuthSettings:
    return DatabaseIamAuthSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_iam_auth=enabled,
        database_aws_region="us-east-1",
    )


def connect_params(engine: Engine, **params: Any) -> dict[str, Any]:
    """What the driver would be called with, after the `do_connect` hooks ran."""
    engine.dialect.dispatch.do_connect(engine.dialect, None, [], params)
    return params


def test_each_connection_gets_a_token_and_ssl() -> None:
    params = connect_params(database_engine(AURORA_URL, iam_auth()))
    assert params["password"].startswith(
        "db.example.us-east-1.rds.amazonaws.com:5432/?Action=connect&DBUser=app_readonly"
    )
    assert params["sslmode"] == "require"


def test_a_stricter_sslmode_from_the_url_is_kept() -> None:
    engine = database_engine(AURORA_URL, iam_auth())
    assert connect_params(engine, sslmode="verify-full")["sslmode"] == "verify-full"


def test_async_engines_get_tokens_too() -> None:
    engine = async_database_engine(AURORA_URL, iam_auth())
    assert "Action=connect" in connect_params(engine.sync_engine)["password"]


def test_off_means_the_url_is_used_as_is() -> None:
    url = "postgresql+psycopg://app_readonly:secret@localhost/transactions"
    assert connect_params(database_engine(url, iam_auth(enabled=False))) == {}
    assert connect_params(database_engine(url)) == {}


def test_a_password_in_an_iam_url_is_refused() -> None:
    with pytest.raises(ValueError, match="must not contain a password"):
        database_engine(
            "postgresql+psycopg://app_readonly:secret@db.example/transactions", iam_auth()
        )
