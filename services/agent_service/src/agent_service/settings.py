from typing import Literal

from pydantic import Field, SecretStr, field_validator

from shared.config import BaseServiceSettings

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
