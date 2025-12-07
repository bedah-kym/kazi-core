"""
Registration and Onboarding Views
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.urls import reverse
from users.models import Workspace


def register(request):
    """User registration view"""
    if request.method == 'POST':
        full_name = request.POST.get('full_name', '')
        email = request.POST.get('email', '')
        username = request.POST.get('username', '')
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        
        # Validation
        if password1 != password2:
            messages.error(request, 'Passwords do not match')
            return render(request, 'users/register.html')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists')
            return render(request, 'users/register.html')
        
        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered')
            return render(request, 'users/register.html')
        
        try:
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password1
            )
            
            # Set full name
            names = full_name.split(' ', 1)
            user.first_name = names[0]
            if len(names) > 1:
                user.last_name = names[1]
            user.save()
            
            # Login user
            login(request, user)
            
            # Redirect to onboarding
            return redirect('users:onboarding')
            
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(request, 'users/register.html')
    
    return render(request, 'users/register.html')


@login_required
def onboarding(request):
    """Multi-step onboarding flow"""
    step = int(request.GET.get('step', 1))
    
    # Check if user already completed onboarding
    if hasattr(request.user, 'workspace') and request.user.workspace.onboarding_completed:
        return redirect('users:dashboard')
    
    if request.method == 'POST':
        if step == 1:
            # Save account type
            account_type = request.POST.get('account_type', 'personal')
            request.session['onboarding_account_type'] = account_type
            return redirect(reverse('users:onboarding') + '?step=2')
        
        elif step == 2:
            # Create workspace
            workspace_name = request.POST.get('workspace_name', '')
            account_type = request.session.get('onboarding_account_type') or 'personal'
            
            workspace, created = Workspace.objects.get_or_create(
                user=request.user,
                defaults={
                    'owner': request.user,
                    'name': workspace_name,
                    'account_type': account_type,
                    'plan': 'free'
                }
            )
            
            if not created:
                workspace.name = workspace_name
                workspace.account_type = account_type
                workspace.save()
            
            return redirect(reverse('users:onboarding') + '?step=3')
        
        elif step == 3:
            # Save plan selection
            plan = request.POST.get('plan', 'free')
            
            workspace = Workspace.objects.get(owner=request.user)
            workspace.plan = plan
            workspace.save()
            
            return redirect(reverse('users:onboarding') + '?step=4')
        
        elif step == 4:
            # Complete onboarding
            workspace = Workspace.objects.get(owner=request.user)
            workspace.onboarding_completed = True
            workspace.save()
            
            messages.success(request, 'Welcome to KwikChat! Your workspace is ready.')
            return redirect('users:dashboard')
    
    return render(request, 'users/onboarding.html', {
        'step': step
    })
