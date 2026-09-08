"""
Model Selector API Views
Per-chatroom LLM model preference (stored in Redis under ``model_pref:{room_id}``).
"""
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chatbot.models import Chatroom
from orchestration.model_catalog import (
    available_models,
    model_id,
    model_pref_key,
    parse_model_id,
)


def _serialize_models(models):
    return [
        {
            "id": model_id(info),
            "provider": info.provider,
            "model": info.model,
            "label": info.label,
            "tier": info.tier,
            "tool_calling": info.tool_calling,
        }
        for info in models
    ]


def _room_access(request, room_id):
    return Chatroom.objects.filter(id=room_id, participants__User=request.user).exists()


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def room_model(request, room_id):
    """
    GET/POST /api/rooms/<room_id>/model/

    GET returns the models whose provider key is configured, plus the room's
    current selection (``provider/model`` or null for Auto).

    POST body: {"model": "provider/model" | ""}. Sets the room override, or
    clears it back to Auto when the value is empty.
    """
    get_object_or_404(Chatroom, id=room_id)

    if not _room_access(request, room_id):
        return Response(
            {"error": "You don't have access to this room"},
            status=status.HTTP_403_FORBIDDEN,
        )

    models = available_models()

    if request.method == 'POST':
        raw = str(request.data.get('model', '') or '').strip()
        if raw:
            parsed = parse_model_id(raw)
            valid = parsed is not None and any(
                info.provider == parsed[0] and info.model == parsed[1]
                for info in models
            )
            if not valid:
                return Response(
                    {"error": "Invalid or unavailable model id"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cache.set(model_pref_key(room_id), raw, timeout=None)
        else:
            cache.delete(model_pref_key(room_id))

    selected = cache.get(model_pref_key(room_id))
    return Response({
        "models": _serialize_models(models),
        "selected": selected or None,
    }, status=status.HTTP_200_OK)
