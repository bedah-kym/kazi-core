from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
import json
import re
from .models import UserProfile, GoalProfile, TrialApplication

User = get_user_model()

def _normalize_list_value(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith('[') or text.startswith('{'):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return _normalize_list_value(parsed)
        parts = re.split(r'[,\n;]+', text)
        return [part.strip() for part in parts if part.strip()]
    text = str(value).strip()
    return [text] if text else []

def _format_list_value(items):
    if not items:
        return ''
    return '\n'.join(str(item).strip() for item in items if str(item).strip())

def _parse_roadmap_value(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if ':' in line:
                timeframe, goals_text = line.split(':', 1)
            elif '-' in line:
                timeframe, goals_text = line.split('-', 1)
            else:
                timeframe, goals_text = 'Anytime', line
            goals = _normalize_list_value(goals_text)
            entries.append({
                'quarter': timeframe.strip(),
                'goals': goals,
                'status': 'planned',
            })
        return entries
    return []

def _format_roadmap_value(entries):
    if not entries:
        return ''
    lines = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        timeframe = entry.get('quarter') or entry.get('timeframe') or entry.get('period') or 'Anytime'
        goals = entry.get('goals') or []
        goals_text = ', '.join(str(goal).strip() for goal in goals if str(goal).strip())
        if goals_text:
            lines.append(f"{timeframe}: {goals_text}")
        else:
            lines.append(str(timeframe))
    return '\n'.join(lines)
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
    email_notifications = forms.BooleanField(
        required=False,
        label='Email notifications',
        help_text='Product updates, alerts, and weekly digests.'
    )
    push_notifications = forms.BooleanField(
        required=False,
        label='Push notifications'
    )
    digest_frequency = forms.ChoiceField(
        required=False,
        label='Digest frequency',
        choices=[
            ('off', 'Off'),
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly'),
        ]
    )

    class Meta:
        model = UserProfile
        fields = [
            'bio', 'location', 'website', 'user_type', 'industry',
            'company_name', 'company_size', 'role', 'twitter_handle',
            'linkedin_url', 'github_url', 'theme_preference', 'avatar'
        ]
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'industry': forms.TextInput(attrs={'class': 'form-input'}),
            'company_name': forms.TextInput(attrs={'class': 'form-input'}),
            'role': forms.TextInput(attrs={'class': 'form-input'}),
            'twitter_handle': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '@handle'}),
            'linkedin_url': forms.URLInput(attrs={'class': 'form-input', 'placeholder': 'https://linkedin.com/in/...'}),
            'github_url': forms.URLInput(attrs={'class': 'form-input', 'placeholder': 'https://github.com/...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-check-input')
                continue
            field.widget.attrs.setdefault('class', 'form-input')

        social_links = (self.instance.social_links or {}) if self.instance else {}
        if not self.initial.get('twitter_handle') and social_links.get('twitter'):
            self.initial['twitter_handle'] = social_links.get('twitter')
        if not self.initial.get('linkedin_url') and social_links.get('linkedin'):
            self.initial['linkedin_url'] = social_links.get('linkedin')
        if not self.initial.get('github_url') and social_links.get('github'):
            self.initial['github_url'] = social_links.get('github')

        prefs = (self.instance.notification_preferences or {}) if self.instance else {}
        self.fields['email_notifications'].initial = prefs.get('email_notifications', True)
        self.fields['push_notifications'].initial = prefs.get('push_notifications', False)
        self.fields['digest_frequency'].initial = prefs.get('digest_frequency', 'weekly')

    def save(self, commit=True):
        instance = super().save(commit=False)
        social_links = dict(instance.social_links or {})
        twitter = (self.cleaned_data.get('twitter_handle') or '').strip()
        linkedin = (self.cleaned_data.get('linkedin_url') or '').strip()
        github = (self.cleaned_data.get('github_url') or '').strip()

        if twitter:
            social_links['twitter'] = twitter
        else:
            social_links.pop('twitter', None)
        if linkedin:
            social_links['linkedin'] = linkedin
        else:
            social_links.pop('linkedin', None)
        if github:
            social_links['github'] = github
        else:
            social_links.pop('github', None)

        prefs = dict(instance.notification_preferences or {})
        prefs['email_notifications'] = bool(self.cleaned_data.get('email_notifications'))
        prefs['push_notifications'] = bool(self.cleaned_data.get('push_notifications'))
        prefs['digest_frequency'] = self.cleaned_data.get('digest_frequency') or prefs.get('digest_frequency', 'weekly')
        instance.social_links = social_links
        instance.notification_preferences = prefs

        if commit:
            instance.save()
        return instance

class GoalProfileForm(forms.ModelForm):
    goals = forms.CharField(
        required=False,
        label='Professional goals',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'class': 'form-input',
            'placeholder': 'Increase revenue\nBook more clients\nLaunch a new offer'
        })
    )
    skills = forms.CharField(
        required=False,
        label='Skills & expertise',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-input',
            'placeholder': 'Brand strategy, Python, Client success'
        })
    )
    needs = forms.CharField(
        required=False,
        label='Where do you want help?',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-input',
            'placeholder': 'Lead generation\nClient follow-ups\nWeekly reporting'
        })
    )
    use_cases = forms.CharField(
        required=False,
        label='How will you use Mathia?',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-input',
            'placeholder': 'Travel planning, Invoicing, Calendar scheduling'
        })
    )
    roadmap = forms.CharField(
        required=False,
        label='Roadmap (optional)',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'class': 'form-input',
            'placeholder': 'Q1 2026: Launch newsletter, Hire a VA\nQ2 2026: Expand into new market'
        })
    )

    class Meta:
        model = GoalProfile
        fields = ['goals', 'custom_goals', 'industry', 'skills', 
                 'experience_level', 'needs', 'custom_needs', 'use_cases', 
                 'roadmap', 'target_revenue', 'target_followers', 
                 'target_clients', 'target_email_subscribers', 'ai_personalization_enabled']
        widgets = {
            'custom_goals': forms.Textarea(attrs={'rows': 2, 'class': 'form-input'}),
            'custom_needs': forms.Textarea(attrs={'rows': 2, 'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-check-input')
                continue
            field.widget.attrs.setdefault('class', 'form-input')

        if self.instance:
            self.initial.setdefault('goals', _format_list_value(self.instance.goals))
            self.initial.setdefault('skills', _format_list_value(self.instance.skills))
            self.initial.setdefault('needs', _format_list_value(self.instance.needs))
            self.initial.setdefault('use_cases', _format_list_value(self.instance.use_cases))
            self.initial.setdefault('roadmap', _format_roadmap_value(self.instance.roadmap))

    def clean_goals(self):
        return _normalize_list_value(self.cleaned_data.get('goals'))

    def clean_skills(self):
        return _normalize_list_value(self.cleaned_data.get('skills'))

    def clean_needs(self):
        return _normalize_list_value(self.cleaned_data.get('needs'))

    def clean_use_cases(self):
        return _normalize_list_value(self.cleaned_data.get('use_cases'))

    def clean_roadmap(self):
        return _parse_roadmap_value(self.cleaned_data.get('roadmap'))


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
