"""
Pins the 2026-07-22 feed residuals (all three found by reading the live feed).

1. MIS-TAG (correctness). "CVBF stock trades steadily as Central Valley
   Community Bancorp reports higher quarterly earnings" was tagged CMTV —
   Vermont's *Community Bancorp*, whose name is a SUBSTRING of California's
   *Central Valley Community Bancorp*. A bank's page showed another bank's
   earnings: the wrong-entity class, in the news domain. _first_real_occurrence
   only guarded the RIGHT side of a match (its trap keys on the phrase's last
   word), so nothing looked left.

2. AGGREGATOR DUPES. One earnings event spawns 4-5 differently-worded Google
   News rewrites, so the content-key dedup can't collapse them. FCCO x5,
   NWFL x5, FMNB x4 took ~14 of 50 slots for three events.

3. 13F HOLDINGS. "…reports $231M in 13F equity holdings" — the bank as an
   INVESTOR; its securities portfolio isn't bank news.

Offline: the store runs on private in-memory SQLite; matching uses the real
name index (skipped if no universe snapshot).
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.db as db  # noqa: E402
import data.events.store as store  # noqa: E402
from data.events.base import Event  # noqa: E402
from data.events.wire_base import is_junk_news, match_tickers  # noqa: E402

NOW = datetime.now(timezone.utc)


def _fresh_db():
    from sqlalchemy import create_engine
    db._engine = create_engine("sqlite://")
    db.USE_POSTGRES = False
    store._USE_POSTGRES = False
    store._engine = None
    store.init_schema()


def setUpModule():
    """Warm the REAL name index before any test swaps the DB engine.

    TestAggregatorCap._fresh_db() repoints data.db at an empty in-memory
    SQLite, and unittest runs it first (alphabetical). The index build then
    finds no universe snapshot, so the mis-tag tests SILENTLY SKIPPED —
    green-looking but guarding nothing, which is worse than failing. Building
    it here (from the real cache, before the swap) makes them actually run."""
    try:
        match_tickers("warmup")
    except Exception:
        pass


def _require_index(tc):
    # Check the built index itself, not the DB — the DB may have been swapped
    # out from under us by another test class (see setUpModule).
    from data.events import wire_base
    if not wire_base._NAME_INDEX:
        tc.skipTest("name index unavailable (no persisted universe snapshot)")


class TestLeftSwallowedMisTag(unittest.TestCase):
    def test_short_name_inside_a_longer_bank_name_is_not_tagged(self):
        _require_index(self)
        h = ("CVBF stock trades steadily as Central Valley Community Bancorp "
             "reports higher quarterly earnings")
        self.assertNotIn("CMTV", match_tickers(h),
                         "Vermont's Community Bancorp must not match inside "
                         "Central Valley Community Bancorp")

    def test_standalone_mention_still_tags(self):
        # The guard must not cost us the real thing.
        _require_index(self)
        for h in ("Community Bancorp Reports Second Quarter 2026 Results",
                  "Shares rise as Community Bancorp declares quarterly dividend"):
            with self.subTest(headline=h):
                self.assertIn("CMTV", match_tickers(h))

    def test_other_banks_still_match(self):
        _require_index(self)
        for h, tk in [
            ("Norwood Financial Corp announces Second Quarter Financial Results", "NWFL"),
            ("First Community Corporation Reports Earnings Results", "FCCO"),
            ("WesBanco Reports Net Income of $88M in Second Quarter", "WSBC"),
            ("Zions Bancorporation Prices $500 Million Senior Notes Offering", "ZION"),
        ]:
            with self.subTest(ticker=tk):
                self.assertIn(tk, match_tickers(h))


class TestThirteenFRows(unittest.TestCase):
    def test_bank_own_13f_holdings_is_junk(self):
        h = "Peoples Financial Services Corp. (PFIS) reports $231M in 13F equity holdings"
        self.assertTrue(is_junk_news(h, "PFIS", source="google_news"))

    def test_real_reports_headlines_survive(self):
        for tk, h in [
            ("FMNB", "Farmers National Banc Corp. Reports Unaudited Consolidated Charge Offs for the Second Quarter"),
            ("NTRS", "Northern Trust Corporation Reports Earnings Results for the Second Quarter"),
            ("WSBC", "WesBanco Reports Net Income of $88M in Second Quarter"),
        ]:
            with self.subTest(ticker=tk):
                self.assertFalse(is_junk_news(h, tk, source="google_news"))


class TestAggregatorCap(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def _ev(self, tk, src, head, ext, et="earnings", mins=1):
        return Event(ticker=tk, source=src, event_type=et, headline=head,
                     published_at=NOW - timedelta(minutes=mins),
                     url=f"https://x.com/{ext}", external_id=ext)

    def test_aggregator_rewrites_are_capped_per_ticker_and_type(self):
        store.insert_events_returning_new([
            self._ev("NWFL", "google_news", f"Norwood Financial rewrite {i}", f"g{i}", mins=i)
            for i in range(1, 6)
        ])
        rows = store.get_universe_recent(limit=50)
        nwfl = [r for r in rows if r["ticker"] == "NWFL"]
        self.assertEqual(len(nwfl), store._AGGREGATOR_DISPLAY_CAP,
                         "aggregator rewrites of one event must be capped")

    def test_first_party_rows_are_never_capped(self):
        store.insert_events_returning_new([
            self._ev("FCCO", "sec_8k", "8-K · Earnings / Results", "acc1", mins=1),
            self._ev("FCCO", "prnewswire", "First Community Reports Q2", "prn1", mins=2),
            self._ev("FCCO", "globenewswire", "First Community Q2 Results", "gnw1", mins=3),
            self._ev("FCCO", "fmp_news", "First Community Corporation Q2", "fmp1", mins=4),
        ])
        rows = store.get_universe_recent(limit=50)
        fcco = [r for r in rows if r["ticker"] == "FCCO"]
        self.assertEqual(len(fcco), 4,
                         "first-party wires / SEC rows must never be capped")

    def test_cap_is_per_event_type_not_per_bank(self):
        # A bank's dividend must still show alongside its capped earnings rows.
        store.insert_events_returning_new(
            [self._ev("JMSB", "google_news", f"JMSB earnings rewrite {i}", f"e{i}", mins=i)
             for i in range(1, 5)]
            + [self._ev("JMSB", "google_news", "JMSB Increases Quarterly Dividend",
                        "d1", et="capital_return", mins=6)])
        rows = store.get_universe_recent(limit=50)
        jmsb = [r for r in rows if r["ticker"] == "JMSB"]
        types_ = {r["event_type"] for r in jmsb}
        self.assertIn("capital_return", types_,
                      "a different event_type must not be squeezed out by the cap")
        self.assertEqual(len([r for r in jmsb if r["event_type"] == "earnings"]),
                         store._AGGREGATOR_DISPLAY_CAP)


if __name__ == "__main__":
    unittest.main()
