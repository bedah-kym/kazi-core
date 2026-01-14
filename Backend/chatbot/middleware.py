"""
Middleware to automatically create Member objects for authenticated users.
This ensures permission checks in context panel work correctly.

PERFORMANCE: Uses session caching to only check once per login session.
"""
from chatbot.models import Member


class EnsureMemberMiddleware:
    """
    Automatically create a Member object for any authenticated user if it doesn't exist.
    This fixes 403 errors in context panel where permission checks filter by Member.
    
    Performance optimization: Once a Member is created, we set a session flag
    to avoid hitting the database on every subsequent request.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Check session flag first to avoid unnecessary DB queries
            if not request.session.get('_member_created', False):
                # Ensure the user has a Member object
                Member.objects.get_or_create(User=request.user)
                # Mark in session to skip this check for future requests
                request.session['_member_created'] = True
        
        response = self.get_response(request)
        return response
