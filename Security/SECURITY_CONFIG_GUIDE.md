# Security Configuration Guide for MATHIA

This guide explains the security fixes applied and how to properly configure the application.

## Environment Variables Required

Create a `.env` file in the project root with these critical variables:

```bash
# === CRITICAL SECURITY VARIABLES ===

# Django Secret Key (REQUIRED IN PRODUCTION)
# Generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
DJANGO_SECRET_KEY=your-super-secret-key-here

# Encryption Key for sensitive data (API tokens, OAuth tokens, etc.)
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=your-encryption-key-here

# Database Configuration
DATABASE_URL=postgresql://user:password@localhost:5432/mathia
POSTGRES_PASSWORD=strong-password-here

# Redis Configuration
REDIS_URL=redis://localhost:6379/0
# For Upstash (managed Redis): REDIS_URL=rediss://user:password@domain.upstash.io:port

# === CORS & CSRF SECURITY ===

# Allowed hosts (comma-separated)
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,yourdomain.com,www.yourdomain.com

# CSRF Trusted Origins (REQUIRED IN PRODUCTION)
# These are the origins allowed to make cross-origin requests
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Enable debug mode (MUST BE FALSE in production!)
DJANGO_DEBUG=False

# === EXTERNAL SERVICE CREDENTIALS ===

# Calendly Integration
CALENDLY_CLIENT_ID=your-calendly-client-id
CALENDLY_CLIENT_SECRET=your-calendly-client-secret

# OAuth Social Providers
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret

LINKEDIN_CLIENT_ID=your-linkedin-client-id
LINKEDIN_CLIENT_SECRET=your-linkedin-client-secret

TWITTER_CLIENT_ID=your-twitter-client-id
TWITTER_CLIENT_SECRET=your-twitter-client-secret

# AI/LLM Services
HF_API_TOKEN=your-huggingface-token

# Third-party APIs
OPENWEATHER_API_KEY=your-openweather-api-key
GIPHY_API_KEY=your-giphy-api-key
EXCHANGE_RATE_API_KEY=your-exchange-rate-api-key

# WhatsApp Integration
WHATSAPP_API_KEY=your-whatsapp-api-key
WHATSAPP_PHONE_NUMBER=your-phone-number

# Email Service (Mailgun)
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_DOMAIN=your-mailgun-domain

# IntaSend (Payment)
INTASEND_PUBLIC_KEY=your-intasend-public-key
INTASEND_API_KEY=your-intasend-api-key
```

## Security Improvements Applied

### 1. **SECRET_KEY Management** ✅
**Issue:** Hardcoded default SECRET_KEY in settings
**Fix:** 
- Now requires `DJANGO_SECRET_KEY` environment variable in production
- Auto-generates unique key in development with warning
- Raises error if missing in production

**Action Required:**
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# Copy output to DJANGO_SECRET_KEY in .env
```

### 2. **Token Encryption** ✅
**Issue:** Weak encryption derived from SECRET_KEY
**Fix:**
- Created `users/encryption.py` module with proper Fernet encryption
- Uses environment-based encryption key (`ENCRYPTION_KEY`)
- Updated `CalendlyProfile` to use secure `TokenEncryption` utility
- Added `safe_decrypt()` for graceful error handling

**Action Required:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Copy output to ENCRYPTION_KEY in .env
```

**Usage in Code:**
```python
from users.encryption import TokenEncryption

# Encrypt
encrypted = TokenEncryption.encrypt(secret_token)

# Decrypt
original = TokenEncryption.decrypt(encrypted)

# Safe decrypt with fallback
original = TokenEncryption.safe_decrypt(encrypted, default=None)
```

### 3. **File Upload Security** ✅
**Issue:** File path traversal vulnerability (no filename validation)
**Fix:**
- Whitelist file extensions check
- File size validation (50MB max, configurable)
- Random UUID filename generation (prevents enumeration)
- Path validation to prevent traversal
- Organized uploads into `/documents` subdirectory
- Added comprehensive error logging

**File:** `Backend/chatbot/views.py::upload_file()`

### 4. **Input Validation** ✅
**Issue:** Missing email and room_id validation in `invite_user()`
**Fix:**
- Email format validation using Django validators
- Room ID integer validation
- Prevent self-invitations
- Proper error messages without leaking system info
- Security logging

**File:** `Backend/chatbot/views.py::invite_user()`

### 5. **Authorization Checks** ✅
**Issue:** Calendar endpoint allows viewing any user's booking link
**Fix:**
- Added authorization check: only self or staff can view
- Returns 403 Forbidden for unauthorized access
- Logs unauthorized access attempts

**File:** `Backend/Api/views.py::calendly_user_booking_link()`

### 6. **Webhook Validation** ✅
**Issue:** Calendly webhook signature not validated
**Fix:**
- Created `orchestration/webhook_validator.py` module
- Implemented HMAC-SHA256 signature verification for Calendly
- Added constant-time comparison to prevent timing attacks
- Comprehensive logging and error handling

**Usage:**
```python
from orchestration.webhook_validator import verify_calendly_signature

signature = request.headers.get('X-Calendly-Signature')
if not verify_calendly_signature(signature, secret, request.body):
    return Response({'error': 'Invalid signature'}, status=401)
```

### 7. **CORS & CSRF Configuration** ✅
**Issue:** Overly permissive CORS, weak CSRF settings
**Fix:**
- Added validation for `DJANGO_CSRF_TRUSTED_ORIGINS`
- Requires explicit configuration in production
- Added `CSRF_COOKIE_SECURE`, `CSRF_COOKIE_HTTPONLY`, `CSRF_COOKIE_SAMESITE`
- Proper error if not configured in production

