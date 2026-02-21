
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from .models import UserIntegration
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from users.encryption import TokenEncryption
import httpx
import json
import secrets
import time
from urllib.parse import urlencode

def get_legacy_fernet():
    from cryptography.fernet import Fernet
    import base64
    import hashlib

    secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
    digest = hashlib.sha256(secret).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_data(data_dict):
    json_str = json.dumps(data_dict)
    return TokenEncryption.encrypt(json_str)


def decrypt_data(encrypted_str):
    if not encrypted_str:
        return {}

    # Preferred: ENCRYPTION_KEY-backed decryption
    try:
        json_str = TokenEncryption.decrypt(encrypted_str)
        return json.loads(json_str)
    except Exception:
        pass

    # Legacy fallback for existing records
    try:
        f = get_legacy_fernet()
        json_str = f.decrypt(encrypted_str.encode('utf-8')).decode('utf-8')
        return json.loads(json_str)
    except Exception:
        return {}


def _get_gmail_redirect_uri(request):
    return settings.GMAIL_OAUTH_REDIRECT_URI or request.build_absolute_uri(reverse('users:gmail_callback'))


@login_required
@require_http_methods(["GET"])
def connect_gmail(request):
    client_id = settings.GMAIL_OAUTH_CLIENT_ID
    client_secret = settings.GMAIL_OAUTH_CLIENT_SECRET

    if not client_id or not client_secret:
        messages.error(request, "Gmail OAuth is not configured. Contact support.")
        return redirect('users:settings')

    state = secrets.token_urlsafe(16)
    request.session['gmail_oauth_state'] = state

    params = {
        "client_id": client_id,
        "redirect_uri": _get_gmail_redirect_uri(request),
        "response_type": "code",
        "scope": settings.GMAIL_SEND_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return redirect(auth_url)


@login_required
@require_http_methods(["GET"])
def gmail_callback(request):
    error = request.GET.get('error')
    if error:
        messages.error(request, f"Gmail connection failed: {error}")
        return redirect('users:settings')

    state = request.GET.get('state')
    expected_state = request.session.pop('gmail_oauth_state', None)
    if not state or state != expected_state:
        messages.error(request, "Invalid Gmail OAuth state. Please try again.")
        return redirect('users:settings')

    code = request.GET.get('code')
    if not code:
        messages.error(request, "Missing authorization code from Gmail.")
        return redirect('users:settings')

    token_payload = {
        "code": code,
        "client_id": settings.GMAIL_OAUTH_CLIENT_ID,
        "client_secret": settings.GMAIL_OAUTH_CLIENT_SECRET,
        "redirect_uri": _get_gmail_redirect_uri(request),
        "grant_type": "authorization_code",
    }

    try:
        response = httpx.post("https://oauth2.googleapis.com/token", data=token_payload, timeout=20)
    except Exception as exc:
        messages.error(request, f"Gmail token exchange failed: {exc}")
        return redirect('users:settings')

    if response.status_code != 200:
        messages.error(request, f"Gmail token exchange failed: {response.text}")
        return redirect('users:settings')

    data = response.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")

    if not access_token:
        messages.error(request, "Gmail connection failed: missing access token.")
        return redirect('users:settings')

    integration, _ = UserIntegration.objects.get_or_create(
        user=request.user,
        integration_type='gmail'
    )

    existing = decrypt_data(integration.encrypted_credentials)
    if not refresh_token:
        refresh_token = existing.get("refresh_token")

    gmail_address = None
    try:
        profile_resp = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if profile_resp.status_code == 200:
            gmail_address = (profile_resp.json() or {}).get("emailAddress")
    except Exception:
        gmail_address = None

    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in) if expires_in else None,
        "scope": data.get("scope") or settings.GMAIL_SEND_SCOPE,
        "token_type": data.get("token_type"),
    }
    if gmail_address:
        credentials["gmail_address"] = gmail_address

    integration.encrypted_credentials = encrypt_data(credentials)
    integration.is_connected = True
    integration.connected_at = timezone.now()
    metadata = integration.metadata or {}
    metadata["scope"] = credentials["scope"]
    if gmail_address:
        metadata["gmail_address"] = gmail_address
    integration.metadata = metadata
    integration.save(update_fields=["encrypted_credentials", "is_connected", "connected_at", "metadata", "updated_at"])

    messages.success(request, "Gmail connected successfully!")
    return redirect('users:settings')


