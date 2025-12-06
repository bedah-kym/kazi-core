from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import dashboard_views
from . import frontend_views
from . import auth_views as custom_auth

app_name='users'
urlpatterns = [
    # Authentication
    path('login/', views.CustomLoginView.as_view(), name="login"),
    path('logout/', auth_views.LogoutView.as_view(template_name="users/logout.html"), name="logout"),
    path('register/', custom_auth.register, name='register'),
    path('onboarding/', custom_auth.onboarding, name='onboarding'),
    
    # Frontend Pages
    path('dashboard/', dashboard_views.dashboard, name='dashboard'),
    path('wallet/', frontend_views.wallet_page, name='wallet'),
    path('wallet/withdraw/', frontend_views.wallet_withdraw, name='wallet_withdraw'),
    path('reminders/', frontend_views.reminders_page, name='reminders'),
    path('reminders/create/', frontend_views.create_reminder, name='create_reminder'),
    path('settings/', frontend_views.settings_page, name='settings'),
]
