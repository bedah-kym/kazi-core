import json
from asgiref.sync import async_to_sync
from django.utils import timezone
from channels.generic.websocket import WebsocketConsumer
from .models import Message,Member
from Api.models import MathiaReply
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .views import(get_last_10messages,
                   get_current_chatroom,
                   get_chatroom_participants,
                   get_mathia_reply,
                   )

user=get_user_model()

class ChatConsumer(WebsocketConsumer):

    def fetch_messages(self,data):
        messages = get_last_10messages(chatid=data['chatid'])
        content = {
            "command":"messages",
            "messages":self.messages_to_json(messages)
        }
        self.send_message(content)

    def messages_to_json(self,messages):
        result =[]
        for message in messages:
            result.append(self.message_to_json(message))
        return result

    def message_to_json(self,message):
        return{
            'member':message.member.User.username,
            'content':message.content,
            'timestamp':str(message.timestamp)
        }
    

    
    def new_message(self,data):
        member = data['from']
        member_user=user.objects.filter(username=member)[0]
        member_user_id = member_user.id
        member_user = Member.objects.filter(User=member_user_id)[0]
        message=Message.objects.create(member=member_user,content=data['message'],timestamp=timezone.now())
        current_chat = get_current_chatroom(chatid=data['chatid'])
        room_members = get_chatroom_participants(current_chat)
        if member_user in room_members:
            current_chat.chats.add(message)
            current_chat.save()
            content={
                "command":"new_message",
                "message":self.message_to_json(message),  
            }
            
            self.send_chat_message(content)
        else:
            
            message={
                'member':'system error',
                'content':"sorry you arent authorized to chat here",
                'timestamp':str(message.timestamp)
            }
            content={
                "command":"new_message",
                "message":message,  
            }
            self.send_chat_message(content)

    command = {
        "fetch_messages":fetch_messages,
        "new_message":new_message
    }
    def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = "chat_%s" % self.room_name

        # Join room group
        async_to_sync(self.channel_layer.group_add)(
            self.room_group_name, self.channel_name
        )

        self.accept()

    def disconnect(self, close_code):
        # Leave room group
        async_to_sync(self.channel_layer.group_discard)(
            self.room_group_name, self.channel_name
        )
           
    def receive(self, text_data):
        # Receive message from WebSocket
        data = json.loads(text_data) 
        senders=['betawaysadmin','test3','test2','Huges']
        if data['command'] != "fetch_messages":
            if data['from'] in senders:  
                self.command[data["command"]](self,data)
                reply = get_mathia_reply()
                self.command[reply["command"]](self,reply)
            else:
                self.command[reply["command"]](self,reply)
        else:
            self.command[data["command"]](self,data)

    def send_chat_message(self,message):     # Send message to room group
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name, {
            "type": "chat_message",
            "message": message
            }
        )
    def send_message(self,message):
        self.send(text_data=json.dumps(message))
    
    def chat_message(self, event):          # Receive message from room group
        message = event["message"]
        # Send message to WebSocket
        self.send(text_data=json.dumps(message))