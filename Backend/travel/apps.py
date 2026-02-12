from django.apps import AppConfig


class TravelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'travel'
    verbose_name = 'Travel Planner'
    
    def ready(self):
        # Import signals if needed
        pass
