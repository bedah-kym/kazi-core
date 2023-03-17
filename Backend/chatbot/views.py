from django.shortcuts import render,redirect,get_object_or_404,get_list_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from .models import Chatroom
from Api.models import MathiaReply
import json 

#@login_required
def home(request,room_name):
    chatrooms = Chatroom.objects.all()
    room = Chatroom.objects.get(id=room_name)
    room_members = room.participants.all()
    return render(
        request,"chatbot/chatbase.html",
        {
        "room_name":mark_safe(json.dumps(room_name)),
        "username":mark_safe(json.dumps(request.user.username)),
        "chatrooms":chatrooms,
        "room_members":room_members
        }
    )

def welcomepage(request):
    if request.user.is_authenticated:
        return redirect(reverse("chatbot:bot-home",kwargs={"room_name":2}))
    return redirect("users:login")


def get_last_10messages(chatid):
    chatroom = get_object_or_404(Chatroom,id=chatid)
    return chatroom.chats.order_by('-timestamp').all()


def get_current_chatroom(chatid):
    chatroom = get_object_or_404(Chatroom,id=chatid)
    return chatroom

def get_chatroom_participants(chatroom):
    return chatroom.participants.all()

def get_mathia_reply():#should return the json/dict message like in chatsocket
    content = MathiaReply.objects.all()[::1]
    return {
                'message': content.message,
                'from': content.sender,
                'command':content.command,
                "chatid": content.chatid
            }

