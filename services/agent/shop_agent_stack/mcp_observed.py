"""Opt-in server entrypoint. All original MCP tool definitions remain unchanged."""
from .mcp_server import app as original_app
from .observability_server import ObservedMCPApp

app = ObservedMCPApp(original_app)
