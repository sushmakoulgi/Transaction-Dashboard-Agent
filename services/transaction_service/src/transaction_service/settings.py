from pydantic import Field, PostgresDsn, SecretStr

from shared.config import BaseServiceSettings


class TransactionServiceSettings(BaseServiceSettings):
    postgres_dsn_readonly: PostgresDsn
    transaction_service_api_token: SecretStr = Field(min_length=16)
    business_timezone: str = "America/New_York"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
