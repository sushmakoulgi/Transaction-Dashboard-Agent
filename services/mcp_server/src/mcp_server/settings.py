from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator

from shared.config.settings import AuthenticatedServiceSettings, api_token_field


class McpServerSettings(AuthenticatedServiceSettings):
    mcp_api_token: SecretStr | None = api_token_field()  # what MCP clients must send
    transaction_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8200")
    transaction_service_api_token: SecretStr | None = api_token_field()
    transaction_service_timeout_seconds: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def require_tokens_when_auth_enabled(self) -> Self:
        self.ensure_api_token(self.mcp_api_token, "MCP_API_TOKEN")
        self.ensure_api_token(self.transaction_service_api_token, "TRANSACTION_SERVICE_API_TOKEN")
        return self
