"""Opt-in Postgres connection pool (DB_POOL_MAX_SIZE).

Under ASGI every worker thread kept its own connection for CONN_MAX_AGE
seconds, so the web process alone filled a 15-client server limit.
"""
from unittest import TestCase, skipUnless

from django.core.exceptions import ImproperlyConfigured
from django.db import connection

PG_URL = "postgresql://kazi:fake-password@db.example.com:5432/kazi"
SQLITE_URL = "sqlite:///fake.sqlite3"


def _config(url, **env):
    from Backend.settings import _database_settings
    return _database_settings(url, env)


class DatabasePoolSettingsTests(TestCase):
    def test_unset_keeps_persistent_connections(self):
        db = _config(PG_URL)
        self.assertEqual(db["CONN_MAX_AGE"], 600)
        self.assertTrue(db["CONN_HEALTH_CHECKS"])
        self.assertNotIn("pool", db.get("OPTIONS", {}))

    def test_zero_keeps_persistent_connections(self):
        db = _config(PG_URL, DB_POOL_MAX_SIZE="0")
        self.assertEqual(db["CONN_MAX_AGE"], 600)
        self.assertNotIn("pool", db.get("OPTIONS", {}))

    def test_postgres_with_size_uses_a_capped_pool(self):
        db = _config(PG_URL, DB_POOL_MAX_SIZE="3")
        self.assertEqual(db["CONN_MAX_AGE"], 0)
        self.assertFalse(db["CONN_HEALTH_CHECKS"])
        self.assertEqual(
            db["OPTIONS"]["pool"],
            {"min_size": 1, "max_size": 3, "timeout": 10, "max_idle": 300},
        )

    def test_pool_keeps_options_from_the_url(self):
        db = _config(PG_URL + "?sslmode=require", DB_POOL_MAX_SIZE="2")
        self.assertEqual(db["OPTIONS"]["sslmode"], "require")
        self.assertEqual(db["OPTIONS"]["pool"]["max_size"], 2)

    def test_postgis_is_pooled_too(self):
        db = _config(PG_URL.replace("postgresql", "postgis", 1), DB_POOL_MAX_SIZE="3")
        self.assertEqual(db["CONN_MAX_AGE"], 0)
        self.assertEqual(db["OPTIONS"]["pool"]["max_size"], 3)

    def test_sqlite_is_never_pooled(self):
        db = _config(SQLITE_URL, DB_POOL_MAX_SIZE="3")
        self.assertNotIn("pool", db.get("OPTIONS", {}))
        self.assertEqual(db["CONN_MAX_AGE"], 600)

    def test_invalid_size_refuses_to_boot(self):
        for value in ("abc", "-1", "2.5"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                _config(PG_URL, DB_POOL_MAX_SIZE=value)

    def test_empty_value_means_unset(self):
        for value in ("", " "):
            with self.subTest(value=value):
                db = _config(PG_URL, DB_POOL_MAX_SIZE=value)
                self.assertNotIn("pool", db.get("OPTIONS", {}))


@skipUnless(connection.vendor == "postgresql", "needs the Postgres CI lane")
class DatabasePoolPostgresTests(TestCase):
    def test_pooled_connection_runs_a_query(self):
        from django.db.backends.postgresql.base import DatabaseWrapper

        pooled = _config(PG_URL, DB_POOL_MAX_SIZE="2")
        settings_dict = dict(connection.settings_dict)
        settings_dict["CONN_MAX_AGE"] = pooled["CONN_MAX_AGE"]
        settings_dict["CONN_HEALTH_CHECKS"] = pooled["CONN_HEALTH_CHECKS"]
        settings_dict["OPTIONS"] = {
            **settings_dict.get("OPTIONS", {}),
            "pool": pooled["OPTIONS"]["pool"],
        }
        wrapper = DatabaseWrapper(settings_dict, alias="pool_check")
        try:
            self.assertIsNotNone(wrapper.pool)
            with wrapper.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            wrapper.close()
            wrapper.close_pool()
