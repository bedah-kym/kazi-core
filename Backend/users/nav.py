"""Central app-shell navigation config.

Single source of truth for the global sidebar. The shell context processor
resolves each entry's URL and marks the active item so no template hard-codes
nav links.
"""
from __future__ import annotations

NAV_SECTIONS = [
    {
        "label": "Workspace",
        "items": [
            {"key": "dashboard", "label": "Dashboard", "icon": "fa-home", "url": "users:dashboard"},
            {"key": "chats", "label": "Chats", "icon": "fa-comment-dots", "url": "chatbot:redirect_to_home"},
        ],
    },
    {
        "label": "Tools",
        "items": [
            {"key": "workflows", "label": "Automations", "icon": "fa-diagram-project", "url": "workflows:workflows_list"},
            {"key": "wallet", "label": "Wallet", "icon": "fa-wallet", "url": "payments:wallet_dashboard"},
            {"key": "reminders", "label": "Reminders", "icon": "fa-clock", "url": "users:reminders"},
            {"key": "notifications", "label": "Notifications", "icon": "fa-bell", "url": "notifications:notification-center"},
            {"key": "travel", "label": "Travel", "icon": "fa-plane", "url": "travel:plan_trip"},
        ],
    },
    {
        "label": "System",
        "items": [
            {"key": "settings", "label": "Settings", "icon": "fa-gear", "url": "users:settings"},
        ],
    },
]

# url_name -> nav key, for the "users" namespace where several pages coexist.
_USERS_URL_KEYS = {
    "dashboard": "dashboard",
    "reminders": "reminders",
    "create_reminder": "reminders",
    "settings": "settings",
    "profile_settings": "settings",
    "goals_settings": "settings",
}

# namespace -> nav key, for single-module namespaces.
_NAMESPACE_KEYS = {
    "chatbot": "chats",
    "workflows": "workflows",
    "payments": "wallet",
    "notifications": "notifications",
    "travel": "travel",
}


def active_nav_key(request) -> str:
    """Return the nav key matching the current request, or '' when unknown."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    namespace = match.namespace or ""
    if namespace == "users":
        return _USERS_URL_KEYS.get(match.url_name or "", "")
    return _NAMESPACE_KEYS.get(namespace, "")
