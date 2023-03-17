from django.urls import path
from . import views

app_name="botApi"
urlpatterns = [
    path('getreplies/',views.GetMessages.as_view(),name="get_replies"),
    path('newreply/',views.CreateReply.as_view(),name="new_replies"),

]