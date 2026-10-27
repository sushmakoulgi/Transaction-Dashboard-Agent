from pydantic import Field, PostgresDsn

from shared.config import BaseServiceSettings


class TransactionServiceSettings(BaseServiceSettings):
    postgres_dsn_readonly: PostgresDsn
    business_timezone: str = "America/New_York"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
