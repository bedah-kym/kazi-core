from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name='users'
urlpatterns = [
    path('login/', views.CustomLoginView.as_view(), name="login"),
    path('logout/', auth_views.LogoutView.as_view(template_name="users/logout.html"), name="logout"),
]