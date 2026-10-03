from rest_framework import serializers
from .models import KaziReply


class KaziReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = KaziReply
        fields = [
            'message',
            'sender',
            'command',
            'chatid'
        ]
