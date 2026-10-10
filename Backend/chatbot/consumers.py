import json
import random
import re
import traceback
from asgiref.sync import sync_to_async
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
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
from typing import Dict, Tuple
from .models import Message, Member, Chatroom, UserModerationStatus, ModerationBatch, RoomReadState
from .tasks import moderate_message_batch, generate_voice_response
from .dispatch import dispatch_task
from . import presence
from .presence import agent_status
from .transcript import (
    BOT_USERNAME,
    build_history_messages,
    has_several_speakers,
    strip_wake_word,
)
from orchestration.user_preferences import get_user_preferences
from django.conf import settings
from django.utils.text import get_valid_filename
logger = logging.getLogger(__name__)
User = get_user_model()

# v0.7 @admin escalation: a message that *starts* with @admin goes to
# superusers instead of the bot. "@admin request persona "Name" - "description""
# opens a PersonaRequest for the admin to approve in the admin UI. The anchor
# keeps an address like x@admin.example.com from escalating.
ADMIN_MENTION_RE = re.compile(r'^@admin(?=$|[\s,:;.!?])', re.IGNORECASE)
ADMIN_PERSONA_REQUEST_RE = re.compile(
    r'^@admin\s+request\s+persona\s+"([^"]+)"(?:\s*[-–—]\s*"?([^"]*)"?)?',
    re.IGNORECASE,
)

# Per-(room, user) locks so concurrent messages from the same user in the
# same room cannot race on paused agent-loop confirmations (the pause state is
# a single Redis key + a single pending durable approval row).
_agent_loop_locks: Dict[Tuple[int, str], asyncio.Lock] = {}
_agent_loop_lock_refs: Dict[Tuple[int, str], int] = {}


def _get_agent_loop_lock(user_id: int, room_id) -> asyncio.Lock:
    key = (int(user_id), str(room_id))
    lock = _agent_loop_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _agent_loop_locks[key] = lock
        _agent_loop_lock_refs[key] = 1
    else:
        _agent_loop_lock_refs[key] += 1
    return lock


def _release_agent_loop_lock(user_id: int, room_id) -> None:
    key = (int(user_id), str(room_id))
    refs = _agent_loop_lock_refs.get(key, 0) - 1
    if refs <= 0:
        _agent_loop_locks.pop(key, None)
        _agent_loop_lock_refs.pop(key, None)
    else:
        _agent_loop_lock_refs[key] = refs


DECRYPT_FAILED = "Error: Could not decrypt message."


