import asyncio

from django.test.runner import DiscoverRunner

DEFAULT_TEST_LABELS = (
    'orchestration',
    'workflows',
    'chatbot',
    'travel',
    'notifications',
    'users',
    'payments',
    'Api',
    'tests',
)


def _close_sync_worker_connections():
    """Close DB connections held by asgiref's long-lived executor thread.

    ``asgiref.sync.sync_to_async`` runs thread-sensitive code in a single
    module-level thread that lives until process exit. Any ORM call made
    through it leaves a Postgres session open there, which fails the test
    teardown with "database is being accessed by other users" when the
    runner tries to DROP the test database.
    """
    from asgiref.sync import sync_to_async
    from django.db import connections

    async def _close():
        await sync_to_async(connections.close_all)()

    asyncio.run(_close())


class KaziDiscoverRunner(DiscoverRunner):
    default_test_labels = DEFAULT_TEST_LABELS

    def run_tests(self, test_labels=None, extra_tests=None, **kwargs):
        return super().run_tests(
            list(test_labels) if test_labels else list(self.default_test_labels),
            extra_tests=extra_tests,
            **kwargs,
        )

    def teardown_databases(self, old_config, **kwargs):
        _close_sync_worker_connections()
        return super().teardown_databases(old_config, **kwargs)
