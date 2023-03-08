
from django.urls import path
from . import views

app_name="chatbot"
urlpatterns = [
    path('home/',views.home,name="bot-home"),
]
