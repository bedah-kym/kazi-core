from django.shortcuts import render, redirect
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.core.cache import cache
from .forms import CustomAuthenticationForm
from django.http import HttpResponseRedirect
from django.urls import reverse

class CustomLoginView(LoginView):
    form_class = CustomAuthenticationForm
    template_name = 'users/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        username = self.request.POST.get('username', '')
        if username:
            # Check rate limiting
            key = f'login_attempts_{username}'
            attempts = cache.get(key, 0)
            if attempts >= 5:  # 5 attempts max
                messages.error(self.request, 'Too many login attempts. Please try again later.')
                context['form'].add_error(None, 'Too many login attempts')
        return context

    def form_valid(self, form):
        username = form.cleaned_data.get('username')
        # Reset rate limiting on successful login
        cache.delete(f'login_attempts_{username}')
        return super().form_valid(form)

    def form_invalid(self, form):
        username = self.request.POST.get('username', '')
        if username:
            # Increment rate limiting counter
            key = f'login_attempts_{username}'
            attempts = cache.get(key, 0)
            cache.set(key, attempts + 1, 300)  # 5 minutes timeout
        return super().form_invalid(form)
