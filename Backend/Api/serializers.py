from rest_framework import serializers
from .models import MathiaReply

class MathiaReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = MathiaReply
        fields =[
            'message',
            'sender',
            'command',
            'chatid'
        ]