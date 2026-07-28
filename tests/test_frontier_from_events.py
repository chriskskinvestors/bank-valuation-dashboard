"""
(AUDIT-2026-07-27) Frontier freshness from the events store instead of a
per-bank EDGAR submissions fetch.

release_metrics._current_accession fetched the multi-MB submissions JSON per
bank, uncached, for every bank past the 15-min re-check gate — ~538 serialized
fetches on every metrics build, the dominant cost of the refresh-home-snapshot
timeout incident (measured: 1650s builds vs 201s when the gate happened to hit).

The blocker to reading it from the events store: BOTH 8-K adapters DROPPED
filings whose only item was 9.01, and some registrants furnish their earnings
release under 9.01 alone (ASB) — so the store could never see those earnings
filings and the bank would keep serving LAST quarter's release. They are now
ingested under EXHIBIT_ONLY_TYPE and hidden from display queries instead.

Pins:
  1. a 9.01-only 8-K is INGESTED (not dropped) and classified exhibit_only.
  2. display readers never surface exhibit_only rows.
  3. the frontier reader DOES see them (that is the whole point).
  4. the store answers the frontier without touching EDGAR.
  5. the store defers to EDGAR whenever it cannot prove an answer: poller not
     demonstrably live, unknown CIK, or no 8-K on record.

All DB access runs on an isolated in-memory SQLite engine; no network.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.events.store as store  # noqa: E402
import data.release_metrics as rm  # noqa: E402
from data.events.sec_8k import (EXHIBIT_ONLY_TYPE,  # noqa: E402
                                _classify_event_type)


def _engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker VARCHAR(20),
                source VARCHAR(40) NOT NULL,
                event_type VARCHAR(40) NOT NULL,
                headline TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                external_id VARCHAR(255),
                published_at TIMESTAMP NOT NULL,
                ingested_at TIMESTAMP,
                raw_json TEXT,
                UNIQUE (source, external_id)
            )
        """))
    return eng


class _IsolatedStore(unittest.TestCase):
    def setUp(self):
        self._eng = _engine()
        p = patch.object(store, "_get_engine", lambda: self._eng)
        p.start()
        self.addCleanup(p.stop)

    def _insert(self, ticker, accession, event_type, published, ingested=None,
                source="sec_8k", headline="8-K"):
        with self._eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO events (ticker, source, event_type, headline,
                                    summary, url, external_id, published_at,
                                    ingested_at)
                VALUES (:t, :s, :et, :h, '', '', :x, :p, :i)
            """), {"t": ticker, "s": source, "et": event_type, "h": headline,
                   "x": accession, "p": published,
                   "i": ingested or datetime.now(timezone.utc)})


class TestExhibitOnlyIsIngestedNotDropped(unittest.TestCase):
    def test_9_01_only_classifies_as_exhibit_only(self):
        self.assertEqual(_classify_event_type(["9.01"], ""), EXHIBIT_ONLY_TYPE)

    def test_9_01_alongside_a_real_item_keeps_the_real_type(self):
        self.assertEqual(_classify_event_type(["2.02", "9.01"], ""), "earnings")
        self.assertEqual(_classify_event_type(["5.02", "9.01"], ""),
                         "executive_change")

    def test_adapters_no_longer_drop_9_01_only_filings(self):
        """Structural: the `continue` that made the store an incomplete 8-K
        record must not come back in either adapter."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "data/events/sec_8k.py").read_text(encoding="utf-8")
        self.assertNotIn('if set(items) == {"9.01"}:\n                continue',
                         src.replace("\r\n", "\n"))
        self.assertNotIn('if set(item_codes) == {"9.01"}:\n                continue',
                         src.replace("\r\n", "\n"))


class TestExhibitOnlyHiddenFromDisplay(_IsolatedStore):
    def setUp(self):
        super().setUp()
        now = datetime.now(timezone.utc)
        self._insert("AAA", "0000-26-000001", "earnings", now - timedelta(days=2))
        self._insert("AAA", "0000-26-000002", EXHIBIT_ONLY_TYPE,
                     now - timedelta(hours=1))

    def test_per_ticker_feed_excludes_exhibit_only(self):
        rows = store.get_recent_events("AAA", limit=10)
        self.assertEqual([r["external_id"] for r in rows], ["0000-26-000001"])

    def test_universe_feed_excludes_exhibit_only(self):
        rows = store.get_universe_recent(limit=10)
        self.assertTrue(all(r["event_type"] != EXHIBIT_ONLY_TYPE for r in rows))
        self.assertEqual(len(rows), 1)

    def test_universe_feed_with_source_filter_excludes_exhibit_only(self):
        rows = store.get_universe_recent(limit=10, sources=["sec_8k"])
        self.assertTrue(all(r["event_type"] != EXHIBIT_ONLY_TYPE for r in rows))
        self.assertEqual(len(rows), 1)

    def test_frontier_reader_DOES_see_exhibit_only(self):
        """The regression this whole change exists to prevent: the newest filing
        is 9.01-only, and the frontier must be it — not the older earnings 8-K,
        which would freeze the bank on last quarter's release."""
        self.assertEqual(store.latest_8k_accession("AAA"), "0000-26-000002")


