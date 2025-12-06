from celery import shared_task
from django.utils import timezone
from django.core.cache import cache
import logging
import json
from .models import Message, Chatroom, ModerationBatch, UserModerationStatus, AIConversation
from django.contrib.auth import get_user_model
import os
import traceback
from django.conf import settings

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
        logger.error(f"Full traceback: {traceback.format_exc()}")
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
        
        logger.info(f"AI response sent to room {room_id}")
        
        return {'status': 'success', 'ai_reply': ai_reply}
        
    except Exception as e:
        logger.error(f"Error generating AI response: {e}")
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