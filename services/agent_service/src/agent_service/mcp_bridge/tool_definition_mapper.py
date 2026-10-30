"""Turns MCP tool listings into Anthropic tool definitions.

Tools are sorted by name so the tool list (the start of every prompt) is byte-identical
between requests.
"""

from collections.abc import Iterable
from typing import Any

from anthropic.types.beta import BetaToolParam
from mcp.types import Tool

# Pydantic emits OpenAPI's `discriminator` next to `oneOf`. It isn't JSON Schema, and the
# `oneOf` alternatives (each with a fixed `kind`) already say the same thing.
NON_STANDARD_SCHEMA_KEYWORDS = frozenset({"discriminator"})


def to_anthropic_tools(mcp_tools: Iterable[Tool]) -> tuple[BetaToolParam, ...]:
    return tuple(to_anthropic_tool(tool) for tool in sorted(mcp_tools, key=lambda t: t.name))


def to_anthropic_tool(mcp_tool: Tool) -> BetaToolParam:
    input_schema = without_non_standard_keywords(mcp_tool.input_schema)
    input_schema.setdefault("type", "object")
    return {
        "name": mcp_tool.name,
        "description": mcp_tool.description or mcp_tool.title or mcp_tool.name,
        "input_schema": input_schema,
    }


def without_non_standard_keywords(schema: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of the schema without keywords outside JSON Schema."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: clean(value)
                for key, value in node.items()
                if key not in NON_STANDARD_SCHEMA_KEYWORDS
            }
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    cleaned: dict[str, Any] = clean(schema)
    return cleaned
