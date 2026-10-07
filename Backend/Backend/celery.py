import logging
import os
from datetime import datetime, timezone
from celery import Celery
from celery.contrib.django.task import DjangoTask
from kombu.utils.uuid import uuid
from pathlib import Path

# === LOAD .ENV BEFORE ANYTHING ===
try:
    from dotenv import load_dotenv
    BASE_DIR = Path(__file__).resolve().parent.parent
    env_path = BASE_DIR.parent / '.env'
    load_dotenv(dotenv_path=env_path, override=True)
except ImportError:
    pass

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')

logger = logging.getLogger(__name__)


def _due_later(options) -> bool:
    countdown = options.get('countdown')
    if countdown:
        return countdown > 0
    eta = options.get('eta')
    if not isinstance(eta, datetime):
        return False
    if eta.tzinfo is None:
        # Celery reads a naive eta as UTC.
        eta = eta.replace(tzinfo=timezone.utc)
    return eta > datetime.now(timezone.utc)


class KaziTask(DjangoTask):
    """Eager mode has no scheduler, and Celery would run a task that is due later
    at once: an idle nudge the moment it is scheduled, a reminder hours early.
    Retries are not covered: an eager retry still runs again at once, up to its limit."""

    def apply_async(self, args=None, kwargs=None, task_id=None, producer=None,
                    link=None, link_error=None, shadow=None, **options):
        if self.app.conf.task_always_eager and _due_later(options):
            logger.info("Task %s is due later and was not run: eager mode has no scheduler", self.name)
            return self.AsyncResult(task_id or uuid())
        return super().apply_async(args, kwargs, task_id, producer, link, link_error, shadow, **options)


# === CELERY APP ===
# Same precedence as settings.py: explicit broker > REDIS_URL > compose default.
# One shared default everywhere — the old localhost default here silently
# diverged from the app cache/channels default (redis://redis:6379/0).
REDIS_URL = (
    os.environ.get('CELERY_BROKER_URL')
    or os.environ.get('REDIS_URL')
    or 'redis://redis:6379/0'
)

app = Celery('Backend', broker=REDIS_URL, task_cls=KaziTask)

# Load config from Django settings with CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
