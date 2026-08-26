import os
from celery import Celery
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

# === CELERY APP ===
# Same precedence as settings.py: explicit broker > REDIS_URL > compose default.
# One shared default everywhere — the old localhost default here silently
# diverged from the app cache/channels default (redis://redis:6379/0).
REDIS_URL = (
    os.environ.get('CELERY_BROKER_URL')
    or os.environ.get('REDIS_URL')
    or 'redis://redis:6379/0'
)

app = Celery('Backend', broker=REDIS_URL)

# Load config from Django settings with CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
