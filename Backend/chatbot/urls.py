
from django.urls import path
from . import views

app_name="chatbot"
urlpatterns = [
    path('home/<str:room_name>/',views.home,name="bot-home"),
]
