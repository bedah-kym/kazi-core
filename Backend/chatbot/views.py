from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from .models import Chatroom,Message
import json 
from django.http import JsonResponse

from django.conf import settings
import os



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


def upload_file(request):
    if request.method == 'POST':
        try:
            # Retrieve the uploaded file from the request
            uploaded_file = request.FILES.get('file')

            if not uploaded_file:
                return JsonResponse({'error': 'No file provided'}, status=400)

            # Save the file to the uploads directory
            file_path = os.path.join(settings.MEDIA_ROOT, uploaded_file.name)
            with open(file_path, 'wb+') as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            # Construct the file URL
            file_url = os.path.join(settings.MEDIA_URL, uploaded_file.name)

            # Return the file URL in the JSON response
            return JsonResponse({'fileUrl': file_url}, status=200)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


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