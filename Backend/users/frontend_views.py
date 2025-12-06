"""
User frontend views for Wallet, Reminders, Settings, and Profile pages
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import datetime
from chatbot.models import Reminder
from users.models import Wallet, WalletTransaction
from orchestration.connectors.intersend_connector import IntersendPayConnector


@login_required
def wallet_page(request):
    """Wallet page - view balance and transactions"""
    try:
        workspace = request.user.workspace
        wallet = workspace.wallet
    except:
        # Create wallet if doesn't exist
        from users.models import Workspace
        workspace, _ = Workspace.objects.get_or_create(
            owner=request.user,
            defaults={'name': f"{request.user.username}'s Workspace"}
        )
        wallet, _ = Wallet.objects.get_or_create(
            workspace=workspace,
            currency='KES'
        )
    
    transactions = wallet.transactions.all()[:20]
    
    return render(request, 'users/wallet.html', {
        'wallet': wallet,
        'transactions': transactions
    })


@login_required
def wallet_withdraw(request):
    """Handle M-Pesa withdrawal"""
    if request.method == 'POST':
        amount = float(request.POST.get('amount', 0))
        mpesa_number = request.POST.get('mpesa_number', '')
        
        try:
            workspace = request.user.workspace
            wallet = workspace.wallet
            
            # Use Intersend connector
            connector = IntersendPayConnector()
            result = connector.withdraw_to_mpesa(
                user=request.user,
                amount=amount,
                phone_number=mpesa_number
            )
            
            if 'error' in result:
                messages.error(request, f"Withdrawal failed: {result['error']}")
            else:
                messages.success(request, f"KES {amount} sent to {mpesa_number}")
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
    
    return redirect('users:wallet')


@login_required
def reminders_page(request):
    """Reminders page - list all  reminders"""
    reminders = Reminder.objects.filter(
        user=request.user
    ).order_by('-created_at')
    
    return render(request, 'users/reminders.html', {
        'reminders': reminders
    })


@login_required
def create_reminder(request):
    """Create a new reminder"""
    if request.method == 'POST':
        content = request.POST.get('content', '')
        scheduled_time_str = request.POST.get('scheduled_time', '')
        via_email = request.POST.get('via_email') == 'on'
        via_whatsapp = request.POST.get('via_whatsapp') == 'on'
        
        try:
            # Parse datetime
            scheduled_time = datetime.fromisoformat(scheduled_time_str)
            
            # Create reminder
            Reminder.objects.create(
                user=request.user,
                content=content,
                scheduled_time=scheduled_time,
                via_email=via_email,
                via_whatsapp=via_whatsapp,
                status='pending'
            )
            
            messages.success(request, 'Reminder created successfully!')
        except Exception as e:
            messages.error(request, f'Error creating reminder: {str(e)}')
    
    return redirect('users:reminders')


@login_required
def settings_page(request):
    """Settings page - integrations and preferences"""
    # Check if Calendly is connected
    calendly_connected = hasattr(request.user, 'calendly') and request.user.calendly.is_connected
    
    return render(request, 'users/settings.html', {
        'calendly_connected': calendly_connected
    })
