"""(Audit 2026-07-27 P2 #5) Metrics-snapshot stale-clobber guard.

Overlapping snapshot jobs are last-write-wins: on 2026-07-20 a run that
STARTED 15 minutes earlier finished LAST and overwrote a fresher news feed
with staler data — which then passed the app's freshness check because
cached_at is stamped at WRITE time. The news feed got an inline guard
(ui/home._warm_news_feed); cache.put_snapshot_if_fresher is the same rule
for the metrics snapshot's two writers (refresh_home_snapshot and
refresh_universe's aggregate warm).

Pins:
  1. the exact failure: an older build finishing last must NOT overwrite —
     and losing the race returns False, not an error;
  2. the normal case writes;
  3. a garbled stored value falls through and writes (never wedges);
  4. both job writers actually route through the guard (structural).

Run: python -m unittest tests.test_snapshot_clobber_guard
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data import cache  # noqa: E402


class TestPutSnapshotIfFresher(unittest.TestCase):
    def test_older_build_finishing_last_does_not_clobber(self):
        """The 2026-07-20 shape: this run began at T; another run wrote at
        T+5min while we built. Our write must be refused."""
        build_started = datetime(2026, 7, 20, 12, 0, 0)
        newer = {"cached_at": (build_started
                               + timedelta(minutes=5)).isoformat(),
                 "metrics": {"fresh": True}}
        with patch.object(cache, "get", return_value=newer), \
                patch.object(cache, "put") as put_:
            wrote = cache.put_snapshot_if_fresher(
                "snap", {"cached_at": datetime.now().isoformat()},
                build_started)
        self.assertFalse(wrote, "losing the race must refuse the write")
        put_.assert_not_called()

    def test_normal_write_proceeds(self):
        build_started = datetime(2026, 7, 20, 12, 0, 0)
        older = {"cached_at": (build_started
                               - timedelta(minutes=30)).isoformat()}
        value = {"cached_at": datetime.now().isoformat()}
        with patch.object(cache, "get", return_value=older), \
                patch.object(cache, "put") as put_:
            wrote = cache.put_snapshot_if_fresher("snap", value, build_started)
        self.assertTrue(wrote)
        put_.assert_called_once_with("snap", value)

    def test_empty_store_writes(self):
        with patch.object(cache, "get", return_value=None), \
                patch.object(cache, "put") as put_:
            self.assertTrue(cache.put_snapshot_if_fresher(
                "snap", {"cached_at": "2026-07-20T12:00:00"},
                datetime(2026, 7, 20, 12, 0, 0)))
        put_.assert_called_once()

    def test_garbled_stored_value_falls_through_and_writes(self):
        """A corrupt cached_at must never wedge the writer into refusing
        forever — fall through and overwrite it."""
        with patch.object(cache, "get",
                          return_value={"cached_at": "not-a-date"}), \
                patch.object(cache, "put") as put_:
            self.assertTrue(cache.put_snapshot_if_fresher(
                "snap", {"cached_at": "x"}, datetime(2026, 7, 20)))
        put_.assert_called_once()


class TestWritersUseTheGuard(unittest.TestCase):
    """Structural: both watchlist_metrics_snap writers must route through the
    guard, or the clobber window silently reopens at one of them."""

    def test_home_snapshot_writer(self):
        src = (REPO / "jobs/refresh_home_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("put_snapshot_if_fresher", src)
        self.assertNotIn('cache.put(SNAP_KEY', src)

    def test_universe_aggregate_warm_writer(self):
        src = (REPO / "jobs/refresh_universe.py").read_text(encoding="utf-8")
        self.assertIn("put_snapshot_if_fresher", src)
        self.assertNotIn('cache.put("watchlist_metrics_snap"', src)


if __name__ == "__main__":
    unittest.main()
