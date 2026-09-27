"""
Tests for travel search cache poisoning (v0.6 local testing fixes).

Error/empty connector results must never be cached: caching them turned
every retry into a fake "success" hit with zero results, trapping the
agent in a retry loop.
"""
from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from orchestration.connectors.base_travel_connector import BaseTravelConnector
from travel.models import SearchCache


class _StubTravelConnector(BaseTravelConnector):
    PROVIDER_NAME = "stub"

    def __init__(self, fetch_results):
        super().__init__()
        self._fetch_results = fetch_results
        self.fetch_calls = 0

    async def _fetch(self, parameters, context):
        self.fetch_calls += 1
        return dict(self._fetch_results)


def _run_execute(connector, params=None):
    return async_to_sync(connector.execute)(params or {"action": "search_flights", "origin": "NBO", "destination": "MBA", "departure_date": "2026-10-02"}, {})


class TravelCachePoisoningTests(TestCase):
    def test_empty_results_are_not_cached(self):
        connector = _StubTravelConnector({"results": [], "metadata": {"error": "past date"}})
        first = _run_execute(connector)
        self.assertEqual(first["count"], 0)
        self.assertEqual(SearchCache.objects.filter(provider="stub").count(), 0)

        connector._fetch_results = {"results": [{"flight": "KQ100"}], "metadata": {}}
        second = _run_execute(connector)
        self.assertEqual(connector.fetch_calls, 2)
        self.assertEqual(second["cached"], False)
        self.assertEqual(second["count"], 1)

    def test_non_empty_results_are_cached_and_served(self):
        connector = _StubTravelConnector({"results": [{"flight": "KQ100"}], "metadata": {}})
        first = _run_execute(connector)
        self.assertEqual(first["cached"], False)
        second = _run_execute(connector)
        self.assertEqual(connector.fetch_calls, 1)
        self.assertEqual(second["cached"], True)
        self.assertEqual(second["count"], 1)

    def test_legacy_poisoned_empty_cache_row_is_treated_as_miss(self):
        from orchestration.connectors.base_travel_connector import BaseTravelConnector as BTC

        connector = _StubTravelConnector({"results": [{"flight": "KQ100"}], "metadata": {}})
        SearchCache.objects.create(
            query_hash=connector._hash_query({"action": "search_flights", "origin": "NBO", "destination": "MBA", "departure_date": "2026-10-02"}),
            provider="stub",
            query_json={},
            result_json={"results": [], "metadata": {"error": "stale"}},
            ttl_seconds=BTC.CACHE_TTL_SECONDS,
            expires_at=timezone.now() + timezone.timedelta(seconds=3600),
        )
        result = _run_execute(connector)
        self.assertEqual(result["cached"], False)
        self.assertEqual(result["count"], 1)
        stored = SearchCache.objects.get(provider="stub")
        self.assertEqual(stored.result_json["results"], [{"flight": "KQ100"}])
