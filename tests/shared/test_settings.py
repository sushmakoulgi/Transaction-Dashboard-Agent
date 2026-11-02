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
