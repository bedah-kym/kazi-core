"""HTML routes for the workflow operations surface (mounted at /workflows/)."""
from django.urls import path

from . import ui_views

app_name = "workflows"

urlpatterns = [
    path("", ui_views.workflows_list, name="workflows_list"),
    path("inbox/", ui_views.operations_inbox, name="operations_inbox"),
    path("<int:workflow_id>/run/", ui_views.run_workflow_ui, name="run_workflow"),
    path("<int:workflow_id>/reactivate/", ui_views.reactivate_workflow_ui, name="reactivate_workflow"),
    path("<int:workflow_id>/executions/", ui_views.workflow_executions, name="workflow_executions"),
    path("<int:workflow_id>/versions/", ui_views.workflow_versions, name="workflow_versions"),
    path("executions/<int:execution_id>/", ui_views.execution_detail, name="execution_detail"),
    path("executions/<int:execution_id>/approve/", ui_views.approve_execution_ui, name="approve_execution"),
    path("executions/<int:execution_id>/reject/", ui_views.reject_execution_ui, name="reject_execution"),
    path("executions/<int:execution_id>/cancel/", ui_views.cancel_execution_ui, name="cancel_execution"),
    path("executions/<int:execution_id>/rerun/", ui_views.rerun_execution_ui, name="rerun_execution"),
    path("triggers/<int:trigger_id>/toggle/", ui_views.toggle_trigger_ui, name="toggle_trigger"),
    path("suggestions/<int:suggestion_id>/accept/", ui_views.accept_suggestion_ui, name="accept_suggestion"),
    path("suggestions/<int:suggestion_id>/dismiss/", ui_views.dismiss_suggestion_ui, name="dismiss_suggestion"),
    path("shell/autopilot/arm/", ui_views.shell_autopilot_arm, name="shell_autopilot_arm"),
    path("shell/autopilot/disarm/", ui_views.shell_autopilot_disarm, name="shell_autopilot_disarm"),
    path("shell/hosts/revoke/", ui_views.shell_host_revoke, name="shell_host_revoke"),
]
