"""
Contact API Views
CRUD + search endpoints for user contacts.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q

from chatbot.models import Contact, RoomContext


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def list_create_contacts(request):
    """
    GET  /api/contacts/?room_id=X  — List user's contacts (global + room-scoped)
    POST /api/contacts/            — Create a contact
    """
    if request.method == 'GET':
        room_id = request.query_params.get('room_id')
        qs = Contact.objects.filter(user=request.user)
        if room_id:
            room_filter = Q(room__isnull=True) | Q(room_id=room_id)
            try:
                ctx = RoomContext.objects.get(chatroom_id=room_id)
                linked_ids = list(ctx.related_rooms.values_list('chatroom_id', flat=True))
                if linked_ids:
                    room_filter = room_filter | Q(room_id__in=linked_ids)
            except RoomContext.DoesNotExist:
                pass
            qs = qs.filter(room_filter)
        else:
            qs = qs.filter(room__isnull=True)

        contacts = [
            {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "label": c.label,
                "source": c.source,
                "room_id": c.room_id,
                "created_at": c.created_at.isoformat(),
            }
            for c in qs[:50]
        ]
        return Response({"contacts": contacts}, status=status.HTTP_200_OK)

    # POST — create
    name = request.data.get('name', '').strip()
    if not name:
        return Response({"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST)

    contact = Contact.objects.create(
        user=request.user,
        room_id=request.data.get('room_id'),
        name=name,
        email=request.data.get('email', '').strip(),
        phone=request.data.get('phone', '').strip(),
        label=request.data.get('label', '').strip(),
        source=request.data.get('source', 'manual'),
    )
    return Response({
        "id": contact.id,
        "name": contact.name,
        "email": contact.email,
        "phone": contact.phone,
        "label": contact.label,
        "source": contact.source,
    }, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def update_delete_contact(request, contact_id):
    """
    PATCH  /api/contacts/<id>/  — Update a contact
    DELETE /api/contacts/<id>/  — Delete a contact (verify ownership)
    """
    try:
        contact = Contact.objects.get(id=contact_id, user=request.user)
    except Contact.DoesNotExist:
        return Response({"error": "Contact not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        contact.delete()
        return Response({"success": True}, status=status.HTTP_200_OK)

    # PATCH
    for field in ('name', 'email', 'phone', 'label'):
        value = request.data.get(field)
        if value is not None:
            setattr(contact, field, value.strip())
    contact.save()
    return Response({
        "id": contact.id,
        "name": contact.name,
        "email": contact.email,
        "phone": contact.phone,
        "label": contact.label,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_contacts(request):
    """
    GET /api/contacts/search/?q=brian&room_id=X
    Autocomplete search (max 10, icontains on name/email/phone)
    """
    q = request.query_params.get('q', '').strip()
    room_id = request.query_params.get('room_id')

    if not q:
        return Response({"contacts": []}, status=status.HTTP_200_OK)

    qs = Contact.objects.filter(user=request.user)
    if room_id:
        room_filter = Q(room__isnull=True) | Q(room_id=room_id)
        try:
            ctx = RoomContext.objects.get(chatroom_id=room_id)
            linked_ids = list(ctx.related_rooms.values_list('chatroom_id', flat=True))
            if linked_ids:
                room_filter = room_filter | Q(room_id__in=linked_ids)
        except RoomContext.DoesNotExist:
            pass
        qs = qs.filter(room_filter)

    qs = qs.filter(
        Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
    )[:10]

    contacts = [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "label": c.label,
            "room_id": c.room_id,
        }
        for c in qs
    ]
    return Response({"contacts": contacts}, status=status.HTTP_200_OK)
