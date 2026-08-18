"""
Price-freshness honesty pins (the badge must reflect DATA age, not key
presence — a broken refresh-prices job used to render week-old prices under
an "FMP LIVE" badge).

Pins:
  • fmp_client.get_quote keeps FMP's own quote timestamp (it used to be
    dropped at parse, so junk quotes were undetectable downstream).
  • price_cache_store.upsert_prices is an INGEST GUARD, not a re-stamper:
    a quote whose FMP timestamp proves it frozen (>5 days old) is dropped
    (previous cached row survives), while updated_at stays WRITE time — the
    job heartbeat the ≤120-ticker repair path and max_age_s reads depend on
    (data-time stamping was tried and reverted: off-hours it made every row
    look stale and the repair path live-refetched all weekend).
  • ui.overview_table.price_badge_state (pure, no Streamlit) returns
    offline / live / as-of (1h–76h warn) / stale (>76h danger); 76h keeps
    a normal weekend gap (~65h) out of danger styling while still flagging
    a dead job (the 2026-06-09..12 incident) by day 3+.

DB tests run on an isolated in-memory SQLite engine (mirrors
tests/test_avg_volume.py); FMP mocked.

Run:  python -m unittest tests.test_price_freshness_badge
"""
from __future__ import annotations
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()


class TestFmpQuoteKeepsTimestamp(unittest.TestCase):

    def test_parse_keeps_fmp_timestamp(self):
        """get_quote must carry FMP's quote timestamp through — the cache's
        ingest guard needs it to detect frozen quotes."""
        from data import fmp_client
        row = {"symbol": "HTBK", "price": 8.71, "previousClose": 8.65,
               "change": 0.06, "changePercentage": 0.69, "volume": 12345,
               "timestamp": 1744920000}   # 2025-04-17-ish epoch seconds
        with patch.object(fmp_client, "_has_key", return_value=True), \
             patch.object(fmp_client, "_cache_get", return_value=None), \
             patch.object(fmp_client, "_cache_put"), \
             patch.object(fmp_client, "_get", return_value=[row]):
            q = fmp_client.get_quote("HTBK")
        self.assertEqual(q["timestamp"], 1744920000)
        self.assertEqual(q["price"], 8.71)

    def test_empty_quote_has_timestamp_key(self):
        from data.fmp_client import _empty_quote
        self.assertIn("timestamp", _empty_quote())
        self.assertIsNone(_empty_quote()["timestamp"])


