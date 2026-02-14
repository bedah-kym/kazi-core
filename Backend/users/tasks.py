from celery import shared_task
from django.core import management


@shared_task
def send_trial_summary_task():
    management.call_command('send_trial_summary')
