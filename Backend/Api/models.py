from django.db import models

# Create your models here.
class MathiaReply(models.Model):
    message = models.TextField()
    sender = models.CharField(max_length=15)
    command = models.TextField()
    chatid = models.IntegerField()

    def __str__(self):
        return "{}".format(self.message)