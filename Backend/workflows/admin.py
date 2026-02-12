from django.contrib import admin
from .models import WorkflowDraft, UserWorkflow, WorkflowExecution, WorkflowTrigger

admin.site.register(WorkflowDraft)
admin.site.register(UserWorkflow)
admin.site.register(WorkflowExecution)
admin.site.register(WorkflowTrigger)
