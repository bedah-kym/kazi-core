import json
import traceback
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import Message, Member, Chatroom
from django.contrib.auth import get_user_model
import os
from django_redis import get_redis_connection
from django.core.cache import cache
from base64 import b64encode, b64decode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidKey
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    # Define constants for key rotation
    KEY_ROTATION_INTERVAL = 36000 * 10 # Rotate key every 100 hours
    MESSAGES_BEFORE_ROTATION = 1000 # Rotate key after 1000 messages

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aes_gcm = None 
        # Initialize key rotation attributes
        self.messages_since_rotation = 0
        self.last_key_rotation = timezone.now()

    async def connect(self):
        # 1. Auth check
        if not self.scope["user"].is_authenticated:
            await self.close(code=4001)
            return

        # 2. Room setup
        self.room_name       = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"

        # 3. Secure session init
        initialized = await self.initialize_secure_session(self.room_name)
        if not initialized:
            await self.close(code=4002)
            return
        # 4. Check if the room exists
        current_chat = await self.get_current_chatroom(self.room_name)
        if not current_chat:
            await self.close(code=4003)
            return
        # 4. Join group
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # presence via Redis SET
        redis = get_redis_connection("default")  # low-level redis-py client
        key = f"online:{self.room_group_name}"
        user = self.scope["user"].username
        await sync_to_async(redis.sadd)(key, user)
        
        # Broadcast to others that you’re online
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "presence_update",
                "user": self.scope["user"].username,
                "status": "online",
            }
        )
        
        await self.accept()
        
        redis = get_redis_connection("default")
        key = f"online:{self.room_group_name}"
        raw = await sync_to_async(redis.smembers)(key)
        online_users = [u.decode() if isinstance(u, bytes) else u for u in raw]

        await self.send(text_data=json.dumps({
            "command": "presence_snapshot",
            "online": online_users,
        }))

        
    async def presence_update(self, event):
        await self.send_message({
            "command": "presence",
            "user":    event["user"],
            "status":  event["status"],
        })

    @sync_to_async
    def get_chatroom_key(self, room_id):
        """Fetches the Chatroom and its encryption key from the database."""
        try:
            chatroom = Chatroom.objects.get(id=room_id)
            return chatroom.encryption_key
        except Chatroom.DoesNotExist:
            logger.error(f"Chatroom with id {room_id} not found.")
            return None

    async def initialize_secure_session(self, room_id):
        """Initialize encryption using the chatroom's shared key."""
        encoded_key = await self.get_chatroom_key(room_id)

        if not encoded_key:
            return False

        try:
            session_key = b64decode(encoded_key.encode('utf-8'))
            self.aes_gcm = AESGCM(session_key)
            return True
        except Exception as e:
            logger.error(f"Failed to create AESGCM instance from key: {e}")
            return False

    async def encrypt_message(self, message_data):
        """Encrypt message data with proper base64 handling"""
        try:
            # Generate proper length nonce (12 bytes is recommended for GCM)
            nonce = os.urandom(12)
            message_bytes = json.dumps(message_data).encode('utf-8')
            
            encrypted_data = self.aes_gcm.encrypt(
                nonce,
                message_bytes,
                None
            )

            # Ensure proper base64 encoding with padding
            def encode_base64(data):
                return b64encode(data).decode('utf-8').rstrip('=') + '=' * (-len(data) % 4)

            return {
                'data': encode_base64(encrypted_data),
                'nonce': encode_base64(nonce)
            }
        except Exception as e:
            logger.error(f"Encryption error: {str(e)}")
            return None

    async def decrypt_message(self, encrypted_data, nonce):
        """Decrypt message data with improved base64 and nonce handling"""
        try:
            # Normalize and validate base64 input
            def normalize_base64(s):
                if not isinstance(s, str):
                    return None
                # Remove whitespace and normalize padding
                s = s.strip().replace(' ', '+')
                # Add padding if needed
                padding = 4 - (len(s) % 4)
                if padding < 4:
                    s += '=' * padding
                return s

            # Normalize inputs
            encrypted_data = normalize_base64(encrypted_data)
            nonce = normalize_base64(nonce)

            if not encrypted_data or not nonce:
                logger.error("Invalid base64 input")
                return None

            try:
                encrypted_bytes = b64decode(encrypted_data)
                nonce_bytes = b64decode(nonce)

                # Validate nonce length
                if not (8 <= len(nonce_bytes) <= 128):
                    logger.error(f"Invalid nonce length: {len(nonce_bytes)}")
                    return None

                decrypted_data = self.aes_gcm.decrypt(
                    nonce_bytes,
                    encrypted_bytes,
                    None
                )
                return json.loads(decrypted_data.decode('utf-8'))
            except InvalidKey:
                logger.error("Decryption failed: Invalid key or MAC.")
                return None
            except Exception as e:
                logger.error(f"Decryption operation failed: {str(e)}")
                return None

        except Exception as e:
            logger.error(f"General decryption error: {str(e)}")
            return None

    async def disconnect(self, close_code):
        # 1. Leave the chat group
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # 2. Remove from Redis set of online users
        cache_key = f"online:{self.room_group_name}"
        redis = get_redis_connection("default")
        await sync_to_async(redis.srem)(cache_key, self.scope["user"].username)

        # 3. Broadcast offline status
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "presence_update",
                "user":   self.scope["user"].username,
                "status": "offline",
            }
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            command = data.get("command", None)
            
            if command == 'typing':
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'typing_message',
                        'from': data.get('from'),
                    }
                )
                return
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
            logger.error(f"Error in receive: {str(e)}")
            await self.send_message({
                'member': 'system',
                'content': "An error occurred processing your request",
                'timestamp': str(timezone.now())
            })

    async def fetch_messages(self, data):
        try:
            messages = await self.get_last_10_messages(data['chatid'])
            # Let message_to_json handle decryption & formatting
            messages_json = [await self.message_to_json(m) for m in messages]
            await self.send_message({
                'command': 'messages',
                'messages': messages_json
            })
        except Exception as e:
            logger.error(f"Error in fetch_messages: {str(e)}")
            await self.send_message({
                'member': 'system',
                'content': 'Error fetching messages',
                'timestamp': str(timezone.now())
            })

    async def check_rate_limit(self, user_id):
        """Basic rate limiting"""
        RATE_LIMIT = 30  # messages per minute
        current_minute = timezone.now().strftime('%Y-%m-%d-%H-%M')
        cache_key = f"rate_limit:{user_id}:{current_minute}"
        
        # Using Django's cache framework instead of Redis for simplicity
        from django.core.cache import cache
        current = cache.get(cache_key, 0)
        if current >= RATE_LIMIT:
            return False
        cache.set(cache_key, current + 1, 60)  # Expire after 60 seconds
        return True

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

            # Rate limiting check
            if not await self.check_rate_limit(member_user.id):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Rate limit exceeded. Please wait a moment.",
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

            # Sanitize and validate message content
            message_content = data.get('message', '').strip()
            if not message_content or len(message_content) > 5000:  # Reasonable limit
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid message content.",
                    'timestamp': str(timezone.now())
                })
                return

            # Check for key rotation
            await self.check_key_rotation()

            # Encrypt the message content
            encrypted_message = await self.encrypt_message({
                'content': message_content,
                'timestamp': str(timezone.now())
            })

            if not encrypted_message:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Message encryption failed.",
                    'timestamp': str(timezone.now())
                })
                return

            create_message = sync_to_async(Message.objects.create)
            # store a JSON blob containing both parts
            payload = json.dumps({
                'data':     encrypted_message['data'],
                'nonce':    encrypted_message['nonce'],
            })
            message = await create_message(
                member=member,
                content=payload,
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
            logger.error(f"Error in new_message: {str(e)}")
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

            # Rate limiting check
            if not await self.check_rate_limit(member_user.id):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Rate limit exceeded for file uploads. Please wait.",
                    'timestamp': str(timezone.now())
                })
                return

            # Validate file size and type
            file_data = data.get('file_data', '')
            file_name = data.get('file_name', '').lower()
            
            # Basic file validation
            if not file_data or not file_name:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid file data.",
                    'timestamp': str(timezone.now())
                })
                return

            # Check file extension
            allowed_extensions = {'.txt', '.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'}
            if not any(file_name.endswith(ext) for ext in allowed_extensions):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Unsupported file type.",
                    'timestamp': str(timezone.now())
                })
                return

            # Check file size (limit to 5MB)
            file_size = len(file_data) * 3/4  # Approximate size for base64
            if file_size > 5 * 1024 * 1024:  # 5MB
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "File too large. Maximum size is 5MB.",
                    'timestamp': str(timezone.now())
                })
                return

            file_path = default_storage.save(file_name, ContentFile(file_data.split(';base64,')[1].encode('utf-8')))
            file_url = default_storage.url(file_path)

            # Encrypt the file message content
            encrypted_message = await self.encrypt_message({
                'content': f"<a href='{file_url}' target='_blank'>{file_name}</a>",
                'timestamp': str(timezone.now())
            })

            create_message = sync_to_async(Message.objects.create)
            # Store payload for file messages as well
            payload = json.dumps({
                'data':     encrypted_message['data'],
                'nonce':    encrypted_message['nonce'],
            })
            message = await create_message(
                member=member_user,
                content=payload,
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

            if member_user in room_members:
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
            logger.error(f"Error in file_message: {str(e)}")
            await self.send_chat_message({
                'member': 'security system',
                'content': "Error processing file",
                'timestamp': str(timezone.now())
            })

    async def typing_message(self, event):
        # fan out typing to all group members
        await self.send(text_data=json.dumps({
            "command": "typing",
            "from":    event["from"],
        }))

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

    async def check_key_rotation(self):
        """Check if key rotation is needed"""
        current_time = timezone.now()
        self.messages_since_rotation += 1
        
        if ((current_time - self.last_key_rotation).seconds >= self.KEY_ROTATION_INTERVAL or 
            self.messages_since_rotation >= self.MESSAGES_BEFORE_ROTATION):
            # Pass the current room_name to re-initialize the secure session
            await self.initialize_secure_session(self.room_name)
            self.last_key_rotation = current_time
            self.messages_since_rotation = 0

    async def message_to_json(self, message):
        """Decrypts message content before sending to the client."""
        try:
            username = await sync_to_async(lambda: message.member.User.username)()
            final_content = "Error: Could not decrypt message." # default error message

            db_content = message.content
            try:
                # The content from DB should be a JSON string with 'data' and 'nonce'
                parsed_payload = json.loads(db_content)
                if isinstance(parsed_payload, dict) and 'data' in parsed_payload and 'nonce' in parsed_payload:
                    # Decrypt the payload from the database
                    decrypted_payload = await self.decrypt_message(parsed_payload['data'], parsed_payload['nonce'])
                    # The decrypted content is also a dict, get the actual message from it
                    if decrypted_payload and 'content' in decrypted_payload:
                        final_content = decrypted_payload['content']
                else:
                    # This handles old messages that might not be encrypted
                    final_content = db_content
            except (json.JSONDecodeError, TypeError):
                # This handles the case where the content is not JSON (e.g., old plaintext messages)
                final_content = db_content

            return {
                'member': username,
                'content': final_content,
                'timestamp': str(message.timestamp)
            }
        except Exception as e:
            logger.error(f"Error in message_to_json: {e}")
            return {
                'member': 'system',
                'content': 'Error processing message',
                'timestamp': str(timezone.now())
            }