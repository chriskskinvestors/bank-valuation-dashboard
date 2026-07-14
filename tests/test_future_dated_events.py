"""
Pins the 2026-07-14 future-dated-events fix.

BAC's IR-scraped note-redemption notices ("…due May 4, 2027") had their 2027
MATURITY dates parsed as the publication date, and — feeds sorting by
published_at DESC — sat pinned at the top of every feed with a negative age.
Three layers are pinned here:

  • _scraped_pub_date — a date ahead of the wall clock is an event date the
    headline mentions, never the publication date → None (caller uses now).
  • store ingest clamp — no adapter can write a future published_at.
  • heal_future_published — rows stored before the clamp are re-stamped to
    their ingested_at on the next poll.

No live network: the store runs on a private in-memory SQLite DB.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.db as db  # noqa: E402
import data.events.store as store  # noqa: E402
from data.events.base import Event  # noqa: E402
from data.events.ir_site import _scraped_pub_date  # noqa: E402

NOW = datetime.now(timezone.utc)

_BAC_HEADLINE = ("Bank of America Announces Redemption of €1,500,000,000 "
                 "1.776% Fixed/Floating Rate Senior Notes, due May 4, 2027")


def _fresh_db():
    """Private in-memory SQLite so tests never touch cache.db / Postgres."""
    from sqlalchemy import create_engine
    db._engine = create_engine("sqlite://")
    db.USE_POSTGRES = False
    store._USE_POSTGRES = False
    store._engine = None
    store.init_schema()


def _parse(ts) -> datetime:
    if hasattr(ts, "year"):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class TestScrapedPubDate(unittest.TestCase):
    def test_future_date_in_headline_is_not_a_pub_date(self):
        # The exact failure: "due May 4, 2027" parsed as published_at.
        self.assertIsNone(_scraped_pub_date("https://x.com/news/redemption",
                                            _BAC_HEADLINE, NOW))

    def test_past_date_in_text_still_used(self):
        pub = _scraped_pub_date("https://x.com/news/item",
                                "Bank Announces Results — July 1, 2026", NOW)
        self.assertEqual((pub.year, pub.month, pub.day), (2026, 7, 1))

    def test_url_date_preferred_and_used(self):
        pub = _scraped_pub_date("https://x.com/news/2026-06-15-release",
                                _BAC_HEADLINE, NOW)
        self.assertEqual((pub.year, pub.month, pub.day), (2026, 6, 15))


class TestStoreFutureClamp(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_ingest_clamps_future_published_at(self):
        future = NOW + timedelta(days=290)
        store.insert_events_returning_new([Event(
            ticker="BAC", source="ir_site", event_type="capital_raise",
            headline=_BAC_HEADLINE, published_at=future,
            url="https://x.com/r1", external_id="https://x.com/r1")])
        rows = store.get_recent_events("BAC")
        self.assertEqual(len(rows), 1)
        stored = _parse(rows[0]["published_at"])
        self.assertLessEqual(stored, NOW + store._FUTURE_GRACE,
                             "future published_at must be clamped at ingest")

    def test_near_now_timestamps_untouched(self):
        recent = NOW - timedelta(minutes=5)
        store.insert_events_returning_new([Event(
            ticker="BAC", source="ir_site", event_type="press_release",
            headline="Bank of America Declares Quarterly Dividend",
            published_at=recent, url="https://x.com/r2",
            external_id="https://x.com/r2")])
        stored = _parse(store.get_recent_events("BAC")[0]["published_at"])
        self.assertEqual(stored.replace(microsecond=0),
                         recent.replace(microsecond=0))

    def test_heal_restamps_previously_stored_future_rows(self):
        # Bypass the ingest clamp (rows written before the fix existed).
        from sqlalchemy import text
        eng = store._get_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO events (ticker, source, event_type, headline, "
                "url, external_id, published_at, ingested_at) VALUES "
                "('BAC', 'ir_site', 'capital_raise', :h, 'https://x.com/r3', "
                "'https://x.com/r3', :pub, :ing)"),
                {"h": _BAC_HEADLINE, "pub": NOW + timedelta(days=290),
                 "ing": NOW - timedelta(hours=2)})
        n = store.heal_future_published()
        self.assertEqual(n, 1)
        stored = _parse(store.get_recent_events("BAC")[0]["published_at"])
        self.assertLessEqual(stored, NOW,
                             "healed row must carry its ingest time")
        # Idempotent — a second sweep finds nothing.
        self.assertEqual(store.heal_future_published(), 0)


if __name__ == "__main__":
    unittest.main()
