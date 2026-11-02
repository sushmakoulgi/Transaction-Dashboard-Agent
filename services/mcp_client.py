import asyncio

from mcp import Client


async def connect():
    mcp_client = Client("http://localhost:8100/mcp")
    await mcp_client.__aenter__()
    print("Connecting to MCP Server ")
    return mcp_client


async def disconnect(client: Client):
    await client.__aexit__(None, None, None)
    return


async def discover_tools(mcp_client: Client):
    tools = await mcp_client.list_tools()

    for tool in tools.tools:
        print(f" tool-- {tool.name}--{tool.description}")

    return tools


async def execute_tool(mcp_client: Client, tool, args):
    if args is None:
        args = {}
    result = await mcp_client.call_tool(tool, arguments=args)
    return result


async def main():
    client = await connect()
    await discover_tools(client)
    result = await execute_tool(
        client,
        "search_transactions",
        {
            "filter": {
                "kind": "condition",
                "field": "customer_id",
                "operator": "eq",
                "value": "C-1015",
            },
            "page_size": 5,
        },
    )
    print(result)
    await disconnect(client=client)


asyncio.run(main())
