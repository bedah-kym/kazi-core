"""Avatar upload endpoint with server-side processing."""
import io
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from PIL import Image

MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


@login_required
@require_POST
def avatar_upload(request):
    """Handle avatar upload with crop and resize."""
    uploaded = request.FILES.get('avatar')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    if uploaded.size > MAX_AVATAR_SIZE:
        return JsonResponse({'error': 'File too large (max 5MB)'}, status=400)

    if uploaded.content_type not in ALLOWED_TYPES:
        return JsonResponse({'error': 'Invalid file type'}, status=400)

    try:
        img = Image.open(uploaded)
        img.verify()
        uploaded.seek(0)
        img = Image.open(uploaded)
    except Exception:
        return JsonResponse({'error': 'Invalid image file'}, status=400)

    # Convert to RGB if needed (for RGBA/P mode images)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    # Resize to 256x256 max, maintaining aspect ratio then center-crop
    img.thumbnail((256, 256), Image.LANCZOS)

    # Save as WebP
    buf = io.BytesIO()
    img.save(buf, format='WEBP', quality=85)
    buf.seek(0)

    from django.core.files.base import ContentFile
    filename = f"avatar_{request.user.id}.webp"
    profile = request.user.profile

    # Delete old avatar file if it exists
    if profile.avatar:
        try:
            profile.avatar.delete(save=False)
        except Exception:
            pass

    profile.avatar.save(filename, ContentFile(buf.read()), save=True)

    return JsonResponse({'url': profile.get_avatar_url()})
