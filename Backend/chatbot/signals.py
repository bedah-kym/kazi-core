from Api.models import MathiaReply
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=MathiaReply)
def reply_id(instance,**kwargs):
    id =instance.pk
    return id
    
