"""
Custom decorators for user authentication and workspace validation
"""
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from functools import wraps


def workspace_required(view_func):
    """
    Decorator to ensure user has completed workspace onboarding.
    Redirects to onboarding if workspace doesn't exist or isn't complete.
    """
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        # Check if user has a workspace
        if not hasattr(request.user, 'workspace'):
            messages.warning(request, 'Please complete your workspace setup first')
            return redirect('users:onboarding')
        
        # Check if onboarding is completed
        workspace = request.user.workspace
        if not workspace.onboarding_completed:
            messages.info(request, 'Almost there! Finish setting up your workspace')
            return redirect('users:onboarding')
        
        # All good, proceed to view
        return view_func(request, *args, **kwargs)
    
    return wrapper