class TestPollerLivenessProbe(_IsolatedStore):
    def test_recent_ingest_is_live(self):
        self._insert("AAA", "x1", "earnings", datetime.now(timezone.utc))
        self.assertTrue(store.events_ingested_within(3600))

    def test_only_old_ingests_is_not_live(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self._insert("AAA", "x2", "earnings", old, ingested=old)
        self.assertFalse(store.events_ingested_within(3600))

    def test_empty_store_is_not_live(self):
        self.assertFalse(store.events_ingested_within(3600))


def _boom(*a, **k):
    raise AssertionError("EDGAR must not be touched on the store fast path")


class TestCurrentAccessionPrefersStore(_IsolatedStore):
    def setUp(self):
        super().setUp()
        rm._CIK_TICKER = {}                       # never leak between tests
        self.addCleanup(setattr, rm, "_CIK_TICKER", {})

    def test_store_answers_without_touching_edgar(self):
        self._insert("AAA", "0000-26-000009", EXHIBIT_ONLY_TYPE,
                     datetime.now(timezone.utc))
        with patch.object(rm, "_ticker_for_cik", return_value="AAA"), \
                patch("data.sec_filing_scraper._get", _boom):
            self.assertEqual(rm._current_accession(123), "0000-26-000009")

    def test_falls_back_to_edgar_when_poller_is_not_live(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self._insert("AAA", "0000-26-000009", "earnings", old, ingested=old)
        called = {}

        def fake_get(url):
            called["url"] = url
            return "{}"

        with patch.object(rm, "_ticker_for_cik", return_value="AAA"), \
                patch("data.sec_filing_scraper._get", fake_get), \
                patch("data.ir_provider._earnings_8k_candidates",
                      return_value=[{"accession": "0000-26-000042"}]):
            self.assertEqual(rm._current_accession(123), "0000-26-000042")
        self.assertIn("data.sec.gov/submissions", called["url"])

    def test_falls_back_to_edgar_when_cik_has_no_ticker(self):
        self._insert("AAA", "x", "earnings", datetime.now(timezone.utc))
        with patch.object(rm, "_ticker_for_cik", return_value=None), \
                patch("data.sec_filing_scraper._get", return_value="{}"), \
                patch("data.ir_provider._earnings_8k_candidates",
                      return_value=[{"accession": "0000-26-000042"}]):
            self.assertEqual(rm._current_accession(123), "0000-26-000042")

    def test_falls_back_to_edgar_when_no_8k_on_record(self):
        # Poller is live (a non-8-K event was ingested) but this bank has none.
        self._insert("BBB", "w1", "press_release", datetime.now(timezone.utc),
                     source="businesswire")
        with patch.object(rm, "_ticker_for_cik", return_value="AAA"), \
                patch("data.sec_filing_scraper._get", return_value="{}"), \
                patch("data.ir_provider._earnings_8k_candidates",
                      return_value=[{"accession": "0000-26-000042"}]):
            self.assertEqual(rm._current_accession(123), "0000-26-000042")


class TestCikTickerMemoization(unittest.TestCase):
    def setUp(self):
        rm._CIK_TICKER = {}
        self.addCleanup(setattr, rm, "_CIK_TICKER", {})

    def test_empty_map_is_never_pinned(self):
        """Audit P1 #3's bug shape: an empty resolution must retry, not pin."""
        calls = []

        def fake_map(tickers):
            calls.append(1)
            return {} if len(calls) == 1 else {5: "AAA"}

        with patch("data.bank_universe.get_universe_tickers",
                   return_value=["AAA"]), \
                patch("data.events.sec_8k._canonical_cik_map", fake_map):
            self.assertIsNone(rm._ticker_for_cik(5))     # first: empty
            self.assertEqual(rm._ticker_for_cik(5), "AAA")  # retried
        self.assertEqual(len(calls), 2)

    def test_non_numeric_cik_is_none(self):
        self.assertIsNone(rm._ticker_for_cik(None))
        self.assertIsNone(rm._ticker_for_cik("not-a-cik"))


if __name__ == "__main__":
    unittest.main()
