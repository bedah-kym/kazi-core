from django.apps import AppConfig


class ChatbotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'chatbot'

    def ready(self):
        import chatbot.signals  # noqa: F401
        # Make the project Celery app current in every Django process, so tasks
        # honour the CELERY_* settings. It is loaded here, after app population:
        # importing it from Backend/__init__.py hung manage.py during start-up.
        from Backend import celery_app  # noqa: F401
