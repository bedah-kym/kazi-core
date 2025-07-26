from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

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