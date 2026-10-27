from typing import Self

from pydantic import Field, PostgresDsn, SecretStr, model_validator

from shared.config.settings import AuthenticatedServiceSettings, api_token_field


class TransactionServiceSettings(AuthenticatedServiceSettings):
    postgres_dsn_readonly: PostgresDsn
    transaction_service_api_token: SecretStr | None = api_token_field()
    business_timezone: str = "America/New_York"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def require_token_when_auth_enabled(self) -> Self:
        self.ensure_api_token(self.transaction_service_api_token, "TRANSACTION_SERVICE_API_TOKEN")
        return self
