import json
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import Message, Member, Chatroom
from django.contrib.auth import get_user_model

User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            command = data.get("command", None)

            if command == "fetch_messages":
                await self.fetch_messages(data)
            elif command == "new_message":
                await self.new_message(data)
            elif command == "file_message":
                await self.file_message(data)
            else:
                await self.send_message({
                    'member': 'system',
                    'content': f"Unknown command: {command}",
                    'timestamp': str(timezone.now())
                })
        except Exception as e:
            print(f"Error in receive: {str(e)}")
            await self.send_message({
                'member': 'system',
                'content': "An error occurred processing your request",
                'timestamp': str(timezone.now())
            })

    async def fetch_messages(self, data):
        try:
            messages = await self.get_last_10_messages(data['chatid'])
            messages_json = [await self.message_to_json(message) for message in messages]
            content = {
                "command": "messages",
                "messages": messages_json
            }
            await self.send_message(content)
        except Exception as e:
            print(f"Error in fetch_messages: {str(e)}")
            await self.send_message({
                'member': 'system',
                'content': "Error fetching messages",
                'timestamp': str(timezone.now())
            })

    async def new_message(self, data):
        try:
            member_username = data['from']
            get_user = sync_to_async(User.objects.filter(username=member_username).first)
            member_user = await get_user()

            if not member_user:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "User not found.",
                    'timestamp': str(timezone.now())
                })
                return

            get_member = sync_to_async(Member.objects.filter(User=member_user).first)
            member = await get_member()

            if not member:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not a member of any group.",
                    'timestamp': str(timezone.now())
                })
                return

            create_message = sync_to_async(Message.objects.create)
            message = await create_message(
                member=member,
                content=data['message'],
                timestamp=timezone.now()
            )

            current_chat = await self.get_current_chatroom(data['chatid'])
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Chatroom not found.",
                    'timestamp': str(timezone.now())
                })
                return

            room_members = await self.get_chatroom_participants(current_chat)

            if member in room_members:
                await sync_to_async(current_chat.chats.add)(message)
                await sync_to_async(current_chat.save)()

                message_json = await self.message_to_json(message)
                content = {
                    "command": "new_message",
                    "message": message_json
                }
                await self.send_chat_message(content)
            else:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not authorized for this chat.",
                    'timestamp': str(timezone.now())
                })
        except Exception as e:
            print(f"Error in new_message: {str(e)}")
            await self.send_chat_message({
                'member': 'security system',
                'content': "Error processing message",
                'timestamp': str(timezone.now())
            })

    async def file_message(self, data):
        try:
            member_username = data['from']
            get_user = sync_to_async(User.objects.filter(username=member_username).first)
            member_user = await get_user()

            if not member_user:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "User not found.",
                    'timestamp': str(timezone.now())
                })
                return

            get_member = sync_to_async(Member.objects.filter(User=member_user).first)
            member = await get_member()

            if not member:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not a member of any group.",
                    'timestamp': str(timezone.now())
                })
                return

            file_data = data['file_data']
            file_name = data['file_name']
            file_path = default_storage.save(file_name, ContentFile(file_data.split(';base64,')[1].encode('utf-8')))
            file_url = default_storage.url(file_path)

            create_message = sync_to_async(Message.objects.create)
            message = await create_message(
                member=member,
                content=f"<a href='{file_url}' target='_blank'>{file_name}</a>",
                timestamp=timezone.now()
            )

            current_chat = await self.get_current_chatroom(data['chatid'])
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Chatroom not found.",
                    'timestamp': str(timezone.now())
                })
                return

            room_members = await self.get_chatroom_participants(current_chat)

            if member in room_members:
                await sync_to_async(current_chat.chats.add)(message)
                await sync_to_async(current_chat.save)()

                message_json = await self.message_to_json(message)
                content = {
                    "command": "new_message",
                    "message": message_json
                }
                await self.send_chat_message(content)
            else:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not authorized for this chat.",
                    'timestamp': str(timezone.now())
                })
        except Exception as e:
            print(f"Error in file_message: {str(e)}")
            await self.send_chat_message({
                'member': 'security system',
                'content': "Error processing file message",
                'timestamp': str(timezone.now())
            })

    async def send_chat_message(self, message):
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": message
            }
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def send_message(self, message):
        await self.send(text_data=json.dumps(message))

    @classmethod
    async def get_last_10_messages(cls, chatid):
        messages = Message.objects.filter(chatroom__id=chatid).order_by('-timestamp')[:10]
        return await sync_to_async(list)(messages)

    @classmethod
    async def get_current_chatroom(cls, chatid):
        get_chatroom = sync_to_async(Chatroom.objects.filter(id=chatid).first)
        return await get_chatroom()

    @classmethod
    async def get_chatroom_participants(cls, chat):
        participants = chat.participants.all()
        return await sync_to_async(list)(participants)

    async def message_to_json(self, message):
        get_username = sync_to_async(lambda: message.member.User.username)
        username = await get_username()
        return {
            'member': username,
            'content': message.content,
            'timestamp': str(message.timestamp)
        }
