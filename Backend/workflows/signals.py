"""Model signals for the workflows app."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserWorkflow
from .versioning import record_initial_version


@receiver(post_save, sender=UserWorkflow, dispatch_uid="workflows.ensure_initial_version")
def ensure_initial_version(sender, instance, created, **kwargs):
    """Guarantee no live workflow exists without a readable version-1 row."""
    if created:
        record_initial_version(instance)
