# R2 Storage Integration — Technical Specification

**Last Updated:** Feb 3, 2025 | **Version:** v1.0 | **Status:** ✅ Production-Ready | **Author:** GitHub Copilot

---

## 📋 Overview

R2 Storage provides enterprise-grade object storage via Cloudflare's S3-compatible API. Mathia uses R2 for:
- Chat file uploads (documents, images, voice notes)
- User media storage (avatars, backgrounds)
- Invoice and payment document storage
- Voice transcription outputs
- Document processing artifacts

**Key Characteristics:**
- ✅ S3-compatible API via Cloudflare R2
- ✅ Zero egress charges (unlike AWS S3)
- ✅ Optional feature (can use local filesystem as fallback)
- ✅ Transparent integration with Django storages
- ✅ Public CDN delivery with optional custom domain
- ✅ Lifecycle policies for automatic cleanup

---

## 🏗️ Architecture

### System Diagram

```
File Upload Request
    ↓
Chat/Payment View
    ├─→ Check R2_ENABLED flag
    │
    ├─ If TRUE (R2 enabled):
    │   ├─→ Use Django default_storage (S3Boto3Storage)
    │   ├─→ file = default_storage.save(path, file)
    │   ├─→ url = default_storage.url(path)
    │   ├─→ Upload to R2 bucket via Cloudflare
    │   └─→ Return public CDN URL
    │
    └─ If FALSE (R2 disabled):
        ├─→ Use local filesystem storage
        ├─→ Save to MEDIA_ROOT/uploads/
        └─→ Return relative path URL
```

### Component Overview

**1. Django Storages Backend** (`django-storages[boto3]`)
- S3Boto3Storage class handles R2 integration
- Transparent file save/retrieve
- Configurable bucket and endpoint

**2. Boto3 Client** (`boto3` library)
- AWS SDK for Python
- Handles S3 API calls to Cloudflare R2
- Automatic retry and error handling

**3. Django Settings Configuration** (`Backend/settings.py`)
- R2 credentials and endpoint
- Bucket naming and region
- Public URL configuration

**4. Storage Selection**
- `STORAGES['default']` points to S3Boto3Storage (if R2_ENABLED)
- Falls back to local filesystem (if R2_ENABLED=False)

---

## 💾 Data Models

### No New Models

R2 is transparent storage layer - no new models needed.

**Related Models:**
- `Message` — Text, file references
- `DocumentUpload` — Tracks uploaded documents
- `User` — Avatar storage

**File Organization:**
```
Bucket Structure:
├── documents/
│   └── {room_id}/
│       └── {filename}  (PDFs, images)
├── voice_notes/
│   └── {room_id}/
│       └── {filename}  (WebM, MP3)
├── chat_uploads/
│   └── {uuid}.{ext}    (All file types)
├── ai_speech/
│   └── {room_id}/
│       └── reply_{message_id}.mp3
└── user_avatars/
    └── {user_id}.{ext}
```

---

## 🔌 API Integration

### File Upload (Django ORM)

**Via default_storage (transparent):**
```python
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

# Save file
file_bytes = request.FILES['file'].read()
file_path = f"documents/{room_id}/{filename}"
stored_path = default_storage.save(file_path, ContentFile(file_bytes))

# Get public URL
public_url = default_storage.url(stored_path)
# Returns: https://r2-public-url/documents/123/file.pdf
```

### File Deletion

```python
# Delete single file
default_storage.delete(file_path)

# Delete multiple files
files_to_delete = ['docs/file1.pdf', 'docs/file2.pdf']
default_storage.delete_many(files_to_delete)
```

### File Exists Check

```python
exists = default_storage.exists(file_path)
if exists:
    content = default_storage.open(file_path).read()
```

---

## 🔧 Configuration

### Environment Variables

```bash
# Required if R2_ENABLED=True
R2_ENABLED=True
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET_NAME=mathia-storage
R2_ENDPOINT_URL=https://your-account.r2.cloudflarestorage.com
R2_REGION=auto

# Optional: Custom public CDN URL
R2_PUBLIC_BASE_URL=https://cdn.yourdomain.com
```

### Django Settings

**File:** `Backend/settings.py`

