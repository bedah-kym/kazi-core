# Security Audit Report - MATHIA PROJECT
**Date:** December 21, 2025  
**Scope:** Django ASGI Backend, WebSocket Consumers, REST API  
**Severity Levels:** 🔴 Critical | 🟠 High | 🟡 Medium | 🔵 Low

---

## Executive Summary

This Django web application has **moderate security issues** across multiple OWASP Top 10 categories. The app handles real-time messaging, file uploads, OAuth integrations, and sensitive user data (Calendly tokens, API keys). Key findings include:

- **1 Critical** - Hardcoded default SECRET_KEY
- **5 High** - CORS misconfiguration, weak password validation, missing rate limiting
- **8 Medium** - Input validation gaps, insufficient authorization checks, weak token encryption
- **6 Low** - Incomplete security headers, logging issues, error message leakage

---

## OWASP TOP 10 & Django-Specific Vulnerabilities

### 🔴 1. A01:2021 – Broken Access Control

#### Finding 1.1: Missing Authorization in WebSocket Consumers
**File:** `Backend/chatbot/consumers.py` (Line ~35)  
**Issue:** WebSocket connection only checks `is_authenticated` but doesn't verify room membership before accepting connection.
```python
async def connect(self):
    if not self.scope["user"].is_authenticated:
        await self.close(code=4001)
        return
    # Missing: verify user is a member of this specific room!
```

**Risk:** Authenticated users could potentially join any room by manipulating the room_name parameter.

**Fix:**
```python
async def connect(self):
    if not self.scope["user"].is_authenticated:
        await self.close(code=4001)
        return
    
    self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
    
    # CRITICAL: Verify room membership BEFORE accepting
    current_chat = await self.get_current_chatroom(self.room_name)
    if not current_chat:
        await self.close(code=4003)
        return
    
    # Check if user is actually a member
    is_member = await self._user_is_room_member(current_chat)
    if not is_member:
        await self.close(code=4003)  # Unauthorized
        return
    
    # ... rest of connection logic
```

---

#### Finding 1.2: Insufficient Authorization in Calendar Endpoints
**File:** `Backend/Api/views.py` (Line 139-150)  
**Issue:** `calendly_user_booking_link` endpoint doesn't verify if requesting user has permission to view another user's booking link.

```python
def calendly_user_booking_link(request, user_id):
    User = get_user_model()
    user = get_object_or_404(User, pk=user_id)
    profile = getattr(user, 'calendly', None)
    # Missing: verify request.user == user or request.user is admin
```

**Fix:**
```python
def calendly_user_booking_link(request, user_id):
    User = get_user_model()
    user = get_object_or_404(User, pk=user_id)
    
    # Only allow users to view their own booking link
    if request.user.id != user.id and not request.user.is_staff:
        return Response({'error': 'Forbidden'}, status=403)
    
    profile = getattr(user, 'calendly', None)
    if not profile or not profile.is_connected:
        return Response({'bookingLink': None})
    return Response({'bookingLink': profile.booking_link})
```

---

#### Finding 1.3: Room Access Control Bypass in `home()` View
**File:** `Backend/chatbot/views.py` (Line 65)  
**Issue:** Uses `get_object_or_404` with two filters which is good, but room_name is assumed to be integer ID - no validation.

**Fix:** Ensure room_name is properly validated as integer:
```python
@login_required
def home(request, room_name):
    try:
        room_id = int(room_name)
    except (ValueError, TypeError):
        raise Http404("Invalid room ID")
    
    room = get_object_or_404(
        Chatroom, 
        id=room_id, 
        participants__User=request.user
    )
```

---

### 🔴 2. A02:2021 – Cryptographic Failures

#### Finding 2.1: Hardcoded DEFAULT SECRET_KEY
**File:** `Backend/Backend/settings.py` (Line 45)  
**Issue:** CRITICAL - Default Django secret key is used in production if `DJANGO_SECRET_KEY` env var is missing.

```python
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'set-DJANGO_SECRET_KEY-env-var')
```

**Impact:** All session tokens, CSRF tokens, and password reset tokens are vulnerable to cryptographic compromise.

**Fix:**
```python
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    raise ValueError(
        "DJANGO_SECRET_KEY environment variable is not set. "
        "This is required for production security. "
        "Generate one with: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
    )
```

