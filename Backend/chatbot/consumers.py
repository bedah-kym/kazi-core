import json
import traceback
import re
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import Message, Member, Chatroom
from django.contrib.auth import get_user_model
import os
import uuid
from django_redis import get_redis_connection
from django.core.cache import cache
from base64 import b64encode, b64decode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidKey
from users.encryption import TokenEncryption
import logging
import asyncio
from .models import Message, Member, Chatroom, UserModerationStatus, ModerationBatch, RoomReadState
from .tasks import moderate_message_batch, generate_ai_response, generate_voice_response
from orchestration.intent_parser import parse_intent
from orchestration.adaptive_task import (
    load_task_state,
    save_task_state,
    clear_task_state,
    init_task_state,
    update_task_state,
    get_action_definition,
    format_missing_prompt,
    apply_summary_defaults,
    should_use_summary,
    store_result_set,
    needs_option_context,
)
from django.conf import settings
from django.utils.text import get_valid_filename
logger = logging.getLogger(__name__)
User = get_user_model()
PENDING_CONFIRM_TTL_SECONDS = 600
LAST_SUMMARY_TTL_SECONDS = 60 * 60

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

        # 3. Check room membership
        current_chat = await self.get_chatroom_for_user(self.room_name, self.scope["user"])
        if not current_chat:
            await self.close(code=4003)
            return

        # 4. Secure session init
        initialized = await self.initialize_secure_session(self.room_name)
        if not initialized:
            await self.close(code=4002)
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

    async def schedule_context_summary(self, room_id, message_id):
        min_messages = getattr(settings, "CONTEXT_SUMMARY_MIN_MESSAGES", 6)
        min_minutes = getattr(settings, "CONTEXT_SUMMARY_MIN_MINUTES", 10)
        counter_key = f"context_summary:count:{room_id}"
        last_key = f"context_summary:last:{room_id}"
        now = timezone.now()

        count = (cache.get(counter_key) or 0) + 1
        cache.set(counter_key, count, timeout=60 * 60)

        should_schedule = count >= min_messages
        if not should_schedule:
            last_iso = cache.get(last_key)
            if last_iso:
                try:
                    last_seen = timezone.datetime.fromisoformat(last_iso)
                    if (now - last_seen).total_seconds() >= min_minutes * 60:
                        should_schedule = True
                except Exception:
                    pass

        if not should_schedule:
            return

        cache.set(last_key, now.isoformat(), timeout=60 * 60 * 6)
        cache.delete(counter_key)

        from .tasks import refresh_room_context_summary
        refresh_room_context_summary.delay(room_id, message_id, count)

    async def schedule_idle_nudge_if_needed(self, room_id, user_id):
        last_activity_key = f"proactive:last_activity:{room_id}:{user_id}"
        cache.set(last_activity_key, timezone.now().isoformat(), timeout=60 * 60 * 24)
        pending_key = f"orchestration:pending:{room_id}:{user_id}"
        if cache.get(pending_key):
            return
        adaptive_state = await load_task_state({"user_id": user_id, "room_id": room_id})
        if adaptive_state and adaptive_state.get("status") in ("awaiting_slots", "ready"):
            return
        pending_key = f"proactive:pending:{room_id}:{user_id}"
        if cache.get(pending_key):
            return
        from .tasks import schedule_idle_nudge
        schedule_idle_nudge.delay(room_id, user_id)

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
            key = chatroom.encryption_key
            if key and key.startswith("enc:"):
                key = TokenEncryption.safe_decrypt(key[4:], default=None)
            return key
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

    def _decode_base64_payload(self, payload):
        if not isinstance(payload, str):
            raise ValueError("Invalid payload")
        if ';base64,' in payload:
            payload = payload.split(';base64,', 1)[1]
        payload = payload.strip().replace(' ', '+')
        return b64decode(payload)

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
            elif command == "get_quotas":
                await self.send_quotas()
            elif command == "voice_message":
                await self.voice_message(data)
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
            
    async def send_quotas(self):
        """Fetch and send user quota stats"""
        try:
            from users.quota_service import QuotaService
            
            # Use sync_to_async for cache access/calculations if needed
            # (QuotaService mainly uses cache which is often sync in Django, but django-redis can be sync)
            service = QuotaService()
            user_id = self.scope["user"].id
            
            # Simple wrapper to run it in threadpool if cache backend is blocking
            quotas = await sync_to_async(service.get_user_quotas)(user_id)
            
            await self.send_message({
                'command': 'user_quotas',
                'quotas': quotas
            })
        except Exception as e:
            logger.error(f"Error sending quotas: {e}")

    async def fetch_messages(self, data):
        try:
            chatid = data['chatid']
            before_id = data.get('before_id')  # None for initial load
            result = await self.get_paginated_messages(chatid, before_id=before_id)
            messages = result['messages']
            # Let message_to_json handle decryption & formatting
            messages_json = [await self.message_to_json(m) for m in messages]
            await self.send_message({
                'command': 'messages',
                'messages': messages_json,
                'has_more': result['has_more'],
                'oldest_id': result['oldest_id']
            })
            try:
                await sync_to_async(RoomReadState.objects.update_or_create)(
                    user=self.scope["user"],
                    room_id=chatid,
                    defaults={"last_read_at": timezone.now()},
                )
            except Exception as e:
                logger.warning(f"Read state update skipped: {e}")
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
        # Skip moderation in DEBUG mode to save Redis operations
        if settings.DEBUG:
            return False
        
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
            if member_username != self.scope["user"].username:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid sender.",
                    'timestamp': str(timezone.now())
                })
                return

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
            if str(room_id) != str(self.room_name):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid room.",
                    'timestamp': str(timezone.now())
                })
                return
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
                timestamp=timezone.now(),
                parent_id=data.get('reply_to')
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

                # Queue room context refresh for summary/notes
                try:
                    await self.schedule_context_summary(room_id, message.id)
                except Exception as e:
                    logger.warning(f"Context summary refresh skipped: {e}")
                try:
                    await self.schedule_idle_nudge_if_needed(room_id, member_user.id)
                except Exception as e:
                    logger.warning(f"Idle nudge schedule skipped: {e}")

                # === ORCHESTRATION: Full pipeline ===
                if message_content.lower().startswith('@mathia'):
                    logger.info(f"Step 16: @mathia detected! Starting orchestration...")
                    
                    from orchestration.intent_parser import parse_intent
                    from orchestration.mcp_router import route_intent
                    from orchestration.data_synthesizer import synthesize_response
                    
                    ai_query = message_content[7:].strip()
                    
                    if ai_query:
                        # Fetch history for conversation context
                        history_text = await self.get_history_as_text(room_id)
                        try:
                            from .context_manager import ContextManager
                            context_prompt = await sync_to_async(ContextManager.get_context_prompt)(room_id)
                            if context_prompt:
                                history_text = "\n\n".join([history_text, context_prompt]).strip()
                        except Exception as e:
                            logger.warning(f"Context prompt load failed: {e}")
                        
                        # Helper to broadcast chunks (Buffered)
                        # We use a mutable container for closure state
                        # Helper to broadcast chunks (Buffered & Whitespace Filtered)
                        # We use a mutable container for closure state
                        stream_state = {'buffer': [], 'last_send': 0, 'first_token_sent': False, 'full_response': []}
                        
                        async def broadcast_chunk(chunk_text, is_final=False):
                            import time
                            
                            # Store all chunks to build full response
                            if chunk_text:
                                stream_state['full_response'].append(chunk_text)
                            
                            # Filter leading whitespace if first token hasn't been sent
                            if not stream_state['first_token_sent'] and not is_final:
                                if not chunk_text.strip():
                                    return # Ignore pure whitespace at start
                                chunk_text = chunk_text.lstrip() # Trim leading space of first word
                                stream_state['first_token_sent'] = True
                                
                            stream_state['buffer'].append(chunk_text)
                            
                            current_time = time.time()
                            joined_text = "".join(stream_state['buffer'])
                            
                            # Send if buffer > 20 chars OR > 0.2s passed OR is_final
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
                        
                        from orchestration.workflow_planner import (
                            plan_user_request,
                            execute_adhoc_workflow,
                            synthesize_workflow_response_stream,
                            looks_like_confirmation,
                            LLM_CONFIDENCE_EXECUTE,
                            LLM_CONFIDENCE_CONFIRM,
                        )

                        pending_key = f"orchestration:pending:{room_id}:{member_user.id}"
                        last_summary_key = f"orchestration:last_summary:{room_id}:{member_user.id}"
                        adaptive_context = {
                            "user_id": member_user.id,
                            "room_id": room_id,
                            "username": member_username,
                        }
                        summary_text_for_cache = None
                        should_cache_summary = False

                        def _bump_signals(actions):
                            try:
                                from .tasks import update_proactive_signals
                                for action in actions:
                                    if action:
                                        update_proactive_signals(room_id, member_user.id, action)
                            except Exception as exc:
                                logger.warning(f"Proactive signal update skipped: {exc}")

                        async def _execute_intent(intent):
                            nonlocal summary_text_for_cache, should_cache_summary
                            prompt = await needs_option_context(
                                adaptive_context,
                                intent.get("action"),
                                intent.get("parameters"),
                                get_action_definition(intent.get("action")),
                            )
                            if prompt:
                                task_state = init_task_state(intent)
                                task_state["status"] = "awaiting_slots"
                                task_state["missing_slots"] = task_state.get("missing_slots") or ["option_context"]
                                task_state["last_prompt"] = prompt
                                await save_task_state(adaptive_context, task_state)
                                await broadcast_chunk(prompt)
                                return {"status": "needs_input", "message": prompt}
                            result = await route_intent(intent, {
                                "user_id": member_user.id,
                                "room_id": room_id,
                                "username": member_username
                            })

                            logger.info(f"MCP result: {result['status']}")

                            if result["status"] == "success":
                                data_payload = result.get("data") if isinstance(result.get("data"), dict) else {}
                                results = data_payload.get("results") if isinstance(data_payload, dict) else None
                                if isinstance(results, list):
                                    await store_result_set(
                                        adaptive_context,
                                        intent.get("action"),
                                        results,
                                        metadata=data_payload.get("metadata") if isinstance(data_payload, dict) else None,
                                    )
                                from orchestration.data_synthesizer import synthesize_response_stream
                                summary_text_for_cache = await synthesize_response(intent, result, use_llm=False)

                                async for chunk in synthesize_response_stream(
                                    intent,
                                    result,
                                    use_llm=True
                                ):
                                    await broadcast_chunk(chunk)
                                should_cache_summary = True
                                _bump_signals([intent.get("action")])
                            else:
                                await broadcast_chunk(f"Error: {result['message']}")
                            return result

                        def _is_dismiss_request(query: str) -> bool:
                            lowered = query.lower()
                            if not re.search(r"\b(dismiss|stop|no thanks)\b", lowered):
                                return False
                            return bool(re.search(r"\b(nudge|suggestion|proactive)\b", lowered))

                        handled_directive = False
                        if _is_dismiss_request(ai_query):
                            last_reason_key = f"proactive:last_reason:{room_id}:{member_user.id}"
                            dismissed_key = f"proactive:dismissed:{room_id}:{member_user.id}"
                            last_reason = cache.get(last_reason_key)
                            if last_reason:
                                dismissed = cache.get(dismissed_key) or []
                                if last_reason not in dismissed:
                                    dismissed.append(last_reason)
                                    cache.set(dismissed_key, dismissed, timeout=60 * 60 * 24 * 14)
                            cache.delete(pending_key)
                            await broadcast_chunk("Got it. I will stop showing that kind of suggestion here.")
                            handled_directive = True

                        pending = cache.get(pending_key)
                        pending_handled = handled_directive
                        if pending and not pending_handled:
                            if looks_like_confirmation(ai_query):
                                pending_kind = pending.get("kind")
                                cache.delete(pending_key)
                                if pending_kind == "workflow":
                                    pending_handled = True
                                    workflow_definition = pending.get("workflow_definition") or {}
                                    pending_message = pending.get("user_message") or ai_query
                                    execution = await execute_adhoc_workflow(
                                        workflow_definition,
                                        member_user.id,
                                        room_id,
                                        trigger_data={"message": pending_message},
                                    )
                                    async for chunk in synthesize_workflow_response_stream(
                                        pending_message,
                                        workflow_definition,
                                        execution.get("result") or {},
                                        execution.get("status") or "running",
                                        execution.get("error") or execution.get("message"),
                                    ):
                                        await broadcast_chunk(chunk)
                                    should_cache_summary = True
                                    if execution.get("status") == "completed":
                                        step_actions = [
                                            step.get("action")
                                            for step in workflow_definition.get("steps", [])
                                            if isinstance(step, dict)
                                        ]
                                        _bump_signals(step_actions + ["workflow_run"])
                                elif pending_kind == "intent":
                                    pending_handled = True
                                    intent = pending.get("intent") or {}
                                    await _execute_intent(intent)
                            else:
                                cache.delete(pending_key)

                        if not pending_handled:
                            adaptive_state = await load_task_state(adaptive_context)
                            if adaptive_state and adaptive_state.get("status") == "awaiting_slots":
                                expected_action = adaptive_state.get("action")
                                followup_intent = await parse_intent(ai_query, {
                                    "user_id": member_user.id,
                                    "username": member_username,
                                    "room_id": room_id,
                                    "history": history_text,
                                    "expected_action": expected_action,
                                    "expected_slots": adaptive_state.get("missing_slots") or [],
                                })
                                if (
                                    followup_intent.get("action")
                                    and followup_intent.get("action") != expected_action
                                    and followup_intent.get("confidence", 0) >= LLM_CONFIDENCE_CONFIRM
                                ):
                                    await clear_task_state(adaptive_context)
                                else:
                                    updated_state = update_task_state(
                                        adaptive_state,
                                        followup_intent.get("parameters") or {},
                                    )
                                    action_def = get_action_definition(updated_state.get("action"))
                                    if updated_state.get("status") == "awaiting_slots":
                                        prompt = format_missing_prompt(
                                            updated_state.get("action"),
                                            updated_state.get("missing_slots") or [],
                                            action_def,
                                        )
                                        if not prompt:
                                            prompt = followup_intent.get("clarifying_question") or "I need a bit more detail to proceed."
                                        updated_state["last_prompt"] = prompt
                                        await save_task_state(adaptive_context, updated_state)
                                        await broadcast_chunk(prompt)
                                        pending_handled = True
                                    else:
                                        summary_text = None
                                        if should_use_summary(ai_query):
                                            summary_text = cache.get(last_summary_key)
                                        params = apply_summary_defaults(
                                            updated_state.get("action"),
                                            updated_state.get("parameters"),
                                            summary_text,
                                        )
                                        updated_state["parameters"] = params
                                        action_def = get_action_definition(updated_state.get("action"))
                                        prompt = await needs_option_context(
                                            adaptive_context,
                                            updated_state.get("action"),
                                            params,
                                            action_def,
                                        )
                                        if prompt:
                                            updated_state["status"] = "awaiting_slots"
                                            updated_state["missing_slots"] = updated_state.get("missing_slots") or ["option_context"]
                                            updated_state["last_prompt"] = prompt
                                            await save_task_state(adaptive_context, updated_state)
                                            await broadcast_chunk(prompt)
                                            pending_handled = True
                                        else:
                                            await save_task_state(adaptive_context, updated_state)
                                            intent = {
                                                "action": updated_state.get("action"),
                                                "parameters": params,
                                                "confidence": 1.0,
                                            }
                                            result = await _execute_intent(intent)
                                            if result.get("status") == "success":
                                                await clear_task_state(adaptive_context)
                                            pending_handled = True

                        if not pending_handled:
                            plan = await plan_user_request(ai_query, history_text, user_id=member_user.id)

                            if plan["mode"] == "automation_request":
                                from workflows.workflow_agent import handle_workflow_message
                                response_text = await handle_workflow_message(member_user.id, room_id, ai_query, history_text)
                                await broadcast_chunk(response_text)
                            elif plan["mode"] == "needs_clarification":
                                await broadcast_chunk(plan.get("assistant_message") or "I need a bit more detail to proceed.")
                            elif plan["mode"] == "needs_confirmation":
                                workflow_definition = plan.get("workflow_definition") or {}
                                cache.set(
                                    pending_key,
                                    {
                                        "kind": "workflow",
                                        "workflow_definition": workflow_definition,
                                        "user_message": ai_query,
                                    },
                                    timeout=PENDING_CONFIRM_TTL_SECONDS,
                                )
                                await broadcast_chunk(plan.get("assistant_message") or "Please confirm to proceed.")
                            elif plan["mode"] == "adhoc_workflow":
                                workflow_definition = plan.get("workflow_definition") or {}
                                execution = await execute_adhoc_workflow(
                                    workflow_definition,
                                    member_user.id,
                                    room_id,
                                    trigger_data={"message": ai_query},
                                )
                                async for chunk in synthesize_workflow_response_stream(
                                    ai_query,
                                    workflow_definition,
                                    execution.get("result") or {},
                                    execution.get("status") or "running",
                                    execution.get("error") or execution.get("message"),
                                ):
                                    await broadcast_chunk(chunk)
                                should_cache_summary = True
                                if execution.get("status") == "completed":
                                    step_actions = [
                                        step.get("action")
                                        for step in workflow_definition.get("steps", [])
                                        if isinstance(step, dict)
                                    ]
                                    _bump_signals(step_actions + ["workflow_run"])
                            else:
                                # Step 1: Parse intent
                                intent = await parse_intent(ai_query, {
                                    "user_id": member_user.id,
                                    "username": member_username,
                                    "room_id": room_id,
                                    "history": history_text
                                })

                                logger.info(f"Intent: {intent}")
                                action = intent.get("action")
                                params = intent.get("parameters") if isinstance(intent.get("parameters"), dict) else {}
                                if should_use_summary(ai_query):
                                    params = apply_summary_defaults(action, params, cache.get(last_summary_key))
                                intent["parameters"] = params

                                task_state = init_task_state(intent)

                                if action == "create_workflow" and intent.get("confidence", 0) > 0.6:
                                    from workflows.workflow_agent import handle_workflow_message
                                    response_text = await handle_workflow_message(member_user.id, room_id, ai_query, history_text)
                                    await broadcast_chunk(response_text)
                                elif task_state.get("status") == "awaiting_slots" and action != "general_chat":
                                    action_def = get_action_definition(action)
                                    question = format_missing_prompt(
                                        action,
                                        task_state.get("missing_slots") or [],
                                        action_def,
                                    )
                                    if not question:
                                        question = intent.get("clarifying_question") or "I need a bit more detail to proceed."
                                    task_state["last_prompt"] = question
                                    await save_task_state(adaptive_context, task_state)
                                    await broadcast_chunk(question)
                                elif intent.get("confidence", 0) >= LLM_CONFIDENCE_EXECUTE and action != "general_chat":
                                    # Route through MCP
                                    result = await _execute_intent(intent)
                                    if result.get("status") == "success":
                                        await clear_task_state(adaptive_context)
                                elif intent.get("confidence", 0) >= LLM_CONFIDENCE_CONFIRM and action != "general_chat":
                                    cache.set(
                                        pending_key,
                                        {"kind": "intent", "intent": intent},
                                        timeout=PENDING_CONFIRM_TTL_SECONDS,
                                    )
                                    await clear_task_state(adaptive_context)
                                    action_label = str(action or "that").replace("_", " ")
                                    await broadcast_chunk(
                                        f"I think you want me to {action_label}. Reply 'yes' to proceed or clarify."
                                    )
                                else:
                                    # Fallback to LLM for general chat or low confidence (STREAMING)
                                    from orchestration.llm_client import get_llm_client
                                    llm_client = get_llm_client()

                                    # Prepend history to query if available
                                    full_query = ai_query
                                    if history_text:
                                        full_query = f"CONVERSATION HISTORY:\n{history_text}\n\nUSER message: {ai_query}"

                                    async for chunk in llm_client.stream_text(
                                        system_prompt="You are Mathia, a helpful AI assistant. Be concise and friendly.",
                                        user_prompt=full_query,
                                        temperature=0.7,
                                        max_tokens=500
                                    ):
                                        await broadcast_chunk(chunk)
                        
                        # End stream
                        await broadcast_chunk("", is_final=True)
                        
                        # === NEW: Save complete AI message to database ===
                        full_response_text = "".join(stream_state['full_response'])

                        if should_cache_summary:
                            summary_value = (summary_text_for_cache or full_response_text).strip()
                            if summary_value:
                                cache.set(last_summary_key, summary_value, timeout=LAST_SUMMARY_TTL_SECONDS)

                        if full_response_text.strip():
                            logger.info(f"Saving AI message to database: {full_response_text[:100]}...")
                            
                            # Encrypt the AI response
                            encrypted_message = await self.encrypt_message({
                                'content': full_response_text,
                                'timestamp': str(timezone.now())
                            })
                            
                            if encrypted_message:
                                # Create Mathia user/member if not exists
                                def _create_ai_message():
                                    ai_user, _ = User.objects.get_or_create(
                                        username='mathia',
                                        defaults={
                                            'first_name': 'Mathia',
                                            'last_name': 'AI',
                                            'is_active': True,
                                            'email': 'mathia@kwikchat.ai'
                                        }
                                    )
                                    # Use filter().first() to handle duplicate Members
                                    ai_member = Member.objects.filter(User=ai_user).first()
                                    if not ai_member:
                                        ai_member = Member.objects.create(User=ai_user)
                                    
                                    payload = json.dumps({
                                        'data': encrypted_message['data'],
                                        'nonce': encrypted_message['nonce'],
                                    })
                                    
                                    return Message.objects.create(
                                        member=ai_member,
                                        content=payload,
                                        timestamp=timezone.now()
                                    )
                                
                                ai_message = await sync_to_async(_create_ai_message)()
                                
                                # Add to chatroom
                                current_chat = await self.get_current_chatroom(room_id)
                                if current_chat:
                                    await sync_to_async(current_chat.chats.add)(ai_message)
                                    await sync_to_async(current_chat.save)()
                                    logger.info(f"AI message saved with ID: {ai_message.id}")
                                    
                                    # Trigger Mathia Voice Response (TTS)
                                    generate_voice_response.delay(ai_message.id)
                                    
                                    # Broadcast saved message to clients for proper rendering
                                    message_json = await self.message_to_json(ai_message)
                                    await self.channel_layer.group_send(
                                        self.room_group_name,
                                        {
                                            "type": "ai_message_saved",
                                            "message": message_json
                                        }
                                    )
                                    
                                    try:
                                        await self.schedule_context_summary(room_id, ai_message.id)
                                    except Exception as e:
                                        logger.warning(f"Context summary refresh skipped: {e}")

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
            if member_username != self.scope["user"].username:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid sender.",
                    'timestamp': str(timezone.now())
                })
                return

            get_user = sync_to_async(User.objects.filter(username=member_username).first)
            member_user = await get_user()

            if not member_user:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "User not found.",
                    'timestamp': str(timezone.now())
                })
                return

            room_id = data.get('chatid')
            if str(room_id) != str(self.room_name):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid room.",
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
            file_name = data.get('file_name', '')

            if not file_data or not file_name:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid file data.",
                    'timestamp': str(timezone.now())
                })
                return

            safe_name = get_valid_filename(file_name)
            _, ext = os.path.splitext(safe_name)
            ext = ext.lower()

            allowed_extensions = {'.txt', '.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'}
            if ext not in allowed_extensions:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Unsupported file type.",
                    'timestamp': str(timezone.now())
                })
                return

            try:
                file_bytes = self._decode_base64_payload(file_data)
            except Exception:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid file encoding.",
                    'timestamp': str(timezone.now())
                })
                return

            if len(file_bytes) > 5 * 1024 * 1024:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "File too large. Maximum size is 5MB.",
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

            current_chat = await self.get_chatroom_for_user(room_id, member_user)
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not authorized for this chat.",
                    'timestamp': str(timezone.now())
                })
                return

            upload_name = f"chat_uploads/{uuid.uuid4().hex}{ext}"
            file_path = default_storage.save(upload_name, ContentFile(file_bytes))
            file_url = default_storage.url(file_path)

            # Encrypt the file message content
            encrypted_message = await self.encrypt_message({
                'content': f"<a href='{file_url}' target='_blank'>{safe_name}</a>",
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
            payload = json.dumps({
                'data': encrypted_message['data'],
                'nonce': encrypted_message['nonce'],
            })
            message = await create_message(
                member=member,
                content=payload,
                timestamp=timezone.now(),
                parent_id=data.get('reply_to')
            )

            await sync_to_async(current_chat.chats.add)(message)
            await sync_to_async(current_chat.save)()

            message_json = await self.message_to_json(message)
            content = {
                "command": "new_message",
                "message": message_json
            }
            await self.send_chat_message(content)
        except Exception as e:
            logger.error(f"Error in file_message: {str(e)}")
            await self.send_chat_message({
                'member': 'security system',
                'content': "Error processing file",
                'timestamp': str(timezone.now())
                })

    async def voice_message(self, data):
        """
        Handle voice note uploads.
        Expects:
        - file_data: Base64 encoded audio
        - file_name: filename (e.g., 'voice_123.webm')
        """
        try:
            member_username = data['from']
            if member_username != self.scope["user"].username:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid sender.",
                    'timestamp': str(timezone.now())
                })
                return

            get_user = sync_to_async(User.objects.filter(username=member_username).first)
            member_user = await get_user()

            if not member_user:
                return

            room_id = data.get('chatid')
            if str(room_id) != str(self.room_name):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid room.",
                    'timestamp': str(timezone.now())
                })
                return

            # Rate limit check
            if not await self.check_rate_limit(member_user.id):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Rate limit exceeded. Please wait.",
                    'timestamp': str(timezone.now())
                })
                return

            file_data = data.get('file_data', '')
            file_name = data.get('file_name', '')

            if not file_data or not file_name:
                return

            safe_name = get_valid_filename(file_name)
            _, ext = os.path.splitext(safe_name)
            ext = ext.lower()

            # Validate Audio Extension
            allowed_audio = {'.webm', '.mp3', '.wav', '.m4a', '.ogg'}
            if ext not in allowed_audio:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid audio format.",
                    'timestamp': str(timezone.now())
                })
                return

            try:
                file_bytes = self._decode_base64_payload(file_data)
            except Exception:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid audio encoding.",
                    'timestamp': str(timezone.now())
                })
                return

            if len(file_bytes) > 10 * 1024 * 1024:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Audio too large. Maximum size is 10MB.",
                    'timestamp': str(timezone.now())
                })
                return

            get_member = sync_to_async(Member.objects.filter(User=member_user).first)
            member = await get_member()

            if not member:
                return

            current_chat = await self.get_chatroom_for_user(room_id, member_user)
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Not authorized for this chat.",
                    'timestamp': str(timezone.now())
                })
                return

            file_path = default_storage.save(
                f"voice/{uuid.uuid4().hex}{ext}",
                ContentFile(file_bytes)
            )
            file_url = default_storage.url(file_path)

            # Create Message with is_voice=True
            encrypted_content = await self.encrypt_message({
                'content': "[Voice Message]",
                'timestamp': str(timezone.now())
            })

            if not encrypted_content:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Message encryption failed.",
                    'timestamp': str(timezone.now())
                })
                return

            create_message = sync_to_async(Message.objects.create)
            payload = json.dumps({
                'data': encrypted_content['data'],
                'nonce': encrypted_content['nonce']
            })

            message = await create_message(
                member=member,
                content=payload,
                timestamp=timezone.now(),
                parent_id=data.get('reply_to'),
                is_voice=True,
                audio_url=file_url
            )

            await sync_to_async(current_chat.chats.add)(message)
            await sync_to_async(current_chat.save)()

            # Broadcast
            message_json = await self.message_to_json(message)
            message_json['audio_url'] = file_url
            message_json['is_voice'] = True

            content = {
                "command": "new_message",
                "message": message_json
            }
            await self.send_chat_message(content)

        except Exception as e:
            logger.error(f"Error in voice_message: {str(e)}")
            await self.send_chat_message({
                'member': 'security system',
                'content': "Error processing voice message",
                'timestamp': str(timezone.now())
            })

    async def typing_message(self, event):
        # fan out typing to all group members
        await self.send(text_data=json.dumps({
            "command": "typing",
            "from":    event["from"],
        }))

    async def ai_message_saved(self, event):
        """AI message fully saved after streaming"""
        await self.send_message({
            "command": "ai_message_saved",
            "message": event["message"]
        })

    async def ai_voice_ready(self, event):
        """Mathia voice response is ready"""
        await self.send_message({
            "command": "ai_voice_ready",
            "message_id": event["message_id"],
            "audio_url": event["audio_url"]
        })

    async def voice_transcription_ready(self, event):
        """User voice transcription is ready"""
        await self.send_message({
            "command": "voice_transcription_ready",
            "message_id": event["message_id"],
            "transcript": event["transcript"]
        })

    async def send_message(self, message):
        """Helper to send JSON to WebSocket"""
        await self.send(text_data=json.dumps(message))

    async def send_chat_message(self, message):
        """Helper to send message to group"""
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
                ai_user, created = User.objects.get_or_create(
                    username='mathia',
                    defaults={
                        'first_name': 'Mathia',
                        'last_name': 'AI',
                        'is_active': True,  # Activate so profile is visible
                        'email': 'mathia@kwikchat.ai'
                    }
                )
                # Use filter().first() to handle duplicate Members
                ai_member = Member.objects.filter(User=ai_user).first()
                if not ai_member:
                    ai_member = Member.objects.create(User=ai_user)
                
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
                try:
                    await self.schedule_context_summary(room_id, message.id)
                except Exception as e:
                    logger.warning(f"Context summary refresh skipped: {e}")
            
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
    
    async def ai_message_saved(self, event):
        """Send saved AI message to client for proper rendering with dropdown"""
        await self.send(text_data=json.dumps({
            "command": "ai_message_saved",
            "message": event.get('message')
        }))
        
    @classmethod
    async def get_paginated_messages(cls, chatid, before_id=None, limit=20):
        """Fetch messages with cursor-based pagination.
        
        Args:
            chatid: The chatroom ID
            before_id: If provided, fetch messages with id < before_id (for loading older)
            limit: Number of messages to fetch (default 20)
            
        Returns:
            Dict with 'messages', 'has_more', 'oldest_id'
        """
        def _fetch():
            qs = Message.objects.filter(chatroom__id=chatid)
            if before_id:
                qs = qs.filter(id__lt=before_id)
            # Optimize: select_related to avoid N+1 queries on member.User
            qs = qs.select_related('member__User').order_by('-timestamp')[:limit + 1]
            msgs = list(qs)
            # Check if there are more messages beyond this page
            has_more = len(msgs) > limit
            return msgs[:limit], has_more
        
        messages, has_more = await sync_to_async(_fetch)()
        oldest_id = messages[-1].id if messages else None
        return {
            'messages': messages,
            'has_more': has_more,
            'oldest_id': oldest_id
        }
    
    # Legacy method for backwards compatibility
    @classmethod
    async def get_last_10_messages(cls, chatid):
        """Legacy method - use get_paginated_messages instead."""
        result = await cls.get_paginated_messages(chatid, before_id=None, limit=10)
        return result['messages']

    @sync_to_async
    def get_chatroom_for_user(self, chatid, user):
        return Chatroom.objects.filter(id=chatid, participants__User=user).first()

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
        
        if ((current_time - self.last_key_rotation).total_seconds() >= self.KEY_ROTATION_INTERVAL or 
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
                'id': message.id,
                'member': username,
                'content': final_content,
                'timestamp': str(message.timestamp),
                'parent_id': message.parent_id
            }
        except Exception as e:
            logger.error(f"Error in message_to_json: {e}")
            return {
                'member': 'system',
                'content': 'Error processing message',
                'timestamp': str(timezone.now())
            }
    async def get_history_as_text(self, room_id, limit=5):
        "Fetches last N messages and formats them as plain text history."
        try:
            from .models import Chatroom
            get_room = sync_to_async(Chatroom.objects.get)
            room = await get_room(id=room_id)
            
            def _get_msgs():
                return list(room.chats.all().order_by('-timestamp')[:limit])
                
            messages = await sync_to_async(_get_msgs)()
            messages.reverse()
            
            history_lines = []
            for msg in messages:
                msg_json = await self.message_to_json(msg)
                content = msg_json.get('content', '')
                member = msg_json.get('member', 'Unknown')
                if content and not content.startswith('Error:'):
                    history_lines.append(f'{member}: {content}')
            
            return '\n'.join(history_lines)
        except Exception as e:
            logger.error(f'Error getting history: {e}')
            return ''
