from django.shortcuts import render,redirect,get_object_or_404,get_list_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from .models import Chatroom,Message
from Api.models import MathiaReply
import json 


@login_required
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


"""def get_mathia_reply():#should return the dict message like in chatsocket
    
    content = MathiaReply.objects.last()
    message = content.message
    sender = content.sender
    command = content.command
    chatid = content.chatid
    return {
                'message': message,
                'from': sender,
                'command':command,
                "chatid": chatid
    }"""

def get_mathia_reply():
    """ this is a feature to connect a bot for a specific room using botlibre api so
    users can talk to this bot.
    """
    #should return the dict message like in chatsocket
    import requests
    text=Message.objects.last()
    text= text.content
    url= 'https://www.botlibre.com/rest/json/chat'
    r = requests.post(url,json=(
        {"application":"2127403001275571408", "instance":"165","message":text}
        ))
    reply=r.json()
    message = reply['message']
    sender = 'mathia'
    command = "new_message"
    chatid = 3
    return {
                'message': message,
                'from': sender,
                'command':command,
                "chatid": chatid
    }