---

#### Finding 2.2: Weak Token Encryption for Calendly/API Keys
**File:** `Backend/users/models.py` (Line 28-34)  
**Issue:** Fernet key is derived deterministically from `SECRET_KEY` - if SECRET_KEY is compromised, all encrypted tokens are compromised.

```python
def _fernet(self):
    secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
    hash = hashlib.sha256(secret).digest()
    fernet_key = base64.urlsafe_b64encode(hash)
    return Fernet(fernet_key)
```

**Problems:**
- Key is derived the same way every time (not random)
- No key rotation mechanism
- If secret_key is exposed, all encrypted data is exposed
- Fallback to 'changeme' is unacceptable

**Fix:** Use Django's `cryptography` with proper key management:
```python
from django.core.management.utils import get_random_secret_key
from cryptography.fernet import Fernet

def get_encryption_key():
    """Get or create encryption key from environment."""
    key = os.environ.get('ENCRYPTION_KEY')
    if not key:
        # In production, this must be set via environment
        if not settings.DEBUG:
            raise ValueError("ENCRYPTION_KEY environment variable required in production")
        # In dev, generate and warn
        key = Fernet.generate_key().decode()
        print(f"WARNING: Generated dev encryption key: {key}")
    return key.encode() if isinstance(key, str) else key

def _fernet(self):
    key = get_encryption_key()
    return Fernet(key)
```

---

#### Finding 2.3: Missing HTTPS Enforcement
**File:** `Backend/Backend/settings.py` (Line 408-416)  
**Issue:** HTTPS redirect is conditional on `DEBUG=False` only, but HSTS headers should be more robust.

**Fix:** Improve HTTPS enforcement:
```python
# Enhanced HTTPS configuration
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
    # Additional headers
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
else:
    # Dev mode - still secure but allow local testing
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
```

---

### 🔴 3. A03:2021 – Injection

#### Finding 3.1: Potential File Path Traversal in File Upload
**File:** `Backend/chatbot/views.py` (Line 87)  
**Issue:** `upload_file()` uses user-supplied `uploaded_file.name` directly without sanitization.

```python
def upload_file(request):
    uploaded_file = request.FILES.get('file')
    file_path = os.path.join(settings.MEDIA_ROOT, uploaded_file.name)  # DANGEROUS!
    with open(file_path, 'wb+') as destination:
```

**Risk:** Attacker could upload file as `../../etc/passwd` or other paths.

**Fix:**
```python
import os
import uuid
from django.core.files.storage import default_storage

@login_required
def upload_file(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)
    
    try:
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return JsonResponse({'error': 'No file provided'}, status=400)
        
        # Validate file size
        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
        if uploaded_file.size > MAX_FILE_SIZE:
            return JsonResponse({'error': 'File too large'}, status=400)
        
        # Validate file type
        ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.txt', '.jpg', '.png', '.gif'}
        ext = os.path.splitext(uploaded_file.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return JsonResponse({'error': 'File type not allowed'}, status=400)
        
        # Generate safe filename
        safe_name = f"{uuid.uuid4()}{ext}"
        file_path = os.path.join(settings.MEDIA_ROOT, 'documents', safe_name)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Save file
        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        file_url = os.path.join(settings.MEDIA_URL, 'documents', safe_name)
        return JsonResponse({'fileUrl': file_url}, status=200)
        
    except Exception as e:
        logger.error(f"File upload error: {e}")
        return JsonResponse({'error': 'File upload failed'}, status=500)
```

---

#### Finding 3.2: Missing SQL Injection Protection in Raw Queries
**File:** No raw SQL found in initial scan, but potential in connectors  
**Issue:** While Django ORM provides protection, verify no raw SQL queries are used.

**Recommendation:** Run Django check:
```bash
python manage.py check --deploy
```

---

### 🔴 4. A05:2021 – Broken Access Control (CORS/CSRF)

#### Finding 4.1: Overly Permissive CORS Configuration
**File:** `Backend/Backend/settings.py` (Line 52)  
**Issue:** `CSRF_TRUSTED_ORIGINS` can include wildcard or overly broad patterns.

```python
CSRF_TRUSTED_ORIGINS = [url for url in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', 'http://localhost:8000').split(',') if url]
```

