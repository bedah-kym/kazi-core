from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import timedelta
from django.core.mail import send_mail
from django.urls import reverse
from .forms import CustomAuthenticationForm, TrialApplicationForm
from .models import TrialApplication, TrialInvite, PlatformInvite, Workspace

class CustomLoginView(LoginView):
    form_class = CustomAuthenticationForm
    template_name = 'users/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        username = self.request.POST.get('username', '')
        if username:
            # Check rate limiting
            key = f'login_attempts_{username}'
            attempts = cache.get(key, 0)
            if attempts >= 5:  # 5 attempts max
                messages.error(self.request, 'Too many login attempts. Please try again later.')
                context['form'].add_error(None, 'Too many login attempts')
        return context

    def form_valid(self, form):
        username = form.cleaned_data.get('username')
        # Reset rate limiting on successful login
        cache.delete(f'login_attempts_{username}')
        return super().form_valid(form)

    def form_invalid(self, form):
        username = self.request.POST.get('username', '')
        if username:
            # Increment rate limiting counter
            key = f'login_attempts_{username}'
            attempts = cache.get(key, 0)
            cache.set(key, attempts + 1, 300)  # 5 minutes timeout
        return super().form_invalid(form)


def landing_page(request):
    """Render the enterprise landing page"""
    return render(request, 'users/landing.html')


def trial_apply(request):
    """Public fun questionnaire for invite-only trial."""
    if request.method == 'POST':
        form = TrialApplicationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Thanks! We got your answers. Our team reviews trial requests daily.")
            return redirect('users:trial_apply')
    else:
        form = TrialApplicationForm()
    return render(request, 'users/trial_apply.html', {'form': form})


@user_passes_test(lambda u: u.is_superuser)
def trial_applications(request):
    """Superuser-only dashboard for managing trial applications and invites."""
    status_filter = request.GET.get('status', '')
    apps = TrialApplication.objects.all()
    if status_filter in ('pending', 'approved', 'rejected'):
        apps = apps.filter(status=status_filter)

    invites = TrialInvite.objects.select_related('application', 'sent_by', 'activated_by')

    # Build invite URLs for display/copy
    for inv in invites:
        inv.register_url = request.build_absolute_uri(
            reverse('users:register') + f'?invite={inv.token}'
        )

    return render(request, 'users/trial_applications_admin.html', {
        'applications': apps,
        'invites': invites,
        'status_filter': status_filter,
    })


@user_passes_test(lambda u: u.is_superuser)
@require_POST
def send_trial_invite(request, pk):
    """Create a TrialInvite, email it, and show the link for manual copy."""
    app = get_object_or_404(TrialApplication, pk=pk)

    # Prevent duplicate invites for same application
    existing = TrialInvite.objects.filter(application=app, status='sent', used=False).first()
    if existing:
        register_url = request.build_absolute_uri(
            reverse('users:register') + f'?invite={existing.token}'
        )
        messages.warning(request, f"An active invite already exists for {app.email}.")
        messages.info(request, f"Link: {register_url}")
        return redirect('users:trial_applications')

    invite = TrialInvite.objects.create(
        application=app,
        email=app.email,
        sent_by=request.user,
        sent_at=timezone.now(),
    )

    # Point to register (not activate) — registration is invite-gated now
    register_url = request.build_absolute_uri(
        reverse('users:register') + f'?invite={invite.token}'
    )
    email_body = (
        "You're invited to join Mathia.\n\n"
        f"Name: {app.name}\nCompany: {app.company}\nUse case: {app.primary_use_case}\n\n"
        f"Create your account: {register_url}\n\n"
        "This link is unique to you and valid for one activation.\n"
        "After registering, your 30-day trial will be activated automatically."
    )
    try:
        send_mail(
            subject="Your Mathia invite",
            message=email_body,
            from_email=None,
            recipient_list=[app.email],
        )
        messages.success(request, f"Invite emailed to {app.email}")
    except Exception:
        messages.warning(request, f"Email delivery failed — copy the link manually.")

    app.status = 'approved'
    app.save()

    messages.info(request, f"Invite link: {register_url}")
    return redirect('users:trial_applications')


