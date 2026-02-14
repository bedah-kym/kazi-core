from django.utils import timezone
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse


class TrialExpiryMiddleware:
    """
    Ensures users on trial are locked out once the 30-day window ends.
    If expired, downgrade plan to free and block access until upgrade.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            workspace = getattr(user, 'workspace', None)
            if workspace and workspace.plan == 'trial' and workspace.trial_active and workspace.trial_ends_at:
                if timezone.now() > workspace.trial_ends_at:
                    workspace.trial_active = False
                    workspace.plan = 'free'
                    workspace.save(update_fields=['trial_active', 'plan'])
                    messages.error(request, "Your 30-day trial ended. Upgrade to keep using Mathia.")
                    pricing_path = reverse('users:pricing')
                    if request.path != pricing_path:
                        return redirect('users:pricing')

        response = self.get_response(request)
        return response
