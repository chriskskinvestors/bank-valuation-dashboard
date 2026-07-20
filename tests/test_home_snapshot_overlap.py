"""
Pins the 2026-07-20 Home-snapshot overlap findings.

Measured from Cloud Run execution history: refresh-home-snapshot runs 13-28
minutes against its own */15 schedule, so two executions are routinely in
flight at once. Two consequences, both pinned here:

1. STALE CLOBBER (correctness). Observed writes 6s apart:
     f4vhc  started 12:30:35  wrote 12:59:10   <- data as of 12:30
     46wtf  started 12:45:25  wrote 12:59:04   <- data as of 12:45
   The run holding OLDER data finished LAST, so last-write-wins overwrote the
   fresher feed. Home could move backwards in time and drop releases that had
   already landed. warm_news_feed_snapshot must now refuse to overwrite a
   snapshot written after its own build began.

2. ORDERING (latency). The news-feed warm ran 4th, behind the full metrics
   build, ~55 live FMP history calls and the per-CIK insider scan — so on a
   13-28 min run the feed refreshed 10-25 min in, which is why a release
   ingested within a minute still wasn't on Home.

Offline: cache get/put are stubbed; no DB, no network.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st.session_state = {}
sys.modules.setdefault("streamlit", _st)

import ui.home as home  # noqa: E402


class TestStaleClobberGuard(unittest.TestCase):
    def test_older_build_does_not_overwrite_a_newer_snapshot(self):
        # A concurrent run wrote AFTER we started building: its data is newer.
        newer = (datetime.now() + timedelta(minutes=5)).isoformat()
        stored = {"cached_at": newer, "guard": 2,
                  "value": [{"head": "fresh"}, {"head": "fresh2"}]}
        writes = []
        with patch.object(home, "_af_feed_items_live",
                          return_value=[{"head": "stale"}]), \
             patch("data.cache.get", return_value=stored), \
             patch("data.cache.put", side_effect=lambda k, v: writes.append(v)):
            n = home.warm_news_feed_snapshot(["AAA", "BBB"])
        self.assertEqual(writes, [], "must NOT clobber a newer snapshot")
        self.assertEqual(n, 2, "reports the retained (newer) snapshot's count")

    def test_newer_build_does_overwrite_an_older_snapshot(self):
        older = (datetime.now() - timedelta(minutes=30)).isoformat()
        stored = {"cached_at": older, "guard": 2, "value": [{"head": "old"}]}
        writes = []
        with patch.object(home, "_af_feed_items_live",
                          return_value=[{"head": "new"}, {"head": "new2"}]), \
             patch("data.cache.get", return_value=stored), \
             patch("data.cache.put", side_effect=lambda k, v: writes.append(v)):
            n = home.warm_news_feed_snapshot(["AAA", "BBB"])
        self.assertEqual(len(writes), 1, "a fresher build must write")
        self.assertEqual(n, 2)

    def test_writes_when_no_snapshot_exists(self):
        writes = []
        with patch.object(home, "_af_feed_items_live",
                          return_value=[{"head": "first"}]), \
             patch("data.cache.get", return_value=None), \
             patch("data.cache.put", side_effect=lambda k, v: writes.append(v)):
            n = home.warm_news_feed_snapshot(["AAA"])
        self.assertEqual(len(writes), 1)
        self.assertEqual(n, 1)

    def test_written_shape_matches_what_served_snapshot_reads(self):
        # served_snapshot checks cached_at (freshness) and guard (invalidation),
        # then returns ["value"]. A shape drift here silently disables the cache.
        writes = []
        with patch.object(home, "_af_feed_items_live", return_value=[{"h": 1}]), \
             patch("data.cache.get", return_value=None), \
             patch("data.cache.put", side_effect=lambda k, v: writes.append(v)):
            home.warm_news_feed_snapshot(["AAA", "BBB", "CCC"])
        w = writes[0]
        self.assertIn("cached_at", w)
        self.assertEqual(w["guard"], 3, "guard must be the ticker count")
        self.assertEqual(w["value"], [{"h": 1}])

    def test_unreadable_stored_value_still_writes(self):
        # A garbled cached_at must not wedge the warm job into never writing.
        with patch.object(home, "_af_feed_items_live", return_value=[{"h": 1}]), \
             patch("data.cache.get", return_value={"cached_at": "not-a-date"}), \
             patch("data.cache.put") as put:
            home.warm_news_feed_snapshot(["AAA"])
        put.assert_called_once()


class TestWarmOrdering(unittest.TestCase):
    def test_news_feed_is_warmed_before_the_expensive_build(self):
        import inspect
        from jobs import refresh_home_snapshot as job
        src = inspect.getsource(job.main)
        first_news = src.find("_warm_news_feed")
        first_fdic = src.find("_load_fdic")
        self.assertNotEqual(first_news, -1)
        self.assertNotEqual(first_fdic, -1)
        self.assertLess(
            first_news, first_fdic,
            "the cheap, time-sensitive news warm must run BEFORE the "
            "13-28min metrics/FMP build, or Home lags ingestion by ~25min")


if __name__ == "__main__":
    unittest.main()
