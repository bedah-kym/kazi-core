from django.urls import path
from . import views

urlpatterns = [
    path('', views.list_workflows, name='workflow_list'),
    path('inbox/', views.operations_inbox, name='workflow_operations_inbox'),
    path('<int:workflow_id>/run/', views.run_workflow, name='workflow_run'),
    path('<int:workflow_id>/executions/', views.list_workflow_executions, name='workflow_execution_list'),
    path('executions/<int:execution_id>/', views.execution_detail, name='workflow_execution_detail'),
    path('executions/<int:execution_id>/approve/', views.approve_execution, name='workflow_execution_approve'),
    path('executions/<int:execution_id>/reject/', views.reject_execution, name='workflow_execution_reject'),
    path('executions/<int:execution_id>/cancel/', views.cancel_execution, name='workflow_execution_cancel'),
    path('executions/<int:execution_id>/rerun/', views.rerun_execution, name='workflow_execution_rerun'),
    path('triggers/<int:trigger_id>/pause/', views.pause_trigger, name='workflow_trigger_pause'),
    path('triggers/<int:trigger_id>/resume/', views.resume_trigger, name='workflow_trigger_resume'),
]
