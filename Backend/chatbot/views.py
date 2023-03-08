from django.shortcuts import render
from django.contrib.auth.decorators import login_required

#@login_required
def home(request,room_name):
    
    return render(request,"chatbot/chatbase.html",{"room_name":room_name})