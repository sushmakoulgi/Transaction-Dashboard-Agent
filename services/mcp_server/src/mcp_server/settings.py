from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator

from shared.config.settings import AuthenticatedServiceSettings, api_token_field


class McpServerSettings(AuthenticatedServiceSettings):
    mcp_api_token: SecretStr | None = api_token_field()  # what MCP clients must send
    transaction_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8200")
    transaction_service_api_token: SecretStr | None = api_token_field()
    transaction_service_timeout_seconds: float = Field(default=10.0, gt=0)
    # DNS-rebinding protection: the Host / Origin headers the MCP endpoint accepts. Containers
    # add their service name (e.g. "mcp-server:*") so other containers can reach it.
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    )
    mcp_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    )

    @model_validator(mode="after")
    def require_tokens_when_auth_enabled(self) -> Self:
        self.ensure_api_token(self.mcp_api_token, "MCP_API_TOKEN")
        self.ensure_api_token(self.transaction_service_api_token, "TRANSACTION_SERVICE_API_TOKEN")
        return self