class _DbCase(unittest.TestCase):
    """Isolated in-memory SQLite (mirrors tests/test_avg_volume.py)."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.price_cache_store as store

        self._db = db
        self._store = store
        self._saved_db_engine = db._engine
        self._saved_store_engine = store._engine
        db._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
        store._engine = None
        store.init_price_cache_schema()

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine = self._saved_db_engine
        self._store._engine = self._saved_store_engine


class TestFrozenQuoteIngestGuard(_DbCase):

    def test_frozen_row_dropped_fresh_and_bare_rows_written(self):
        """A 6-day-old FMP timestamp is a frozen/junk quote (the HTBK case:
        months-old quote served as current) — dropped, while a fresh-ts row
        and a no-ts row in the SAME batch are upserted normally."""
        now = datetime.now(timezone.utc)
        n = self._store.upsert_prices({
            "OLD": {"price": 8.71,
                    "timestamp": (now - timedelta(days=6)).timestamp()},
            "FRESH": {"price": 10.0,
                      "timestamp": (now - timedelta(hours=1)).timestamp()},
            "NOTS": {"price": 11.0},
        })
        self.assertEqual(n, 2)
        got = self._store.get_prices(["OLD", "FRESH", "NOTS"])
        self.assertNotIn("OLD", got)          # never cached as current
        self.assertEqual(got["FRESH"]["price"], 10.0)
        self.assertEqual(got["NOTS"]["price"], 11.0)

    def test_frozen_row_preserves_prior_cached_value(self):
        """Dropping a frozen quote leaves the previous cached row in place
        (stale-but-real beats junk; absence would beat junk too)."""
        now = datetime.now(timezone.utc)
        self._store.upsert_prices({"AAA": {"price": 10.0}})
        n = self._store.upsert_prices(
            {"AAA": {"price": 99.0,
                     "timestamp": (now - timedelta(days=30)).timestamp()}})
        self.assertEqual(n, 0)
        self.assertEqual(self._store.get_prices(["AAA"])["AAA"]["price"], 10.0)

    def test_garbage_timestamp_accepted(self):
        """Unparseable / epoch-0 timestamps prove nothing → row accepted
        (can't prove it's junk)."""
        n = self._store.upsert_prices({
            "DDD": {"price": 13.0, "timestamp": "not-a-number"},
            "EEE": {"price": 14.0, "timestamp": 0},          # epoch-0 junk
        })
        self.assertEqual(n, 2)

    def test_updated_at_is_write_time_despite_sane_old_ts(self):
        """updated_at stays the job heartbeat (write time) even when the
        quote carries a sane 2-day-old FMP timestamp — a Friday quote
        upserted over the weekend must NOT look >6h old, or app.py's
        ≤120-ticker repair path would live-refetch on every page view."""
        now = datetime.now(timezone.utc)
        self._store.upsert_prices(
            {"BBB": {"price": 11.0,
                     "timestamp": (now - timedelta(days=2)).timestamp()}})
        age = self._store.get_prices(["BBB"])["BBB"]["age_seconds"]
        self.assertLess(abs(age), 60)

    def test_max_updated_at_reflects_last_write(self):
        before = datetime.now(timezone.utc) - timedelta(seconds=60)
        self._store.upsert_prices({"AAA": {"price": 10.0}})
        ts = self._store.get_max_updated_at()
        self.assertIsNotNone(ts)
        self.assertGreater(ts, before)

    def test_max_updated_at_none_on_empty_table(self):
        self.assertIsNone(self._store.get_max_updated_at())


class TestPriceBadgeState(unittest.TestCase):

    NOW = datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)  # 14:00 EDT Tue

    def _badge(self, fmp_ok, ts):
        from ui.overview_table import price_badge_state
        return price_badge_state(fmp_ok, ts, self.NOW)

    def test_offline_without_key(self):
        label, cls = self._badge(False, self.NOW)
        self.assertEqual(label, "PRICES OFFLINE")
        self.assertEqual(cls, "freshness-stale")

    def test_live_under_an_hour(self):
        label, cls = self._badge(True, self.NOW - timedelta(minutes=59))
        self.assertEqual(label, "FMP LIVE")
        self.assertEqual(cls, "freshness-live")

    def test_as_of_time_within_a_day(self):
        # 16:00 UTC = 12:00 EDT on 2026-08-18
        label, cls = self._badge(True, self.NOW - timedelta(hours=2))
        self.assertEqual(label, "PRICES AS OF 12:00 ET")
        self.assertEqual(cls, "freshness-cached")

    def test_as_of_date_weekend_gap_stays_warn(self):
        # 75h ago = Fri 2026-08-15 15:00 UTC — a normal weekend gap (~65h)
        # plus margin must render as warn "as of", never danger STALE.
        label, cls = self._badge(True, self.NOW - timedelta(hours=75))
        self.assertEqual(label, "PRICES AS OF 2026-08-15")
        self.assertEqual(cls, "freshness-cached")

    def test_stale_beyond_76_hours(self):
        label, cls = self._badge(True, self.NOW - timedelta(hours=77))
        self.assertEqual(label, "PRICES STALE — 2026-08-15")
        self.assertEqual(cls, "freshness-stale")

    def test_no_cache_rows_with_key_is_live(self):
        # Empty cache + key: served prices are live FMP fetches (app.py's
        # fallback path), so LIVE is the honest state.
        label, cls = self._badge(True, None)
        self.assertEqual(label, "FMP LIVE")
        self.assertEqual(cls, "freshness-live")


if __name__ == "__main__":
    unittest.main()
