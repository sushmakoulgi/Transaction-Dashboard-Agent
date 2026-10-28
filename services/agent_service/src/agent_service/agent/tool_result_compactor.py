"""What Claude sees of a tool result.

Search results are trimmed to their totals plus the first few rows: the user sees the full
page in the dashboard, so it never needs to sit in Claude's context. Detail and metadata
results are small and passed whole; errors are passed verbatim so Claude can fix its call.
"""

import json
from typing import Any

from anthropic.types.beta import BetaToolResultBlockParam

from agent_service.mcp_bridge.mcp_tool_gateway import ToolCallOutcome

SEARCH_RESULT_KEYS = ("total_count", "page", "page_size", "total_pages")


class ToolResultCompactor:
    def __init__(self, preview_rows: int) -> None:
        self._preview_rows = preview_rows

    def to_tool_result(
        self, tool_use_id: str, outcome: ToolCallOutcome
    ) -> BetaToolResultBlockParam:
        if outcome.is_error:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": outcome.text or "the tool failed without a message",
                "is_error": True,
            }
        content = outcome.structured_content
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": compact_json(self._compact(content))
            if content is not None
            else outcome.text,
        }

    def _compact(self, content: dict[str, Any]) -> dict[str, Any]:
        items = content.get("items")
        if not isinstance(items, list) or not all(key in content for key in SEARCH_RESULT_KEYS):
            return content
        shown = items[: self._preview_rows]
        compacted: dict[str, Any] = {key: content[key] for key in SEARCH_RESULT_KEYS}
        compacted["rows_on_this_page"] = len(items)
        compacted["rows"] = shown
        if len(items) > len(shown):
            compacted["note"] = (
                f"Showing the first {len(shown)} of {len(items)} rows on this page; "
                "the user sees all of them in the dashboard table."
            )
        return compacted


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
