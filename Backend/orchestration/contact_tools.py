"""
Contact tools for the Mathia agentic loop.

Provides lookup and save operations on the Contact model,
executed as internal tools (no external connector needed).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from asgiref.sync import sync_to_async
from django.db.models import Q

from orchestration.tool_schemas import _build_input_schema

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Catalog entries (same shape as ACTION_CATALOG items)               #
# ------------------------------------------------------------------ #

CONTACT_TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "action": "lookup_contact",
        "aliases": [],
        "service": "contacts",
        "description": (
            "Search the user's contacts by name. Use this BEFORE asking "
            "for an email or phone when the user mentions a person by name."
        ),
        "params": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Name (or partial name) to search for",
            },
        },
        "risk_level": "low",
        "confirmation_policy": "never",
        "capability_gate": None,
    },
    {
        "action": "save_contact",
        "aliases": [],
        "service": "contacts",
        "description": (
            "Save a new contact for future use. Deduplicates by email or phone — "
            "if an existing contact has the same email or phone, returns it instead."
        ),
        "params": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Contact's name",
            },
            "email": {
                "type": "string",
                "required": False,
                "description": "Contact's email address",
            },
            "phone": {
                "type": "string",
                "required": False,
                "description": "Contact's phone number",
            },
            "label": {
                "type": "string",
                "required": False,
                "description": "Label like 'colleague', 'client', 'friend'",
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


CONTACT_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    _build_tool_def(entry) for entry in CONTACT_TOOL_CATALOG
]

# ------------------------------------------------------------------ #
#  Async executor functions                                           #
# ------------------------------------------------------------------ #


async def execute_lookup_contact(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    from chatbot.models import Contact

    user_id = context.get("user_id")
    room_id = context.get("room_id")
    name = params.get("name", "").strip()

    if not name:
        return {"status": "error", "message": "Name is required."}

    def _lookup():
        qs = Contact.objects.filter(user_id=user_id, name__icontains=name)
        if room_id:
            qs = qs.filter(Q(room__isnull=True) | Q(room_id=room_id))
        else:
            qs = qs.filter(room__isnull=True)

        results = []
        for c in qs[:5]:
            entry = {"name": c.name}
            if c.email:
                entry["email"] = c.email
            if c.phone:
                entry["phone"] = c.phone
            results.append(entry)

        # Fallback: workspace members if no dedicated contacts found
        if not results:
            from chatbot.models import Chatroom, Member
            try:
                if room_id:
                    room = Chatroom.objects.get(id=room_id)
                    for member in room.participants.all():
                        u = member.User
                        full_name = u.get_full_name() or u.username
                        if name.lower() in full_name.lower():
                            results.append({
                                "name": full_name,
                                "email": u.email or "",
                            })
                            if len(results) >= 5:
                                break
            except Chatroom.DoesNotExist:
                pass

        return results

    results = await sync_to_async(_lookup)()
    if not results:
        return {"status": "success", "message": f"No contacts found matching '{name}'.", "results": []}
    return {"status": "success", "results": results, "count": len(results)}


async def execute_save_contact(
    params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    from chatbot.models import Contact

    user_id = context.get("user_id")
    room_id = context.get("room_id")
    name = params.get("name", "").strip()
    email = params.get("email", "").strip()
    phone = params.get("phone", "").strip()
    label = params.get("label", "").strip()

    if not name:
        return {"status": "error", "message": "Name is required."}

    def _save():
        # Dedup by email or phone
        if email:
            existing = Contact.objects.filter(user_id=user_id, email=email).first()
            if existing:
                return existing, False
        if phone:
            existing = Contact.objects.filter(user_id=user_id, phone=phone).first()
            if existing:
                return existing, False

        contact = Contact.objects.create(
            user_id=user_id,
            room_id=room_id,
            name=name,
            email=email,
            phone=phone,
            label=label,
            source='ai_extracted',
        )
        return contact, True

    contact, created = await sync_to_async(_save)()
    action_word = "saved" if created else "already exists"
    return {
        "status": "success",
        "message": f"Contact '{contact.name}' {action_word}.",
        "contact": {
            "id": contact.id,
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
        },
        "created": created,
    }


# ------------------------------------------------------------------ #
#  Tool dispatch map                                                  #
# ------------------------------------------------------------------ #

_CONTACT_TOOL_MAP: Dict[str, Any] = {
    "lookup_contact": execute_lookup_contact,
    "save_contact": execute_save_contact,
}
