from pydantic import AnyHttpUrl, Field, SecretStr

from shared.config import BaseServiceSettings


class McpServerSettings(BaseServiceSettings):
    mcp_api_token: SecretStr = Field(min_length=16)  # what MCP clients must send
    transaction_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8200")
    transaction_service_api_token: SecretStr = Field(min_length=16)
    transaction_service_timeout_seconds: float = Field(default=10.0, gt=0)
