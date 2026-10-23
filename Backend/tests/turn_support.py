"""Shared test doubles for the durable Turn writes (#216a).

The coordinator records a Turn through the ORM. Most coordinator tests are
``SimpleTestCase`` subclasses, where database access is forbidden, and they
are about routing rather than the record. Patching these call sites keeps the
routing assertions independent of the database; ``test_turns.py`` exercises
the real functions.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock


def turn_test_patches() -> dict:
    """Fresh coordinator-level doubles for one test run."""
    turn = SimpleNamespace(pk="00000000-0000-0000-0000-000000000001", version=0, state="running")
    return {
        "orchestration.coordinator.aget_paused_turn": AsyncMock(return_value=None),
        "orchestration.coordinator.aget_turn": AsyncMock(return_value=None),
        "orchestration.coordinator.aclaim_turn": AsyncMock(return_value=turn),
        "orchestration.coordinator.atransition": AsyncMock(return_value=True),
        "orchestration.coordinator.alink_message": AsyncMock(return_value=True),
        "orchestration.coordinator._room_exists": lambda room_id: True,
        # These read the Turn store in production; a routing test must not.
        "orchestration.coordinator.load_pending_state": AsyncMock(return_value=None),
        "orchestration.coordinator.pending_approval_prompt": AsyncMock(return_value={}),
        "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
    }
