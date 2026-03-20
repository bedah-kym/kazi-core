"""
Wallet, Reminders, and Settings views with workspace guards
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from users.decorators import workspace_required
from users.models import Wallet, PlatformInvite
from users.forms import UserForm, UserProfileForm
from chatbot.models import Reminder


# Wallet view moved to payments app
# @workspace_required
# def wallet(request):
#     ...


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
    """Unified settings page with profile, AI, capabilities, integrations, workspace."""
    workspace = request.user.workspace
    profile = request.user.profile
    prefs = profile.notification_preferences or {}

    capability_defaults = {
        "capability_mode": "custom",
        "proactive_assistant_enabled": True,
        "nudge_frequency": "low",
        "proactive_snooze_until": None,
        "ai_voice_enabled": False,
        "manager_llm_enabled": True,
        "allow_web_search": True,
        "allow_travel": True,
        "allow_payments": True,
        "allow_reminders": True,
        "allow_whatsapp": True,
        "allow_email": True,
        "allow_calendar": True,
    }

    capability_presets = {
        "conserve": {
            "proactive_assistant_enabled": False,
            "nudge_frequency": "low",
            "proactive_snooze_until": None,
            "ai_voice_enabled": False,
            "manager_llm_enabled": False,
            "allow_web_search": False,
            "allow_travel": True,
            "allow_payments": True,
            "allow_reminders": True,
            "allow_whatsapp": True,
            "allow_email": True,
            "allow_calendar": True,
        },
        "balanced": {
            "proactive_assistant_enabled": True,
            "nudge_frequency": "low",
            "proactive_snooze_until": None,
            "ai_voice_enabled": False,
            "manager_llm_enabled": True,
            "allow_web_search": True,
            "allow_travel": True,
            "allow_payments": True,
            "allow_reminders": True,
            "allow_whatsapp": True,
            "allow_email": True,
            "allow_calendar": True,
        },
        "max": {
            "proactive_assistant_enabled": True,
            "nudge_frequency": "high",
            "proactive_snooze_until": None,
            "ai_voice_enabled": False,
            "manager_llm_enabled": True,
            "allow_web_search": True,
            "allow_travel": True,
            "allow_payments": True,
            "allow_reminders": True,
            "allow_whatsapp": True,
            "allow_email": True,
            "allow_calendar": True,
        },
    }

    def _build_settings_context(user_form, profile_form):
        """Build a full template context for settings page rendering."""
        from .models import UserIntegration, CalendlyProfile

        try:
            calendly_connected = request.user.calendly.is_connected
        except (CalendlyProfile.DoesNotExist, AttributeError):
            calendly_connected = False

        integrations = UserIntegration.objects.filter(
            user=request.user, is_connected=True
        ).values_list('integration_type', flat=True)
        gmail_integration = UserIntegration.objects.filter(
            user=request.user, integration_type='gmail', is_connected=True
        ).first()
        gmail_address = (gmail_integration.metadata or {}).get('gmail_address') if gmail_integration else None

        # Invite chain context
        can_send_invites = profile.invite_depth == 0
        sent_invites = []
        invites_remaining = 0
        if can_send_invites:
            sent_invites = PlatformInvite.objects.filter(invited_by=request.user).order_by('-sent_at')
            used_count = sent_invites.filter(status__in=['sent', 'activated']).count()
            invites_remaining = max(0, 3 - used_count)

        # Build normalized notify_matrix for template
        from orchestration.user_preferences import _DEFAULT_NOTIFY_MATRIX, _NOTIFY_CHANNELS
        raw_matrix = (profile.notification_preferences or {}).get('notify_matrix', {})
        notify_matrix = {}
        for event_type, defaults in _DEFAULT_NOTIFY_MATRIX.items():
            user_event = raw_matrix.get(event_type, {}) if isinstance(raw_matrix.get(event_type), dict) else {}
            notify_matrix[event_type] = {
                ch: user_event.get(ch, defaults[ch]) for ch in _NOTIFY_CHANNELS
            }

        # Build human-readable labels for event types
        notify_event_labels = {
            'payment.deposit': 'Deposit Received',
            'payment.withdrawal': 'Withdrawal Processed',
            'payment.invoice': 'Invoice Paid',
            'payment.error': 'Payment Error',
            'reminder.due': 'Reminder Due',
            'message.unread': 'Unread Message',
            'message.mention': 'Mentioned in Chat',
            'system.info': 'System Info',
            'system.warning': 'System Warning',
        }

        return {
            'workspace': workspace,
            'user_form': user_form,
            'profile_form': profile_form,
            'calendly_connected': calendly_connected,
            'whatsapp_connected': 'whatsapp' in integrations,
            'intasend_connected': 'intasend' in integrations,
            'gmail_connected': 'gmail' in integrations,
            'gmail_address': gmail_address,
            'capability_prefs': {**capability_defaults, **(profile.notification_preferences or {})},
            'ai_personalization_enabled': profile.ai_personalization_enabled,
            'can_send_invites': can_send_invites,
            'sent_invites': sent_invites,
            'invites_remaining': invites_remaining,
            'notify_matrix': notify_matrix,
            # Preserve table column order: In-App, Email, WhatsApp.
            'notify_channels': ['in_app', 'email', 'whatsapp'],
            'notify_event_labels': notify_event_labels,
        }

    if request.method == 'POST':
        section = request.POST.get('section', '')

        if section == 'profile':
            user_form = UserForm(request.POST, instance=request.user)
            profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
            if user_form.is_valid() and profile_form.is_valid():
                user_form.save()
                profile_form.save()
                messages.success(request, 'Profile updated successfully!')
                return redirect('/accounts/settings/#profile')
            else:
                for form in (user_form, profile_form):
                    for field, errors in form.errors.items():
                        for error in errors:
                            messages.error(request, f'{field}: {error}')
                context = _build_settings_context(user_form, profile_form)
                return render(request, 'users/settings.html', context)

        elif section == 'ai':
            profile.ai_personalization_enabled = request.POST.get('ai_personalization_enabled') == 'on'
            profile.save(update_fields=['ai_personalization_enabled'])
            messages.success(request, 'AI settings updated.')
            return redirect('/accounts/settings/#ai')

        elif section == 'capabilities':
            mode = request.POST.get('capability_mode', prefs.get('capability_mode', 'custom'))
            mode = mode if mode in capability_presets or mode == 'custom' else 'custom'
            prefs.update(capability_defaults)
            prefs["capability_mode"] = mode

            if mode in capability_presets:
                prefs.update(capability_presets[mode])
            else:
                prefs["proactive_assistant_enabled"] = request.POST.get('proactive_assistant_enabled') == 'on'
                prefs["ai_voice_enabled"] = request.POST.get('ai_voice_enabled') == 'on'
                prefs["manager_llm_enabled"] = request.POST.get('manager_llm_enabled') == 'on'
                prefs["allow_web_search"] = request.POST.get('allow_web_search') == 'on'
                prefs["allow_travel"] = request.POST.get('allow_travel') == 'on'
                prefs["allow_payments"] = request.POST.get('allow_payments') == 'on'
                prefs["allow_reminders"] = request.POST.get('allow_reminders') == 'on'
                prefs["allow_whatsapp"] = request.POST.get('allow_whatsapp') == 'on'
                prefs["allow_email"] = request.POST.get('allow_email') == 'on'
                prefs["allow_calendar"] = request.POST.get('allow_calendar') == 'on'
                prefs["nudge_frequency"] = request.POST.get('nudge_frequency', prefs.get('nudge_frequency', 'low'))

            snooze_hours_raw = request.POST.get('proactive_snooze_hours', '0')
            try:
                snooze_hours = int(snooze_hours_raw)
            except (TypeError, ValueError):
                snooze_hours = 0
            if snooze_hours > 0:
                prefs["proactive_snooze_until"] = (timezone.now() + timedelta(hours=snooze_hours)).isoformat()
            else:
                prefs.pop("proactive_snooze_until", None)

            profile.notification_preferences = prefs
            profile.save(update_fields=['notification_preferences'])
            messages.success(request, 'Capability settings updated.')
            return redirect('/accounts/settings/#capabilities')

        elif section == 'notifications':
            # Build notify_matrix from form checkboxes:
            # Each checkbox name is like "notif__payment.deposit__email"
            from orchestration.user_preferences import _DEFAULT_NOTIFY_MATRIX, _NOTIFY_CHANNELS
            matrix = {}
            for event_type in _DEFAULT_NOTIFY_MATRIX:
                matrix[event_type] = {}
                for channel in _NOTIFY_CHANNELS:
                    key = f"notif__{event_type}__{channel}"
                    matrix[event_type][channel] = request.POST.get(key) == 'on'
            prefs["notify_matrix"] = matrix
            profile.notification_preferences = prefs
            profile.save(update_fields=['notification_preferences'])
            messages.success(request, 'Notification preferences updated.')
            return redirect('/accounts/settings/#notifications')

        elif section == 'workspace':
            ws_name = request.POST.get('workspace_name', '').strip()
            if ws_name:
                workspace.name = ws_name
                workspace.save(update_fields=['name'])
                messages.success(request, 'Workspace updated.')
            return redirect('/accounts/settings/#workspace')

        return redirect('users:settings')

    # GET: prepare forms and context
    user_form = UserForm(instance=request.user)
    profile_form = UserProfileForm(instance=profile)
    context = _build_settings_context(user_form, profile_form)
    return render(request, 'users/settings.html', context)