**Risk:** If env variable is misconfigured, frontend could bypass CSRF protection.

**Fix:** Add explicit validation and use django-cors-headers:
```bash
pip install django-cors-headers
```

Update settings:
```python
# settings.py
INSTALLED_APPS = [
    # ...
    'corsheaders',
    # ...
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # Must be before CommonMiddleware
    'django.middleware.security.SecurityMiddleware',
    # ...
]

# CORS Configuration
CORS_ALLOWED_ORIGINS = [
    url.strip() for url in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',')
    if url.strip()
]

# In production, CORS_ALLOWED_ORIGINS MUST be explicitly set, not empty
if not CORS_ALLOWED_ORIGINS and not DEBUG:
    raise ValueError("CORS_ALLOWED_ORIGINS must be set in production")

CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# Ensure credentials are not sent
CORS_ALLOW_CREDENTIALS = False
```

---

#### Finding 4.2: Missing CSRF Token Validation in Some Forms
**File:** `Backend/users/templates/users/settings.html` (Line 360+)  
**Issue:** Form modals for integrations may not have proper CSRF token inclusion.

**Fix:** Ensure ALL forms include:
```html
<form method="POST" action="...">
    {% csrf_token %}
    <!-- form fields -->
</form>
```

---

### 🟠 5. A07:2021 – Cross-Site Scripting (XSS)

#### Finding 5.1: Potential XSS in Template Mark_Safe Usage
**File:** `Backend/chatbot/views.py` (Line 71-73)  
**Issue:** Using `mark_safe()` with user-supplied JSON could be risky.

```python
return render(
    request,"chatbot/chatbase.html",
    {
        "room_name":mark_safe(json.dumps(room_name)),
        "username":mark_safe(json.dumps(request.user.username)),
```

**Risk:** If room_name or username is compromised, XSS is possible.

**Fix:** Only mark_safe if content is validated:
```python
import json
from django.utils.html import escape

context = {
    "room_name": json.dumps(str(room_id)),  # No mark_safe needed for JSON
    "username": json.dumps(request.user.username),
    "chatrooms": chatrooms_data,
    "room_member": current_room_name,
}
```

---

#### Finding 5.2: DOMPurify Usage Correct but Verify Configuration
**File:** `Backend/chatbot/static/js/main.js` (Line 324+)  
**Status:** ✅ GOOD - DOMPurify is being used correctly.

```javascript
const safeHtml = DOMPurify.sanitize(parsedHtml, {
    ADD_TAGS: ['img', 'code', 'pre'],
    ADD_ATTR: ['src', 'alt', 'class', 'style', 'target']
});
```

**Recommendation:** Restrict attributes more strictly:
```javascript
const safeHtml = DOMPurify.sanitize(parsedHtml, {
    ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'a', 'p', 'br', 'ul', 'ol', 'li', 'code', 'pre', 'img'],
    ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'target', 'rel'],
    KEEP_CONTENT: true,
    RETURN_DOM: false,
    FORCE_BODY: false,
    SANITIZE_DOM: true,
});
```

---

### 🟠 6. A02:2021 – Identification and Authentication Failures

#### Finding 6.1: Weak Password Policy
**File:** `Backend/Backend/settings.py` (Line 283)  
**Issue:** Password validators are basic, no custom strength requirements.

```python
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},  # Default is 8
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
```

**Fix:** Enhance password policy:
```python
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
        'OPTIONS': {'user_attributes': ['email', 'username', 'first_name', 'last_name']}
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 12}  # Increase from default 8
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Add custom validator for complexity
PASSWORD_COMPLEXITY = {
    "UPPER": 1,  # Minimum uppercase
    "LOWER": 1,  # Minimum lowercase
    "DIGITS": 1,
    "SPECIAL": 1,  # Minimum special characters !@#$%^&*
}
```

---

#### Finding 6.2: Missing Rate Limiting on Login Endpoint
**File:** `Backend/Backend/settings.py` (Line 390+)  
**Issue:** Axes is installed for brute force protection, but verify it's properly configured.

```python
# --- AXES (Brute Force Protection) ---
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # Hours (should be longer!)
AXES_LOCK_OUT_BY_COMBINATION_USER_AND_IP = True
```

