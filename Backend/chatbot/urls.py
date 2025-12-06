
from django.urls import path
from . import views
from . import context_api

app_name="chatbot"
urlpatterns = [
    path('home/<int:room_name>/',views.home,name="bot-home"),
    path('redirect/',views.welcomepage,name="redirect_to_home"),
    
    # Context API
    path('api/rooms/<int:room_id>/context/', context_api.get_room_context, name='room-context'),
    path('api/rooms/<int:room_id>/notes/', context_api.add_note, name='add-note'),
]
