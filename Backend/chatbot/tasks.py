from celery import shared_task
from django.utils import timezone
from django.core.cache import cache
import logging
import json
from datetime import datetime, timedelta
from base64 import b64decode, b64encode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidKey
from asgiref.sync import async_to_sync
from typing import Optional, Dict, Any, Tuple
from .models import (
    Message,
    Chatroom,
    ModerationBatch,
    UserModerationStatus,
    AIConversation,
    Reminder,
    DocumentUpload,
    RoomContext,
    RoomNote,
    DailySummary,
    Member,
)
from .context_manager import ContextManager
from orchestration.llm_client import get_llm_client, extract_json
from users.encryption import TokenEncryption
import pypdf
from PIL import Image
from django.contrib.auth import get_user_model
import os
import traceback
from django.conf import settings
import openai
from pydub import AudioSegment

logger = logging.getLogger(__name__)
User = get_user_model()
OpenAIError = getattr(openai, "OpenAIError", Exception)

# HF API imports (install: pip install huggingface_hub)
try:
    from huggingface_hub import InferenceClient
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    logger.warning("huggingface_hub not installed. AI features disabled.")


@shared_task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def moderate_message_batch(self, batch_id):
    """
    Process a batch of messages for moderation using HF API
    """
    if not HF_AVAILABLE:
        logger.error("HuggingFace not available")
        return {"error": "HF not installed"}
    
    try:
        batch = ModerationBatch.objects.get(id=batch_id)
        
        # Check if already processed
        if batch.status == 'processed':
            return {"status": "already_processed"}
        
        batch.status = 'processing'
        batch.save()
        
        logger.info(f"Processing moderation batch {batch_id}")
        
        # Initialize HF client
        hf_token = os.environ.get('HF_API_TOKEN', '')
        client = InferenceClient(token=hf_token if hf_token else None)
        
        flagged_messages = []
        
        # Get messages from batch
        message_ids = json.loads(batch.message_ids)
        messages = Message.objects.filter(id__in=message_ids)
        
        logger.info(f"Moderating {len(messages)} messages")
        
        for msg in messages:
            # Skip if already moderated
            cache_key = f"moderated:{msg.id}"
            if cache.get(cache_key):
                continue
            
            try:
                # Get message content (need to decrypt)
                content_data = json.loads(msg.content)
                
                # For now, we'll moderate based on message metadata
                # In production, you'd decrypt in a secure context
                
                # === REAL HF MODERATION ===
                # Use a simple approach: check if content exists and basic length
                # Real implementation would decrypt and check actual text
                
                # Placeholder check (replace with actual decryption + moderation)
                is_toxic = False
                reason = None
                
                # You can add actual HF moderation call here:
                # Example (if you had plaintext):
                # result = client.text_classification(
                #     text=plaintext,
                #     model="unitary/toxic-bert"
                # )
                # is_toxic = result[0]['label'] == 'toxic' and result[0]['score'] > 0.7
                
                # For now, flag based on message patterns (demo mode)
                # This will be replaced when we add decryption context
                
                if is_toxic:
                    flagged_messages.append({
                        'message_id': msg.id,
                        'user_id': msg.member.User.id,
                        'reason': reason or 'toxic_content'
                    })
                    
                    # Update user flag count
                    user_status, created = UserModerationStatus.objects.get_or_create(
                        user=msg.member.User,
                        room=batch.room,
                        defaults={'flag_count': 0, 'is_muted': False}
                    )
                    user_status.flag_count += 1
                    
                    # Auto-mute after threshold
                    flag_threshold = getattr(settings, 'MODERATION_FLAG_THRESHOLD', 3)
                    if user_status.flag_count >= flag_threshold:
                        user_status.is_muted = True
                        user_status.muted_at = timezone.now()
                    
                    user_status.save()
                    
                    logger.warning(f"Message {msg.id} flagged. User {msg.member.User.username} now has {user_status.flag_count} flags")
                
                # Mark as moderated
                cache.set(cache_key, True, 86400)  # 24h cache
                
            except Exception as e:
                logger.error(f"Error moderating message {msg.id}: {e}")
                continue
        
        # Update batch status
        batch.status = 'processed'
        batch.flagged_count = len(flagged_messages)
        batch.processed_at = timezone.now()
        batch.save()
        
        logger.info(f"Batch {batch_id} processed: {len(message_ids)} messages, {len(flagged_messages)} flagged")
        
        return {
            'batch_id': batch_id,
            'total': len(message_ids),
            'flagged': len(flagged_messages),
            'details': flagged_messages
        }
        
    except ModerationBatch.DoesNotExist:
        logger.error(f"Batch {batch_id} not found")
        return {"error": "batch_not_found"}
    except Exception as e:
        logger.error(f"Error in moderate_message_batch: {e}")
        logger.error(traceback.format_exc())
        # Retry on failure
        raise self.retry(exc=e)


