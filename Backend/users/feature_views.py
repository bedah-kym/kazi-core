"""
Wallet, Reminders, and Settings views with workspace guards
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from users.decorators import workspace_required
from users.models import Wallet
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


from django.contrib import messages
from django.shortcuts import redirect
from users.forms import UserForm, UserProfileForm, GoalProfileForm
from users.models import GoalProfile

@workspace_required
def settings(request):
    """
    Settings and integrations page
    """
    workspace = request.user.workspace
    profile = request.user.profile
    prefs = profile.notification_preferences or {}

    capability_defaults = {
        "capability_mode": "custom",
        "proactive_assistant_enabled": True,
        "nudge_frequency": "low",
        "ai_voice_enabled": True,
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
            "ai_voice_enabled": True,
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

    if request.method == 'POST':
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

        profile.notification_preferences = prefs
        profile.save(update_fields=['notification_preferences'])
        messages.success(request, 'Capability settings updated.')
        return redirect('users:settings')
    
    # Get integration status
    from .models import UserIntegration, CalendlyProfile
    
    # Check Calendly (Legacy model)
    try:
        calendly_connected = request.user.calendly.is_connected
    except (CalendlyProfile.DoesNotExist, AttributeError):
        calendly_connected = False

    # Check other integrations
    integrations = UserIntegration.objects.filter(user=request.user, is_connected=True).values_list('integration_type', flat=True)
    gmail_integration = UserIntegration.objects.filter(
        user=request.user,
        integration_type='gmail',
        is_connected=True
    ).first()
    gmail_address = (gmail_integration.metadata or {}).get('gmail_address') if gmail_integration else None
    
    context = {
        'workspace': workspace,
        'active_tab': 'integrations',
        'calendly_connected': calendly_connected,
        'whatsapp_connected': 'whatsapp' in integrations,
        'intasend_connected': 'intasend' in integrations,
        'gmail_connected': 'gmail' in integrations,
        'gmail_address': gmail_address,
        'capability_prefs': {**capability_defaults, **prefs},
    }
    
    return render(request, 'users/settings.html', context)


@workspace_required
def profile_settings(request):
    """
    Profile settings page
    """
    user = request.user
    if request.method == 'POST':
        user_form = UserForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=user.profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('users:profile_settings')
    else:
        user_form = UserForm(instance=user)
        profile_form = UserProfileForm(instance=user.profile)
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'workspace': request.user.workspace,
        'active_tab': 'profile',
    }
    return render(request, 'users/profile_settings.html', context)


@workspace_required
def goals_settings(request):
    """
    Goals and AI personalization page
    """
    workspace = request.user.workspace
    # Ensure goal profile exists
    goal_profile, created = GoalProfile.objects.get_or_create(workspace=workspace)
    
    if request.method == 'POST':
        form = GoalProfileForm(request.POST, instance=goal_profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Goals updated successfully! AI will use this to personalize your experience.')
            return redirect('users:goals_settings')
    else:
        form = GoalProfileForm(instance=goal_profile)
    
    context = {
        'form': form,
        'workspace': workspace,
        'active_tab': 'goals',
    }
    return render(request, 'users/goals.html', context)


@workspace_required
def profile_settings(request):
    """
    Profile settings page
    """
    user = request.user
    if request.method == 'POST':
        user_form = UserForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=user.profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('users:profile_settings')
    else:
        user_form = UserForm(instance=user)
        profile_form = UserProfileForm(instance=user.profile)
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'workspace': request.user.workspace,
    }
    return render(request, 'users/profile_settings.html', context)


@workspace_required
def goals_settings(request):
    """
    Goals and AI personalization page
    """
    workspace = request.user.workspace
    # Ensure goal profile exists
    goal_profile, created = GoalProfile.objects.get_or_create(workspace=workspace)
    
    if request.method == 'POST':
        form = GoalProfileForm(request.POST, instance=goal_profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Goals updated successfully! AI will use this to personalize your experience.')
            return redirect('users:goals_settings')
    else:
        form = GoalProfileForm(instance=goal_profile)
    
    context = {
        'form': form,
        'workspace': workspace,
    }
    return render(request, 'users/goals.html', context)
