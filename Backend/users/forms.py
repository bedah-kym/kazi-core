from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from .models import UserProfile, GoalProfile, TrialApplication

User = get_user_model()

class CustomAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_messages = {
            'invalid_login': 'Please enter a correct username and password. Note that both fields may be case-sensitive.',
            'inactive': 'This account is inactive. Please contact support.',
            'invalid_username': 'Please enter a valid username.',
            'required': 'This field is required.',
            'too_short': 'This field must be at least %(min_length)d characters long.',
        }

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if username:
            if len(username) < 3:
                raise ValidationError(
                    self.error_messages['too_short'],
                    code='too_short',
                    params={'min_length': 3},
                )
        return username

    def clean(self):
        try:
            return super().clean()
        except ValidationError as e:
            # Add custom error handling here
            if 'username' in self.cleaned_data and not self.cleaned_data.get('password'):
                self.add_error('password', self.error_messages['required'])
            raise


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['bio', 'location', 'website', 'user_type', 'industry', 
                 'company_name', 'company_size', 'role', 'social_links', 
                 'notification_preferences', 'theme_preference', 'avatar']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'social_links': forms.Textarea(attrs={'rows': 4, 'placeholder': 'JSON format: {"twitter": "@handle"}', 'class': 'form-input font-mono'}),
            'notification_preferences': forms.Textarea(attrs={'rows': 4, 'class': 'form-input font-mono'}),
            'industry': forms.TextInput(attrs={'class': 'form-input'}),
            'company_name': forms.TextInput(attrs={'class': 'form-input'}),
            'role': forms.TextInput(attrs={'class': 'form-input'}),
        }

class GoalProfileForm(forms.ModelForm):
    class Meta:
        model = GoalProfile
        fields = ['goals', 'custom_goals', 'industry', 'skills', 
                 'experience_level', 'needs', 'custom_needs', 'use_cases', 
                 'roadmap', 'target_revenue', 'target_followers', 
                 'target_clients', 'target_email_subscribers', 'ai_personalization_enabled']
        widgets = {
            'goals': forms.Textarea(attrs={'rows': 3, 'class': 'form-input font-mono', 'placeholder': '["goal1", "goal2"]'}),
            'skills': forms.Textarea(attrs={'rows': 3, 'class': 'form-input font-mono'}),
            'needs': forms.Textarea(attrs={'rows': 3, 'class': 'form-input font-mono'}),
            'roadmap': forms.Textarea(attrs={'rows': 5, 'class': 'form-input font-mono'}),
            'custom_goals': forms.Textarea(attrs={'rows': 2, 'class': 'form-input'}),
            'custom_needs': forms.Textarea(attrs={'rows': 2, 'class': 'form-input'}),
        }


class TrialApplicationForm(forms.ModelForm):
    class Meta:
        model = TrialApplication
        fields = [
            'name', 'email', 'role', 'company', 'industry', 'team_size',
            'current_stack', 'primary_use_case', 'pain_points',
            'success_metric', 'budget_readiness', 'go_live_timeframe', 'heard_from'
        ]
        widgets = {
            'primary_use_case': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What do you want Mathia to handle first?'}),
            'pain_points': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Where are you stuck today?'}),
            'success_metric': forms.TextInput(attrs={'placeholder': 'E.g., ship 3 workflows/week, reduce follow-ups missed'}),
            'budget_readiness': forms.TextInput(attrs={'placeholder': 'Ready now / this quarter / exploring'}),
            'go_live_timeframe': forms.TextInput(attrs={'placeholder': 'E.g., this week, 30 days, later'}),
            'current_stack': forms.TextInput(attrs={'placeholder': 'Notion, Calendly, QuickBooks, X, Slack...'}),
            'heard_from': forms.TextInput(attrs={'placeholder': 'Referral, X, community, demo, etc.'}),
        }
