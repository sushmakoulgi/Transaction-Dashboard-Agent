import json
from typing import Any

import boto3
import pytest
from pydantic import ValidationError

from shared.config import DatabaseSettings

OWNER_DSN = "postgresql+psycopg://app_owner:secret@localhost:5432/transactions"
READONLY_DSN = "postgresql+psycopg://app_readonly:secret@localhost:5432/transactions"
BOTH_URLS = {"POSTGRES_DSN_OWNER": OWNER_DSN, "POSTGRES_DSN_READONLY": READONLY_DSN}


def test_reads_database_urls_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_OWNER", OWNER_DSN)
    monkeypatch.setenv("POSTGRES_DSN_READONLY", READONLY_DSN)

    settings = DatabaseSettings(_env_file=None)  # type: ignore[call-arg]

    assert str(settings.postgres_dsn_owner) == OWNER_DSN
    assert str(settings.postgres_dsn_readonly) == READONLY_DSN


def test_missing_database_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_DSN_OWNER", raising=False)
    monkeypatch.delenv("POSTGRES_DSN_READONLY", raising=False)

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_non_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_OWNER", "mysql://root@localhost/transactions")
    monkeypatch.setenv("POSTGRES_DSN_READONLY", READONLY_DSN)

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)  # type: ignore[call-arg]


def test_errors_never_show_the_values_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing setting must not print the others' values (e.g. tokens into Lambda logs)."""
    monkeypatch.delenv("POSTGRES_DSN_OWNER", raising=False)
    monkeypatch.setenv("POSTGRES_DSN_READONLY", READONLY_DSN)

    with pytest.raises(ValidationError) as error:
        DatabaseSettings(_env_file=None)  # type: ignore[call-arg]

    assert "postgres_dsn_owner" in str(error.value)
    assert "secret" not in str(error.value)


class FakeSecretsManager:
    def __init__(self, secret: dict[str, str]) -> None:
        self.secret = secret
        self.requested: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803 (boto3 name)
        self.requested.append(SecretId)
        return {"SecretString": json.dumps(self.secret)}


def test_reads_settings_from_a_secrets_manager_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSecretsManager(
        {"POSTGRES_DSN_OWNER": OWNER_DSN, "POSTGRES_DSN_READONLY": READONLY_DSN}
    )
    monkeypatch.setattr(boto3, "client", lambda *_, **__: fake)
    monkeypatch.setenv("SETTINGS_SECRET_ID", "transaction-dashboard/test")
    monkeypatch.delenv("POSTGRES_DSN_OWNER", raising=False)
    monkeypatch.delenv("POSTGRES_DSN_READONLY", raising=False)

    settings = DatabaseSettings(_env_file=None)  # type: ignore[call-arg]

    assert fake.requested == ["transaction-dashboard/test"]
    assert str(settings.postgres_dsn_owner) == OWNER_DSN


def test_environment_variables_take_precedence_over_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSecretsManager(
        {"POSTGRES_DSN_OWNER": OWNER_DSN, "POSTGRES_DSN_READONLY": READONLY_DSN}
    )
    monkeypatch.setattr(boto3, "client", lambda *_, **__: fake)
    monkeypatch.setenv("SETTINGS_SECRET_ID", "transaction-dashboard/test")
    override = "postgresql+psycopg://app_owner:other@db.example:5432/transactions"
    monkeypatch.setenv("POSTGRES_DSN_OWNER", override)

    assert str(DatabaseSettings(_env_file=None).postgres_dsn_owner) == override  # type: ignore[call-arg]


def test_no_secret_is_read_without_settings_secret_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("Secrets Manager must not be called")

    monkeypatch.setattr(boto3, "client", fail)
    monkeypatch.delenv("SETTINGS_SECRET_ID", raising=False)
    monkeypatch.setenv("POSTGRES_DSN_OWNER", OWNER_DSN)
    monkeypatch.setenv("POSTGRES_DSN_READONLY", READONLY_DSN)

    DatabaseSettings(_env_file=None)  # type: ignore[call-arg]
