__all__ = ('celery_app',)


def __getattr__(name):
    # Lazy-load the Celery app so importing the ``Backend`` package (which
    # Django does while reading settings) never triggers Celery's
    # config/autodiscover machinery. Eager import here caused ``manage.py
    # check`` to hang during app population (re-entrant kombu/amqp init).
    if name == 'celery_app':
        from .celery import app as celery_app
        return celery_app
    raise AttributeError(name)
