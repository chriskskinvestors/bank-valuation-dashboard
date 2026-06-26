"""Pin the serve-stale-on-warm-gap contract for the Home overlay's history reads
(2026-06-26).

The above-the-fold overlay chart reads ETF price history `cache_only=True` on the
render thread; a background job (jobs/refresh_home_snapshot) warms the cache every
~15 min. On 2026-06-26 the chart blanked to "No history available" during a brief
window where one warm tick wrote nothing (FMP EOD fetch returned empty) while the
prior entry had lapsed its 1h freshness — and the cache_only read returned empty
with no fallback.

Fix: get_history(cache_only=True, allow_stale=True) serves the last-known value on
a freshness miss instead of empty (the backend's 24h cap still bounds staleness).
These tests pin: fresh→served, stale+allow_stale→served (not blank), stale without
allow_stale→empty, absent→empty, and the LIVE path is never pinned to stale.

Run: python -m unittest tests.test_overlay_stale_cache
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.fmp_client as fmp

_RECORDS = [{"date": "2026-06-24", "close": 100.0},
            {"date": "2026-06-25", "close": 101.0},
            {"date": "2026-06-26", "close": 102.5}]


def _entry(age_s: float):
    """A backend cache entry whose inner _ts is `age_s` seconds old."""
    return {"_ts": time.time() - age_s, "_v": _RECORDS}


class TestOverlayStaleCache(unittest.TestCase):
    def setUp(self):
        self._orig = fmp._has_key
        fmp._has_key = lambda: True

    def tearDown(self):
        fmp._has_key = self._orig

    def _get(self, backend_value, **kw):
        """Drive get_history with data.cache.get mocked to backend_value and the
        live FMP fetch hard-disabled (so only the cache path can succeed)."""
        with patch("data.cache.get", return_value=backend_value), \
             patch.object(fmp, "_get",
                          side_effect=AssertionError("must not hit FMP live")):
            return fmp.get_history("SPY", period="6M", **kw)

    def test_fresh_is_served(self):
        df = self._get(_entry(60), cache_only=True, allow_stale=True)
        self.assertEqual(df["close"].tolist(), [100.0, 101.0, 102.5])

    def test_stale_served_when_allow_stale(self):
        # 2h old > 1h TTL — without the fix this blanked the chart.
        df = self._get(_entry(7200), cache_only=True, allow_stale=True)
        self.assertEqual(df["close"].tolist(), [100.0, 101.0, 102.5],
                         "stale-present must serve last-known, not blank")

    def test_stale_is_empty_without_allow_stale(self):
        df = self._get(_entry(7200), cache_only=True, allow_stale=False)
        self.assertTrue(df.empty, "default cache_only still honors the 1h TTL")

    def test_absent_is_empty_even_with_allow_stale(self):
        df = self._get(None, cache_only=True, allow_stale=True)
        self.assertTrue(df.empty, "nothing cached → empty (no stale to serve)")

    def test_live_path_not_pinned_to_stale(self):
        # cache_only=False: a stale entry must NOT short-circuit; the live fetch
        # runs (here asserted by the _get side_effect firing).
        with patch("data.cache.get", return_value=_entry(7200)), \
             patch.object(fmp, "_get",
                          side_effect=AssertionError("live fetch attempted")):
            with self.assertRaises(AssertionError):
                fmp.get_history("SPY", period="6M",
                                cache_only=False, allow_stale=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
