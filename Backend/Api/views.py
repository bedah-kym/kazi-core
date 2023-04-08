from .serializers import MathiaReplySerializer
from chatbot.models import Chatroom
from chatbot.serializers import ChatroomSerializer
from rest_framework import generics
from .permissions import IsStaffEditorPermissions
from .models import MathiaReply


class CreateReply(generics.ListCreateAPIView):
    queryset = MathiaReply.objects.all()
    serializer_class = MathiaReplySerializer
    #permission_classes =[IsStaffEditorPermissions]

class GetMessage(generics.RetrieveAPIView):
    serializer_class = ChatroomSerializer
    lookup_field="room"
    #permission_classes =[IsStaffEditorPermissions]

    def get_queryset(request):
        room_id = request.kwargs['room']
        room = Chatroom.objects.get(id=room_id)
        qs = room
        return qs

    def get_object(self):
        queryset  = self.get_queryset()
        obj = queryset.chats.last()
        return obj

class GetAllMessages(generics.ListAPIView):
    serializer_class = ChatroomSerializer

    def get_queryset(request):
        room_id = request.kwargs['room']
        room = Chatroom.objects.get(id=room_id)
        qs = room.chats.all()
        return qs
    