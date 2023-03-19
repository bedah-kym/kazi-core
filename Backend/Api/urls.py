from django.urls import path
from . import views

app_name="botApi"
urlpatterns = [
    path('getreplies/<int:room>/',views.GetMessage.as_view(),name="get_replies"),
    path('getmessages/<int:room>/',views.GetAllMessages.as_view(),name="get_messages"),
    path('newreply/',views.CreateReply.as_view(),name="new_replies"),

]