"""
Custom allauth adapter that gates social signup behind invite tokens.
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from users.models import PlatformInvite, TrialInvite


class InviteGatedSocialAdapter(DefaultSocialAccountAdapter):
    """Only allow social signup when a valid invite token is in the session."""

    def is_open_for_signup(self, request, sociallogin):
        token = request.session.get('invite_token')
        if not token:
            return False
        # Check PlatformInvite
        if PlatformInvite.objects.filter(token=token, status='sent').exists():
            inv = PlatformInvite.objects.get(token=token, status='sent')
            return not inv.is_expired
        # Check TrialInvite
        if TrialInvite.objects.filter(token=token, status='sent', used=False).exists():
            return True
        return False