**Fix:** Strengthen Axes configuration:
```python
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(hours=2)  # 2 hours lockout
AXES_LOCK_OUT_BY_COMBINATION_USER_AND_IP = True
AXES_USE_USER_AGENT = True
AXES_LOCKOUT_TEMPLATE = 'axes/lockout.html'
AXES_RESET_ON_SUCCESS = True
AXES_VERBOSE = True  # Log all attempts
```

---

#### Finding 6.3: Session Timeout Not Configured
**File:** `Backend/Backend/settings.py`  
**Issue:** No `SESSION_COOKIE_AGE` configuration means default 2-week timeout.

**Fix:** Add session security configuration:
```python
# Session Security
SESSION_COOKIE_AGE = 3600  # 1 hour (or user preference)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SECURE = True if not DEBUG else False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
```

---

### 🟠 7. A06:2021 – Vulnerable and Outdated Components

#### Finding 7.1: Dependency Audit Required
**File:** `requirements.txt` (should be checked)  
**Issue:** No visibility into package versions or vulnerability checks.

**Fix:** Run security check:
```bash
pip install safety
safety check --json > safety-report.json
```

Also add to CI/CD:
```bash
pip install pip-audit
pip-audit --desc
```

---

### 🟡 8. A08:2021 – Software and Data Integrity Failures

#### Finding 8.1: Missing Webhook Signature Validation
**File:** `Backend/Api/views.py` (Line 148)  
**Issue:** Calendly webhook doesn't validate signature.

```python
@api_view(['POST'])
def calendly_webhook(request):
    payload = request.data
    event = payload.get('event')
    # Minimal handling - in prod validate signature
    if not event:
        return Response({'ok': True})
```

**Fix:**
```python
import hmac
import hashlib

@api_view(['POST'])
def calendly_webhook(request):
    # Verify webhook signature
    signature = request.headers.get('X-Calendly-Signature')
    if not signature:
        return Response({'error': 'Missing signature'}, status=401)
    
    secret = settings.CALENDLY_CLIENT_SECRET
    body = request.body
    expected_signature = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_signature):
        logger.warning("Invalid Calendly webhook signature")
        return Response({'error': 'Invalid signature'}, status=401)
    
    payload = request.data
    # ... process webhook
    return Response({'ok': True})
```

---

### 🟡 9. A09:2021 – Logging and Monitoring

#### Finding 9.1: Missing Security Logging
**File:** `Backend/chatbot/consumers.py` and `Backend/Api/views.py`  
**Issue:** Limited logging of security events.

**Fix:** Add comprehensive logging:
```python
import logging

logger = logging.getLogger(__name__)

# Log all authentication attempts
logger.warning(f"Unauthorized room access attempt: user={user_id}, room={room_id}")

# Log API errors
logger.error(f"File upload validation failed: user={user_id}, size={file_size}, ext={ext}")

# Log sensitive operations
logger.info(f"Integration disconnected: user={user_id}, type={integration_type}")
```

Add to settings:
```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/mathia/security.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 10,
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django.security': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'chatbot': {
            'handlers': ['file'],
            'level': 'WARNING',
        },
    },
}
```

---

#### Finding 9.2: Error Messages Leak Information
**File:** `Backend/Api/views.py` and other views  
**Issue:** Detailed error messages may expose system information to attackers.

**Fix:** Use generic error messages in production:
```python
try:
    # ... code
except Exception as e:
    logger.error(f"Detailed error for debugging: {e}")  # Log detailed error
    return JsonResponse({'error': 'An error occurred'}, status=500)  # Generic response
```

---

### 🔵 10. Additional Django Security Issues

#### Finding 10.1: No Click-Jacking Protection Verification
**File:** `Backend/Backend/settings.py` (Line 101)  
**Status:** ✅ Good - `django.middleware.clickjacking.XFrameOptionsMiddleware` is present.

```python
X_FRAME_OPTIONS = 'SAMEORIGIN'  # Should be DENY for sensitive pages
```

**Fix:**
```python
X_FRAME_OPTIONS = 'DENY'  # Prevent framing entirely
```

---

#### Finding 10.2: Missing Admin Security Hardening
**File:** `Backend/Backend/urls.py` (Line 17)  
**Issue:** Admin interface at default `/admin/` path.

