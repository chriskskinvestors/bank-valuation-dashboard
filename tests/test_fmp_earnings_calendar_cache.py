"""Pin the caching contract on fmp_client.get_earnings_calendar (2026-06-22).

The Earnings page renders 7 sub-tabs via st.tabs, which evaluates every tab body
on each load — so the Calls & Webcasts tab's whole-market 75-day earnings-calendar
fetch ran LIVE on every Earnings open. It is now Postgres-cached 1h keyed on the
date range. These tests pin: serves from cache on a repeat call, keys distinct
windows separately, and (cardinal rule) never caches a failure.

Run: python -m unittest tests.test_fmp_earnings_calendar_cache
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.fmp_client as fmp


class TestEarningsCalendarCache(unittest.TestCase):
    def setUp(self):
        self._orig = (fmp._has_key, fmp._get, fmp._cache_get, fmp._cache_put)
        self.store = {}
        fmp._has_key = lambda: True
        fmp._cache_get = lambda key, ttl: self.store.get(key)
        fmp._cache_put = lambda key, val: self.store.__setitem__(key, val)

    def tearDown(self):
        (fmp._has_key, fmp._get, fmp._cache_get, fmp._cache_put) = self._orig

    def test_repeat_call_hits_cache_not_fmp(self):
        calls = []
        rows = [{"symbol": "JPM", "date": "2026-07-15"}]
        fmp._get = lambda path, params, timeout=10: (calls.append(1), rows)[1]
        a = fmp.get_earnings_calendar("2026-07-01", "2026-07-31")
        b = fmp.get_earnings_calendar("2026-07-01", "2026-07-31")
        self.assertEqual(a, rows)
        self.assertEqual(a, b)
        self.assertEqual(len(calls), 1, "second call must serve from cache, not FMP")

    def test_distinct_windows_keyed_separately(self):
        fmp._get = lambda path, params, timeout=10: [{"symbol": "X", "from": params["from"]}]
        a = fmp.get_earnings_calendar("2026-07-01", "2026-07-31")
        b = fmp.get_earnings_calendar("2026-08-01", "2026-08-31")
        self.assertNotEqual(a, b, "different date windows must not collide in the cache")

    def test_failure_is_not_cached(self):
        calls = []

        def g(path, params, timeout=10):
            calls.append(1)
            return None if len(calls) == 1 else [{"symbol": "JPM"}]

        fmp._get = g
        first = fmp.get_earnings_calendar("2026-07-01", "2026-07-31")
        second = fmp.get_earnings_calendar("2026-07-01", "2026-07-31")
        self.assertIsNone(first)
        self.assertEqual(second, [{"symbol": "JPM"}])
        self.assertEqual(len(calls), 2, "a None failure must retry, never be cached")

    def test_no_key_returns_none_without_fetch(self):
        fmp._has_key = lambda: False
        fmp._get = lambda *a, **k: self.fail("must not fetch when no FMP key")
        self.assertIsNone(fmp.get_earnings_calendar("2026-07-01", "2026-07-31"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
