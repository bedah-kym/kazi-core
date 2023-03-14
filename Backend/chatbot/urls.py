
from django.urls import path
from . import views

app_name="chatbot"
urlpatterns = [
    path('home/<int:room_name>/',views.home,name="bot-home"),
    path('redirect/',views.welcomepage,name="redirect_to_home"),

]
