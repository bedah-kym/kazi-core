from django.urls import path
from . import views

urlpatterns = [
    path('', views.list_workflows, name='workflow_list'),
    path('<int:workflow_id>/run/', views.run_workflow, name='workflow_run'),
]
