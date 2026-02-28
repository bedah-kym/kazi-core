from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from users.models import Workspace

User = get_user_model()


class Command(BaseCommand):
    help = 'Create superuser and backfill workspace settings (auto-runs on Railway deploy)'

    def handle(self, *args, **options):
        # Create superuser if it doesn't exist
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser(
                username='admin',
                email='admin@example.com',
                password='change_me_in_production'
            )
            self.stdout.write(self.style.SUCCESS('✅ Superuser created: admin'))
        else:
            self.stdout.write(self.style.WARNING('✅ Superuser already exists'))

        # Backfill workspace settings - Pro/Agency tier
        pro_count = Workspace.objects.filter(plan__in=['pro', 'agency']).update(
            moderation_enabled=True,
            idle_nudges_enabled=True,
            proactive_suggestions_enabled=True
        )
        self.stdout.write(f"  Pro/Agency workspaces: {pro_count} updated")

        # Trial tier
        trial_count = Workspace.objects.filter(plan='trial', trial_active=True).update(
            moderation_enabled=True,
            idle_nudges_enabled=True,
            proactive_suggestions_enabled=True
        )
        self.stdout.write(f"  Trial workspaces: {trial_count} updated")

        # Free tier
        free_count = Workspace.objects.filter(plan='free').update(
            moderation_enabled=False,
            idle_nudges_enabled=False,
            proactive_suggestions_enabled=False
        )
        self.stdout.write(f"  Free workspaces: {free_count} updated")

        self.stdout.write(self.style.SUCCESS('✅ Workspace settings backfilled'))
