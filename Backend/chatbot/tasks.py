from celery import shared_task
from django.utils import timezone
from django.core.cache import cache
import logging
import json
from .models import Message, Chatroom, ModerationBatch, UserModerationStatus, AIConversation, Reminder, DocumentUpload
from .context_manager import ContextManager
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

# HF API imports (install: pip install huggingface_hub)
try:
    from huggingface_hub import InferenceClient
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    logger.warning("huggingface_hub not installed. AI features disabled.")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
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


@shared_task
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


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
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

@shared_task(bind=True, max_retries=3)
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

@shared_task(bind=True, max_retries=3)
def generate_voice_response(self, message_id):
    """Generate audio for AI response using OpenAI TTS"""
    try:
        message = Message.objects.get(id=message_id)
        text = message.content
        
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
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise self.retry(exc=e)


@shared_task
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


@shared_task
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


@shared_task
def check_due_reminders():
    """
    Periodic task to check for due reminders and send notifications
    """
    now = timezone.now()
    due_reminders = Reminder.objects.filter(
        status='pending',
        scheduled_time__lte=now
    )
    
    count = 0
    for reminder in due_reminders:
        try:
            logger.info(f"Sending reminder {reminder.id}: {reminder.content}")

            # Rate limit: max 10 sends per user per 12h
            rl_key = f"reminder_send_count:{reminder.user_id}"
            sends = cache.get(rl_key, 0)
            if sends >= 10:
                reminder.status = 'failed'
                reminder.error_log = "Rate limit exceeded (10/12h)"
                reminder.save(update_fields=['status', 'error_log'])
                logger.warning(f"Reminder {reminder.id} blocked by rate limit for user {reminder.user_id}")
                continue

            # Choose channels based on flags; try primary then fallback
            channels = []
            if reminder.via_whatsapp:
                channels.append('whatsapp')
            if reminder.via_email:
                channels.append('email')
            if not channels:
                channels = ['email']  # default fallback

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
                        from orchestration.connectors.mailgun_connector import MailgunConnector
                        mg = MailgunConnector()
                        resp = mg.execute({
                            "action": "send_email",
                            "to": getattr(reminder.user, 'email', None),
                            "subject": "Reminder",
                            "body": reminder.content
                        }, {"user_id": reminder.user_id})
                        if resp.get("status") == "sent":
                            sent = True
                            break
                        errors.append(str(resp))
                    except Exception as e:
                        errors.append(str(e))

            if sent:
                count += 1
                reminder.status = 'sent'
                reminder.error_log = ''
                reminder.save(update_fields=['status', 'error_log'])
                if sends == 0:
                    cache.set(rl_key, 1, 60 * 60 * 12)
                else:
                    cache.incr(rl_key)
                    cache.expire(rl_key, 60 * 60 * 12)
            else:
                reminder.status = 'failed'
                reminder.error_log = "; ".join(errors)[:500]
                reminder.save(update_fields=['status', 'error_log'])
                logger.error(f"Reminder {reminder.id} failed: {reminder.error_log}")
            
        except Exception as e:
            logger.error(f"Error sending reminder {reminder.id}: {e}")
            reminder.status = 'failed'
            reminder.save()
            
    return {"processed": count}


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
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
