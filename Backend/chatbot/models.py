from django.db import models
from django.contrib.auth import get_user_model

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

    def __str__(self):
        return "{}".format(self.pk)



