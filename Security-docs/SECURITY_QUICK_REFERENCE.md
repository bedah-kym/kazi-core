# Security Quick Reference for Developers

## Before Committing Code

- [ ] No hardcoded secrets, API keys, or passwords
- [ ] All user input is validated
- [ ] All database queries use ORM (no raw SQL)
- [ ] CSRF tokens included in all forms
- [ ] Sensitive operations are logged
- [ ] Error messages don't leak system info
- [ ] File uploads use allowed extensions whitelist
- [ ] Authorization checks on all endpoints

## Security Checklist for Common Tasks

### Adding a New API Endpoint

```python
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
import logging

logger = logging.getLogger(__name__)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_secure_endpoint(request):
    """
    Do this:
    1. Check authentication (permission_classes handles this)
    2. Check authorization (user owns the resource)
    3. Validate all inputs
    4. Log sensitive operations
    """
    
    # 1. Get and validate input
    user_id = request.POST.get('user_id', '').strip()
    
    try:
        user_id = int(user_id)
    except ValueError:
        return Response({'error': 'Invalid user_id'}, status=400)
    
    # 2. Check authorization
    if request.user.id != user_id and not request.user.is_staff:
        logger.warning(f"Unauthorized access: user={request.user.id}, target={user_id}")
        return Response({'error': 'Forbidden'}, status=403)
    
    # 3. Process safely
    # Use ORM, not raw SQL
    user = get_object_or_404(User, pk=user_id)
    
    # 4. Return without leaking info
    return Response({'success': True}, status=200)
```

### Storing Sensitive Data

```python
from users.encryption import TokenEncryption

class MyModel(models.Model):
    encrypted_secret = models.TextField()
    
    def set_secret(self, secret_value):
        # Encrypt before storing
        self.encrypted_secret = TokenEncryption.encrypt(secret_value)
        self.save()
    
    def get_secret(self):
        # Decrypt when retrieving
        if not self.encrypted_secret:
            return None
        return TokenEncryption.safe_decrypt(self.encrypted_secret, default=None)
```

### Accepting File Uploads

```python
import os
import uuid
from django.conf import settings

ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

@login_required
def upload_document(request):
    file = request.FILES.get('file')
    
    # 1. Validate existence
    if not file:
        return JsonResponse({'error': 'No file'}, status=400)
    
    # 2. Validate size
    if file.size > MAX_FILE_SIZE:
        return JsonResponse({'error': 'Too large'}, status=413)
    
    # 3. Validate extension
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JsonResponse({'error': 'Type not allowed'}, status=400)
    
    # 4. Use safe random filename
    safe_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(settings.MEDIA_ROOT, 'documents', safe_name)
    
    # 5. Verify path is safe
    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.MEDIA_ROOT)):
        return JsonResponse({'error': 'Invalid path'}, status=400)
    
    # 6. Save safely
    with open(file_path, 'wb') as f:
        for chunk in file.chunks():
            f.write(chunk)
    
    logger.info(f"File uploaded: user={request.user.id}, size={file.size}")
    return JsonResponse({'url': f'/uploads/documents/{safe_name}'})
```

### Validating Email Input

```python
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

email = request.POST.get('email', '').strip().lower()

try:
    validate_email(email)
except ValidationError:
    return JsonResponse({'error': 'Invalid email'}, status=400)
```

### Protecting WebSocket Endpoints

```python
from channels.generic.websocket import AsyncWebsocketConsumer

class MySecureConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # 1. Check authentication
        if not self.scope["user"].is_authenticated:
            await self.close(code=4001)
            return
        
        # 2. Check authorization (e.g., room membership)
        room_id = self.scope["url_route"]["kwargs"]["room_id"]
        user_is_member = await self._check_room_membership(room_id)
        
        if not user_is_member:
            await self.close(code=4003)
            return
        
        # 3. Safe to accept
        await self.accept()
    
    async def _check_room_membership(self, room_id):
        # Check that user is actually in this room
        return await sync_to_async(
            lambda: Chatroom.objects.filter(
                id=room_id,
                participants__User=self.scope["user"]
            ).exists()
        )()
```

### Validating Webhook Signatures

```python
from orchestration.webhook_validator import verify_calendly_signature
from django.conf import settings

@api_view(['POST'])
def my_webhook(request):
    # 1. Verify signature
    signature = request.headers.get('X-Webhook-Signature')
    secret = settings.MY_SERVICE_SECRET
    
    if not verify_calendly_signature(signature, secret, request.body):
        logger.warning("Invalid webhook signature")
        return Response({'error': 'Invalid'}, status=401)
    
    # 2. Process safely
    payload = request.data
    # ... handle webhook
    
    return Response({'ok': True})
```

### Implementing Rate Limiting

