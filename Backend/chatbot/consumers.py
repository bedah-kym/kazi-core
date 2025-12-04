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
import asyncio
from .models import Message, Member, Chatroom, UserModerationStatus, ModerationBatch
from .tasks import moderate_message_batch, generate_ai_response
from orchestration.intent_parser import parse_intent
from django.conf import settings 
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
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
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

        # 5. Join group FIRST
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        
        # 6. Update presence in Redis ATOMICALLY
        redis = get_redis_connection("default")
        key = f"online:{self.room_group_name}"
        user = self.scope["user"].username

        # Atomic presence update
        await sync_to_async(redis.srem)(key, user)
        await sync_to_async(redis.sadd)(key, user)
        
        # Set last_seen timestamp
        last_seen_key = f"lastseen:{user}"
        current_time = timezone.now().isoformat()
        await sync_to_async(redis.set)(last_seen_key, current_time)

        # 7. Accept connection
        await self.accept()

        # 8. Broadcast online status to ALL other users
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "presence_update",
                "user": user,
                "status": "online",
                "last_seen": current_time,
            }
        )

        # 9. Small delay to ensure broadcast propagates
        try:
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # 10. Build FRESH presence snapshot
        presence = []
        try:
            participants = await self.get_chatroom_participants(current_chat)
            
            # Re-read Redis to get absolute latest state
            raw_online = await sync_to_async(redis.smembers)(key)
            online_set = set(u.decode() if isinstance(u, bytes) else u for u in raw_online)
            
            logger.debug(f"Building presence for {user}: {len(participants)} participants, {len(online_set)} online")
            
            for member in participants:
                try:
                    # CRITICAL: Must wrap DB access in sync_to_async
                    uname = await sync_to_async(lambda m: m.User.username)(member)
                except Exception as e:
                    logger.error(f"Could not get username from member: {e}")
                    continue
                
                # Check if user is in the online set
                is_online = uname in online_set
                
                # Fetch last_seen from Redis
                ls = await sync_to_async(redis.get)(f"lastseen:{uname}")
                if isinstance(ls, bytes):
                    try:
                        ls = ls.decode()
                    except Exception:
                        ls = None
                
                # Force current connecting user to online status
                if uname == user:
                    is_online = True
                    ls = current_time
                
                status = 'online' if is_online else 'offline'
                presence.append({
                    "user": uname, 
                    "status": status, 
                    "last_seen": ls
                })
                
                logger.debug(f"Presence: {uname} -> {status}")
            
        except Exception as e:
            logger.error(f"Error building presence snapshot: {e}")
            logger.error(traceback.format_exc())

        logger.info(f"Sending presence snapshot to {user}: {len(presence)} users")

        # 11. Send snapshot to the newly connected user
        await self.send(text_data=json.dumps({
            "command": "presence_snapshot",
            "presence": presence,
        }))
        
    async def presence_update(self, event):
        logger.debug(f"presence_update received by {self.scope['user'].username}: {event}")
        
        payload = {
            "command": "presence",
            "user": event.get("user"),
            "status": event.get("status"),
        }
        
        if 'last_seen' in event:
            payload['last_seen'] = event.get('last_seen')
        
        logger.debug(f"Sending presence update to client: {payload}")
        await self.send_message(payload)
    
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
            
            # Offload CPU-intensive encryption to thread
            encrypted_data = await sync_to_async(self.aes_gcm.encrypt)(
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

                # Offload CPU-intensive decryption to thread
                decrypted_data = await sync_to_async(self.aes_gcm.decrypt)(
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
        if not hasattr(self, 'room_group_name'):
            # Connection was never fully established
            return

        try:
            # 1. Leave the chat group with timeout
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_discard(self.room_group_name, self.channel_name),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Group discard timed out for {self.channel_name}")

            # 2. Remove from Redis set of online users
            cache_key = f"online:{self.room_group_name}"
            redis = get_redis_connection("default")
            try:
                await sync_to_async(redis.srem)(cache_key, self.scope["user"].username)
            except Exception as e:
                logger.error(f"Redis srem error: {e}")

            # 3. Update last seen
            try:
                last_seen_key = f"lastseen:{self.scope['user'].username}"
                now_iso = timezone.now().isoformat()
                await sync_to_async(redis.set)(last_seen_key, now_iso)

                # persist to Member.last_seen if Member exists
                def persist_last_seen(username, iso_ts):
                    try:
                        u = User.objects.filter(username=username).first()
                        if not u:
                            return
                        m = Member.objects.filter(User=u).first()
                        if not m:
                            return
                        from django.utils.dateparse import parse_datetime
                        dt = parse_datetime(iso_ts)
                        if dt is None:
                            return
                        m.last_seen = dt
                        m.save()
                    except Exception as e:
                        logger.error(f"Error persisting last_seen: {e}")

                await sync_to_async(persist_last_seen)(self.scope['user'].username, now_iso)

                # Broadcast offline status with last_seen to the group with timeout
                logger.debug(f"Broadcasting offline for {self.scope['user'].username}")
                try:
                    await asyncio.wait_for(
                        self.channel_layer.group_send(
                            self.room_group_name,
                            {
                                "type": "presence_update",
                                "user": self.scope["user"].username,
                                "status": "offline",
                                "last_seen": now_iso
                            }
                        ),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Presence broadcast timed out for {self.scope['user'].username}")
                except Exception as e:
                    logger.error(f"Presence broadcast error: {e}")
                    
            except Exception as e:
                logger.error(f"Last seen update error: {e}")
                
        except Exception as e:
            logger.error(f"Disconnect error for {self.channel_name}: {e}")

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
      
    async def check_user_muted(self, user, room_id):
            """Check if user is muted in this room"""
            def _check():
                try:
                    status = UserModerationStatus.objects.get(
                        user=user,
                        room_id=room_id
                    )
                    return status.is_muted
                except UserModerationStatus.DoesNotExist:
                    return False
            
            return await sync_to_async(_check)()

    async def buffer_message_for_moderation(self, room_id, message_id):
        """
        Buffer messages in Redis and trigger moderation when batch is ready
        Returns True if batch was triggered
        """
        room = await self.get_current_chatroom(room_id)
        
        # Skip if moderation disabled for this room
        if hasattr(room, 'moderation_enabled') and not room.moderation_enabled:
            return False
        
        redis = get_redis_connection("default")
        buffer_key = f"message_buffer:{room_id}"
        
        # Add message to buffer
        await sync_to_async(redis.lpush)(buffer_key, message_id)
        
        # Get buffer size
        buffer_size = await sync_to_async(redis.llen)(buffer_key)
        
        # Check if batch size reached
        batch_size = getattr(settings, 'MODERATION_BATCH_SIZE', 10)
        
        if buffer_size >= batch_size:
            # Get all messages from buffer
            message_ids = await sync_to_async(redis.lrange)(buffer_key, 0, -1)
            message_ids = [mid.decode() if isinstance(mid, bytes) else mid for mid in message_ids]
            
            # Create moderation batch
            def _create_batch():
                return ModerationBatch.objects.create(
                    room=room,
                    message_ids=json.dumps(message_ids),
                    status='pending'
                )
            
            batch = await sync_to_async(_create_batch)()
            
            # Clear buffer
            await sync_to_async(redis.delete)(buffer_key)
            
            # Trigger async moderation task
            moderate_message_batch.delay(batch.id)
            
            logger.info(f"Triggered moderation batch {batch.id} for room {room_id}")
            return True
        
        return False                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             
    
    async def new_message(self, data):
        try:
            logger.info(f"=== NEW MESSAGE START === Data: {data}")
            
            member_username = data['from']
            get_user = sync_to_async(User.objects.filter(username=member_username).first)
            member_user = await get_user()
            logger.info(f"Step 1: Got user: {member_user}")

            if not member_user:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "User not found.",
                    'timestamp': str(timezone.now())
                })
                return

            # === NEW: Check if user is muted ===
            room_id = data.get('chatid')
            logger.info(f"Step 2: room_id={room_id}, checking mute status...")
            
            try:
                is_muted = await self.check_user_muted(member_user, room_id)
                logger.info(f"Step 3: Mute check passed. is_muted={is_muted}")
            except Exception as e:
                logger.error(f"ERROR in check_user_muted: {e}")
                logger.error(traceback.format_exc())
                raise
            
            if is_muted:
                await self.send_message({
                    'member': 'security system',
                    'content': "You are muted in this room due to multiple flags.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info(f"Step 4: Rate limit check...")
            # Rate limiting check
            if not await self.check_rate_limit(member_user.id):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Rate limit exceeded. Please wait a moment.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info(f"Step 5: Get member...")
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
            logger.info(f"Step 6: Message content: {message_content[:50]}...")
            
            if not message_content or len(message_content) > 5000:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid message content.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info(f"Step 8: Regular message, checking key rotation...")
            # Check for key rotation
            await self.check_key_rotation()

            logger.info(f"Step 9: Encrypting message...")
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

            logger.info(f"Step 10: Creating message in DB...")
            create_message = sync_to_async(Message.objects.create)
            payload = json.dumps({
                'data':     encrypted_message['data'],
                'nonce':    encrypted_message['nonce'],
            })
            message = await create_message(
                member=member,
                content=payload,
                timestamp=timezone.now()
            )

            logger.info(f"Step 11: Getting chatroom...")
            current_chat = await self.get_current_chatroom(room_id)
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Chatroom not found.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info(f"Step 12: Getting room members...")
            room_members = await self.get_chatroom_participants(current_chat)

            if member in room_members:
                logger.info(f"Step 13: Adding message to chatroom...")
                await sync_to_async(current_chat.chats.add)(message)
                await sync_to_async(current_chat.save)()

                logger.info(f"Step 14: Buffering for moderation...")
                # === NEW: Buffer message for moderation ===
                try:
                    await self.buffer_message_for_moderation(room_id, message.id)
                    logger.info("Buffering successful!")
                except Exception as e:
                    logger.error(f"ERROR in buffer_message_for_moderation: {e}")
                    logger.error(traceback.format_exc())
                    raise

                logger.info(f"Step 15: Sending to clients...")
                message_json = await self.message_to_json(message)
                content = {
                    "command": "new_message",
                    "message": message_json
                }
                await self.send_chat_message(content)
                logger.info("=== NEW MESSAGE SUCCESS ===")

                # === ORCHESTRATION: Full pipeline ===
                if message_content.lower().startswith('@mathia'):
                    logger.info(f"Step 16: @mathia detected! Starting orchestration...")
                    
                    from orchestration.intent_parser import parse_intent
                    from orchestration.mcp_router import route_intent
                    from orchestration.data_synthesizer import synthesize_response
                    
                    ai_query = message_content[7:].strip()
                    
                    if ai_query:
                        # Step 1: Parse intent
                        intent = await parse_intent(ai_query, {
                            "user_id": member_user.id,
                            "username": member_username,
                            "room_id": room_id
                        })
                        
                        logger.info(f"Intent: {intent}")
                        
                        # Helper to broadcast chunks (Buffered)
                        # We use a mutable container for closure state
                        stream_state = {'buffer': [], 'last_send': 0}
                        
                        async def broadcast_chunk(chunk_text, is_final=False):
                            import time
                            stream_state['buffer'].append(chunk_text)
                            
                            current_time = time.time()
                            joined_text = "".join(stream_state['buffer'])
                            
                            # Send if buffer > 10 chars OR > 0.1s passed OR is_final
                            if len(joined_text) > 20 or (current_time - stream_state['last_send']) > 0.2 or is_final:
                                if joined_text or is_final:
                                    await self.channel_layer.group_send(
                                        self.room_group_name,
                                        {
                                            "type": "ai_stream_chunk",
                                            "chunk": joined_text,
                                            "is_final": is_final
                                        }
                                    )
                                    stream_state['buffer'] = []
                                    stream_state['last_send'] = current_time

                        # Step 2: Route if high confidence & not general chat
                        if intent["confidence"] > 0.7 and intent["action"] != "general_chat":
                            # Route through MCP
                            result = await route_intent(intent, {
                                "user_id": member_user.id,
                                "room_id": room_id,
                                "username": member_username
                            })
                            
                            logger.info(f"MCP result: {result['status']}")
                            
                            if result["status"] == "success":
                                # Step 3: Synthesize natural language response (STREAMING)
                                from orchestration.data_synthesizer import synthesize_response_stream
                                
                                async for chunk in synthesize_response_stream(
                                    intent, 
                                    result, 
                                    use_llm=True
                                ):
                                    await broadcast_chunk(chunk)
                                    
                            else:
                                await broadcast_chunk(f"❌ {result['message']}")
                        else:
                            # Fallback to LLM for general chat or low confidence (STREAMING)
                            from orchestration.llm_client import get_llm_client
                            llm_client = get_llm_client()
                            
                            async for chunk in llm_client.stream_text(
                                system_prompt="You are Mathia, a helpful AI assistant. Be concise and friendly.",
                                user_prompt=ai_query,
                                temperature=0.7,
                                max_tokens=500
                            ):
                                await broadcast_chunk(chunk)
                        
                        # End stream
                        await broadcast_chunk("", is_final=True)

            else:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not authorized for this chat.",
                    'timestamp': str(timezone.now())
                })
        except Exception as e:
            logger.error(f"Error in new_message: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
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
        
    async def ai_response_message(self, event):
        """
        Handler for AI bot responses sent via channel layer
        Called by Celery task after generating AI reply
        """
        try:
            ai_reply = event.get('ai_reply')
            user_id = event.get('user_id')
            
            # Encrypt AI response
            encrypted_message = await self.encrypt_message({
                'content': ai_reply,
                'timestamp': str(timezone.now())
            })
            
            if not encrypted_message:
                logger.error("Failed to encrypt AI response")
                return
            
            # Create message from AI bot
            def _create_ai_message():
                ai_user, _ = User.objects.get_or_create(
                    username='mathia',
                    defaults={'is_active': False}
                )
                ai_member, _ = Member.objects.get_or_create(User=ai_user)
                
                payload = json.dumps({
                    'data': encrypted_message['data'],
                    'nonce': encrypted_message['nonce'],
                })
                
                return Message.objects.create(
                    member=ai_member,
                    content=payload,
                    timestamp=timezone.now()
                )
            
            message = await sync_to_async(_create_ai_message)()
            
            # Add to room
            room_id = event.get('room_id')
            current_chat = await self.get_current_chatroom(room_id)
            if current_chat:
                await sync_to_async(current_chat.chats.add)(message)
                await sync_to_async(current_chat.save)()
            
            # Send to clients with correct command
            message_json = await self.message_to_json(message)
            
            await self.send(text_data=json.dumps({
                "command": "ai_message",  # Changed from "new_message"
                "message": message_json
            }))
            
        except Exception as e:
            logger.error(f"Error in ai_response_message: {e}")
            
    async def ai_stream_chunk(self, event):
        """Handle streaming AI response chunks"""
        await self.send(text_data=json.dumps({
            "command": "ai_stream",
            "chunk": event.get('chunk'),
            "is_final": event.get('is_final', False)
        }))
        
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
        """Get all participants in a chatroom"""
        try:
            participants = chat.participants.all()
            participants_list = await sync_to_async(list)(participants)
            logger.info(f"get_chatroom_participants returned {len(participants_list)} participants")
            return participants_list
        except Exception as e:
            logger.error(f"Error in get_chatroom_participants: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []

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