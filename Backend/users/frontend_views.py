"""
User frontend views for Wallet, Reminders, Settings, and Profile pages
"""
import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from datetime import datetime
from chatbot.models import Reminder

logger = logging.getLogger(__name__)


# Wallet views moved to payments app
# def wallet_page(request): ...
# def wallet_withdraw(request): ...


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
            reminder = Reminder.objects.create(
                user=request.user,
                content=content,
                scheduled_time=scheduled_time,
                via_email=via_email,
                via_whatsapp=via_whatsapp,
                status='pending'
            )
            try:
                from chatbot.tasks import schedule_reminder_delivery
                schedule_reminder_delivery(reminder.id, scheduled_time)
            except Exception:
                logger.exception("Reminder scheduling failed")
                messages.warning(request, 'Reminder saved but not scheduled.')

            messages.success(request, 'Reminder created successfully!')
        except Exception:
            logger.exception("Reminder creation failed")
            messages.error(request, 'Error creating reminder. Please try again.')

    return redirect('users:reminders')


@login_required
def settings_page(request):
    """Settings page - integrations and preferences"""
    # Check if Calendly is connected
    calendly_connected = hasattr(request.user, 'calendly') and request.user.calendly.is_connected

    return render(request, 'users/settings.html', {
        'calendly_connected': calendly_connected
    })