class ChatConsumer(AsyncWebsocketConsumer):
    # Define constants for key rotation
    KEY_ROTATION_INTERVAL = 36000 * 10  # Rotate key every 100 hours
    MESSAGES_BEFORE_ROTATION = 1000  # Rotate key after 1000 messages

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

        # 5. Join group FIRST. A channel-layer outage must not kill the
        # handshake — the socket stays up degraded (direct sends still work,
        # group events are dropped until Redis recovers).
        try:
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        except Exception as exc:
            logger.warning("Channel layer unavailable on connect (%s); joining room degraded", exc)

        room_id = getattr(current_chat, "id", None)
        user_id = self.scope["user"].id
        connection_id = uuid.uuid4().hex[:12]
        self._presence_room_id = room_id
        self._presence_user_id = user_id
        self._presence_connection_id = connection_id

        first_connection = False
        online_ids: set = set()
        if room_id is not None:
            first_connection, online_ids = await presence.connect(
                room_id, user_id, connection_id,
            )
        self._presence_ids = set(online_ids)

        # 6. Accept connection
        await self.accept()

        # 7. Announce online once, on the user's first connection.
        if first_connection:
            await self._broadcast_presence("online", timezone.now().isoformat())

        # 8. Send the current snapshot (one store read for the whole room).
        entries = []
        try:
            participants = await self.get_chatroom_participants(current_chat)
            entries = await self._build_presence(participants, online_ids, user_id)
        except Exception as e:
            logger.error(f"Error building presence snapshot: {e}")
            logger.error(traceback.format_exc())

        logger.info(f"Sending presence snapshot to {self.scope['user'].username}: {len(entries)} users")
        await self.send(text_data=json.dumps({
            "command": "presence_snapshot",
            "presence": entries,
        }))

        # 9. Keep this connection's member fresh until it closes.
        if room_id is not None:
            self._presence_task = asyncio.create_task(
                self._presence_loop(room_id, user_id, connection_id)
            )

    async def _broadcast_presence(self, status, last_seen):
        """Broadcast a human's presence change to the room (best effort)."""
        try:
            await asyncio.wait_for(
                self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "presence_update",
                        "user": self.scope["user"].username,
                        "status": status,
                        "last_seen": last_seen,
                        "kind": "human",
                    },
                ),
                timeout=2.0,
            )
        except Exception as exc:
            logger.warning("Presence broadcast skipped (%s); room sees stale presence", exc)

    async def _build_presence(self, participants, online_ids, connecting_user_id):
        """One entry per participant, from the room's online ids and the agent's status."""
        entries = []
        for member in participants:
            try:
                uname, uid = await sync_to_async(
                    lambda m: (m.User.username, m.User.id)
                )(member)
            except Exception as e:
                logger.error(f"Could not get username from member: {e}")
                continue

            # The bot has no socket, so its status is declared, not inferred.
            if uname == BOT_USERNAME:
                entries.append({
                    "user": uname,
                    "status": agent_status(),
                    "last_seen": None,
                    "kind": "agent",
                })
                continue

            is_online = uid in online_ids or uid == connecting_user_id
            last_seen = None
            if not is_online:
                member_last_seen = getattr(member, "last_seen", None)
                if member_last_seen is not None:
                    try:
                        last_seen = member_last_seen.isoformat()
                    except Exception:
                        last_seen = None
            entries.append({
                "user": uname,
                "status": "online" if is_online else "offline",
                "last_seen": last_seen,
                "kind": "human",
            })
        return entries

    async def _save_member_last_seen(self, user_id):
        """Save "now" as the member's last seen time and return it."""
        now = timezone.now()

        def _save():
            Member.objects.filter(User_id=user_id).update(last_seen=now)

        try:
            await sync_to_async(_save)()
        except Exception as exc:
            logger.debug("Member.last_seen save skipped: %s", exc)
        return now

    async def _presence_loop(self, room_id, user_id, connection_id):
        # A random first sleep spreads connections restored by a deploy.
        await asyncio.sleep(
            random.uniform(0, presence.beat_interval_seconds())  # nosec B311 — deploy jitter, not a security decision
        )
        while True:
            try:
                await self._presence_tick(room_id, user_id, connection_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Presence tick skipped: %s", exc)
            await asyncio.sleep(presence.beat_interval_seconds())

    async def _presence_tick(self, room_id, user_id, connection_id):
        # A store hiccup must not close the socket; the loop keeps going.
        try:
            online_ids = await presence.beat(room_id, user_id, connection_id)
            # None: the store could not be read. Keep what the page last showed
            # rather than turning the whole room offline.
            if online_ids is not None and online_ids != getattr(self, "_presence_ids", set()):
                chat = await self.get_current_chatroom(room_id)
                if chat is not None:
                    participants = await self.get_chatroom_participants(chat)
                    entries = await self._build_presence(participants, online_ids, user_id)
                    await self.send(text_data=json.dumps({
                        "command": "presence_snapshot",
                        "presence": entries,
                    }))
                    self._presence_ids = set(online_ids)
            self._presence_ticks = getattr(self, "_presence_ticks", 0) + 1
            if self._presence_ticks % 10 == 0:
                await self._save_member_last_seen(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Presence tick skipped: %s", exc)

    async def _stop_presence(self):
        task = getattr(self, "_presence_task", None)
        self._presence_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def __call__(self, scope, receive, send):
        # A beat made with create_task is not cancelled with the consumer and
        # `disconnect` does not run in every teardown, so stop it here too.
        try:
            await super().__call__(scope, receive, send)
        finally:
            task = getattr(self, "_presence_task", None)
            self._presence_task = None
            if task is not None:
                task.cancel()

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

        from .tasks import refresh_room_context_summary
        if not dispatch_task(refresh_room_context_summary, room_id, message_id, count):
            return

        cache.set(last_key, now.isoformat(), timeout=60 * 60 * 6)
        cache.delete(counter_key)

    async def schedule_idle_nudge_if_needed(self, room_id, user_id):
        """
        Schedule idle nudge only if:
        1. No nudge is already pending
        2. Workspace has idle_nudges_enabled
        """
        last_activity_key = f"proactive:last_activity:{room_id}:{user_id}"
        cache.set(last_activity_key, timezone.now().isoformat(), timeout=60 * 60 * 24)

        # Check for pending nudge already scheduled
        pending_key = f"proactive:pending:{room_id}:{user_id}"
        if cache.get(pending_key):
            return

        # Check if idle nudges are enabled for this user's workspace
        try:
            user = await sync_to_async(User.objects.get)(id=user_id)
            workspace = await sync_to_async(lambda: user.workspace)()
            if not workspace.should_use_idle_nudges():
                logger.debug(f"Idle nudges disabled for user {user_id} ({workspace.plan})")
                return
        except Exception as e:
            logger.warning(f"Error checking workspace for idle nudge: {e}")
            return

        # Queue the idle nudge task
        from .tasks import schedule_idle_nudge
        dispatch_task(schedule_idle_nudge, room_id, user_id)

    async def presence_update(self, event):
        logger.debug(f"presence_update received by {self.scope['user'].username}: {event}")

        payload = {
            "command": "presence",
            "user": event.get("user"),
            "status": event.get("status"),
            "kind": event.get("kind", "human"),
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

        # Stop this connection's beat first so it cannot re-add the member.
        await self._stop_presence()

        try:
            # 1. Leave the chat group with timeout
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_discard(self.room_group_name, self.channel_name),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Group discard timed out for {self.channel_name}")

            # 2. The user is offline only when this was their last connection.
            room_id = getattr(self, "_presence_room_id", None)
            user_id = getattr(self, "_presence_user_id", None)
            connection_id = getattr(self, "_presence_connection_id", None)
            if room_id is not None and connection_id is not None:
                last = await presence.disconnect(room_id, user_id, connection_id)
                if last:
                    seen_at = await self._save_member_last_seen(user_id)
                    await self._broadcast_presence("offline", seen_at.isoformat())

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
            if dispatch_task(moderate_message_batch, batch.id):
                logger.info(f"Triggered moderation batch {batch.id} for room {room_id}")
            else:
                logger.info(f"Moderation batch {batch.id} for room {room_id} left for the periodic sweep")
            return True

        return False

    async def _send_plain_ai_message(self, room_id, text: str) -> None:
        """Send a short bot message (used for @admin acknowledgements)."""
        def _create():
            kazi_user, _ = User.objects.get_or_create(
                username='kazi',
                defaults={
                    'first_name': 'Kazi',
                    'last_name': 'AI',
                    'is_active': True,
                    'email': 'kazi@kwikchat.ai',
                },
            )
            kazi_member, _ = Member.objects.get_or_create(User=kazi_user)
            message = Message.objects.create(
                member=kazi_member, content=text, timestamp=timezone.now(),
            )
            room = Chatroom.objects.filter(id=room_id).first()
            if room:
                room.chats.add(message)
                room.save()
            return message

        message = await sync_to_async(_create)()
        await self.send_chat_message({
            "command": "new_message",
            "message": await self.message_to_json(message),
        })

    async def _handle_admin_escalation(self, room_id, message_content: str, member_user) -> None:
        """Route @admin mentions to superusers; never to the bot."""
        cooldown_key = f"admin_escalation:{member_user.id}"
        if not cache.add(cooldown_key, 1, timeout=300):
            await self._send_plain_ai_message(
                room_id,
                "You recently contacted the admin. Please wait a few minutes before sending another request.",
            )
            return

        def _notify():
            from django.contrib.auth import get_user_model as _get_user_model

            from notifications.services import NotificationService
            from workflows.models import PersonaRequest

            room = Chatroom.objects.filter(id=room_id).first()
            persona_match = ADMIN_PERSONA_REQUEST_RE.search(message_content)
            if persona_match:
                name = persona_match.group(1).strip()[:100]
                already_pending = PersonaRequest.objects.filter(
                    user=member_user, name=name, status='pending',
                ).exists()
                if not already_pending:
                    request_row = PersonaRequest.objects.create(
                        user=member_user,
                        room=room,
                        name=name,
                        description=(persona_match.group(2) or "").strip()[:1000],
                    )
                    logger.info("@admin persona request %s created in room %s", request_row.id, room_id)

            for admin_user in _get_user_model().objects.filter(is_superuser=True, is_active=True):
                NotificationService.notify(
                    user=admin_user,
                    event_type="message.mention",
                    title=f"@admin from {member_user.username}",
                    body=message_content[:500],
                    related_room=room,
                    metadata={"room_id": room_id, "from_user_id": member_user.id},
                )

        await sync_to_async(_notify)()
        if ADMIN_PERSONA_REQUEST_RE.search(message_content):
            ack = "Persona request sent to the admin. You'll see it on your Personas page once reviewed."
        else:
            ack = "Message sent to the admin. They'll see it in their inbox."
        await self._send_plain_ai_message(room_id, ack)

    async def new_message(self, data):
        try:
            logger.info(
                "=== NEW MESSAGE START === room=%r chars=%s",
                data.get('chatid'), len(str(data.get('message') or '')),
            )

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

            logger.info("Step 4: Rate limit check...")
            # Rate limiting check
            if not await self.check_rate_limit(member_user.id):
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Rate limit exceeded. Please wait a moment.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info("Step 5: Get member...")
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
            logger.info("Step 6: Message length: %s", len(message_content))

            if not message_content or len(message_content) > 5000:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Invalid message content.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info("Step 8: Regular message, checking key rotation...")
            # Check for key rotation
            await self.check_key_rotation()

            logger.info("Step 9: Encrypting message...")
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

            logger.info("Step 10: Creating message in DB...")
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

            logger.info("Step 11: Getting chatroom...")
            current_chat = await self.get_current_chatroom(room_id)
            if not current_chat:
                await self.send_chat_message({
                    'member': 'security system',
                    'content': "Chatroom not found.",
                    'timestamp': str(timezone.now())
                })
                return

            logger.info("Step 12: Getting room members...")
            room_members = await self.get_chatroom_participants(current_chat)
            kazi_member = next((m for m in room_members if m.User.username == 'kazi'), None)
            human_members = [m for m in room_members if m.User.username != 'kazi']
            is_ai_room = bool(kazi_member) and len(human_members) == 1

            if member in room_members:
                logger.info("Step 13: Adding message to chatroom...")
                await sync_to_async(current_chat.chats.add)(message)
                await sync_to_async(current_chat.save)()

                logger.info("Step 14: Buffering for moderation...")
                # === NEW: Buffer message for moderation ===
                try:
                    await self.buffer_message_for_moderation(room_id, message.id)
                    logger.info("Buffering successful!")
                except Exception as e:
                    logger.error(f"ERROR in buffer_message_for_moderation: {e}")
                    logger.error(traceback.format_exc())
                    raise

                logger.info("Step 15: Sending to clients...")
                message_json = await self.message_to_json(message)
                content = {
                    "command": "new_message",
                    "message": message_json
                }
                await self.send_chat_message(content)
                logger.info("=== NEW MESSAGE SUCCESS ===")

                # Notify offline room participants
                try:
                    from notifications.services import NotificationService
                    await sync_to_async(NotificationService.notify_room_message)(
                        member_user, current_chat, message,
                    )
                except Exception as e:
                    logger.debug(f"Room notification dispatch skipped: {e}")

                # Queue room context refresh for summary/notes
                try:
                    await self.schedule_context_summary(room_id, message.id)
                except Exception as e:
                    logger.warning(f"Context summary refresh skipped: {e}")
                try:
                    await self.schedule_idle_nudge_if_needed(room_id, member_user.id)
                except Exception as e:
                    logger.warning(f"Idle nudge schedule skipped: {e}")

                # === ADMIN ESCALATION (@admin) ===
                # @admin messages go to superusers and are never routed to the
                # bot (persona requests open a PersonaRequest for approval).
                if ADMIN_MENTION_RE.search(message_content):
                    await self._handle_admin_escalation(room_id, message_content, member_user)
                    return

                # === ORCHESTRATION: Full pipeline ===
                should_route_ai = False
                ai_query = None
                addressed = strip_wake_word(message_content)
                if addressed is not None:
                    ai_query = addressed
                    should_route_ai = True
                elif is_ai_room:
                    ai_query = message_content.strip()
                    should_route_ai = True

                if should_route_ai:
                    logger.info("Step 16: @kazi detected! Starting orchestration...")

                    if ai_query:
                        from .context_manager import ContextManager
                        from orchestration.coordinator import OrchestrationCoordinator

                        async def send_chunk(correlation_id, chunk_text, is_final):
                            await self.channel_layer.group_send(
                                self.room_group_name,
                                {
                                    "type": "ai_stream_chunk",
                                    "chunk": chunk_text,
                                    "is_final": is_final,
                                    "correlation_id": correlation_id,
                                },
                            )

                        async def send_step_event(correlation_id, event_payload):
                            await self.channel_layer.group_send(
                                self.room_group_name,
                                {
                                    "type": "ai_step_event",
                                    "event": event_payload,
                                    "correlation_id": correlation_id,
                                },
                            )

                        speaker_note = ""

                        async def get_context_prompt():
                            base = await sync_to_async(ContextManager.get_context_prompt)(room_id) or ""
                            return "\n\n".join(part for part in (speaker_note, base) if part)

                        def bump_signals(actions):
                            from .tasks import update_proactive_signals
                            for action in actions:
                                update_proactive_signals(room_id, member_user.id, action)

                        agent_lock = _get_agent_loop_lock(member_user.id, room_id)
                        try:
                            await agent_lock.acquire()
                            # Read history under the lock, so a message queued behind a
                            # running turn sees that turn's reply.
                            history_rows = await self.get_history_rows(room_id, limit=8)
                            multi_user = len(human_members) > 1 or has_several_speakers(history_rows)
                            history_messages = build_history_messages(
                                history_rows,
                                exclude_message_id=message.id,
                                multi_user=multi_user,
                                max_chars=int(getattr(settings, "HISTORY_MAX_CHARS", 60000)),
                                viewer_user_id=member_user.id,
                            )
                            if multi_user:
                                speaker_note = (
                                    "Several people share this room. Earlier user turns are prefixed with "
                                    f"the speaker's name. The message you are answering is from {member_username}."
                                )
                            result = await OrchestrationCoordinator().handle_message(
                                query=ai_query,
                                user_id=member_user.id,
                                room_id=room_id,
                                username=member_username,
                                message_id=message.id,
                                history_messages=history_messages,
                                send_chunk=send_chunk,
                                send_step_event=send_step_event,
                                get_context_prompt=get_context_prompt,
                                bump_signals=bump_signals,
                            )
                        finally:
                            agent_lock.release()
                            _release_agent_loop_lock(member_user.id, room_id)

                        full_response_text = result.full_response

                        if result.persist and full_response_text.strip():
                            logger.info("Saving AI message to database (%s chars)", len(full_response_text))

                            # Encrypt the AI response
                            encrypted_message = await self.encrypt_message({
                                'content': full_response_text,
                                'timestamp': str(timezone.now()),
                                'tools': result.tools,
                                'by': member_user.id,
                                'harness': result.harness,
                            })

                            if encrypted_message:
                                # Create Kazi user/member if not exists
                                def _create_ai_message():
                                    ai_user, _ = User.objects.get_or_create(
                                        username='kazi',
                                        defaults={
                                            'first_name': 'Kazi',
                                            'last_name': 'AI',
                                            'is_active': True,
                                            'email': 'kazi@kwikchat.ai'
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

                                    # Trigger Kazi Voice Response (TTS) only in voice mode
                                    voice_enabled = False
                                    try:
                                        prefs = await sync_to_async(get_user_preferences)(member_user.id)
                                        voice_enabled = bool(prefs.get("ai_voice_enabled", False))
                                    except Exception as e:
                                        logger.warning(f"Voice preference check failed: {e}")
                                    if voice_enabled:
                                        dispatch_task(generate_voice_response, ai_message.id)

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

            # Notify offline room participants
            try:
                from notifications.services import NotificationService
                await sync_to_async(NotificationService.notify_room_message)(
                    member_user, current_chat, message,
                )
            except Exception as e:
                logger.debug(f"Room notification dispatch skipped: {e}")

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

            # Notify offline room participants
            try:
                from notifications.services import NotificationService
                await sync_to_async(NotificationService.notify_room_message)(
                    member_user, current_chat, message,
                )
            except Exception as e:
                logger.debug(f"Room notification dispatch skipped: {e}")

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
            "from": event["from"],
        }))

    async def ai_voice_ready(self, event):
        """Kazi voice response is ready"""
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

    async def ai_stream_chunk(self, event):
        """Handle streaming AI response chunks"""
        await self.send(text_data=json.dumps({
            "command": "ai_stream",
            "chunk": event.get('chunk'),
            "is_final": event.get('is_final', False)
        }))

    async def ai_step_event(self, event):
        """Handle structured orchestration progress updates."""
        await self.send(text_data=json.dumps({
            "command": "orchestration_step",
            "event": event.get("event") or {},
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
            def _fetch():
                return list(chat.participants.select_related('User'))
            participants_list = await sync_to_async(_fetch)()
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

        if ((current_time - self.last_key_rotation).total_seconds() >= self.KEY_ROTATION_INTERVAL
                or self.messages_since_rotation >= self.MESSAGES_BEFORE_ROTATION):
            # Pass the current room_name to re-initialize the secure session
            await self.initialize_secure_session(self.room_name)
            self.last_key_rotation = current_time
            self.messages_since_rotation = 0

    async def _decrypt_stored(self, message):
        """Return (content, payload) for a stored message; payload is {} when there is none."""
        db_content = message.content
        try:
            # The content from DB should be a JSON string with 'data' and 'nonce'
            parsed_payload = json.loads(db_content)
        except (json.JSONDecodeError, TypeError):
            # Old plaintext messages are not JSON.
            return db_content, {}
        if not (isinstance(parsed_payload, dict) and 'data' in parsed_payload and 'nonce' in parsed_payload):
            return db_content, {}
        decrypted_payload = await self.decrypt_message(parsed_payload['data'], parsed_payload['nonce'])
        if isinstance(decrypted_payload, dict) and 'content' in decrypted_payload:
            return decrypted_payload['content'], decrypted_payload
        return DECRYPT_FAILED, {}

    async def message_to_json(self, message):
        """Decrypts message content before sending to the client."""
        try:
            username = await sync_to_async(lambda: message.member.User.username)()
            final_content, _payload = await self._decrypt_stored(message)

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

    async def get_history_rows(self, room_id, limit=5):
        "Last N messages, oldest first, as (message_id, username, content, extra)."
        try:
            from .models import Chatroom
            get_room = sync_to_async(Chatroom.objects.get)
            room = await get_room(id=room_id)

            def _get_msgs():
                return list(room.chats.all().order_by('-timestamp')[:limit])

            messages = await sync_to_async(_get_msgs)()
            messages.reverse()

            rows = []
            for msg in messages:
                msg_json = await self.message_to_json(msg)
                content = msg_json.get('content', '')
                member = msg_json.get('member', 'Unknown')
                if not content or content == DECRYPT_FAILED or member == 'system':
                    continue
                _content, payload = await self._decrypt_stored(msg)
                extra = None
                if isinstance(payload, dict) and isinstance(payload.get('tools'), list):
                    extra = {
                        'tools': payload.get('tools') or [],
                        'by': payload.get('by'),
                        'harness': payload.get('harness') or '',
                    }
                rows.append((msg.id, member, content, extra))

            return rows
        except Exception as e:
            logger.error(f'Error getting history: {e}')
            return []
