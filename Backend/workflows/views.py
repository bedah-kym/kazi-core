from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from orchestration.security_policy import sanitize_parameters, user_has_room_access
from .models import UserWorkflow
from .temporal_integration import start_workflow_execution


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_workflows(request):
    workflows = UserWorkflow.objects.filter(user=request.user).order_by('-created_at')
    data = [
        {
            'id': wf.id,
            'name': wf.name,
            'description': wf.description,
            'status': wf.status,
            'created_at': wf.created_at.isoformat(),
            'execution_count': wf.execution_count,
        }
        for wf in workflows
    ]
    return Response({'workflows': data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def run_workflow(request, workflow_id):
    workflow = UserWorkflow.objects.filter(id=workflow_id, user=request.user).first()
    if not workflow:
        return Response({'error': 'Workflow not found'}, status=404)
    if workflow.status != 'active':
        return Response({'error': 'Workflow is not active'}, status=400)

    trigger_data = request.data.get('trigger_data', {})
    if trigger_data is None:
        trigger_data = {}
    if not isinstance(trigger_data, dict):
        return Response({'error': 'trigger_data must be an object'}, status=400)
    trigger_data = sanitize_parameters(trigger_data)

    room_id = trigger_data.get('room_id')
    if room_id is not None:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return Response({'error': 'Invalid room_id in trigger_data'}, status=400)
        allowed = async_to_sync(user_has_room_access)(request.user.id, room_id)
        if not allowed:
            return Response({'error': 'You do not have access to that room_id'}, status=403)
        trigger_data['room_id'] = room_id

    async_to_sync(start_workflow_execution)(
        workflow,
        trigger_data=trigger_data,
        trigger_type='manual'
    )

    return Response({'status': 'started', 'workflow_id': workflow.id})
