"""Compatibility import; production implementation ships in the Agent image.

Repository-root tooling uses a namespace import. The service uses a relative import;
neither layout requires adding the tools directory to the production Docker context.
"""
from services.agent.shop_agent_stack.observability_core import (  # noqa: F401
    BOOLEANS, NUMERIC, OPERATIONS, Telemetry, TraceMiddleware,
)