@login_required
@require_http_methods(["POST"])
def disconnect_gmail(request):
    integration = UserIntegration.objects.filter(
        user=request.user,
        integration_type='gmail'
    ).first()

    if not integration:
        messages.error(request, "Gmail integration not found.")
        return redirect('users:settings')

    credentials = decrypt_data(integration.encrypted_credentials)
    token = credentials.get("refresh_token") or credentials.get("access_token")
    if token:
        try:
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=10)
        except Exception:
            pass

    integration.is_connected = False
    integration.encrypted_credentials = None
    integration.metadata = {}
    integration.connected_at = None
    integration.save(update_fields=["encrypted_credentials", "is_connected", "metadata", "connected_at", "updated_at"])

    messages.success(request, "Gmail disconnected.")
    return redirect('users:settings')

@login_required
@require_http_methods(["POST"])
def connect_whatsapp(request):
    """Save WhatsApp credentials"""
    try:
        phone_number = request.POST.get('phone_number')
        account_sid = request.POST.get('account_sid')
        auth_token = request.POST.get('auth_token')
        
        if not all([phone_number, account_sid, auth_token]):
            messages.error(request, "All fields are required")
            return redirect('users:settings')
            
        credentials = {
            'account_sid': account_sid,
            'auth_token': auth_token,
            'phone_number': phone_number
        }
        
        # Verify credentials first
        from orchestration.connectors.whatsapp_connector import WhatsAppConnector
        connector = WhatsAppConnector()
        
        # 1. Validate Credentials
        is_valid, error_msg = connector.validate_credentials(account_sid, auth_token)
        if not is_valid:
             messages.error(request, f"Invalid Credentials: {error_msg}")
             return redirect('users:settings')
             
        # 2. Send Test Message (Optional but good for UX)
        # Note: 'from_number' typically needs to be the Sandbox number or Sender ID.
        # For this prototype we might assume the env var 'from_number' or ask user for it.
        # Using the env var one for now or falling back.
        # result = connector.send_test_message(phone_number, account_sid, auth_token, connector.from_number)
        
        integration, created = UserIntegration.objects.get_or_create(
            user=request.user,
            integration_type='whatsapp'
        )
        integration.encrypted_credentials = encrypt_data(credentials)
        integration.is_connected = True
        integration.save()
        
        messages.success(request, "WhatsApp connected and verified successfully!")
        
    except Exception as e:
        messages.error(request, f"Failed to connect WhatsApp: {str(e)}")
        
    return redirect('users:settings')

@login_required
@require_http_methods(["POST"])
def connect_mailgun(request):
    """Save Mailgun credentials"""
    try:
        api_key = request.POST.get('api_key')
        domain = request.POST.get('domain')
        
        if not all([api_key, domain]):
            messages.error(request, "API Key and Domain are required")
            return redirect('users:settings')
            
        credentials = {
            'api_key': api_key,
            'domain': domain
        }
        
        integration, created = UserIntegration.objects.get_or_create(
            user=request.user,
            integration_type='mailgun'
        )
        integration.encrypted_credentials = encrypt_data(credentials)
        integration.is_connected = True
        integration.save()
        
        messages.success(request, "Mailgun connected successfully!")
        
    except Exception as e:
        messages.error(request, f"Failed to connect Mailgun: {str(e)}")
        
    return redirect('users:settings')

@login_required
@require_http_methods(["POST"])
def connect_intasend(request):
    """Save IntaSend credentials"""
    try:
        public_key = request.POST.get('public_key')
        api_key = request.POST.get('api_key')
        is_test = request.POST.get('is_test') == 'on'
        
        if not all([public_key, api_key]):
            messages.error(request, "Keys are required")
            return redirect('users:settings')
            
        credentials = {
            'public_key': public_key,
            'api_key': api_key,
            'is_test': is_test
        }
        
        integration, created = UserIntegration.objects.get_or_create(
            user=request.user,
            integration_type='intasend'
        )
        integration.encrypted_credentials = encrypt_data(credentials)
        integration.is_connected = True
        integration.save()
        
        messages.success(request, "IntaSend connected successfully!")
        
    except Exception as e:
        messages.error(request, f"Failed to connect IntaSend: {str(e)}")
        
    return redirect('users:settings')

@login_required
@require_http_methods(["POST"])
def disconnect_integration(request, integration_type):
    """Disconnect an integration"""
    try:
        integration = UserIntegration.objects.get(
            user=request.user,
            integration_type=integration_type
        )
        integration.is_connected = False
        integration.encrypted_credentials = None
        integration.save()
        messages.success(request, f"{integration_type.title()} disconnected.")
    except UserIntegration.DoesNotExist:
        messages.error(request, "Integration not found.")
        
    return redirect('users:settings')
