from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("api/", views.notification_list, name="notification-list"),
    path("api/counts/", views.notification_counts, name="notification-counts"),
    path("api/<int:pk>/read/", views.mark_read, name="mark-read"),
    path("api/read-all/", views.mark_all_read, name="mark-all-read"),
    path("api/<int:pk>/dismiss/", views.dismiss, name="dismiss"),
]
