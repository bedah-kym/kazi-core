from django.db import models
from django.contrib.auth import get_user_model
from base64 import b64encode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

user=get_user_model()

class Member(models.Model):
    User = models.ForeignKey(user,on_delete=models.CASCADE)
    # persisted last seen timestamp (updated on disconnect)
    last_seen = models.DateTimeField(null=True, blank=True)
    def __str__(self):
        return self.User.username

class Message(models.Model):
    member = models.ForeignKey(Member,null=True,on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(null=False)

    def __str__(self):
        return self.content
    
class Chatroom(models.Model):
    participants = models.ManyToManyField(Member)
    chats = models.ManyToManyField(Message,blank=True)
    encryption_key = models.CharField(max_length=100, blank=True)
    
    def save(self, *args, **kwargs):
        # Generate a key for the room when it's first created
        if not self.encryption_key:
            key = AESGCM.generate_key(bit_length=256)
            self.encryption_key = b64encode(key).decode('utf-8')
        super().save(*args, **kwargs)

    def __str__(self):
        return "{}".format(self.pk)


class ModerationBatch(models.Model):
    """Batch of messages queued for moderation"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
    ]
    
    room = models.ForeignKey(Chatroom, on_delete=models.CASCADE)
    message_ids = models.TextField()  # JSON array of message IDs
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    flagged_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Moderation Batches"
    
    def __str__(self):
        return f"Batch {self.id} - Room {self.room.id} - {self.status}"


class UserModerationStatus(models.Model):
    """Track user moderation flags and mute status per room"""
    user = models.ForeignKey(user, on_delete=models.CASCADE)
    room = models.ForeignKey(Chatroom, on_delete=models.CASCADE)
    flag_count = models.IntegerField(default=0)
    is_muted = models.BooleanField(default=False)
    last_flagged = models.DateTimeField(auto_now=True)
    muted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ['user', 'room']
        ordering = ['-last_flagged']
        verbose_name = "User Moderation Status"
        verbose_name_plural = "User Moderation Statuses"
    
    def __str__(self):
        status = "MUTED" if self.is_muted else f"Flags: {self.flag_count}"
        return f"{self.user.username} - Room {self.room.id} - {status}"


class AIConversation(models.Model):
    """Store AI conversation context for continuity"""
    user = models.ForeignKey(user, on_delete=models.CASCADE)
    room = models.ForeignKey(Chatroom, on_delete=models.CASCADE)
    context = models.TextField(default='[]')  # JSON array of message exchanges
    last_interaction = models.DateTimeField(auto_now=True)
    message_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'room']
        ordering = ['-last_interaction']
        verbose_name = "AI Conversation"
        verbose_name_plural = "AI Conversations"
    
    def __str__(self):
        return f"AI Conv - {self.user.username} - Room {self.room.id} ({self.message_count} msgs)"


class Reminder(models.Model):
    """
    Scheduled reminders/notifications for users.
    Processable via Celery beat or scheduled tasks.
    """
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(user, on_delete=models.CASCADE, related_name='reminders')
    room = models.ForeignKey(Chatroom, on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField()  # "Remind me to call John"
    scheduled_time = models.DateTimeField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Delivery channels
    via_email = models.BooleanField(default=True)
    via_whatsapp = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_log = models.TextField(blank=True)

    class Meta:
        ordering = ['scheduled_time']

    def __str__(self):
        return f"Reminder for {self.user.username}: {self.content[:30]}..."


class RoomContext(models.Model):
    """
    3-Tier Context Storage for AI Memory
    Tier 1: Hot storage (recent messages, active notes)
    """
    chatroom = models.OneToOneField(Chatroom, on_delete=models.CASCADE, related_name='context')
    
    # Compressed context summary (LLM-generated)
    summary = models.TextField(blank=True, help_text="AI-generated summary of conversation")
    
    # Key participants and entities mentioned
    participants = models.JSONField(default=list)  # ["John", "Sarah", "@mike"]
    entities = models.JSONField(default=dict)  # {"people": [...], "companies": [...], "projects": [...]}
    
    # Active topics/themes
    active_topics = models.JSONField(default=list)  # ["project_launch", "budget_discussion"]
    
    # Link to related rooms (cross-room context)
    related_rooms = models.ManyToManyField('self', blank=True, symmetrical=False)
    
    # Metadata
    message_count = models.IntegerField(default=0)
    last_compressed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Context for Room {self.chatroom.id}"


class RoomNote(models.Model):
    """
    Tier 2: Important notes/decisions extracted by AI or created by users
    """
    NOTE_TYPES = [
        ('decision', 'Decision'),
        ('action_item', 'Action Item'),
        ('insight', 'Insight'),
        ('reference', 'Reference'),
        ('reminder', 'Reminder'),
        ('written', 'Written Note'),
    ]
    
    room_context = models.ForeignKey(RoomContext, on_delete=models.CASCADE, related_name='notes')
    note_type = models.CharField(max_length=20, choices=NOTE_TYPES)
    content = models.TextField()
    
    # Who created it (AI or User)
    created_by = models.ForeignKey(user, on_delete=models.SET_NULL, null=True, blank=True)
    is_ai_generated = models.BooleanField(default=False)
    
    # Importance/priority
    priority = models.CharField(max_length=10, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ], default='medium')
    
    # Tags for searchability
    tags = models.JSONField(default=list)  # ["budget", "deadline", "client_meeting"]
    
    # Linked message (if extracted from conversation)
    source_message_id = models.IntegerField(null=True, blank=True)
    source_message_content = models.TextField(blank=True, help_text="Full content of the pinned message")
    
    # Status tracking
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['room_context', '-created_at']),
            models.Index(fields=['note_type', 'is_completed']),
        ]
    
    def __str__(self):
        return f"{self.get_note_type_display()}: {self.content[:50]}..."


class DailySummary(models.Model):
    """
    Tier 3: Cold storage - Daily/weekly compressed summaries
    """
    room_context = models.ForeignKey(RoomContext, on_delete=models.CASCADE, related_name='daily_summaries')
    date = models.DateField()
    
    # AI-generated summary
    summary = models.TextField(help_text="What happened today in this room")
    
    # Key highlights
    highlights = models.JSONField(default=list)  # ["Decided on Q1 budget", "John joined team"]
    
    # Statistics
    message_count = models.IntegerField(default=0)
    participant_count = models.IntegerField(default=0)
    notes_created = models.IntegerField(default=0)
    
    # Sentiment/tone (optional, AI-analyzed)
    sentiment = models.CharField(max_length=20, blank=True, choices=[
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ])
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['room_context', 'date']
        ordering = ['-date']
        indexes = [
            models.Index(fields=['room_context', '-date']),
        ]
    
    def __str__(self):
        return f"Summary for Room {self.room_context.chatroom.id} on {self.date}"


class DocumentUpload(models.Model):
    """
    Track document uploads for rate limiting and quota management
    """
    FILE_TYPE_CHOICES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
    ]
    
    user = models.ForeignKey(user, on_delete=models.CASCADE, related_name='document_uploads')
    chatroom = models.ForeignKey(Chatroom, on_delete=models.CASCADE, related_name='document_uploads')
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES)
    file_path = models.CharField(max_length=500)
    file_size = models.IntegerField(help_text="File size in bytes")
    quota_window_start = models.DateTimeField(help_text="Start of the 10-hour quota window")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['user', 'quota_window_start']),
            models.Index(fields=['chatroom', '-uploaded_at']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.file_type} - {self.uploaded_at}"
