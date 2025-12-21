
from django.urls import path
from . import views
from . import context_api
from . import message_actions
from . import voice_views

app_name="chatbot"
urlpatterns = [
    path('home/<int:room_name>/',views.home,name="bot-home"),
    path('redirect/',views.welcomepage,name="redirect_to_home"),
    path('create/', views.create_room, name="create_room"),
    path('invite/', views.invite_user, name='invite_user'),
    
    # Context API
    path('api/rooms/<int:room_id>/context/', context_api.get_room_context, name='room-context'),
    path('api/rooms/<int:room_id>/notes/', context_api.add_note, name='add-note'),
    
    # Message Actions API
    path('api/rooms/<int:room_id>/messages/<int:message_id>/pin/', message_actions.pin_message_to_notes, name='pin-message'),
    path('api/rooms/<int:room_id>/messages/<int:message_id>/reply/', message_actions.reply_to_message, name='reply-message'),
    path('api/rooms/<int:room_id>/messages/<int:message_id>/retry/', message_actions.retry_ai_message, name='retry-message'),
    path('api/rooms/<int:room_id>/documents/upload/', message_actions.upload_document_to_ai, name='upload-document'),
    path('api/rooms/<int:room_id>/documents/quota/', message_actions.get_upload_quota, name='upload-quota'),
    
    # Voice API
    path('api/rooms/<int:room_id>/voice/upload/', voice_views.upload_voice_note, name='upload-voice-note'),
    path('api/messages/<int:message_id>/voice/status/', voice_views.get_voice_status, name='voice-status'),
]
