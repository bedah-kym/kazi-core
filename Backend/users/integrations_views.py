
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from .models import UserIntegration
from cryptography.fernet import Fernet
from django.conf import settings
import base64
import hashlib
import json

def get_fernet():
    secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
    hash = hashlib.sha256(secret).digest()
    fernet_key = base64.urlsafe_b64encode(hash)
    return Fernet(fernet_key)

def encrypt_data(data_dict):
    f = get_fernet()
    json_str = json.dumps(data_dict)
    return f.encrypt(json_str.encode('utf-8')).decode('utf-8')

def decrypt_data(encrypted_str):
    if not encrypted_str:
        return {}
    try:
        f = get_fernet()
        json_str = f.decrypt(encrypted_str.encode('utf-8')).decode('utf-8')
        return json.loads(json_str)
    except Exception:
        return {}

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
