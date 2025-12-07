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
    
    context = {
        'workspace': workspace,
        'active_tab': 'integrations',
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
