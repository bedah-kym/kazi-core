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