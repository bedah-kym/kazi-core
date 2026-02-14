from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone
from users.models import TrialApplication


def _score(app: TrialApplication):
    hot_words = ['now', 'this week', 'today', 'immediately', 'urgent']
    intent = 'hot' if any(w in (app.go_live_timeframe or '').lower() for w in hot_words) else 'warm'
    size = app.team_size or ''
    if size.startswith(('10', '11', '12', '20', '25', '50', '5', '6')):
        team = 'team'
    else:
        team = 'solo'
    return f"{intent} / {team}"


class Command(BaseCommand):
    help = "Send a daily summary of trial applications to superusers"

    def handle(self, *args, **options):
        today = timezone.now().date()
        applications = TrialApplication.objects.filter(created_at__date=today)
        if not applications.exists():
            self.stdout.write("No applications today.")
            return

        lines = ["Trial applications received today:\n"]
        for app in applications:
        lines.append(
            f"- {app.name} ({app.email}) | {app.company or 'N/A'} | "
            f"use case: {app.primary_use_case[:120]}... | fit: {_score(app)}"
        )
        body = "\n".join(lines)

        User = get_user_model()
        recipients = list(User.objects.filter(is_superuser=True).values_list('email', flat=True))
        if "bedankimani860@gmail.com" not in recipients:
            recipients.append("bedankimani860@gmail.com")
        recipients = [r for r in recipients if r]

        if recipients:
            send_mail(
                subject=f"Mathia trial applications for {today}",
                message=body,
                from_email=None,
                recipient_list=recipients,
            )
            self.stdout.write(f"Sent summary to {len(recipients)} recipients.")
        else:
            self.stdout.write("No recipients found for summary.")
