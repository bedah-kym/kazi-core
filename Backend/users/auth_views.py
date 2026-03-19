"""
Registration and Onboarding Views
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from uuid import uuid4
from users.models import Workspace, PlatformInvite, TrialInvite


def _resolve_invite_token(token):
    """Validate an invite token against PlatformInvite then TrialInvite.
    Returns (invite_obj, invite_type) or (None, None)."""
    if not token:
        return None, None
    # Check PlatformInvite first
    try:
        inv = PlatformInvite.objects.get(token=token, status='sent')
        if inv.is_expired:
            inv.status = 'expired'
            inv.save(update_fields=['status'])
            return None, None
        return inv, 'platform'
    except PlatformInvite.DoesNotExist:
        pass
    # Fallback to TrialInvite
    try:
        inv = TrialInvite.objects.get(token=token, status='sent', used=False)
        return inv, 'trial'
    except TrialInvite.DoesNotExist:
        pass
    return None, None


def register(request):
    """User registration view — invite-only."""
    token = request.GET.get('invite', '') or request.POST.get('invite_token', '')
    invite, invite_type = _resolve_invite_token(token)

    # Gate: no valid token → redirect to trial application
    if not invite:
        if token:
            messages.error(request, 'This invite link is invalid or has expired.')
        else:
            messages.info(request, 'Mathia is invite-only. Request access below.')
        return redirect('users:trial_apply')

    # Store token in session for social auth adapter
    request.session['invite_token'] = token

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        username = request.POST.get('username', '').strip()
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        ctx = {'invite_token': token}

        if not full_name or not email or not username or not password1 or not password2:
            messages.error(request, 'All fields are required')
            return render(request, 'users/register.html', ctx)

        if password1 != password2:
            messages.error(request, 'Passwords do not match')
            return render(request, 'users/register.html', ctx)

        try:
            validate_password(password1)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(request, 'users/register.html', ctx)

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, 'Username already exists')
            return render(request, 'users/register.html', ctx)

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'Email already registered')
            return render(request, 'users/register.html', ctx)

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password1
            )
            names = full_name.split(' ', 1)
            user.first_name = names[0]
            if len(names) > 1:
                user.last_name = names[1]
            user.save()

            # Mark invite as activated and set profile invite chain fields
            _activate_invite(user, invite, invite_type)

            auth_user = authenticate(request, username=username, password=password1)
            if auth_user is not None:
                login(request, auth_user)
            else:
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

            # Clear session token
            request.session.pop('invite_token', None)

            return redirect('users:onboarding')

        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(request, 'users/register.html', ctx)

    return render(request, 'users/register.html', {'invite_token': token})


def _activate_invite(user, invite, invite_type):
    """Mark the invite as used and set profile invite chain fields."""
    now = timezone.now()
    profile = user.profile

    if invite_type == 'platform':
        invite.status = 'activated'
        invite.activated_by = user
        invite.activated_at = now
        invite.save(update_fields=['status', 'activated_by', 'activated_at'])
        profile.invited_by = invite.invited_by
        profile.invite_depth = invite.invite_depth
        profile.save(update_fields=['invited_by', 'invite_depth'])

    elif invite_type == 'trial':
        invite.used = True
        invite.status = 'activated'
        invite.activated_by = user
        invite.activated_at = now
        invite.save(update_fields=['used', 'status', 'activated_by', 'activated_at'])
        # TrialInvite → admin-seeded, depth 0
        profile.invite_depth = 0
        profile.save(update_fields=['invite_depth'])


@login_required
def onboarding(request):
    """Multi-step onboarding flow"""
    step = int(request.GET.get('step', 1))
    
    # Check if user already completed onboarding
    if hasattr(request.user, 'workspace') and request.user.workspace.onboarding_completed:
        return redirect('users:dashboard')
    
    if request.method == 'POST':
        if step == 1:
            # Save account type
            account_type = request.POST.get('account_type', 'personal')
            request.session['onboarding_account_type'] = account_type
            return redirect(reverse('users:onboarding') + '?step=2')

        elif step == 2:
            # Save profile info
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            role = request.POST.get('role', '').strip()
            industry = request.POST.get('industry', '').strip()

            if first_name:
                request.user.first_name = first_name
            if last_name:
                request.user.last_name = last_name
            request.user.save(update_fields=['first_name', 'last_name'])

            profile = request.user.profile
            if role:
                profile.role = role
            if industry:
                profile.industry = industry

            # Handle avatar upload
            avatar_file = request.FILES.get('avatar')
            if avatar_file:
                try:
                    import io
                    from PIL import Image
                    from django.core.files.base import ContentFile

                    img = Image.open(avatar_file)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    img.thumbnail((256, 256), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format='WEBP', quality=85)
                    buf.seek(0)
                    # Use a versioned filename to avoid stale browser/CDN caches after updates.
                    filename = f"avatar_{request.user.id}_{uuid4().hex[:12]}.webp"
                    if profile.avatar:
                        try:
                            profile.avatar.delete(save=False)
                        except Exception:
                            pass
                    profile.avatar.save(filename, ContentFile(buf.read()), save=False)
                except Exception:
                    pass  # Skip avatar if processing fails

            profile.save()
            return redirect(reverse('users:onboarding') + '?step=3')

        elif step == 3:
            # Create workspace (was step 2)
            workspace_name = request.POST.get('workspace_name', '')
            account_type = request.session.get('onboarding_account_type') or 'personal'

            workspace, created = Workspace.objects.get_or_create(
                user=request.user,
                defaults={
                    'owner': request.user,
                    'name': workspace_name,
                    'account_type': account_type,
                    'plan': 'free'
                }
            )

            if not created:
                workspace.name = workspace_name
                workspace.account_type = account_type
                workspace.save()

            return redirect(reverse('users:onboarding') + '?step=4')

        elif step == 4:
            # Save plan selection (was step 3)
            plan = request.POST.get('plan', 'free')

            workspace = Workspace.objects.get(owner=request.user)
            workspace.plan = plan
            workspace.save()

            return redirect(reverse('users:onboarding') + '?step=5')

        elif step == 5:
            # Complete onboarding (was step 4)
            workspace = Workspace.objects.get(owner=request.user)
            workspace.onboarding_completed = True
            workspace.save()

            messages.success(request, 'Welcome to Mathia! Your workspace is ready.')
            return redirect('users:dashboard')
    
    return render(request, 'users/onboarding.html', {
        'step': step
    })
