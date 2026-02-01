from .serializers import MathiaReplySerializer
from chatbot.models import Chatroom
from chatbot.serializers import ChatroomSerializer
from rest_framework import generics
from .permissions import IsStaffEditorPermissions
from .models import MathiaReply
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.shortcuts import get_object_or_404
from users.models import CalendlyProfile
from django.contrib.auth import get_user_model
from urllib.parse import quote
import requests
import logging

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def calendly_connect(request):
    """Return an authorization URL to start OAuth with Calendly."""
    client_id = getattr(settings, 'CALENDLY_CLIENT_ID', None)
    
    if not client_id:
        return Response({'error': 'Calendly client id not configured'}, status=500)
    
    # Build the EXACT redirect URI - ensure it matches what's in Calendly settings
    # Use build_absolute_uri to get the full URL
    redirect_uri = request.build_absolute_uri('/api/calendly/callback/')
    
    # Debug: Print the redirect URI to console
    print(f"[calendly] Redirect URI being used: {redirect_uri}")
    
    # Use user ID as state for security
    state = str(request.user.id)
    
    # URL encode the redirect_uri properly
    encoded_redirect_uri = quote(redirect_uri, safe='')
    
    # Build authorization URL
    auth_url = (
        f"https://auth.calendly.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={encoded_redirect_uri}"
        f"&state={state}"
    )
    
    print(f"[calendly] Full auth URL: {auth_url}")
    
    return Response({'authorization_url': auth_url})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendly_callback(request):
    """Handle OAuth callback from Calendly and store tokens."""
    code = request.GET.get('code')
    state = request.GET.get('state')
    if not code:
        return Response({'error': 'code missing'}, status=400)
    token_url = 'https://auth.calendly.com/oauth/token'
    client_id = getattr(settings, 'CALENDLY_CLIENT_ID', None)
    client_secret = getattr(settings, 'CALENDLY_CLIENT_SECRET', None)
    redirect_uri = request.build_absolute_uri('/api/calendly/callback/')
    payload = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'redirect_uri': redirect_uri
    }
    r = requests.post(token_url, data=payload)
    if r.status_code != 200:
        logger.error('Calendly token exchange failed: %s', r.text)
        return Response({'error': 'token exchange failed'}, status=500)
    data = r.json()
    access_token = data.get('access_token')
    refresh_token = data.get('refresh_token')

    # fetch user info
    headers = {'Authorization': f'Bearer {access_token}'}
    userinfo = requests.get('https://api.calendly.com/users/me', headers=headers).json()
    calendly_user_uri = userinfo.get('resource', {}).get('uri') or userinfo.get('uri') or userinfo.get('data', {}).get('uri')

    profile, _ = CalendlyProfile.objects.get_or_create(user=request.user)
    # For free tier assume single event type - try to fetch event_types
    et_resp = requests.get('https://api.calendly.com/event_types', headers=headers, params={'user': calendly_user_uri})
    event_type_uri = None
    event_type_name = None
    booking_link = None
    if et_resp.status_code == 200:
        et_json = et_resp.json()
        items = et_json.get('collection') or et_json.get('data')
        if items:
            first = items[0]
            event_type_uri = first.get('uri') or first.get('resource', {}).get('uri') or first.get('data', {}).get('uri')
            event_type_name = first.get('name') or first.get('resource', {}).get('name')
            booking_link = first.get('scheduling_url') or first.get('resource', {}).get('scheduling_url')

    # Fallback: try to get scheduling_url from user info if not found in event types
    if not booking_link:
        user_resource = userinfo.get('resource') or userinfo.get('data') or userinfo
        booking_link = user_resource.get('scheduling_url')

    profile.connect(access_token, refresh_token, calendly_user_uri, event_type_uri, event_type_name, booking_link)
    return Response({'ok': True, 'connected': True})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendly_status(request):
    profile = getattr(request.user, 'calendly', None)
    if not profile or not profile.is_connected:
        return Response({'isConnected': False})
    return Response({'isConnected': True, 'eventTypeName': profile.event_type_name, 'bookingLink': profile.booking_link, 'calendlyUserUri': profile.calendly_user_uri})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendly_events(request):
    profile = getattr(request.user, 'calendly', None)
    if not profile or not profile.is_connected:
        return Response({'events': []})
    access_token = profile.get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    r = requests.get('https://api.calendly.com/scheduled_events', headers=headers, params={'user': profile.calendly_user_uri})
    if r.status_code != 200:
        return Response({'events': []})
    data = r.json()
    items = data.get('collection') or data.get('data') or []
    events = []
    for item in items:
        start = item.get('start_time') or item.get('resource', {}).get('start_time')
        end = item.get('end_time') or item.get('resource', {}).get('end_time')
        duration = item.get('duration') or item.get('resource', {}).get('duration')
        name = item.get('name') or item.get('event_type') or 'Meeting'
        invitee = None
        invitee_obj = item.get('invitees', [])
        if invitee_obj:
            inv = invitee_obj[0]
            invitee = inv.get('name') or inv.get('email')
        events.append({'title': name, 'start': start, 'end': end, 'duration': duration, 'invitee': invitee, 'raw': item})
    return Response({'events': events})


