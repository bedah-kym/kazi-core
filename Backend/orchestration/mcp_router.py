"""
Deprecated alias for `orchestration.tool_router` (renamed v0.5).

This module exists only so external importers of the legacy path keep
working for one release cycle. New code must import from
`orchestration.tool_router`; this shim will be removed in v0.6.
"""
from orchestration.tool_router import *  # noqa: F401,F403
from orchestration.tool_router import (  # noqa: F401
    MCPRouter,
    get_mcp_router,
    route_intent,
)