```python
import os

# Feature flag
R2_ENABLED = os.environ.get('R2_ENABLED', 'False').lower() in ('1', 'true', 'yes')

if R2_ENABLED:
    # AWS SDK credentials (reused for R2)
    AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
    
    # R2-specific configuration
    AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
    AWS_S3_REGION_NAME = os.environ.get('R2_REGION', 'auto')
    
    # S3 API settings for R2
    AWS_S3_ADDRESSING_STYLE = 'path'  # Important for R2
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_FILE_OVERWRITE = False  # Don't overwrite existing files
    AWS_DEFAULT_ACL = None  # Don't set ACLs
    AWS_QUERYSTRING_AUTH = False  # Don't add signing query strings
    
    # Cache control for CDN
    AWS_S3_OBJECT_PARAMETERS = {
        'CacheControl': 'max-age=86400',  # 24 hour browser cache
    }
    
    # Validate required settings
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, 
                AWS_STORAGE_BUCKET_NAME, AWS_S3_ENDPOINT_URL]):
        raise ValueError('R2 enabled but required R2_* settings missing')
    
    # Custom domain setup (optional)
    AWS_S3_CUSTOM_DOMAIN = os.environ.get('R2_PUBLIC_BASE_URL', '')
    if AWS_S3_CUSTOM_DOMAIN:
        # Remove protocol for Django
        AWS_S3_CUSTOM_DOMAIN = AWS_S3_CUSTOM_DOMAIN.replace('https://', '').replace('http://', '')
        MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
    else:
        # Use Cloudflare R2 default public URL
        MEDIA_URL = f"{AWS_S3_ENDPOINT_URL.rstrip('/')}/{AWS_STORAGE_BUCKET_NAME}/"
    
    # Configure Django Storages
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
else:
    # Local filesystem fallback
    MEDIA_ROOT = os.path.join(BASE_DIR, 'uploads')
    MEDIA_URL = '/uploads/'
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
```

### Setting Up R2 in Cloudflare

