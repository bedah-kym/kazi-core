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

# === CREATE CELERY APP WITH EXPLICIT BROKER ===
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
print(f"Celery.py DEBUG: Using broker = {REDIS_URL}")

app = Celery('Backend', broker=REDIS_URL, backend='django-db')

# Load config from Django settings with CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')