import json
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import Message, Member, Chatroom
from django.contrib.auth import get_user_model
import os
from base64 import b64encode, b64decode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidKey
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_key = None
        self.aes_gcm = None
        self.messages_since_rotation = 0
        self.last_key_rotation = timezone.now()
        self.KEY_ROTATION_INTERVAL = 3600  # 1 hour
        self.MESSAGES_BEFORE_ROTATION = 100

    async def connect(self):
        try:
            # Verify authentication
            if not self.scope["user"].is_authenticated:
                await self.close(code=4001)
                return

            # Initialize secure session
            await self.initialize_secure_session()
            
            self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
            self.room_group_name = f"chat_{self.room_name}"
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
            await self.accept()
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            await self.close(code=4000)

    async def initialize_secure_session(self):
        """Initialize encryption for the session"""
        self.session_key = AESGCM.generate_key(bit_length=256)
        self.aes_gcm = AESGCM(self.session_key)

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
            except Exception as e:
                logger.error(f"Decryption operation failed: {str(e)}")
                return None

        except Exception as e:
            logger.error(f"General decryption error: {str(e)}")
            return None

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

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
            messages_json = [await self.message_to_json(message) for message in messages]
            content = {
                "command": "messages",
                "messages": messages_json
            }
            await self.send_message(content)
        except Exception as e:
            logger.error(f"Error in fetch_messages: {str(e)}")
            await self.send_message({
                'member': 'system',
                'content': "Error fetching messages",
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
            message = await create_message(
                member=member,
                content=encrypted_message['data'],
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
            await self.initialize_secure_session()
            self.last_key_rotation = current_time
            self.messages_since_rotation = 0

    async def message_to_json(self, message):
        """Convert message to JSON with improved decryption error handling"""
        try:
            get_username = sync_to_async(lambda: message.member.User.username)
            username = await get_username()
            
            # Handle legacy messages that might not be encrypted
            content = message.content
            if isinstance(content, str):
                try:
                    # Try to parse as JSON first
                    msg_data = None
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and 'data' in parsed and 'nonce' in parsed:
                            msg_data = parsed        
                        else:
                            # Try the content itself as the encrypted data
                            decrypted = await self.decrypt_message(content, content)
                            if decrypted:
                                content = decrypted.get('content', content)
                    except json.JSONDecodeError:
                        # Try direct decryption as fallback
                        decrypted = await self.decrypt_message(content, content)
                        if decrypted:
                            content = decrypted.get('content', content)

                    # Handle properly formatted encrypted message
                    if msg_data:
                        decrypted = await self.decrypt_message(msg_data['data'], msg_data['nonce'])
                        if decrypted:
                            content = decrypted.get('content', content)

                except Exception as e:
                    logger.debug(f"Decryption attempt failed: {str(e)}")
                    # Keep original content if decryption fails
                    pass
            
            return {
                'member': username,
                'content': content,
                'timestamp': str(message.timestamp)
            }
        except Exception as e:
            logger.error(f"Error in message_to_json: {str(e)}")
            return {
                'member': 'system',
                'content': 'Error processing message',
                'timestamp': str(timezone.now())
            }
