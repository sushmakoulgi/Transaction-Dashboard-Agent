import logging
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator

from shared.config import BaseServiceSettings
from shared.config.settings import AuthenticatedServiceSettings, api_token_field

Effort = Literal["low", "medium", "high", "xhigh", "max"]

logger = logging.getLogger(__name__)


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


class McpSettings(BaseServiceSettings):
    """The MCP tools run inside the agent and are reached in memory: no URL or token."""

    mcp_tool_call_timeout_seconds: float = Field(default=30.0, gt=0)


class SessionSettings(BaseServiceSettings):
    session_context_question_limit: int = Field(default=10, ge=1, le=50)
    session_idle_ttl_minutes: int = Field(default=120, ge=1)


class AgentSettings(BaseServiceSettings):
    business_timezone: str = "America/New_York"
    max_tool_rounds: int = Field(default=8, ge=1, le=20)  # Claude calls per question
    tool_result_preview_rows: int = Field(default=10, ge=1, le=100)  # rows Claude sees


class PlanCacheSettings(BaseServiceSettings):
    """Phase 2 plan cache: reuse a stored query plan for a question of the same shape.

    Off by default. It needs VOYAGE_API_KEY (the embeddings of the templates); without the
    key the service still starts and every question goes to Claude, as with the cache off.
    """

    plan_cache_enabled: bool = False
    plan_hit_threshold: float = Field(default=0.90, gt=0, le=1)  # cosine similarity
    voyage_api_key: SecretStr | None = None
    chroma_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8010")  # the stack's published port
    chroma_timeout_seconds: float = Field(default=5.0, gt=0)
    voyage_api_url: AnyHttpUrl = AnyHttpUrl("https://api.voyageai.com/v1")
    embedding_model: str = "voyage-4-lite"
    embedding_timeout_seconds: float = Field(default=10.0, gt=0)
    embedding_max_retries: int = Field(default=2, ge=0)  # on 429, 5xx and network errors
    template_extractor_model: str = "claude-haiku-5-5"
    template_extractor_effort: Effort = "low"  # a short extraction, not a hard problem
    template_extractor_max_tokens: int = Field(default=4000, ge=512)

    @field_validator("voyage_api_key", mode="before")
    @classmethod
    def blank_voyage_key_means_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def warn_when_enabled_without_a_key(self) -> Self:
        if self.plan_cache_enabled and self.voyage_api_key is None:
            logger.warning(
                "PLAN_CACHE_ENABLED is true but VOYAGE_API_KEY is not set: the plan cache is "
                "off and every question goes to Claude"
            )
        return self

    @property
    def plan_cache_active(self) -> bool:
        """Whether questions are looked up in the plan cache before going to Claude."""
        return self.plan_cache_enabled and self.voyage_api_key is not None


class AgentServiceSettings(
    AuthenticatedServiceSettings,
    ClaudeSettings,
    McpSettings,
    SessionSettings,
    AgentSettings,
    PlanCacheSettings,
):
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
