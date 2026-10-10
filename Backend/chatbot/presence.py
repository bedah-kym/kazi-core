"""Presence for chat rooms.

PR 1 of the presence work: the agent's declared status only. The per-connection
store lands later, so this module stays small.
"""
from __future__ import annotations

from orchestration.model_catalog import available_models


def agent_status() -> str:
    """``"online"`` when a message sent now would reach a model, else ``"offline"``.

    The bot never opens a socket, so the web process declares its status rather
    than inferring it from a connection. A model is reachable when at least one
    catalog provider has its key configured — the same set the model picker and
    the agent loop use.
    """
    return "online" if available_models() else "offline"
