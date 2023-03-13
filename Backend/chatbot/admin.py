from django.contrib import admin
from .models import Message,Member,Chatroom

admin.site.register(Message)
admin.site.register(Member)
admin.site.register(Chatroom)
