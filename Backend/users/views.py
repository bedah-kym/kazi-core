from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from datetime import timedelta
from django.core.mail import send_mail
from django.urls import reverse
from .forms import CustomAuthenticationForm, TrialApplicationForm
from .models import TrialApplication, TrialInvite, Workspace

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


@staff_member_required
def trial_applications(request):
    apps = TrialApplication.objects.all()
    invites = TrialInvite.objects.select_related('application', 'sent_by', 'activated_by')
    return render(request, 'users/trial_applications_admin.html', {'applications': apps, 'invites': invites})


@user_passes_test(lambda u: u.is_superuser)
def send_trial_invite(request, pk):
    app = get_object_or_404(TrialApplication, pk=pk)
    token = TrialInvite.objects.create(
        application=app,
        email=app.email,
        sent_by=request.user,
        sent_at=timezone.now(),
    )
    invite_url = request.build_absolute_uri(reverse('users:activate_trial', args=[token.token]))
    email_body = (
        "You're invited to a 30-day Mathia trial.\n\n"
        f"Name: {app.name}\nCompany: {app.company}\nUse case: {app.primary_use_case}\n\n"
        f"Claim your trial: {invite_url}\n\n"
        "This link is unique to you and valid for one activation."
    )
    send_mail(
        subject="Your Mathia trial invite",
        message=email_body,
        from_email=None,
        recipient_list=[app.email],
    )
    app.status = 'approved'
    app.save()
    messages.success(request, f"Invite sent to {app.email}")
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
