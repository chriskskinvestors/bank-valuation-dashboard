"""
(AUDIT-2026-07-02 P1 #3) cache.get's global 24h TTL silently defeated the
universe snapshot's serve-stale fallback: a snapshot >24h old read as MISSING,
not stale, so one failed/late nightly refresh put the ~6.5-min live build back
on the request path (the 2026-06-13 hang mode) — and bank_mapping's snapshot
resolver tier memoized the empty result for the process lifetime.

Pins:
  1. cache.get(key, max_age_s=None) returns entries older than the default
     TTL; the default path still expires them.
  2. bank_universe._load_lastgood serves a >24h-old snapshot (stale, never
     missing).
  3. bank_mapping._universe_snapshot_map never pins an empty snapshot — it
     retries and picks the snapshot up once it exists, at any age.

All DB access runs on an isolated in-memory SQLite engine; no network.
"""
import sys
import time
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402


def _fresh_engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
            "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
    return eng


class _IsolatedCache(unittest.TestCase):
    """Route data.cache at a private in-memory engine for the test's duration."""

    def setUp(self):
        self._eng = _fresh_engine()
        p = patch.object(cache, "_engine", self._eng)
        p.start()
        self.addCleanup(p.stop)

    def _age(self, key: str, seconds: float):
        """Backdate a stored row's timestamp by `seconds`."""
        with self._eng.begin() as conn:
            conn.execute(text("UPDATE cache SET timestamp = :t WHERE key = :k"),
                         {"t": time.time() - seconds, "k": key})


class TestMaxAge(_IsolatedCache):
    def test_default_ttl_still_expires(self):
        cache.put("k", {"v": 1})
        self._age("k", cache.TTL_SECONDS + 60)
        self.assertIsNone(cache.get("k"))

    def test_max_age_none_serves_any_age(self):
        cache.put("k", {"v": 1})
        self._age("k", cache.TTL_SECONDS + 60)
        self.assertEqual(cache.get("k", max_age_s=None), {"v": 1})

    def test_fresh_entry_served_by_default(self):
        cache.put("k", {"v": 2})
        self.assertEqual(cache.get("k"), {"v": 2})


class TestUniverseStaleFallback(_IsolatedCache):
    def test_lastgood_older_than_24h_is_stale_not_missing(self):
        import data.bank_universe as bu
        cached_at = (datetime.now() - timedelta(hours=30)).isoformat()
        cache.put("bank_universe_lastgood",
                  {"cached_at": cached_at,
                   "universe": {"TBNK": {"name": "Test Bank", "cik": 1}}})
        self._age("bank_universe_lastgood", 30 * 3600)
        uni, fresh = bu._load_lastgood()
        self.assertIsNotNone(
            uni, "a >24h snapshot must be servable (stale), never missing — "
                 "it is the designed fallback when the nightly refresh fails")
        self.assertIn("TBNK", uni)
        self.assertFalse(fresh)


class TestSnapshotTierRetries(_IsolatedCache):
    def setUp(self):
        super().setUp()
        import data.bank_mapping as bm
        self._bm = bm
        bm._SNAPSHOT_MAP = None
        self.addCleanup(lambda: setattr(bm, "_SNAPSHOT_MAP", None))

    def test_empty_snapshot_not_pinned_for_process_lifetime(self):
        self.assertEqual(self._bm._universe_snapshot_map(), {})
        cache.put("bank_universe_lastgood",
                  {"cached_at": datetime.now().isoformat(),
                   "universe": {"TBNK": {"cik": 99}}})
        self.assertIn("TBNK", self._bm._universe_snapshot_map(),
                      "the tier must retry after an empty read, not memoize {}")

    def test_stale_snapshot_still_resolves(self):
        cache.put("bank_universe_lastgood",
                  {"cached_at": datetime.now().isoformat(),
                   "universe": {"TBNK": {"cik": 99}}})
        self._age("bank_universe_lastgood", 48 * 3600)
        self.assertEqual(self._bm._universe_snapshot_map()["TBNK"]["cik"], 99)


if __name__ == "__main__":
    unittest.main()
