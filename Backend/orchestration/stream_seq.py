"""Per-turn v2 stream ``seq`` counters (217a-2a).

A run numbers its own frames in memory, so a plain turn writes nothing here.
The high-water mark reaches the database inside the run's existing pause or
commit compare-and-set (``turns.transition`` reads ``get_run_seq``). A writer
outside the run takes its number with one atomic increment, then broadcasts its
terminal frame through the channel layer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import F

from orchestration.models import Turn

logger = logging.getLogger(__name__)

_RUN_SEQ: Dict[str, int] = {}

TERMINAL_TYPES = frozenset({
    "turn.completed", "turn.failed", "turn.cancelled",
    "turn.superseded", "turn.expired",
})


def set_run_seq(turn_id: Any, seq: int) -> None:
    """Record the highest ``seq`` the running task has used for ``turn_id``."""
    if not turn_id:
        return
    _RUN_SEQ[str(turn_id)] = int(seq)


def get_run_seq(turn_id: Any) -> Optional[int]:
    if not turn_id:
        return None
    return _RUN_SEQ.get(str(turn_id))


def clear_run_seq(turn_id: Any) -> None:
    if turn_id:
        _RUN_SEQ.pop(str(turn_id), None)


def persist_run_seq(turn_id: Any, seq: int) -> bool:
    """Save the run's high-water mark for a later resume.

    A pause writes this after its ``turn.paused`` frame: the loop's pause CAS
    runs before that frame exists, so the frame's number is the only one the
    CAS cannot carry. The write never happens on a plain or tool turn.
    """
    if not turn_id:
        return False
    try:
        return bool(
            Turn.objects.filter(pk=turn_id).update(stream_seq=int(seq))
        )
    except (ValueError, TypeError):
        return False


def reset_run_seqs() -> None:
    """Test helper: drop every in-memory counter."""
    _RUN_SEQ.clear()


def next_out_of_run_seq(turn_id: Any) -> Optional[int]:
    """Take the turn's next ``seq`` with one atomic increment.

    The UPDATE and the read share a transaction, so a concurrent writer either
    waits for it to commit (Postgres row lock) or is serialised by SQLite. The
    row's own ``stream_seq`` is the source of truth, never the process map.
    """
    if not turn_id:
        return None
    with transaction.atomic():
        updated = (
            Turn.objects.filter(pk=turn_id)
            .update(stream_seq=F("stream_seq") + 1)
        )
        if updated != 1:
            return None
        return (
            Turn.objects.filter(pk=turn_id)
            .values_list("stream_seq", flat=True)
            .first()
        )


def build_terminal_frame(
    turn_id: Any, frame_type: str, seq: int, **fields: Any,
) -> Dict[str, Any]:
    frame = {
        "v": 2,
        "type": frame_type,
        "turn_id": str(turn_id),
        "seq": int(seq),
    }
    frame.update(fields)
    return frame


def broadcast_terminal(room_id: Any, frame: Dict[str, Any]) -> bool:
    """Send one already-built v2 terminal frame to the room's sockets.

    Best effort: a writer outside the run has already won its CAS and must
    never be killed by a failed broadcast.
    """
    if room_id is None or not frame:
        return False
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return False
    try:
        async_to_sync(layer.group_send)(
            f"chat_{room_id}",
            {"type": "stream_v2", "frame": frame},
        )
    except Exception as exc:  # noqa: BLE001 - delivery is best effort
        logger.warning(
            "v2 terminal frame not delivered: %s", type(exc).__name__,
        )
        return False
    return True


def emit_terminal(
    turn_id: Any, room_id: Any, frame_type: str, **fields: Any,
) -> Optional[int]:
    """Allocate the next out-of-run ``seq`` and broadcast a terminal frame.

    A turn whose room is gone gets no frame.
    """
    if room_id is None:
        return None
    seq = next_out_of_run_seq(turn_id)
    if seq is None:
        return None
    broadcast_terminal(
        room_id, build_terminal_frame(turn_id, frame_type, seq, **fields),
    )
    return seq
