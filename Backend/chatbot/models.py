from django.db import models
from django.contrib.auth import get_user_model
from base64 import b64encode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

user=get_user_model()

class Member(models.Model):
    User = models.ForeignKey(user,on_delete=models.CASCADE)
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



