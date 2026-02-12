
import os
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from users.models import User, Workspace, UserIntegration
from users.integrations_views import encrypt_data, decrypt_data
from django.test import RequestFactory
from users.integrations_views import connect_whatsapp

def verify():
    print("Starting verification...")
    
    # Create test user
    username = "test_integration_user"
    if User.objects.filter(username=username).exists():
        User.objects.filter(username=username).delete()
        
    user = User.objects.create_user(username=username, password="password")
    workspace = Workspace.objects.create(user=user, name="Test Workspace")
    
    print(f"Created user: {user.username}")
    
    # Test encryption helper
    data = {"foo": "bar"}
    enc = encrypt_data(data)
    dec = decrypt_data(enc)
    assert dec == data, "Encryption/Decryption failed"
    print("Encryption helper: OK")
    
    # Test creating WhatsApp integration manually (backend logic)
    # We can't easily test the view directly without mocking messages/redirects, 
    # but we can test the model logic used in the view.
    
    creds = {
        'account_sid': 'AC123',
        'auth_token': 'secret',
        'phone_number': '+254700000000'
    }
    
    integration = UserIntegration.objects.create(
        user=user,
        integration_type='whatsapp',
        encrypted_credentials=encrypt_data(creds),
        is_connected=True
    )
    print("Created WhatsApp integration object")
    
    fetched = UserIntegration.objects.get(user=user, integration_type='whatsapp')
    decrypted_creds = decrypt_data(fetched.encrypted_credentials)
    
    assert fetched.is_connected == True
    assert decrypted_creds['account_sid'] == 'AC123'
    print("Verified WhatsApp integration storage: OK")
    
    # Cleanup
    user.delete()
    print("Verification complete. Cleanup done.")

if __name__ == "__main__":
    verify()
