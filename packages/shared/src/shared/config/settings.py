from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Reads settings from environment variables, falling back to a local `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class DatabaseSettings(BaseServiceSettings):
    postgres_dsn_owner: PostgresDsn
    postgres_dsn_readonly: PostgresDsn
