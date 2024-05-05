from Api.models import MathiaReply
from django.db.models.signals import pre_save
from django.dispatch import receiver
import requests
from .models import Message

"""@receiver(pre_save, sender=Message)

def reply_id(instance,**kwargs):
    text= instance.content
    url ="http://127.0.0.1:8800/api/chatterbot/"
    r = requests.post(url,json=({'text': text,}))
    return id"""
    
""" here we can send a post request to mathias Api when the Message model is saved so that
    mathia can send post request back to our api which will be saved and displayed in the web socket
    speed is key,we need a sleep function to wait for this apis to talk dont sleep too long or too little
    
"""
