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
    # Wallet moved to 'payments' app
    # path('wallet/', feature_views.wallet, name='wallet'),
    # path('wallet/withdraw/', frontend_views.wallet_withdraw, name='wallet_withdraw'),
    path('reminders/', feature_views.reminders, name='reminders'),
    path('reminders/create/', frontend_views.create_reminder, name='create_reminder'),
    path('settings/', feature_views.settings, name='settings'),
    path('settings/profile/', feature_views.profile_settings, name='profile_settings'),
    path('settings/goals/', feature_views.goals_settings, name='goals_settings'),
    
    # Integrations
    path('integrations/whatsapp/connect/', integrations_views.connect_whatsapp, name='connect_whatsapp'),
    path('integrations/mailgun/connect/', integrations_views.connect_mailgun, name='connect_mailgun'),
    path('integrations/intasend/connect/', integrations_views.connect_intasend, name='connect_intasend'),
    path('integrations/disconnect/<str:integration_type>/', integrations_views.disconnect_integration, name='disconnect_integration'),

    # Marketing / value pages
    path('why/', views.why_mathia, name='why_mathia'),
    path('playbooks/', views.playbooks, name='playbooks'),
    path('pricing/', views.pricing, name='pricing'),
    path('trust/', views.trust, name='trust'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),
    path('workflows/', views.workflows_library, name='workflows_library'),
    path('updates/', views.updates, name='updates'),

    # Trial funnel
    path('trial/apply/', views.trial_apply, name='trial_apply'),
    path('trial/applications/', views.trial_applications, name='trial_applications'),
    path('trial/applications/<int:pk>/send/', views.send_trial_invite, name='send_trial_invite'),
    path('trial/activate/<str:token>/', views.activate_trial, name='activate_trial'),
]
