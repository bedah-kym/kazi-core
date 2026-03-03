"""
Django-Jazzmin Dashboard Configuration

This module provides dashboard widgets and metrics for the admin panel,
including key business metrics, recent activity, and quick actions.
"""

from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count, Sum, Q
from datetime import timedelta
from django.utils import timezone


def get_dashboard_widgets():
    """
    Generates dashboard widgets with key metrics and recent activity.

    Returns:
        list: Configuration for jazzmin dashboard widgets
    """

    # Import models at function call time to avoid circular imports
    from users.models import UserProfile, TrialApplication, Workspace
    from payments.models import JournalEntry, Dispute
    from workflows.models import UserWorkflow

    widgets = []

    # ==========================================
    # Key Metrics Widgets
    # ==========================================

    # User Stats Widget
    try:
        total_users = UserProfile.objects.count()
        active_workspaces = Workspace.objects.filter(is_active=True).count()
        trial_apps = TrialApplication.objects.filter(status='pending').count()

        widgets.append({
            'type': 'model_list',
            'title': 'User & Workspace Overview',
            'models': ['users.UserProfile', 'users.Workspace'],
            'app': 'users',
            'css_classes': {'card': 'col-md-4'},
        })
    except Exception:
        pass

    # Payment Stats Widget
    try:
        from payments.models import PaymentRequest

        # Get financial metrics
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_transactions = JournalEntry.objects.filter(
            created_at__gte=thirty_days_ago
        ).count()
        pending_disputes = Dispute.objects.filter(status='open').count()

        widgets.append({
            'type': 'model_list',
            'title': 'Payment Management',
            'models': ['payments.PaymentRequest', 'payments.JournalEntry'],
            'app': 'payments',
            'css_classes': {'card': 'col-md-4'},
        })
    except Exception:
        pass

    # Workflow Stats Widget
    try:
        active_workflows = UserWorkflow.objects.filter(
            status='active'
        ).count()

        widgets.append({
            'type': 'model_list',
            'title': 'Workflows',
            'models': ['workflows.UserWorkflow'],
            'app': 'workflows',
            'css_classes': {'card': 'col-md-4'},
        })
    except Exception:
        pass

    # ==========================================
    # Recent Activity Widgets
    # ==========================================

    # Recent Users
    try:
        widgets.append({
            'type': 'model_list',
            'title': 'Recent User Registrations',
            'models': ['users.UserProfile'],
            'app': 'users',
            'models_custom_list': [
                ('users.UserProfile', 'user__email'),
            ],
        })
    except Exception:
        pass

    # Recent Transactions
    try:
        widgets.append({
            'type': 'model_list',
            'title': 'Recent Transactions',
            'models': ['payments.JournalEntry'],
            'app': 'payments',
        })
    except Exception:
        pass

    # Trial Applications
    try:
        widgets.append({
            'type': 'model_list',
            'title': 'Trial Applications (Pending)',
            'models': ['users.TrialApplication'],
            'app': 'users',
        })
    except Exception:
        pass

    # Chatroom Activity
    try:
        from chatbot.models import Message

        widgets.append({
            'type': 'model_list',
            'title': 'Recent Chat Activity',
            'models': ['chatbot.Message'],
            'app': 'chatbot',
        })
    except Exception:
        pass

    # ==========================================
    # Quick Stats
    # ==========================================

    # Create stat widgets showing key metrics
    try:
        total_users = UserProfile.objects.count()
        active_workspaces = Workspace.objects.filter(is_active=True).count()
        trial_pending = TrialApplication.objects.filter(status='pending').count()

        stats_html = format_html(
            '<div class="row">'
            '<div class="col-md-3 text-center"><h5>Total Users</h5><h3 style="color: #3498db;">{}</h3></div>'
            '<div class="col-md-3 text-center"><h5>Active Workspaces</h5><h3 style="color: #2ecc71;">{}</h3></div>'
            '<div class="col-md-3 text-center"><h5>Pending Trials</h5><h3 style="color: #f39c12;">{}</h3></div>'
            '<div class="col-md-3 text-center"><h5>Open Disputes</h5><h3 style="color: #e74c3c;"></h3></div>'
            '</div>',
            total_users,
            active_workspaces,
            trial_pending,
        )

        try:
            open_disputes = Dispute.objects.filter(status='open').count()
            stats_html = format_html(
                '<div class="row">'
                '<div class="col-md-3 text-center"><h5>Total Users</h5><h3 style="color: #3498db;">{}</h3></div>'
                '<div class="col-md-3 text-center"><h5>Active Workspaces</h5><h3 style="color: #2ecc71;">{}</h3></div>'
                '<div class="col-md-3 text-center"><h5>Pending Trials</h5><h3 style="color: #f39c12;">{}</h3></div>'
                '<div class="col-md-3 text-center"><h5>Open Disputes</h5><h3 style="color: #e74c3c;">{}</h3></div>'
                '</div>',
                total_users,
                active_workspaces,
                trial_pending,
                open_disputes,
            )
        except Exception:
            pass

        widgets.insert(0, {
            'type': 'html',
            'title': 'Key Metrics',
            'content': stats_html,
            'css_classes': {'card': 'col-md-12'},
        })
    except Exception:
        pass

    return widgets


def get_dashboard_config():
    """
    Returns the complete dashboard configuration for Jazzmin.

    Add this to JAZZMIN_SETTINGS in settings.py:
    JAZZMIN_SETTINGS['dashboard_widgets'] = get_dashboard_config()

    Returns:
        list: Dashboard widget configurations
    """
    return get_dashboard_widgets()
