"""
Wallet, Reminders, and Settings views with workspace guards
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from users.decorators import workspace_required
from users.models import Wallet
from chatbot.models import Reminder


@workspace_required
def wallet(request):
    """
    Wallet management page
    """
    workspace = request.user.workspace
    
    # Get or create wallet
    wallet, created = Wallet.objects.get_or_create(
        workspace=workspace,
        currency='KES',
        defaults={'balance': 0}
    )
    
    context = {
        'wallet': wallet,
        'workspace': workspace,
    }
    
    return render(request, 'users/wallet.html', context)


@workspace_required
def reminders(request):
    """
    Reminders management page
    """
    user_reminders = Reminder.objects.filter(
        user=request.user
    ).order_by('-created_at')
    
    context = {
        'reminders': user_reminders,
        'workspace': request.user.workspace,
    }
    
    return render(request, 'users/reminders.html', context)


@workspace_required
def settings(request):
    """
    Settings and integrations page
    """
    workspace = request.user.workspace
    
    context = {
        'workspace': workspace,
    }
    
    return render(request, 'users/settings.html', context)