@user_passes_test(lambda u: u.is_superuser)
@require_POST
def reject_trial_application(request, pk):
    """Reject a trial application."""
    app = get_object_or_404(TrialApplication, pk=pk)
    app.status = 'rejected'
    app.save()
    messages.success(request, f"Application from {app.email} rejected.")
    return redirect('users:trial_applications')


@login_required
def activate_trial(request, token):
    invite = get_object_or_404(TrialInvite, token=token)
    if invite.used or invite.status == 'expired':
        messages.error(request, "This invite was already used or expired.")
        return redirect('users:dashboard')
    if invite.email.lower() != request.user.email.lower():
        messages.error(request, "This invite is tied to a different email. Use the invited email to claim it.")
        return redirect('users:dashboard')

    now = timezone.now()
    invite.used = True
    invite.status = 'activated'
    invite.activated_by = request.user
    invite.activated_at = now
    invite.trial_ends_at = now + timedelta(days=30)
    invite.save()

    # Ensure workspace exists
    workspace, _ = Workspace.objects.get_or_create(user=request.user, defaults={
        'owner': request.user,
        'name': f"{request.user.username}'s Workspace",
        'plan': 'trial',
        'trial_started_at': now,
        'trial_ends_at': invite.trial_ends_at,
        'trial_active': True,
    })
    # Update if existing
    workspace.plan = 'trial'
    workspace.trial_started_at = now
    workspace.trial_ends_at = invite.trial_ends_at
    workspace.trial_active = True
    workspace.save()

    # Mark as admin-seeded — can send platform invites
    profile = request.user.profile
    profile.invite_depth = 0
    profile.save(update_fields=['invite_depth'])

    messages.success(request, f"Trial activated! You have access until {invite.trial_ends_at.date()}.")
    return redirect('users:dashboard')


# Marketing pages (value-focused, non-technical)
def why_mathia(request):
    return render(request, 'users/why-mathia.html')


def playbooks(request):
    return render(request, 'users/playbooks.html')


def pricing(request):
    return render(request, 'users/pricing.html')


def trust(request):
    return render(request, 'users/trust.html')


def how_it_works(request):
    return render(request, 'users/how-it-works.html')


def workflows_library(request):
    return render(request, 'users/workflows-library.html')


def updates(request):
    return render(request, 'users/updates.html')


@login_required
@require_POST
def send_platform_invite(request):
    """Depth-0 users can send up to 3 platform invites."""
    profile = request.user.profile
    if profile.invite_depth != 0:
        return JsonResponse({'error': 'You do not have permission to send invites.'}, status=403)

    email = request.POST.get('email', '').strip().lower()
    if not email:
        return JsonResponse({'error': 'Email is required.'}, status=400)

    # Validate email format
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'error': 'Invalid email address.'}, status=400)

    # Check quota: 3 max (sent + activated count)
    used = PlatformInvite.objects.filter(
        invited_by=request.user,
        status__in=['sent', 'activated']
    ).count()
    if used >= 3:
        return JsonResponse({'error': 'You have used all 3 invites.'}, status=400)

    # Check if already invited
    if PlatformInvite.objects.filter(email=email, status__in=['sent', 'activated']).exists():
        return JsonResponse({'error': 'This email has already been invited.'}, status=400)

    from django.contrib.auth.models import User as AuthUser
    if AuthUser.objects.filter(email__iexact=email).exists():
        return JsonResponse({'error': 'This email is already registered.'}, status=400)

    invite = PlatformInvite.objects.create(
        invited_by=request.user,
        email=email,
        invite_depth=profile.invite_depth + 1,
        expires_at=timezone.now() + timedelta(days=7),
    )

    invite_url = request.build_absolute_uri(
        reverse('users:register') + f'?invite={invite.token}'
    )
    send_mail(
        subject=f"{request.user.get_full_name() or request.user.username} invited you to Mathia",
        message=(
            f"You've been invited to join Mathia by {request.user.get_full_name() or request.user.username}.\n\n"
            f"Create your account: {invite_url}\n\n"
            "This link expires in 7 days."
        ),
        from_email=None,
        recipient_list=[email],
    )

    return JsonResponse({
        'ok': True,
        'remaining': 3 - (used + 1),
        'invite': {
            'email': invite.email,
            'status': invite.status,
            'sent_at': invite.sent_at.isoformat() if invite.sent_at else None,
        }
    })
