from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import dashboard_views
from . import frontend_views
from . import feature_views
from . import auth_views as custom_auth
from . import integrations_views

app_name='users'
urlpatterns = [
    # Authentication
    path('login/', views.CustomLoginView.as_view(), name="login"),
    path('logout/', auth_views.LogoutView.as_view(template_name="users/logout.html"), name="logout"),
    path('register/', custom_auth.register, name='register'),
    path('onboarding/', custom_auth.onboarding, name='onboarding'),
    
    # Frontend Pages (with workspace guards)
    path('dashboard/', dashboard_views.dashboard, name='dashboard'),
    path('reminders/', feature_views.reminders, name='reminders'),
    path('reminders/create/', frontend_views.create_reminder, name='create_reminder'),
    path('settings/', feature_views.settings, name='settings'),
    path('settings/profile/', feature_views.profile_settings, name='profile_settings'),
    path('settings/goals/', feature_views.goals_settings, name='goals_settings'),
    path('rooms/list/', dashboard_views.list_rooms, name='list_rooms'),
    
    # Integrations
    path('integrations/whatsapp/connect/', integrations_views.connect_whatsapp, name='connect_whatsapp'),
    path('integrations/mailgun/connect/', integrations_views.connect_mailgun, name='connect_mailgun'),
    path('integrations/intasend/connect/', integrations_views.connect_intasend, name='connect_intasend'),
    path('integrations/disconnect/<str:integration_type>/', integrations_views.disconnect_integration, name='disconnect_integration'),
]