@api_view(['POST'])
def calendly_webhook(request):
    """
    Handle Calendly webhook events securely.
    
    Security features:
    - Signature verification to prevent spoofed webhooks
    - Event type validation
    - Proper error handling and logging
    """
    from orchestration.webhook_validator import verify_calendly_signature, log_webhook_verification
    import json
    
    try:
        # Verify webhook signature for security
        signature = request.headers.get('X-Calendly-Signature')
        secret = getattr(settings, 'CALENDLY_WEBHOOK_SIGNING_KEY', None) or getattr(settings, 'CALENDLY_CLIENT_SECRET', None)
        
        if not secret:
            logger.error("Calendly webhook signing key not configured")
            return Response({'error': 'Webhook not configured'}, status=500)
        if not getattr(settings, 'CALENDLY_WEBHOOK_SIGNING_KEY', None):
            logger.warning("CALENDLY_WEBHOOK_SIGNING_KEY not set; falling back to CALENDLY_CLIENT_SECRET for webhook verification")
        
        # Get raw request body for signature verification
        raw_body = request.body if isinstance(request.body, bytes) else request.body.encode()
        
        # Verify signature
        if not verify_calendly_signature(signature, secret, raw_body):
            logger.warning(f"Invalid Calendly webhook signature from {request.META.get('REMOTE_ADDR')}")
            log_webhook_verification('calendly', False)
            return Response({'error': 'Invalid signature'}, status=401)
        
        log_webhook_verification('calendly', True)
        
        # Parse event
        payload = request.data
        if not isinstance(payload, dict):
            return Response({'ok': True})
        
        event = payload.get('event')
        if not event:
            logger.debug("Calendly webhook received with no event data")
            return Response({'ok': True})
        
        event_type = event.get('type')

        from workflows.webhook_handlers import handle_calendly_webhook_event
        handle_calendly_webhook_event(payload)
        
        # Handle different event types
        if event_type == 'invitee.created':
            logger.info(f'Calendly invitee.created received: {event.get("resource", {}).get("uri")}')
            # Process invitee.created event
            # find which user this belongs to by payload['config']['webhook_subscription']['owner']
            
        elif event_type == 'invitee.canceled':
            logger.info(f'Calendly invitee.canceled received: {event.get("resource", {}).get("uri")}')
            # Process cancellation
        
        else:
            logger.debug(f"Unhandled Calendly event type: {event_type}")
        
        return Response({'ok': True})
        
    except json.JSONDecodeError:
        logger.error("Invalid JSON in Calendly webhook")
        return Response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error processing Calendly webhook: {str(e)}")
        return Response({'error': 'Processing error'}, status=500)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendly_user_booking_link(request, user_id):
    """
    Retrieve a user's Calendly booking link.
    
    Security: Only authenticated users can view their own booking link,
    or staff members can view any user's link for management purposes.
    """
    User = get_user_model()
    user = get_object_or_404(User, pk=user_id)
    
    # Authorization check: Only allow users to view their own link or staff
    if request.user.id != user.id and not request.user.is_staff:
        logger.warning(f"Unauthorized calendly booking link access: user={request.user.id}, target={user.id}")
        return Response({'error': 'Forbidden'}, status=403)
    
    profile = getattr(user, 'calendly', None)
    if not profile or not profile.is_connected:
        return Response({'bookingLink': None})

    return Response({'bookingLink': profile.booking_link})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def calendly_user_booking_link_by_username(request, username):
    User = get_user_model()
    user = get_object_or_404(User, username=username)
    profile = getattr(user, 'calendly', None)
    if not profile or not profile.is_connected:
        return Response({'bookingLink': None, 'isConnected': False})
    return Response({'bookingLink': profile.booking_link, 'isConnected': True, 'eventTypeName': profile.event_type_name})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def calendly_disconnect(request):
    profile = getattr(request.user, 'calendly', None)
    if not profile:
        return Response({'ok': True})
    profile.disconnect()
    return Response({'ok': True})


class CreateReply(generics.ListCreateAPIView):
    queryset = MathiaReply.objects.all()
    serializer_class = MathiaReplySerializer
    #permission_classes =[IsStaffEditorPermissions]

class GetMessage(generics.RetrieveAPIView):
    serializer_class = ChatroomSerializer
    lookup_field="room"
    permission_classes =[IsAuthenticated]

    def get_queryset(self):
        room_id = self.kwargs['room']
        # Security Fix: Only allow access if user is participant
        return Chatroom.objects.filter(id=room_id, participants__User=self.request.user)

    def get_object(self):
        # This implementation is a bit non-standard for RetrieveAPIView which expects one object.
        # But keeping logic mostly as is, just safer.
        # get_queryset returns a queryset of rooms (should be 0 or 1)
        qs = self.get_queryset()
        room = get_object_or_404(qs) # This validates room exists and user is member
        obj = room.chats.last()
        return obj

class GetAllMessages(generics.ListAPIView):
    serializer_class = ChatroomSerializer
    permission_classes =[IsAuthenticated]

    def get_queryset(self):
        room_id = self.kwargs['room']
        # Security Fix: Only allow access if user is participant
        room = get_object_or_404(Chatroom, id=room_id, participants__User=self.request.user)
        qs = room.chats.all()
        return qs
    
