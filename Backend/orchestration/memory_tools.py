"""
Memory lifecycle tools for the Mathia agentic loop.

Provides create/complete/update/archive/search operations on RoomNote,
executed as internal tools (no external connector needed).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from asgiref.sync import sync_to_async
from django.utils import timezone

from orchestration.tool_schemas import _build_input_schema

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Catalog entries (same shape as ACTION_CATALOG items)               #
# ------------------------------------------------------------------ #

MEMORY_TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "action": "create_note",
        "aliases": [],
        "service": "memory",
        "description": (
            "Create a memory note to record a decision, action item, insight, "
            "reference, or reminder for future recall."
        ),
        "params": {
            "content": {
                "type": "string",
                "required": True,
                "description": "The text content of the note",
            },
            "note_type": {
                "type": "string",
                "required": True,
                "description": "Type of note",
                "enum": [
                    "decision",
                    "action_item",
                    "insight",
                    "reference",
                    "reminder",
                ],
            },
            "priority": {
                "type": "string",
                "required": False,
                "description": "Priority level (default medium)",
                "enum": ["low", "medium", "high"],
            },
            "tags": {
                "type": "string",
                "required": False,
                "description": "Comma-separated tags for searchability",
            },
            "is_private": {
                "type": "boolean",
                "required": False,
                "description": "If true, note stays room-only and is not shared across linked rooms",
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
    {
        "action": "complete_note",
        "aliases": [],
        "service": "memory",
        "description": "Mark a memory note as completed.",
        "params": {
            "note_id": {
                "type": "integer",
                "required": True,
                "description": "The ID of the note to complete",
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
    {
        "action": "update_note",
        "aliases": [],
        "service": "memory",
        "description": "Update the content or priority of a memory note.",
        "params": {
            "note_id": {
                "type": "integer",
                "required": True,
                "description": "The ID of the note to update",
            },
            "content": {
                "type": "string",
                "required": False,
                "description": "New content for the note",
            },
            "priority": {
                "type": "string",
                "required": False,
                "description": "New priority level",
                "enum": ["low", "medium", "high"],
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
    {
        "action": "archive_note",
        "aliases": [],
        "service": "memory",
        "description": (
            "Archive a note that is no longer relevant. "
            "Its knowledge is preserved in long-term episodic memory."
        ),
        "params": {
            "note_id": {
                "type": "integer",
                "required": True,
                "description": "The ID of the note to archive",
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Optional reason for archiving",
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
    {
        "action": "search_notes",
        "aliases": [],
        "service": "memory",
        "description": (
            "Search your memory notes by keyword. Use when the user asks "
            "about past decisions, bookings, or things you discussed before."
        ),
        "params": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Keyword to search for in notes",
            },
            "note_type": {
                "type": "string",
                "required": False,
                "description": "Filter by note type",
                "enum": [
                    "decision",
                    "action_item",
                    "insight",
                    "reference",
                    "reminder",
                ],
            },
            "include_archived": {
                "type": "boolean",
                "required": False,
                "description": "Include archived notes in results (default false)",
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
]

# ------------------------------------------------------------------ #
#  Pre-built Claude tool_use definitions                              #
# ------------------------------------------------------------------ #


def _build_tool_def(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": entry["action"],
        "description": entry.get("description") or entry["action"].replace("_", " ").title(),
        "input_schema": _build_input_schema(entry),
    }


MEMORY_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    _build_tool_def(entry) for entry in MEMORY_TOOL_CATALOG
]

# ------------------------------------------------------------------ #
#  Executor helpers                                                   #
# ------------------------------------------------------------------ #


def _get_note_or_error(note_id: int, room_id: int):
    """Fetch a RoomNote, verifying it belongs to the given room."""
    from chatbot.models import RoomNote

    try:
        note = RoomNote.objects.select_related("room_context").get(id=note_id)
    except RoomNote.DoesNotExist:
        return None, {"status": "error", "message": f"Note #{note_id} not found."}

    if note.room_context.chatroom_id != room_id:
        return None, {"status": "error", "message": "Access denied: note belongs to another room."}

    return note, None


def _compress_note_to_episode(note, reason: str = "") -> Dict[str, Any]:
    """Convert a note into an episodic memory entry before archiving."""
    summary = f"[{note.get_note_type_display()}] {note.content}"
    if reason:
        summary += f" (archived: {reason})"
    return {
        "summary": summary[:300],
        "date": note.created_at.strftime("%Y-%m-%d"),
        "importance": note.priority,
        "updated_at": timezone.now().isoformat(),
    }


# ------------------------------------------------------------------ #
#  Async executor functions                                           #
# ------------------------------------------------------------------ #


async def execute_create_note(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    from chatbot.models import RoomContext, RoomNote

    room_id = context.get("room_id")
    content = params.get("content", "").strip()
    note_type = params.get("note_type", "insight")
    priority = params.get("priority", "medium")
    tags_raw = params.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    is_private = params.get("is_private", False)

    if not content:
        return {"status": "error", "message": "Content is required."}

    def _create():
        ctx, _ = RoomContext.objects.get_or_create(chatroom_id=room_id)
        return RoomNote.objects.create(
            room_context=ctx,
            note_type=note_type,
            content=content,
            priority=priority,
            tags=tags,
            is_ai_generated=True,
            is_private=is_private,
        )

    note = await sync_to_async(_create)()
    return {
        "status": "success",
        "message": f"Note #{note.id} created.",
        "note_id": note.id,
    }


async def execute_complete_note(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    room_id = context.get("room_id")
    note_id = params.get("note_id")

    def _complete():
        note, err = _get_note_or_error(note_id, room_id)
        if err:
            return err
        note.is_completed = True
        note.completed_at = timezone.now()
        note.last_accessed_at = timezone.now()
        note.save(update_fields=["is_completed", "completed_at", "last_accessed_at", "updated_at"])
        return {"status": "success", "message": f"Note #{note_id} marked as completed."}

    return await sync_to_async(_complete)()


async def execute_update_note(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    room_id = context.get("room_id")
    note_id = params.get("note_id")
    new_content = params.get("content")
    new_priority = params.get("priority")

    if not new_content and not new_priority:
        return {"status": "error", "message": "Provide content or priority to update."}

    def _update():
        note, err = _get_note_or_error(note_id, room_id)
        if err:
            return err
        fields = ["last_accessed_at", "updated_at"]
        note.last_accessed_at = timezone.now()
        if new_content:
            note.content = new_content
            fields.append("content")
        if new_priority and new_priority in ("low", "medium", "high"):
            note.priority = new_priority
            fields.append("priority")
        note.save(update_fields=fields)
        return {"status": "success", "message": f"Note #{note_id} updated."}

    return await sync_to_async(_update)()


async def execute_archive_note(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    room_id = context.get("room_id")
    note_id = params.get("note_id")
    reason = params.get("reason", "")

    def _archive():
        note, err = _get_note_or_error(note_id, room_id)
        if err:
            return err

        # Compress into episodic memory
        episode = _compress_note_to_episode(note, reason)
        ctx = note.room_context
        episodes = list(ctx.memory_episodes or [])
        episodes.append(episode)
        # Cap at 50
        if len(episodes) > 50:
            episodes = episodes[-50:]
        ctx.memory_episodes = episodes
        ctx.memory_updated_at = timezone.now()
        ctx.save(update_fields=["memory_episodes", "memory_updated_at"])

        note.is_archived = True
        note.last_accessed_at = timezone.now()
        note.save(update_fields=["is_archived", "last_accessed_at", "updated_at"])
        return {"status": "success", "message": f"Note #{note_id} archived. Knowledge preserved in episodic memory."}

    return await sync_to_async(_archive)()


async def execute_search_notes(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    from chatbot.models import RoomNote

    room_id = context.get("room_id")
    query = params.get("query", "").strip()
    note_type = params.get("note_type")
    include_archived = params.get("include_archived", False)

    if not query:
        return {"status": "error", "message": "Query is required."}

    def _search():
        qs = RoomNote.objects.filter(
            room_context__chatroom_id=room_id,
            content__icontains=query,
        )
        if not include_archived:
            qs = qs.filter(is_archived=False)
        if note_type:
            qs = qs.filter(note_type=note_type)
        qs = qs.order_by("-created_at")[:10]

        results = []
        for n in qs:
            status = "active"
            if n.is_archived:
                status = "archived"
            elif n.is_completed:
                status = "completed"
            results.append({
                "id": n.id,
                "type": n.note_type,
                "content": n.content,
                "priority": n.priority,
                "status": status,
                "created_at": n.created_at.isoformat(),
            })
        return results

    results = await sync_to_async(_search)()
    if not results:
        return {"status": "success", "message": f"No notes found matching '{query}'.", "results": []}
    return {"status": "success", "results": results, "count": len(results)}


# ------------------------------------------------------------------ #
#  Tool dispatch map                                                  #
# ------------------------------------------------------------------ #

_MEMORY_TOOL_MAP: Dict[str, Any] = {
    "create_note": execute_create_note,
    "complete_note": execute_complete_note,
    "update_note": execute_update_note,
    "archive_note": execute_archive_note,
    "search_notes": execute_search_notes,
}