```python
from axes.decorators import axes_exempt
from django_ratelimit.decorators import ratelimit

# Use Axes for login rate limiting (already configured)
# For custom endpoints:

@api_view(['POST'])
@ratelimit(key='user', rate='10/h', method='POST')
def expensive_operation(request):
    """Rate limit to 10 requests per hour per user"""
    # ... operation logic
```

### Logging Security Events

```python
import logging

logger = logging.getLogger(__name__)

# Good logging examples:
logger.warning(f"Failed login attempt: user={username}, ip={ip}")
logger.info(f"User deleted: user_id={user_id}, deleted_by={admin_id}")
logger.error(f"Encryption failed: user={user_id}, error={e}")

# BAD - don't do this:
logger.info(f"User password: {password}")  # Never log passwords!
logger.error(f"Full traceback: {full_traceback}")  # Avoid in production
```

## Common Vulnerabilities & Fixes

### SQL Injection
```python
# WRONG - vulnerable to SQL injection
User.objects.raw(f"SELECT * FROM users WHERE email = '{email}'")

# RIGHT - use ORM or parameterized queries
User.objects.filter(email=email)
```

### XSS (Cross-Site Scripting)
```python
# WRONG - unsafe HTML rendering
render(request, 'template.html', {'content': mark_safe(user_input)})

# RIGHT - let Django template engine handle escaping
render(request, 'template.html', {'content': user_input})
# In template: {{ content }}  # Auto-escaped

# For markdown content:
from django.utils.html import escape
import DOMPurify  # Client-side for additional security
safe_html = escape(markdown_content)
```

### CSRF (Cross-Site Request Forgery)
```python
# WRONG - no CSRF protection
return JsonResponse({'data': result})

# RIGHT - all forms include {% csrf_token %}
<form method="POST">
    {% csrf_token %}
    <!-- form fields -->
</form>

# RIGHT - API uses token
headers = {'X-CSRFToken': csrftoken}
```

### Path Traversal
```python
# WRONG - vulnerable
file_path = os.path.join(base_dir, user_input)

# RIGHT - validate path stays in base directory
file_path = os.path.join(base_dir, user_input)
if not os.path.abspath(file_path).startswith(os.path.abspath(base_dir)):
    raise ValueError("Invalid path")
```

### Insecure Deserialization
```python
# WRONG - could deserialize malicious data
import pickle
data = pickle.loads(request.data)

# RIGHT - use JSON
import json
data = json.loads(request.data)
```

### Weak Cryptography
```python
# WRONG - not cryptographically secure
import random
token = ''.join(random.choices('abcdef', k=32))

# RIGHT - use secrets module
import secrets
token = secrets.token_urlsafe(32)

# RIGHT - use django utilities
from django.core.management.utils import get_random_secret_key
key = get_random_secret_key()

# RIGHT - use TokenEncryption for sensitive data
from users.encryption import TokenEncryption
encrypted = TokenEncryption.encrypt(sensitive_data)
```

## Security Tools to Use

### Before committing:
```bash
# Check for security issues in code
bandit -r .

# Check for outdated/vulnerable packages
safety check
pip-audit --desc

# Check for common Django security issues
python manage.py check --deploy

# Type checking
mypy Backend/
```

### In CI/CD:
```bash
# All of above plus:
pytest --cov=Backend/  # Test coverage
black --check Backend/  # Code formatting
flake8 Backend/  # Code quality
```

## Environment Variables Checklist

Before deploying:
- [ ] `DJANGO_SECRET_KEY` - Set to generated unique key
- [ ] `ENCRYPTION_KEY` - Set to generated unique key
- [ ] `DJANGO_DEBUG` - Set to `False`
- [ ] `DJANGO_ALLOWED_HOSTS` - Set to your domain
- [ ] `DJANGO_CSRF_TRUSTED_ORIGINS` - Set to your domain
- [ ] `DATABASE_URL` - Set with strong password
- [ ] `REDIS_URL` - Set with strong password (if using Redis)
- [ ] All third-party API keys loaded from environment

## Quick Commands

```bash
# Generate SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Generate ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Check deployment readiness
python manage.py check --deploy

# Run tests
python manage.py test

# Check dependencies
safety check

# Scan code
bandit -r Backend/
```

## Security Documentation Links

- SECURITY_AUDIT_REPORT.md - Detailed vulnerability findings
- SECURITY_CONFIG_GUIDE.md - Configuration and setup
- SECURITY_IMPLEMENTATION_SUMMARY.md - Summary of all fixes

## When in Doubt

1. **Log it** - If an action affects user data or security, log it
2. **Validate it** - All user input must be validated
3. **Encrypt it** - All sensitive data must be encrypted
4. **Test it** - Write tests for security-critical code
5. **Review it** - Have another developer review security changes
6. **Document it** - Explain security decisions in code comments

---

**Remember:** Security is everyone's responsibility. When in doubt, ask for a review!
