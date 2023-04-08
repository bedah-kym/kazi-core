from rest_framework import serializers
from .models import Chatroom,Message,Member


class ChatroomSerializer(serializers.ModelSerializer):
    text = serializers.CharField(source= "content",read_only=True)
    class Meta:
        model = Chatroom
        fields =[
            'chats'
        ]
        model = Message
        fields =[
            'member',
            'text',
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