"""Prints the agent service's OpenAPI schema (the UI generates its TypeScript types from it).

    uv run python -m agent_service.export_openapi > ui/src/api/agent-openapi.json

The app is built but not started, so nothing is connected to; placeholder settings keep the
output independent of the local .env.
"""

import json
import sys

from pydantic import SecretStr

from agent_service.main import create_app
from agent_service.settings import AgentServiceSettings

PLACEHOLDER_TOKEN = SecretStr("placeholder-token-for-schema-export")


def openapi_schema() -> dict[str, object]:
    settings = AgentServiceSettings(
        auth_enabled=True,
        agent_api_token=PLACEHOLDER_TOKEN,
        mcp_api_token=PLACEHOLDER_TOKEN,
        transaction_service_api_token=PLACEHOLDER_TOKEN,
        anthropic_api_key=None,
    )
    return create_app(settings).openapi()


def main() -> None:
    json.dump(openapi_schema(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