**Fix:** Change admin path:
```python
from django.contrib import admin

# Change admin URL to non-standard path
admin.site.site_header = "Mathia Administration"
admin.site.site_title = "Mathia Admin"

urlpatterns = [
    path('admin-panel-secret-path/', admin.site.urls),  # Hide admin from easy discovery
    # ... rest of urls
]
```

Also add admin IP whitelisting in middleware.

---

#### Finding 10.3: No Input Validation on Email Invites
**File:** `Backend/chatbot/views.py` (Line 185)  
**Issue:** Email validation in `invite_user()` relies only on form validation.

**Fix:**
```python
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

@login_required
def invite_user(request):
    if request.method == 'POST':
        room_id = request.POST.get('room_id')
        email = request.POST.get('email', '').strip().lower()
        
        # Validate inputs
        if not room_id or not email:
            return JsonResponse({'status': 'error', 'message': 'Missing room_id or email'}, status=400)
        
        try:
            int(room_id)
        except ValueError:
            return JsonResponse({'status': 'error', 'message': 'Invalid room_id'}, status=400)
        
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'status': 'error', 'message': 'Invalid email format'}, status=400)
        
        # Prevent inviting yourself
        if email == request.user.email:
            return JsonResponse({'status': 'info', 'message': 'You cannot invite yourself'}, status=400)
        
        # ... rest of logic
```

---

## Summary Table

| # | Category | Severity | Location | Fix Status |
|---|----------|----------|----------|------------|
| 1 | Hardcoded SECRET_KEY | 🔴 Critical | settings.py:45 | ⬜ TODO |
| 2 | Weak Token Encryption | 🔴 Critical | models.py:28 | ⬜ TODO |
| 3 | WebSocket Room Access | 🔴 Critical | consumers.py:35 | ⬜ TODO |
| 4 | File Path Traversal | 🔴 Critical | views.py:87 | ⬜ TODO |
| 5 | Missing Rate Limiting | 🟠 High | settings.py:390 | ⬜ TODO |
| 6 | CORS Misconfiguration | 🟠 High | settings.py:52 | ⬜ TODO |
| 7 | Weak Password Policy | 🟠 High | settings.py:283 | ⬜ TODO |
| 8 | Missing Webhook Validation | 🟠 High | views.py:148 | ⬜ TODO |
| 9 | Calendar Endpoint AuthZ | 🟡 Medium | views.py:139 | ⬜ TODO |
| 10 | Email Invite Validation | 🟡 Medium | views.py:185 | ⬜ TODO |

---

## Implementation Priority

### Phase 1 (Immediate - 24 hours)
- [ ] Fix hardcoded SECRET_KEY
- [ ] Implement proper token encryption
- [ ] Fix WebSocket room authorization
- [ ] Fix file upload path traversal

### Phase 2 (1 week)
- [ ] Strengthen password policy
- [ ] Fix CORS configuration
- [ ] Implement webhook signature validation
- [ ] Add input validation to all user endpoints

### Phase 3 (2-4 weeks)
- [ ] Implement comprehensive security logging
- [ ] Add security headers (CSP enhancements)
- [ ] Implement rate limiting on all endpoints
- [ ] Add admin panel security hardening
- [ ] Security testing and penetration testing

---

## Additional Recommendations

1. **Add a WAF (Web Application Firewall):**
   - Consider AWS WAF, Cloudflare, or ModSecurity

2. **Implement API Rate Limiting:**
   ```bash
   pip install django-ratelimit djangorestframework-throttling
   ```

3. **Enable Security Headers Middleware:**
   ```bash
   pip install django-csp
   ```

4. **Use a Secrets Manager:**
   - AWS Secrets Manager
   - HashiCorp Vault
   - Azure Key Vault

5. **Regular Security Audits:**
   - Run `python manage.py check --deploy` monthly
   - Use `safety check` for dependency vulnerabilities
   - Conduct code reviews focusing on security

6. **Incident Response Plan:**
   - Document procedures for data breach
   - Have backup encryption keys
   - Regular security team drills

---

## References

- OWASP Top 10 2021: https://owasp.org/Top10/
- Django Security: https://docs.djangoproject.com/en/stable/topics/security/
- NIST Cybersecurity Framework: https://www.nist.gov/cyberframework
- CWE Top 25: https://cwe.mitre.org/top25/

