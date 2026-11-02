import os

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import (
    AWSSecretsManagerSettingsSource,
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

MIN_API_TOKEN_LENGTH = 16
# Names an AWS Secrets Manager secret (JSON object, keys like ANTHROPIC_API_KEY) to read
# settings from, e.g. on AWS Lambda, so secrets are never plain environment variables there.
SETTINGS_SECRET_ID_VARIABLE = "SETTINGS_SECRET_ID"


class BaseServiceSettings(BaseSettings):
    """Reads settings from environment variables, then a local `.env` file, then (when
    SETTINGS_SECRET_ID is set) an AWS Secrets Manager secret, in that order of precedence."""

    # hide_input_in_errors: a bad or missing setting names the field but never prints the
    # values that were read (API tokens, keys), e.g. into a Lambda's CloudWatch logs.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = [init_settings, env_settings, dotenv_settings]
        secret_id = os.environ.get(SETTINGS_SECRET_ID_VARIABLE)
        if secret_id:  # read once per settings object; region and credentials from boto3
            sources.append(
                AWSSecretsManagerSettingsSource(settings_cls, secret_id, case_sensitive=False)
            )
        return (*sources, file_secret_settings)


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


class DatabaseIamAuthSettings(BaseServiceSettings):
    """IAM database authentication (Amazon Aurora / RDS), e.g. an express-configuration cluster.

    When on, the database URLs carry no password: every new connection signs a 15-minute
    token with the AWS credentials of DATABASE_AWS_PROFILE (or the default chain), and SSL is
    required. Off by default, for the local Postgres with passwords.
    """

    database_iam_auth: bool = False
    database_aws_region: str | None = None  # the cluster's Region; else the profile's
    database_aws_profile: str | None = None  # a profile in ~/.aws/config; else the default


class DatabaseSettings(DatabaseIamAuthSettings):
    postgres_dsn_owner: PostgresDsn
    postgres_dsn_readonly: PostgresDsn
