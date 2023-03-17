from .serializers import MathiaReplySerializer
from chatbot.models import Chatroom
from chatbot.serializers import ChatroomSerializer
from rest_framework import generics
from .permissions import IsStaffEditorPermissions
#from rest_framework.authentication import SessionAuthentication

class CreateReply(generics.CreateAPIView):
    serializer_class = MathiaReplySerializer
    permission_classes =[IsStaffEditorPermissions]
    #authentication_classes = [SessionAuthentication]

class GetMessages(generics.ListAPIView):
    queryset = Chatroom.objects.all()
    serializer_class = ChatroomSerializer
    permission_classes =[IsStaffEditorPermissions]
    #authentication_classes = [SessionAuthentication]
    def get_queryset(self,*args,**kwargs):
        chatid = self.request.query_params.get('chatid')
        chatroom = Chatroom.objects.get(id=chatid)
        qs = chatroom.chats.all()
        return qs



    