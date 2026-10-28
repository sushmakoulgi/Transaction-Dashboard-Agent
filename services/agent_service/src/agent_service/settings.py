from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator

from shared.config import BaseServiceSettings
from shared.config.settings import AuthenticatedServiceSettings, api_token_field

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ClaudeSettings(BaseServiceSettings):
    # When unset, the Anthropic SDK resolves credentials itself (ANTHROPIC_API_KEY in the
    # process environment, or an `ant auth login` profile). Set it in .env to use that key.
    anthropic_api_key: SecretStr | None = None
    planner_model: str = "claude-opus-5-5"
    planner_effort: Effort = "medium"  # set explicitly: this is also the model's default
    planner_max_tokens: int = Field(default=16000, ge=1024)  # thinking + the reply
    claude_request_timeout_seconds: float = Field(default=120.0, gt=0)
    claude_max_retries: int = Field(default=3, ge=0)  # SDK retries 408/409/429/5xx/network
    use_refusal_fallbacks: bool = True  # re-run declined requests on Anthropic's fallback

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def blank_key_means_unset(cls, value: object) -> object:
        """`ANTHROPIC_API_KEY=` (empty) in .env lets the SDK resolve credentials itself."""
        return None if isinstance(value, str) and not value.strip() else value


class McpConnectionSettings(AuthenticatedServiceSettings):
    """How the agent reaches the MCP server. The token is needed only while AUTH_ENABLED."""

    mcp_server_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8100/mcp")
    mcp_api_token: SecretStr | None = api_token_field()
    mcp_tool_call_timeout_seconds: float = Field(default=30.0, gt=0)
    mcp_startup_wait_seconds: float = Field(default=20.0, ge=0)  # MCP server may start later

    @model_validator(mode="after")
    def require_token_when_auth_enabled(self) -> Self:
        self.ensure_api_token(self.mcp_api_token, "MCP_API_TOKEN")
        return self


class SessionSettings(BaseServiceSettings):
    session_context_question_limit: int = Field(default=10, ge=1, le=50)
    session_idle_ttl_minutes: int = Field(default=120, ge=1)


class AgentSettings(BaseServiceSettings):
    business_timezone: str = "America/New_York"
    max_tool_rounds: int = Field(default=8, ge=1, le=20)  # Claude calls per question
    tool_result_preview_rows: int = Field(default=10, ge=1, le=100)  # rows Claude sees


class AgentServiceSettings(ClaudeSettings, McpConnectionSettings, SessionSettings, AgentSettings):
    """Everything the agent service reads from the environment / .env."""

    agent_api_token: SecretStr | None = api_token_field()  # what the UI sends (AUTH_ENABLED)
    transaction_service_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8200")
    transaction_service_api_token: SecretStr | None = api_token_field()
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"]  # the Vite dev server
    )

    @model_validator(mode="after")
    def require_service_tokens_when_auth_enabled(self) -> Self:
        self.ensure_api_token(self.agent_api_token, "AGENT_API_TOKEN")
        self.ensure_api_token(self.transaction_service_api_token, "TRANSACTION_SERVICE_API_TOKEN")
        return self