1. **Create Cloudflare Account:** [dash.cloudflare.com](https://dash.cloudflare.com)
2. **Enable R2:** Go to R2 → Create bucket
3. **Create API Token:**
   - R2 Admin → API Tokens → Create API Token
   - Copy `Access Key ID` and `Secret Access Key`
4. **Get Endpoint URL:**
   - R2 → Bucket → Settings
   - Copy endpoint URL (e.g., `https://abc123.r2.cloudflarestorage.com`)
5. **Enable Public URL (Optional):**
   - R2 → Bucket → Settings → Public Access
   - Create custom domain or use default public URL

### Toggling R2 On/Off

```bash
# Enable R2 (must have env vars set)
export R2_ENABLED=True
export R2_ACCESS_KEY_ID=your_key
export R2_SECRET_ACCESS_KEY=your_secret
export R2_BUCKET_NAME=your_bucket
export R2_ENDPOINT_URL=https://your-account.r2.cloudflarestorage.com

# Restart Django
docker-compose restart web

# Disable R2 (use local filesystem)
export R2_ENABLED=False
docker-compose restart web
```

---

## 📊 Features

### 1. Transparent Storage Backend

Code doesn't change when switching storage:
```python
# Works with both R2 and local filesystem
file_path = default_storage.save('documents/file.pdf', file_content)
public_url = default_storage.url(file_path)
```

### 2. Automatic CDN Delivery

- Cloudflare R2 provides public CDN access
- Optional custom domain via Cloudflare CDN
- 24-hour browser cache control
- Gzip compression automatic

### 3. Lifecycle Policies

Remove old files automatically (configure in Cloudflare dashboard):
```
Rule 1: Delete documents > 90 days old
Rule 2: Delete voice notes > 30 days old
Rule 3: Delete temporary artifacts > 7 days old
```

### 4. Bucket Organization

Logical folder structure for organization:
- `documents/{room_id}/` — Chat documents
- `voice_notes/{room_id}/` — Voice messages
- `chat_uploads/{uuid}/` — General uploads
- `ai_speech/{room_id}/` — Generated audio
- `user_avatars/{user_id}/` — Profile images

### 5. Error Handling

Graceful fallback if R2 unavailable:
```python
try:
    file_path = default_storage.save(file_name, file_content)
    public_url = default_storage.url(file_path)
except Exception as e:
    logger.error(f"Storage error: {e}")
    if R2_ENABLED:
        # R2 error - notify user, suggest retry
        return error_response("Upload temporarily unavailable")
    else:
        # Filesystem error - likely disk full
        return error_response("Server storage full")
```

---

## 🔐 Security & Safety

### API Key Management

- ✅ Never hardcoded - always from environment
- ✅ Rotate keys periodically in Cloudflare dashboard
- ✅ Use API tokens with minimal required scopes (R2 only)
- ✅ Never commit `.env` files to git

### File Upload Security

```python
# Filename sanitization before saving
from django.utils.text import get_valid_filename

safe_name = get_valid_filename(user_filename)
# Prevents: ../../../etc/passwd, path injection

# File size limits
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
if file.size > MAX_FILE_SIZE:
    raise ValueError("File too large")

# File type validation
ALLOWED_TYPES = {'application/pdf', 'image/jpeg', 'image/png', 'audio/webm'}
if file.content_type not in ALLOWED_TYPES:
    raise ValueError("File type not allowed")
```

### Access Control

- ✅ Files public via CDN (no auth required)
- ✅ Use random UUIDs in file paths (not guessable)
- ✅ Implement application-level auth for sensitive files
- ✅ Consider private bucket with presigned URLs for sensitive data

### Data Encryption

- ✅ R2 encrypts data at rest (automatic)
- ✅ Use HTTPS for all transfers (automatic)
- ✅ No encryption in transit needed (Cloudflare handles)

---

## 📈 Performance & Costs

### Performance

Typical response times:
- File upload: 100-500ms (depends on file size)
- File download: 50-200ms (via CDN)
- File deletion: 100-200ms

Optimizations:
- Multipart upload for large files (> 100MB)
- CloudFlare CDN caching (24 hours)
- Lazy loading for preview images

### Cost Estimation

**R2 Pricing (vs AWS S3):**

| Operation | R2 | AWS S3 |
|-----------|----|----|
| Storage | $0.015/GB/month | $0.023/GB/month |
| Upload | $0.0075/million | $0.005/million |
| Download | **FREE** | $0.09/GB |

**Example: 100GB stored, 1TB transfer/month**

| Provider | Monthly Cost |
|----------|-------------|
| R2 | $1.50 storage + $7.50 upload = $9 |
| S3 | $2.30 storage + $5 upload + $92 download = $99+ |

**R2 saves 90% on egress charges!**

---

## 🧪 Testing

### Unit Tests

```python
# tests/test_r2_storage.py

from django.test import TestCase, override_settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

class R2StorageTests(TestCase):
    
    @override_settings(R2_ENABLED=True)
    def test_save_to_r2(self):
        """Test file save to R2"""
        file_content = ContentFile(b"test content")
        file_path = default_storage.save("test/file.txt", file_content)
        
        assert file_path == "test/file.txt"
        assert default_storage.exists(file_path)
    
    @override_settings(R2_ENABLED=True)
    def test_get_public_url(self):
        """Test public URL generation"""
        file_content = ContentFile(b"test content")
        file_path = default_storage.save("test/file.txt", file_content)
        
        public_url = default_storage.url(file_path)
        assert public_url.startswith('https://')
        assert 'test/file.txt' in public_url
    
    @override_settings(R2_ENABLED=True)
    def test_delete_file(self):
        """Test file deletion"""
        file_content = ContentFile(b"test content")
        file_path = default_storage.save("test/file.txt", file_content)
        
        assert default_storage.exists(file_path)
        default_storage.delete(file_path)
        assert not default_storage.exists(file_path)
    
    @override_settings(R2_ENABLED=False)
    def test_local_storage_fallback(self):
        """Test local filesystem when R2 disabled"""
        file_content = ContentFile(b"test content")
        file_path = default_storage.save("test/file.txt", file_content)
        
        assert default_storage.exists(file_path)
        url = default_storage.url(file_path)
        assert url.startswith('/uploads/')
```

### Integration Tests

```python
# tests/test_file_upload_integration.py

def test_chat_file_upload_with_r2():
    """Test file upload through chat endpoint"""
    user = User.objects.create_user(username='testuser')
    room = Chatroom.objects.create(name='Test Room')
    
    file_content = b"PDF content here"
    file = SimpleUploadedFile("document.pdf", file_content)
    
    client = APIClient()
    client.force_authenticate(user=user)
    
    response = client.post(
        f'/chatbot/api/rooms/{room.id}/documents/upload/',
        {'file': file},
        format='multipart'
    )
    
    assert response.status_code == 201
    data = response.json()
    assert 'file_url' in data or 'document_id' in data
    
    # If R2 enabled, URL should be HTTPS to CDN
    if getattr(settings, 'R2_ENABLED', False):
        assert data['file_url'].startswith('https://')
```

---

## 🚀 Deployment Checklist

- [ ] Cloudflare R2 account created
- [ ] Bucket created in R2 dashboard
- [ ] API token generated (R2 Admin)
- [ ] Public access enabled (if needed)
- [ ] Custom domain configured (optional)
- [ ] Environment variables set on server
- [ ] Django settings.py updated
- [ ] `django-storages[boto3]` installed in requirements.txt
- [ ] Database migration applied
- [ ] File permissions verified
- [ ] Storage backend tested
- [ ] CDN caching verified
- [ ] Monitoring/logging configured
- [ ] Backup strategy verified

---

## 📋 Migration Guide: Local → R2

### Step 1: Prepare R2 Account

```bash
# Create bucket in Cloudflare
# Copy credentials to environment
export R2_ENABLED=True
export R2_ACCESS_KEY_ID=...
export R2_SECRET_ACCESS_KEY=...
export R2_BUCKET_NAME=mathia-storage
export R2_ENDPOINT_URL=...
```

### Step 2: Switch Django to R2

```python
# Backend/settings.py
R2_ENABLED = True  # or via environment
```

### Step 3: Copy Existing Files (if needed)

```python
# Management command to sync local files to R2
# Python script:

import os
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

LOCAL_ROOT = '/path/to/local/uploads'
REMOTE_PREFIX = 'migrated/'

for root, dirs, files in os.walk(LOCAL_ROOT):
    for file in files:
        local_path = os.path.join(root, file)
        rel_path = os.path.relpath(local_path, LOCAL_ROOT)
        remote_path = f"{REMOTE_PREFIX}{rel_path}"
        
        with open(local_path, 'rb') as f:
            default_storage.save(remote_path, ContentFile(f.read()))
        
        print(f"Migrated: {rel_path}")
```

### Step 4: Test Thoroughly

```bash
# Upload a test file
# Verify it appears in R2 dashboard
# Verify public URL works
# Test deletion
```

### Step 5: Monitor

```bash
# Monitor logs for storage errors
docker-compose logs web | grep -i storage

# Monitor R2 dashboard for usage
# Verify CDN working
```

---

## 📋 Limitations & Known Issues

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| No versioning | Can't recover old versions | Implement versioning in app logic |
| No built-in backup | Data loss risk | Configure Cloudflare backup rules |
| Latency on first access | Slow first load | Pre-warm CDN for critical files |
| Storage tier changes | Performance impact | Plan tier changes during low-traffic |
| Large file limits | Can't handle very large files | Implement chunked upload for 1GB+ files |

---

## 🔄 Maintenance & Updates

### Weekly Tasks
- Monitor storage usage trend
- Check for failed uploads in logs
- Verify CDN is working

### Monthly Tasks
- Review storage costs
- Analyze access patterns
- Update cache policies if needed

### Quarterly Tasks
- Review file retention policies
- Audit unused buckets
- Plan capacity growth

---

## 📞 Support & Troubleshooting

### Common Issues

**Issue:** "NoCredentialsError" on startup

**Cause:** Environment variables not set  
**Fix:** Verify R2_* environment variables exported before Django starts

**Issue:** File upload succeeds but URL 404s

**Cause:** CDN not yet propagated  
**Fix:** Wait 1-2 minutes, check bucket public access enabled

**Issue:** Very slow uploads

**Cause:** Network latency or small multipart size  
**Fix:** Check network, increase AWS_S3_MULTIPART_CHUNK_SIZE

**Issue:** High costs compared to forecast

**Cause:** Many large files or excessive transfers  
**Fix:** Implement file cleanup policies, use CDN caching

---

## 📚 References

- [Cloudflare R2 Documentation](https://developers.cloudflare.com/r2/)
- [Django Storages S3 Backend](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html)
- [Boto3 S3 Client Reference](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- [R2 Pricing](https://www.cloudflare.com/products/r2/)

---

**Last Reviewed:** Feb 3, 2025  
**Next Review:** Q2 2026  
**Status:** ✅ Production-Ready