@shared_task(ignore_result=True)
def process_pending_batches():
    """
    Periodic task to process batches that haven't been processed
    Runs every 5 minutes via Celery Beat
    """
    # Skip in DEBUG mode
    if settings.DEBUG:
        return {"queued": 0, "skipped": "DEBUG mode"}
    
    pending_batches = ModerationBatch.objects.filter(
        status='pending',
        created_at__lte=timezone.now() - timezone.timedelta(minutes=5)
    )
    
    count = 0
    for batch in pending_batches:
        moderate_message_batch.delay(batch.id)
        count += 1
    
    logger.info(f"Queued {count} pending moderation batches")
    return {"queued": count}


@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def generate_ai_response(self, room_id, user_id, user_message):
    """
    Generate AI assistant response with streaming support
    """
    if not HF_AVAILABLE:
        logger.error("HuggingFace not available")
        return {"error": "HF not installed"}
    
    try:
        user = User.objects.get(id=user_id)
        room = Chatroom.objects.get(id=room_id)
        
        # Get conversation context
        conversation, created = AIConversation.objects.get_or_create(
            user=user,
            room=room,
            defaults={'context': '[]', 'last_interaction': timezone.now()}
        )
        
        context = json.loads(conversation.context)[-3:]
        
        hf_token = os.environ.get('HF_API_TOKEN', '')
        client = InferenceClient(token=hf_token if hf_token else None)
        
        # Build messages for chat completion
        messages = [
        {
            "role": "system",
            "content": (
                "You are Mathia, an assistant within kwikchat which is part of the TaskShare freelancing platform. "
                "short responses are preferred. "
                " kiwkchat helps freelancers manage tasks and projects collaboratively, so your responses should focus on that context. "
                "offer knowlege about task management, project planning, and effective communication, but avoid going off-topic."
                "You can also help freelancers spot bad actors or scams by analyzing message patterns, but never accuse directly. "
                "if you detect potential scams or harmful behavior, respond with a polite warning about safety and suggest reporting to admins."
                " Taskshare is a gig-sharing platform connecting freelancers with clients for short-term projects or out sourcing needs,"
                "kwikchat is the integrated chat system within Taskshare that enables seamless communication between freelancers and clients regarding tasks, project updates, and collaboration and meetings."
                " To non taskshare freelancers kwikchat is a general purpose chat system for managing tasks and projects with others."
                "Non taskshare users can also generate invoices, plan meetings using calendy , manage simple tasks, and share files securely."
                "for disputes or complex issues, always suggest escalating to human support rather than trying to resolve directly."
                " in a dispute resolution room you should summarize the pain points for the human moderators rather than taking sides.if users insist on discussing legal, financial, or medical topics, firmly refuse and suggest consulting a qualified professional."
                "Your job is to help users communicate clearly and safely while discussing tasks and projects. "
                "Be concise, polite, and professional. "
                "Encourage respectful collaboration, and flag or refuse to generate content that includes harassment, spam, or scams. "
                "You can summarize conversations, clarify task details, and offer neutral guidance—never make legal, financial, or medical claims."
                f"\n\n{ContextManager.get_context_prompt(room_id)}"
            ),
        }
        ]

        # Add conversation history
        for exchange in context:
            messages.append({"role": "user", "content": exchange['user']})
            messages.append({"role": "assistant", "content": exchange['assistant']})
        
        messages.append({"role": "user", "content": user_message})
        
        models_to_try = [
            "meta-llama/Llama-3.2-3B-Instruct",
            "mistralai/Mistral-7B-Instruct-v0.2",
        ]
        
        ai_reply = None
        for model in models_to_try:
            try:
                response = client.chat_completion(
                    messages=messages,
                    model=model,
                    max_tokens=150,
                    temperature=0.7,
                    stream=True  # ✅ Enable streaming
                )
                
                # Collect full response and send chunks
                full_response = ""
                chunk_buffer = ""
                
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                room_group_name = f"chat_{room_id}"
                
                for chunk in response:
                    if hasattr(chunk.choices[0].delta, 'content'):
                        token = chunk.choices[0].delta.content
                        if token:
                            full_response += token
                            chunk_buffer += token
                            
                            # Send chunks every ~5 characters for smooth typing
                            if len(chunk_buffer) >= 5:
                                async_to_sync(channel_layer.group_send)(
                                    room_group_name,
                                    {
                                        "type": "ai_stream_chunk",
                                        "room_id": room_id,
                                        "chunk": chunk_buffer,
                                        "is_final": False
                                    }
                                )
                                chunk_buffer = ""
                
                # Send remaining buffer
                if chunk_buffer:
                    async_to_sync(channel_layer.group_send)(
                        room_group_name,
                        {
                            "type": "ai_stream_chunk",
                            "room_id": room_id,
                            "chunk": chunk_buffer,
                            "is_final": False
                        }
                    )
                
                ai_reply = full_response.strip()
                break
                
            except Exception as model_error:
                logger.warning(f"Model {model} failed: {model_error}")
                continue
        
        if not ai_reply or len(ai_reply) < 2:
            ai_reply = "I'm experiencing high demand. Please try again! 🤖"
        
        # Update conversation context
        context.append({
            'user': user_message,
            'assistant': ai_reply,
            'timestamp': timezone.now().isoformat()
        })
        conversation.context = json.dumps(context)
        conversation.message_count = len(json.loads(conversation.context))
        conversation.last_interaction = timezone.now()
        conversation.save()
        
        # Send final complete message
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "ai_response_message",
                "room_id": room_id,
                "user_id": user_id,
                "ai_reply": ai_reply
            }
        )
        
        return {'status': 'success', 'ai_reply': ai_reply}
        
    except Exception as e:
        logger.error(f"Error generating AI response: {e}")
        raise self.retry(exc=e)

