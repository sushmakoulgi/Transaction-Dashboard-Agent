from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_API_TOKEN_LENGTH = 16


class BaseServiceSettings(BaseSettings):
    """Reads settings from environment variables, falling back to a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AuthenticatedServiceSettings(BaseServiceSettings):
    """Settings for a service whose API is protected by a bearer token.

    `AUTH_ENABLED` (default true) applies across the whole app. When it is false, no token
    is checked and tokens may be left unset.
    """

    auth_enabled: bool = True

    def ensure_api_token(self, token: SecretStr | None, setting_name: str) -> None:
        if self.auth_enabled and token is None:
            raise ValueError(f"{setting_name} is required while AUTH_ENABLED is true")


def api_token_field() -> SecretStr | None:
    """An optional API token; when set it must be at least MIN_API_TOKEN_LENGTH characters."""
    return Field(default=None, min_length=MIN_API_TOKEN_LENGTH)


class DatabaseSettings(BaseServiceSettings):
    postgres_dsn_owner: PostgresDsn
    postgres_dsn_readonly: PostgresDsn