**Configuration in .env:**
```bash
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

### 8. **Session Security** ✅
**Issue:** No session timeout configured
**Fix:**
- Set `SESSION_COOKIE_AGE = 3600` (1 hour)
- Enabled `SESSION_EXPIRE_AT_BROWSER_CLOSE`
- Set `SESSION_COOKIE_HTTPONLY = True`
- Set `SESSION_COOKIE_SAMESITE = 'Strict'`

### 9. **Password Policy** ✅
**Issue:** Weak password validation
**Fix:**
- Increased minimum length to 12 characters (from 8)
- Added user attribute similarity check
- Enhanced password validator configuration

### 10. **Brute Force Protection** ✅
**Issue:** Basic Axes configuration
**Fix:**
- Increased lockout time to 2 hours
- Added user agent tracking
- Enabled reset on successful login
- Enabled verbose logging
- Proper timedelta import

**Configuration:**
```python
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(hours=2)
AXES_LOCK_OUT_BY_COMBINATION_USER_AND_IP = True
AXES_USE_USER_AGENT = True
AXES_RESET_ON_SUCCESS = True
AXES_VERBOSE = True
```

---

## Files Modified

1. **Backend/Backend/settings.py**
   - Fixed hardcoded SECRET_KEY
   - Enhanced CORS/CSRF configuration
   - Added session security settings
   - Improved password validators
   - Enhanced AXES configuration

2. **Backend/users/models.py**
   - Updated CalendlyProfile to use TokenEncryption
   - Removed weak custom encryption

3. **Backend/users/encryption.py** (NEW)
   - Created secure encryption utility
   - Proper Fernet-based encryption
   - Environment-based key management

4. **Backend/chatbot/views.py**
   - Fixed file upload vulnerability
   - Enhanced input validation in invite_user()
   - Added security logging

5. **Backend/Api/views.py**
   - Fixed calendar endpoint authorization
   - Added webhook signature validation
   - Improved error handling

6. **Backend/orchestration/webhook_validator.py** (NEW)
   - Webhook signature verification utilities
   - Support for Calendly, WhatsApp, generic HMAC
   - Security event logging

---

## Deployment Checklist

- [ ] Set all required environment variables in `.env`
- [ ] Generate new `DJANGO_SECRET_KEY`
- [ ] Generate new `ENCRYPTION_KEY`
- [ ] Set `DJANGO_DEBUG=False` in production
- [ ] Configure `DJANGO_ALLOWED_HOSTS` with actual domain
- [ ] Configure `DJANGO_CSRF_TRUSTED_ORIGINS` with actual domain
- [ ] Set strong database password
- [ ] Set strong Redis password (if applicable)
- [ ] Enable HTTPS/SSL certificates
- [ ] Run `python manage.py check --deploy`
- [ ] Run migrations: `python manage.py migrate`
- [ ] Collect static files: `python manage.py collectstatic --noinput`
- [ ] Run security tests: `python manage.py test`
- [ ] Set up log rotation for security logs
- [ ] Configure WAF or rate limiting at reverse proxy level
- [ ] Enable HSTS preload in production
- [ ] Set up regular security scanning

---

## Testing Security Changes

### Test File Upload Validation
```bash
curl -X POST http://localhost:8000/uploads/ \
  -F "file=@test.exe"  # Should fail - exe not allowed
  
curl -X POST http://localhost:8000/uploads/ \
  -F "file=@document.pdf"  # Should succeed
```

### Test Authorization
```bash
# Try to access another user's booking link
curl http://localhost:8000/api/calendly/booking/999/

# Should get 403 Forbidden
```

### Test CSRF Protection
```bash
# POST without CSRF token should fail
curl -X POST http://localhost:8000/accounts/invite_user/
```

### Test Rate Limiting (Axes)
```bash
# Make 6 failed login attempts
# 6th should be locked out
```

---

## Monitoring & Logging

All security events are logged. Monitor:

- Failed login attempts (Axes logs)
- Unauthorized access attempts (custom logging)
- File upload violations
- Webhook verification failures
- Encryption/decryption errors

Example log entry:
```
WARNING - Unauthorized calendly booking link access: user=5, target=10
WARNING - Invalid Calendly webhook signature from 192.168.1.1
INFO - File uploaded successfully: user=3, size=1024000, type=.pdf
```

---

## Regular Security Maintenance

1. **Monthly:**
   - Run `python manage.py check --deploy`
   - Review security logs
   - Update dependencies

2. **Quarterly:**
   - Security code review
   - Rotate encryption keys (if policy requires)
   - Penetration testing

3. **Annually:**
   - Full security audit
   - Update OWASP Top 10 compliance check

---

## Additional Recommendations

1. **Add django-cors-headers:**
   ```bash
   pip install django-cors-headers
   ```

2. **Add rate limiting package:**
   ```bash
   pip install djangorestframework-throttling
   ```

3. **Enable Content Security Policy:**
   Already configured via `CSP_*` settings in settings.py

4. **Use a WAF:**
   - AWS WAF
   - Cloudflare
   - Imperva

5. **Security Headers:**
   All configured in settings.py:
   - HSTS
   - X-Frame-Options
   - X-Content-Type-Options
   - Content-Security-Policy

---

For questions or issues, refer to the main SECURITY_AUDIT_REPORT.md file.