@shared_task(bind=True, max_retries=3, ignore_result=True)
def transcribe_voice_note(self, message_id):
    """Transcribe user voice note using OpenAI Whisper"""
    try:
        message = Message.objects.get(id=message_id)
        if not message.audio_url:
            return "No audio URL found"

        file_path = os.path.join(settings.MEDIA_ROOT, message.audio_url)
        
        # OpenAI Whisper
        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        
        message.voice_transcript = transcript.text
        message.content = transcript.text # Update content so AI can read it
        message.save()
        
        # Notify room via WebSocket
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        room = message.chatroom_set.first()
        if room:
            async_to_sync(channel_layer.group_send)(
                f"chat_{room.id}",
                {
                    "type": "voice_transcription_ready",
                    "message_id": message.id,
                    "transcript": transcript.text
                }
            )
        
        return transcript.text
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise self.retry(exc=e)

@shared_task(bind=True, max_retries=1, default_retry_delay=300, ignore_result=True)
def generate_voice_response(self, message_id):
    """Generate audio for AI response using OpenAI TTS"""
    try:
        if not getattr(settings, "AI_VOICE_ENABLED", True):
            return {"status": "skipped", "reason": "disabled"}

        message = Message.objects.get(id=message_id)
        if message.has_ai_voice:
            return {"status": "skipped", "reason": "already_generated"}

        if message.timestamp and (timezone.now() - message.timestamp) > timedelta(hours=1):
            return {"status": "skipped", "reason": "stale"}

        room = message.chatroom_set.first()
        if room:
            participants = list(room.participants.select_related('User__profile'))
            if participants:
                prefs = []
                for member in participants:
                    profile = getattr(member.User, "profile", None) if member else None
                    settings_json = profile.notification_preferences if profile and profile.notification_preferences else {}
                    prefs.append(bool(settings_json.get("ai_voice_enabled", True)))
                if prefs and not any(prefs):
                    return {"status": "skipped", "reason": "disabled_by_users"}

        text = message.content
        if isinstance(text, str) and '"data"' in text and '"nonce"' in text:
            cipher = _get_room_cipher(room) if room else None
            text = _decrypt_message_content(message, cipher) or ""

        text = (text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "empty_text"}
        
        # OpenAI TTS
        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        
        response = client.audio.speech.create(
            model="tts-1",
            voice="alloy", # alloy, echo, fable, onyx, nova, shimmer
            input=text
        )
        
        # Save audio file
        room_id = message.chatroom_set.first().id
        voice_dir = os.path.join(settings.MEDIA_ROOT, 'ai_speech', str(room_id))
        os.makedirs(voice_dir, exist_ok=True)
        
        file_name = f"reply_{message.id}.mp3"
        file_path = os.path.join(voice_dir, file_name)
        response.stream_to_file(file_path)
        
        message.audio_url = os.path.join('ai_speech', str(room_id), file_name)
        message.has_ai_voice = True
        message.save()
        
        # Notify room via WebSocket
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{room_id}",
            {
                "type": "ai_voice_ready",
                "message_id": message.id,
                "audio_url": message.audio_url
            }
        )
        
        return message.audio_url
    except OpenAIError as e:
        error_text = str(e)
        logger.error(f"TTS error: {error_text}")
        if "insufficient_quota" in error_text or "Error code: 429" in error_text:
            return {"status": "skipped", "reason": "quota_exceeded"}
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise self.retry(exc=e)


@shared_task(ignore_result=True)
def moderate_text_realtime(text_content):
    """
    Real-time text moderation using HF (optional, for suspicious content)
    This runs async and logs results, doesn't block message sending
    """
    if not HF_AVAILABLE:
        return {"error": "HF not available"}
    
    try:
        hf_token = os.environ.get('HF_API_TOKEN', '')
        client = InferenceClient(token=hf_token if hf_token else None)
        
        # Use lightweight toxicity model
        result = client.text_classification(
            text=text_content,
            model="unitary/toxic-bert"
        )
        
        # Log if toxic (for admin review)
        if result and result[0]['label'] == 'toxic' and result[0]['score'] > 0.7:
            logger.warning(f"Toxic content detected: {text_content[:50]}... Score: {result[0]['score']}")
            return {"toxic": True, "score": result[0]['score']}
        
        return {"toxic": False}
        
    except Exception as e:
        logger.error(f"Realtime moderation error: {e}")
        return {"error": str(e)}


