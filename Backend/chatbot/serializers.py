from rest_framework import serializers
from .models import Chatroom,Message,Member


class ChatroomSerializer(serializers.ModelSerializer):
    #username = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = Chatroom
        fields =[
            'chats'
        ]
        model = Message
        fields =[
            'member',
            'content',
            'timestamp',
        ]

    """
        model = Member
        fields = [
            'User'
        ]
    def get_username(self,obj):
        return {
            "username":obj.member.User.username
        }
    """