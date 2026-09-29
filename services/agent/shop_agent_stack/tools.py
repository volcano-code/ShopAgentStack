from contextlib import asynccontextmanager
import json
import os
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .observability import observe_tool
from .observability_transport import MCPTraceBridge

MODEL_TOOLS = {"list_my_orders", "get_my_order", "list_my_after_sales", "preview_after_sale", "get_operation_status", "search_policies", "search_products", "get_product"}


@asynccontextmanager
async def connect(execution: str):
    # The transport lives for the whole run and carries immutable per-run identity.
    bridge = MCPTraceBridge()
    try:
        async with httpx.AsyncClient(headers={"X-ShopAgentStack-Execution": execution}, timeout=20, trust_env=False, event_hooks={"request": [bridge.inject]}) as client:
            async with streamable_http_client(os.getenv("SHOP_AGENT_STACK_MCP_URL", "http://commerce-mcp:8011/mcp"), http_client=client) as (read, write, _):
                async with ClientSession(read, bridge.wrap(write)) as session:
                    await session.initialize()
                    yield session
    finally:
        bridge.clear()


@observe_tool
async def call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if result.isError:
        message = " ".join(c.text for c in result.content if hasattr(c, "text"))
        raise ValueError(message[:300] or "工具调用失败")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(c.text for c in result.content if hasattr(c, "text")))
