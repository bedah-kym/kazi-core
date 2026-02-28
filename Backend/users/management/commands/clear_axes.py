from django.core.management.base import BaseCommand
from axes.models import AccessLog, AccessAttempt


class Command(BaseCommand):
    help = 'Clear AXES lockout records (brute force protection)'

    def handle(self, *args, **options):
        log_count = AccessLog.objects.all().count()
        attempt_count = AccessAttempt.objects.all().count()

        AccessLog.objects.all().delete()
        AccessAttempt.objects.all().delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ AXES lockout cleared\n'
                f'   Deleted {log_count} access logs\n'
                f'   Deleted {attempt_count} access attempts\n'
                f'   You can now login'
            )
        )
