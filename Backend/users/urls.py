from django.urls import path
from django.contrib.auth import views

app_name='users'
urlpatterns = [
    path('login/',views.LoginView.as_view(template_name="users/login.html"),name="login"),
    path('logout/',views.LogoutView.as_view(template_name="users/logout.html"),name="logout"),
]