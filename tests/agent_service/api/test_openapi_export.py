"""The UI's TypeScript types are generated from ui/src/api/agent-openapi.json; it must match
the backend. If this fails, run `make ui-api-types` and commit the regenerated files."""

import json
from pathlib import Path

from agent_service.export_openapi import openapi_schema

COMMITTED_SCHEMA = Path(__file__).parents[3] / "ui" / "src" / "api" / "agent-openapi.json"


def test_committed_ui_schema_matches_the_backend() -> None:
    committed = json.loads(COMMITTED_SCHEMA.read_text())
    assert committed == json.loads(json.dumps(openapi_schema(), sort_keys=True)), (
        "the agent API changed: run `make ui-api-types` to regenerate the UI types"
    )
