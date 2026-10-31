"""Mapping MCP tool listings (from the real MCP server) to Anthropic tool definitions."""

import json
from typing import Any

from mcp import Client
from mcp.types import Tool

from agent_service.mcp_bridge.tool_definition_mapper import (
    to_anthropic_tool,
    to_anthropic_tools,
    without_non_standard_keywords,
)
from agent_service.mcp_server.server import create_mcp_server


async def real_mcp_tools() -> list[Tool]:
    server = create_mcp_server("http://transaction-service", None)
    async with Client(server) as client:
        return list((await client.list_tools()).tools)


def contains_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(contains_key(item, key) for item in node)
    return False


async def test_maps_every_mcp_tool_sorted_by_name() -> None:
    tools = to_anthropic_tools(await real_mcp_tools())
    names = [tool["name"] for tool in tools]
    assert (
        names
        == sorted(names)
        == [
            "get_transaction_detail",
            "list_filterable_fields",
            "search_chargebacks",
            "search_transactions",
            "validate_filters",
        ]
    )
    for tool in tools:
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"


async def test_order_and_content_are_stable_between_listings() -> None:
    first = to_anthropic_tools(await real_mcp_tools())
    second = to_anthropic_tools(reversed(await real_mcp_tools()))
    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)


async def test_search_schema_keeps_the_recursive_filter_without_discriminator() -> None:
    tools = {tool["name"]: tool for tool in to_anthropic_tools(await real_mcp_tools())}
    schema = tools["search_transactions"]["input_schema"]
    assert {"FilterCondition", "FilterGroup"} <= set(schema["$defs"])
    assert not contains_key(schema, "discriminator")
    assert contains_key(schema, "oneOf")
    assert schema["properties"]["page_size"]["maximum"] == 100


def test_removal_is_deep_and_does_not_mutate_the_source() -> None:
    source = {"a": {"discriminator": {"x": 1}, "oneOf": [{"discriminator": 2, "b": 3}]}}
    assert without_non_standard_keywords(source) == {"a": {"oneOf": [{"b": 3}]}}
    assert source["a"]["discriminator"] == {"x": 1}


def test_missing_description_falls_back_to_title_then_name() -> None:
    bare = Tool(name="ping", input_schema={"type": "object"})
    titled = Tool(name="ping", title="Ping the server", input_schema={})
    assert to_anthropic_tool(bare)["description"] == "ping"
    assert to_anthropic_tool(titled)["description"] == "Ping the server"
    assert to_anthropic_tool(titled)["input_schema"] == {"type": "object"}
