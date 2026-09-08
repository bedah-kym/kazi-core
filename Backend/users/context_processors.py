"""Context processor providing workspace + resolved nav to shell templates."""
from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from .nav import NAV_SECTIONS, active_nav_key


def shell_context(request):
    """Inject `workspace` and `nav_sections` for the app shell."""
    workspace = None
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        try:
            workspace = user.workspace
        except Exception:
            workspace = None

    active = active_nav_key(request)
    sections = []
    for section in NAV_SECTIONS:
        items = []
        for item in section["items"]:
            try:
                url = reverse(item["url"])
            except NoReverseMatch:
                url = "#"
            items.append({
                "key": item["key"],
                "label": item["label"],
                "icon": item["icon"],
                "url": url,
                "active": item["key"] == active,
            })
        sections.append({"label": section["label"], "items": items})

    return {"workspace": workspace, "nav_sections": sections}