@shared_task(ignore_result=True)
def cleanup_old_moderation_batches():
    """
    Cleanup processed batches older than 30 days
    """
    cutoff = timezone.now() - timezone.timedelta(days=30)
    deleted = ModerationBatch.objects.filter(
        status='processed',
        processed_at__lt=cutoff
    ).delete()
    
    logger.info(f"Cleaned up {deleted[0]} old moderation batches")
    return {"deleted": deleted[0]}


@shared_task(ignore_result=True)
def check_due_reminders():
    """
    Periodic task to check for due reminders and send notifications
    """
    now = timezone.now()
    due_reminders = Reminder.objects.filter(
        status='pending',
        scheduled_time__lte=now
    ).select_related('user')
    
    count = 0
    for reminder in due_reminders:
        try:
            send_reminder.delay(reminder.id)
            count += 1
        except Exception as e:
            logger.error(f"Error queueing reminder {reminder.id}: {e}")
            
    return {"processed": count}


def schedule_reminder_delivery(reminder_id: int, scheduled_time):
    if not scheduled_time:
        return
    if timezone.is_naive(scheduled_time):
        scheduled_time = timezone.make_aware(scheduled_time)
    send_reminder.apply_async((reminder_id,), eta=scheduled_time)


def _deliver_reminder(reminder: Reminder) -> bool:
    logger.info(f"Sending reminder {reminder.id}: {reminder.content}")

    # Rate limit: max 10 sends per user per 12h
    rl_key = f"reminder_send_count:{reminder.user_id}"
    sends = cache.get(rl_key, 0)
    if sends >= 10:
        reminder.status = 'failed'
        reminder.error_log = "Rate limit exceeded (10/12h)"
        reminder.save(update_fields=['status', 'error_log'])
        logger.warning(f"Reminder {reminder.id} blocked by rate limit for user {reminder.user_id}")
        return False

    channels = []
    if reminder.via_whatsapp:
        channels.append('whatsapp')
    if reminder.via_email:
        channels.append('email')
    if not channels:
        channels = ['email']

    sent = False
    errors = []

    for ch in channels:
        if ch == 'whatsapp':
            try:
                from orchestration.connectors.whatsapp_connector import WhatsAppConnector
                wa = WhatsAppConnector()
                resp = wa.send_message(
                    to=getattr(reminder.user, 'phone_number', None) or "",
                    body=f"Reminder: {reminder.content}"
                )
                if resp.get("status") == "sent":
                    sent = True
                    break
                errors.append(str(resp))
            except Exception as e:
                errors.append(str(e))
        elif ch == 'email':
            try:
                from asgiref.sync import async_to_sync
                from orchestration.connectors.gmail_connector import GmailConnector
                gmail = GmailConnector()
                resp = async_to_sync(gmail.execute)({
                    "action": "send_email",
                    "to": getattr(reminder.user, 'email', None),
                    "subject": "Reminder",
                    "text": reminder.content
                }, {"user_id": reminder.user_id})
                if resp.get("status") in ("sent", "success"):
                    sent = True
                    break
                errors.append(str(resp))
            except Exception as e:
                errors.append(str(e))

    if sent:
        reminder.status = 'sent'
        reminder.error_log = ''
        reminder.save(update_fields=['status', 'error_log'])
        if sends == 0:
            cache.set(rl_key, 1, 60 * 60 * 12)
        else:
            cache.incr(rl_key)
            cache.expire(rl_key, 60 * 60 * 12)
        return True

    reminder.status = 'failed'
    reminder.error_log = "; ".join(errors)[:500]
    reminder.save(update_fields=['status', 'error_log'])
    logger.error(f"Reminder {reminder.id} failed: {reminder.error_log}")
    return False


