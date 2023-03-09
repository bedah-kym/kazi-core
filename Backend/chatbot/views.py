from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
import json 

#@login_required
def home(request,room_name):
    return render(
        request,"chatbot/chatbase.html",
        {
        "room_name":mark_safe(json.dumps(room_name)),
        "username":mark_safe(json.dumps(request.user.username))
        }
    )