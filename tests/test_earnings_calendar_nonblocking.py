"""
Regression pin (2026-06-13): fetch_earnings_calendar must NEVER trigger a
live yfinance build on the interactive path.

The earlier version rebuilt ~440 yfinance calls inline whenever the
cross-instance snapshot was stale (6h TTL, warmed only by the 6am job) or
its ticker-count guard drifted (439<->440). Under Yahoo throttling that
took minutes and blocked Home's Alert Inbox — which left Streamlit stuck
mid-render and the top navigation bar unpainted. The fetch must serve the
persisted snapshot whatever its age and return [] when absent; the live
build belongs only in refresh_earnings_calendar_snapshot (background job).

Run: python -m unittest tests.test_earnings_calendar_nonblocking
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _install_streamlit_stub():
    st = types.ModuleType("streamlit")

    def _cache(*a, **k):
        # Pass-through decorator so the wrapped function runs directly.
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

    st.cache_data = _cache
    st.cache_resource = _cache
    sys.modules["streamlit"] = st


class TestEarningsCalendarNonBlocking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import importlib
        import data.estimates as est
        cls.est = importlib.reload(est)

    def setUp(self):
        # Any call to the live builder is a test failure — the whole point
        # is that the interactive path never reaches it.
        self.est._fetch_earnings_calendar_live = lambda tickers: (
            self.fail("live earnings build must NEVER run on fetch path"))

    def _patch_cache(self, snap):
        import data.cache as cache
        orig = cache.get
        cache.get = lambda k: snap if k == "earnings_calendar_snap" else orig(k)
        self.addCleanup(lambda: setattr(cache, "get", orig))

    def test_fresh_snapshot_served(self):
        rows = [{"ticker": "WAL", "next_earnings_date": "2026-06-20"}]
        self._patch_cache({"cached_at": "2026-06-13T08:00:00",
                           "guard": 440, "value": rows})
        self.assertEqual(self.est.fetch_earnings_calendar(("WAL", "BANR")), rows)

    def test_stale_snapshot_still_served_not_rebuilt(self):
        # Year-old snapshot: must STILL be served (never a live rebuild).
        rows = [{"ticker": "BANR", "next_earnings_date": "2025-01-01"}]
        self._patch_cache({"cached_at": "2025-01-01T00:00:00",
                           "guard": 1, "value": rows})
        self.assertEqual(self.est.fetch_earnings_calendar(("BANR",)), rows)

    def test_count_drift_does_not_trigger_rebuild(self):
        # snapshot guard 439 but caller passes 440 tickers — old code
        # rebuilt; new code serves regardless.
        rows = [{"ticker": "X", "next_earnings_date": "2026-06-15"}]
        self._patch_cache({"cached_at": "2026-06-13T08:00:00",
                           "guard": 439, "value": rows})
        self.assertEqual(
            self.est.fetch_earnings_calendar(tuple(f"T{i}" for i in range(440))),
            rows)

    def test_absent_snapshot_returns_empty_not_rebuild(self):
        self._patch_cache(None)
        self.assertEqual(self.est.fetch_earnings_calendar(("WAL",)), [])

    def test_refresh_helper_does_build_and_persist(self):
        # The background helper IS allowed to build; verify it persists the
        # served shape.
        import data.cache as cache
        self.est._fetch_earnings_calendar_live = lambda tickers: [
            {"ticker": "WAL", "next_earnings_date": "2026-06-20"}]
        puts = {}
        orig = cache.put
        cache.put = lambda k, v: puts.__setitem__(k, v)
        self.addCleanup(lambda: setattr(cache, "put", orig))
        out = self.est.refresh_earnings_calendar_snapshot(("WAL",))
        self.assertEqual(len(out), 1)
        self.assertIn("earnings_calendar_snap", puts)
        self.assertEqual(puts["earnings_calendar_snap"]["value"], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