@shared_task(ignore_result=True)
def send_reminder(reminder_id: int):
    reminder = Reminder.objects.filter(id=reminder_id).select_related('user').first()
    if not reminder:
        return {"status": "skipped", "reason": "not_found"}
    if reminder.status != 'pending':
        return {"status": "skipped", "reason": "not_pending"}

    now = timezone.now()
    scheduled_time = reminder.scheduled_time
    if timezone.is_naive(scheduled_time):
        scheduled_time = timezone.make_aware(scheduled_time)

    if scheduled_time > now + timedelta(minutes=1):
        schedule_reminder_delivery(reminder_id, scheduled_time)
        return {"status": "rescheduled", "run_at": scheduled_time.isoformat()}

    try:
        _deliver_reminder(reminder)
        return {"status": "sent"}
    except Exception as e:
        logger.error(f"Error sending reminder {reminder.id}: {e}")
        reminder.status = 'failed'
        reminder.save(update_fields=['status'])
        return {"status": "error", "reason": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def process_document_task(self, document_id):
    """
    Extract text and metadata from uploaded documents
    """
    try:
        doc = DocumentUpload.objects.get(id=document_id)
        doc.status = 'processing'
        doc.save()
        
        logger.info(f"Processing document {document_id} ({doc.file_type})")
        
        file_path = doc.file_path
        # If using local storage, file_path needs to be joined with MEDIA_ROOT
        full_path = os.path.join(settings.MEDIA_ROOT, file_path)
        
        if not os.path.exists(full_path):
            doc.status = 'failed'
            doc.processed_text = "File not found on storage"
            doc.save()
            return {"error": "file_not_found"}
            
        extracted_text = ""
        metadata = {}
        
        if doc.file_type == 'pdf':
            extracted_text, metadata = extract_text_from_pdf(full_path)
        elif doc.file_type == 'image':
            extracted_text, metadata = extract_text_from_image(full_path)
            
        doc.processed_text = extracted_text
        doc.extracted_metadata = metadata
        doc.status = 'completed'
        doc.save()
        
        logger.info(f"Successfully processed document {document_id}")
        return {"status": "success", "length": len(extracted_text)}
        
    except DocumentUpload.DoesNotExist:
        logger.error(f"Document {document_id} not found")
        return {"error": "doc_not_found"}
    except Exception as e:
        logger.error(f"Error processing document {document_id}: {e}")
        logger.error(traceback.format_exc())
        
        # Update status to failed
        try:
            doc = DocumentUpload.objects.get(id=document_id)
            doc.status = 'failed'
            doc.save()
        except:
            pass
            
        raise self.retry(exc=e)


def extract_text_from_pdf(file_path):
    """Utility to extract text from a PDF file"""
    text = ""
    metadata = {}
    try:
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            metadata = {
                "pages": len(reader.pages),
                "info": reader.metadata if reader.metadata else {}
            }
            
            # Extract from first 10 pages to avoid prompt bloat
            for i in range(min(len(reader.pages), 10)):
                text += reader.pages[i].extract_text() + "\n"
                
        return text.strip(), metadata
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return f"Extraction failed: {str(e)}", {}


def extract_text_from_image(file_path):
    """Utility to extract metadata and basic info from an image"""
    metadata = {}
    try:
        with Image.open(file_path) as img:
            metadata = {
                "format": img.format,
                "size": img.size,
                "mode": img.mode
            }
        return "Indexed image metadata.", metadata
    except Exception as e:
        logger.error(f"Image extraction error: {e}")
        return f"Image extraction failed: {str(e)}", {}


def _normalize_base64(value):
    if not isinstance(value, str):
        return None
    value = value.strip().replace(" ", "+")
    padding = 4 - (len(value) % 4)
    if padding < 4:
        value += "=" * padding
    return value


def _get_room_cipher(chatroom):
    key = chatroom.encryption_key
    if key and key.startswith("enc:"):
        key = TokenEncryption.safe_decrypt(key[4:], default=None)
    if not key:
        return None
    try:
        session_key = b64decode(key.encode("utf-8"))
        return AESGCM(session_key)
    except Exception as exc:
        logger.warning(f"Context summary: failed to init AESGCM ({exc})")
        return None


def _decrypt_message_content(message, cipher):
    raw_content = message.content
    try:
        parsed_payload = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return raw_content if isinstance(raw_content, str) else ""

    if not isinstance(parsed_payload, dict) or "data" not in parsed_payload or "nonce" not in parsed_payload:
        return raw_content if isinstance(raw_content, str) else ""

    if cipher is None:
        return ""

    data = _normalize_base64(parsed_payload.get("data"))
    nonce = _normalize_base64(parsed_payload.get("nonce"))
    if not data or not nonce:
        return ""

    try:
        decrypted = cipher.decrypt(b64decode(nonce), b64decode(data), None)
        payload = json.loads(decrypted.decode("utf-8"))
        if isinstance(payload, dict):
            return payload.get("content", "")
        return ""
    except InvalidKey:
        logger.warning("Context summary: invalid decryption key.")
        return ""
    except Exception as exc:
        logger.warning(f"Context summary: decrypt failed ({exc})")
        return ""


def _coerce_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


@shared_task(bind=True, max_retries=2, default_retry_delay=60, ignore_result=True)
def refresh_room_context_summary(self, room_id, message_id=None, message_delta=1):
    """
    Update RoomContext summary/topics/notes with lightweight throttling.
    """
    try:
        room = Chatroom.objects.get(id=room_id)
        context, _ = RoomContext.objects.get_or_create(chatroom=room)
        now = timezone.now()

        min_messages = getattr(settings, "CONTEXT_SUMMARY_MIN_MESSAGES", 6)
        min_minutes = getattr(settings, "CONTEXT_SUMMARY_MIN_MINUTES", 10)
        max_minutes = getattr(settings, "CONTEXT_SUMMARY_MAX_MINUTES", 120)
        max_messages = getattr(settings, "CONTEXT_SUMMARY_MAX_MESSAGES", 40)

        try:
            delta = int(message_delta)
        except (TypeError, ValueError):
            delta = 1
        if delta < 1:
            delta = 1
        context.message_count = (context.message_count or 0) + delta
        minutes_since = None
        if context.last_compressed_at:
            minutes_since = (now - context.last_compressed_at).total_seconds() / 60.0

        should_summarize = False
        if context.message_count >= min_messages and (minutes_since is None or minutes_since >= min_minutes):
            should_summarize = True
        elif minutes_since is not None and minutes_since >= max_minutes:
            should_summarize = True

        if not should_summarize:
            context.save(update_fields=["message_count", "updated_at"])
            return {"status": "skipped", "reason": "throttled"}

        messages = list(
            room.chats.select_related("member__User").order_by("-timestamp")[:max_messages]
        )
        messages.reverse()

        cipher = _get_room_cipher(room)
        lines = []
        for msg in messages:
            content = _decrypt_message_content(msg, cipher)
            if not content:
                continue
            member_name = "Unknown"
            if msg.member and getattr(msg.member, "User", None):
                member_name = msg.member.User.username
            content = content.strip()
            if not content:
                continue
            if len(content) > 600:
                content = content[:600] + "..."
            lines.append(f"{member_name}: {content}")

        if not lines:
            context.save(update_fields=["message_count", "updated_at"])
            return {"status": "skipped", "reason": "no_content"}

        system_prompt = "\n".join([
            "You summarize chatroom context for an AI assistant.",
            "Return JSON only with keys:",
            "summary: 2-4 sentence summary.",
            "active_topics: list of up to 5 short topics.",
            "notes: list of objects {type, content, priority}.",
            "highlights: list of up to 5 short strings.",
            "Valid note types: decision, action_item, insight, reminder, reference.",
            "Valid priorities: low, medium, high.",
        ])

        user_prompt = "\n".join([
            f"Existing summary: {context.summary or ''}",
            "",
            "Conversation (most recent last):",
            "\n".join(lines),
            "",
            "Return JSON only.",
        ])

        llm = get_llm_client()
        response_text = async_to_sync(llm.generate_text)(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=700,
            json_mode=True,
        )
        payload = extract_json(response_text) or {}

        summary_text = str(payload.get("summary") or "").strip()
        active_topics = _coerce_list(payload.get("active_topics"))[:5]
        notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
        highlights = _coerce_list(payload.get("highlights"))[:5]

        if summary_text:
            context.summary = summary_text
        if active_topics:
            context.active_topics = active_topics

        context.last_compressed_at = now
        context.message_count = 0
        context.save(update_fields=[
            "summary",
            "active_topics",
            "last_compressed_at",
            "message_count",
            "updated_at",
        ])

        created_notes = 0
        allowed_types = {"decision", "action_item", "insight", "reminder", "reference"}
        allowed_priorities = {"low", "medium", "high"}
        for note in notes[:8]:
            if not isinstance(note, dict):
                continue
            note_type = str(note.get("type") or "insight").strip()
            if note_type not in allowed_types:
                note_type = "insight"
            content = str(note.get("content") or "").strip()
            if not content:
                continue
            priority = str(note.get("priority") or "medium").strip()
            if priority not in allowed_priorities:
                priority = "medium"
            if RoomNote.objects.filter(
                room_context=context,
                content=content,
                note_type=note_type,
                created_at__gte=now - timedelta(days=7)
            ).exists():
                continue
            RoomNote.objects.create(
                room_context=context,
                note_type=note_type,
                content=content,
                created_by=None,
                is_ai_generated=True,
                priority=priority,
                tags=note.get("tags") if isinstance(note.get("tags"), list) else [],
            )
            created_notes += 1

        daily_summary, created = DailySummary.objects.get_or_create(
            room_context=context,
            date=now.date(),
            defaults={
                "summary": summary_text or context.summary or "",
                "highlights": highlights,
                "message_count": len(messages),
                "participant_count": room.participants.count(),
                "notes_created": created_notes,
            },
        )
        if not created:
            if summary_text:
                daily_summary.summary = summary_text
            if highlights:
                daily_summary.highlights = highlights
            daily_summary.message_count = len(messages)
            daily_summary.participant_count = room.participants.count()
            daily_summary.notes_created = daily_summary.notes_created + created_notes
            daily_summary.save(update_fields=[
                "summary",
                "highlights",
                "message_count",
                "participant_count",
                "notes_created",
            ])

        return {"status": "success", "notes_created": created_notes}

    except Chatroom.DoesNotExist:
        logger.warning(f"Context summary: room {room_id} not found")
        return {"status": "skipped", "reason": "room_not_found"}
    except Exception as exc:
        logger.error(f"Context summary failed: {exc}")
        logger.error(traceback.format_exc())
        return {"status": "error", "reason": str(exc)}


def _encode_base64(data: bytes) -> str:
    return b64encode(data).decode("utf-8").rstrip("=") + "=" * (-len(data) % 4)


def _encrypt_message_for_room(room: Chatroom, content: str) -> Optional[Dict[str, str]]:
    cipher = _get_room_cipher(room)
    if cipher is None:
        return None
    nonce = os.urandom(12)
    payload = json.dumps({
        "content": content,
        "timestamp": str(timezone.now()),
    }).encode("utf-8")
    encrypted = cipher.encrypt(nonce, payload, None)
    return {
        "data": _encode_base64(encrypted),
        "nonce": _encode_base64(nonce),
    }


_SIGNAL_TTL_SECONDS = 60 * 60 * 48
_SIGNAL_CATEGORY_MAP = {
    "search_flights": "travel",
    "search_hotels": "travel",
    "search_buses": "travel",
    "search_transfers": "travel",
    "search_events": "travel",
    "create_itinerary": "travel",
    "send_email": "communication",
    "send_whatsapp": "communication",
    "set_reminder": "productivity",
    "find_jobs": "jobs",
    "search_info": "research",
    "workflow_run": "automation",
}


def _signal_cache_key(room_id: int, user_id: int) -> str:
    return f"proactive:signals:{room_id}:{user_id}"


def get_proactive_signals(room_id: int, user_id: int) -> Dict[str, Any]:
    return cache.get(_signal_cache_key(room_id, user_id)) or {}


def update_proactive_signals(room_id: int, user_id: int, action: str) -> Dict[str, Any]:
    if not room_id or not user_id or not action:
        return {}
    key = _signal_cache_key(room_id, user_id)
    signals = cache.get(key) or {}
    counts = signals.get("counts", {})
    categories = signals.get("categories", {})
    counts[action] = counts.get(action, 0) + 1
    category = _SIGNAL_CATEGORY_MAP.get(action)
    if category:
        categories[category] = categories.get(category, 0) + 1
    signals.update({
        "counts": counts,
        "categories": categories,
        "last_action": action,
        "last_action_at": timezone.now().isoformat(),
    })
    cache.set(key, signals, timeout=_SIGNAL_TTL_SECONDS)
    return signals


def _get_proactive_settings(user) -> Dict[str, Any]:
    prefs = {}
    if hasattr(user, "profile") and user.profile:
        prefs = user.profile.notification_preferences or {}
    return {
        "enabled": prefs.get("proactive_assistant_enabled", True),
        "nudge_frequency": prefs.get("nudge_frequency", "low"),
        "snoozed_until": prefs.get("proactive_snooze_until"),
    }


def _proactive_allowed(user) -> bool:
    settings_flag = getattr(settings, "PROACTIVE_ASSISTANT_ENABLED", True)
    if not settings_flag:
        return False
    prefs = _get_proactive_settings(user)
    if not prefs.get("enabled", True):
        return False
    snoozed_until = prefs.get("snoozed_until")
    if snoozed_until:
        try:
            until = datetime.fromisoformat(str(snoozed_until))
            if until.tzinfo is None:
                until = timezone.make_aware(until)
            if timezone.now() < until:
                return False
        except Exception:
            pass
    try:
        goal_profile = getattr(user.workspace, "goals", None)
        if goal_profile and not goal_profile.ai_personalization_enabled:
            return False
    except Exception:
        pass
    return True


def _nudge_gap_minutes(frequency: str) -> int:
    mapping = {
        "low": 360,
        "medium": 120,
        "high": 30,
    }
    return mapping.get(str(frequency).lower(), 360)


def _build_nudge_message(user, room: Chatroom, signals: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[str]]:
    signals = signals or get_proactive_signals(room.id, user.id)
    counts = signals.get("counts", {})
    categories = signals.get("categories", {})

    if categories.get("travel", 0) >= 3 and counts.get("create_itinerary", 0) == 0:
        return "Based on your recent travel searches, want me to build an itinerary for you?", "travel_itinerary"
    if categories.get("communication", 0) >= 3 and counts.get("workflow_run", 0) == 0:
        return "Based on your recent updates, want me to automate those messages as a workflow?", "communication_automation"
    if counts.get("set_reminder", 0) >= 3:
        return "You have been setting reminders lately. Want a recurring workflow instead?", "recurring_reminders"

    try:
        from payments.models import PaymentRequest
        from workflows.models import UserWorkflow
    except Exception:
        PaymentRequest = None
        UserWorkflow = None

    has_workflow = UserWorkflow.objects.filter(user=user).exists() if UserWorkflow else False
    has_invoice = PaymentRequest.objects.filter(issuer=user).exists() if PaymentRequest else False
    has_reminder = Reminder.objects.filter(user=user).exists()

    if not has_workflow:
        return "Want me to set up a simple workflow for you? I can draft one based on what you're working on.", "no_workflow"
    if not has_invoice:
        return "Need to get paid faster? I can create a quick invoice link you can share.", "no_invoice"
    if not has_reminder:
        return "Want me to set a reminder so nothing slips through the cracks?", "no_reminder"

    context = getattr(room, "context", None)
    if context and context.summary:
        return "Want me to turn this room summary into next steps or a checklist?", "summary_checklist"
    return None, None


@shared_task(ignore_result=True)
def schedule_idle_nudge(room_id: int, user_id: int):
    try:
        user = User.objects.filter(id=user_id).first()
        room = Chatroom.objects.filter(id=room_id).first()
        if not user or not room:
            return {"status": "skipped", "reason": "missing_user_or_room"}
        if not _proactive_allowed(user):
            return {"status": "skipped", "reason": "disabled"}

        now = timezone.now()
        last_activity_key = f"proactive:last_activity:{room_id}:{user_id}"
        cache.set(last_activity_key, now.isoformat(), timeout=60 * 60 * 24)

        idle_minutes = getattr(settings, "PROACTIVE_IDLE_MINUTES", 10)
        pending_key = f"proactive:pending:{room_id}:{user_id}"
        pending_until = cache.get(pending_key)
        if pending_until:
            return {"status": "skipped", "reason": "pending_exists"}

        scheduled_at = now + timedelta(minutes=idle_minutes)
        cache.set(pending_key, scheduled_at.isoformat(), timeout=(idle_minutes * 60) + 60)
        send_idle_nudge.apply_async(
            (room_id, user_id, scheduled_at.isoformat()),
            countdown=idle_minutes * 60,
        )
        return {"status": "scheduled", "run_at": scheduled_at.isoformat()}
    except Exception as exc:
        logger.error(f"Idle nudge schedule failed: {exc}")
        logger.error(traceback.format_exc())
        return {"status": "error", "reason": str(exc)}


@shared_task(bind=True, max_retries=1, default_retry_delay=60, ignore_result=True)
def send_idle_nudge(self, room_id: int, user_id: int, scheduled_at_iso: str):
    try:
        user = User.objects.filter(id=user_id).first()
        room = Chatroom.objects.filter(id=room_id).first()
        if not user or not room:
            return {"status": "skipped", "reason": "missing_user_or_room"}
        if not _proactive_allowed(user):
            return {"status": "skipped", "reason": "disabled"}

        last_activity_key = f"proactive:last_activity:{room_id}:{user_id}"
        last_activity_iso = cache.get(last_activity_key)
        if last_activity_iso:
            last_activity = datetime.fromisoformat(last_activity_iso)
            scheduled_at = datetime.fromisoformat(scheduled_at_iso)
            if last_activity > scheduled_at:
                cache.delete(f"proactive:pending:{room_id}:{user_id}")
                return {"status": "skipped", "reason": "recent_activity"}

        prefs = _get_proactive_settings(user)
        gap_minutes = _nudge_gap_minutes(prefs.get("nudge_frequency", "low"))
        last_nudge_key = f"proactive:last_nudge:{room_id}:{user_id}"
        last_nudge_iso = cache.get(last_nudge_key)
        if last_nudge_iso:
            last_nudge = timezone.datetime.fromisoformat(last_nudge_iso)
            if (timezone.now() - last_nudge).total_seconds() < gap_minutes * 60:
                cache.delete(f"proactive:pending:{room_id}:{user_id}")
                return {"status": "skipped", "reason": "rate_limited"}

        signals = get_proactive_signals(room_id, user_id)
        message_text, reason = _build_nudge_message(user, room, signals=signals)
        if not message_text:
            cache.delete(f"proactive:pending:{room_id}:{user_id}")
            return {"status": "skipped", "reason": "no_message"}

        dismissed_key = f"proactive:dismissed:{room_id}:{user_id}"
        dismissed = cache.get(dismissed_key) or []
        if reason and reason in dismissed:
            cache.delete(f"proactive:pending:{room_id}:{user_id}")
            return {"status": "skipped", "reason": "dismissed"}

        encrypted_payload = _encrypt_message_for_room(room, message_text)
        if not encrypted_payload:
            cache.delete(f"proactive:pending:{room_id}:{user_id}")
            return {"status": "skipped", "reason": "encryption_failed"}

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
            ai_member = Member.objects.filter(User=ai_user).first()
            if not ai_member:
                ai_member = Member.objects.create(User=ai_user)
            payload = json.dumps({
                "data": encrypted_payload["data"],
                "nonce": encrypted_payload["nonce"],
            })
            return Message.objects.create(
                member=ai_member,
                content=payload,
                timestamp=timezone.now()
            )

        ai_message = _create_ai_message()
        room.chats.add(ai_message)
        room.save()

        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{room_id}",
            {
                "type": "ai_message_saved",
                "message": {
                    "id": ai_message.id,
                    "member": "mathia",
                    "content": message_text,
                    "timestamp": str(ai_message.timestamp),
                    "parent_id": ai_message.parent_id,
                }
            }
        )

        cache.set(last_nudge_key, timezone.now().isoformat(), timeout=60 * 60 * 24)
        if reason:
            cache.set(
                f"proactive:last_reason:{room_id}:{user_id}",
                reason,
                timeout=60 * 60 * 24,
            )
        cache.delete(f"proactive:pending:{room_id}:{user_id}")
        return {"status": "sent"}
    except Exception as exc:
        logger.error(f"Idle nudge failed: {exc}")
        logger.error(traceback.format_exc())
        cache.delete(f"proactive:pending:{room_id}:{user_id}")
        return {"status": "error", "reason": str(exc)}
