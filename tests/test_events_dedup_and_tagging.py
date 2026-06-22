"""
Pins the 2026-06-15 news-quality pass on the events pipeline:

  • cross-source dedup — the SAME syndicated release ingested from one wire is
    NOT re-added from another (collapses cross-wire/aggregator duplicates),
    while distinct headlines and SEC 8-K filings are left untouched.
  • wire bank-tagging — wire adapters match on the TITLE only, so a bank named
    only in the body (as an underwriter / investor / advisor) is NOT mis-tagged.
  • name-matching coverage — one-word brands ("JPMorganChase") and legal-suffix
    forms ("Zions Bancorporation"→"Zions", "Comerica Incorporated"→"Comerica")
    now resolve to their ticker.
  • regulatory coverage — material enforcement events pass the Google News
    first-party gate even though they carry no company PR verb.

No live network: the RSS layer is mocked; the store runs on a private in-memory
SQLite DB.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.db as db  # noqa: E402
import data.events.store as store  # noqa: E402
from data.events.base import Event  # noqa: E402
from data.events.wire_base import (  # noqa: E402
    match_tickers, _normalize_name, RSSItem,
)

NOW = datetime.now(timezone.utc)


def _fresh_db():
    """Private in-memory SQLite so tests never touch cache.db / Postgres."""
    from sqlalchemy import create_engine
    db._engine = create_engine("sqlite://")
    db.USE_POSTGRES = False
    store._USE_POSTGRES = False
    store._engine = None
    store.init_schema()


def _ev(ticker, source, headline, ext_id, age_min=1, url="https://x.com/a"):
    return Event(ticker=ticker, source=source, event_type="press_release",
                 headline=headline, published_at=NOW - timedelta(minutes=age_min),
                 url=url, external_id=ext_id)


class TestCrossSourceDedup(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_same_release_two_wires_collapses(self):
        # A release ingested from Business Wire is NOT duplicated when Google
        # News re-syndicates the identical headline for the same bank.
        h = "Ameris Bancorp Reports Second Quarter 2026 Results"
        first = store.insert_events_returning_new(
            [_ev("ABCB", "businesswire", h, "bw-guid-1")])
        second = store.insert_events_returning_new(
            [_ev("ABCB", "google_news", h, "ABCB::ameris-q2")])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0, "cross-wire duplicate must collapse")

    def test_collapse_within_a_single_batch(self):
        # Both copies arriving in one insert call still collapse to one row.
        h = "Zions Bancorporation Prices $500 Million Senior Notes Offering"
        new = store.insert_events_returning_new([
            _ev("ZION", "prnewswire", h, "prn-1"),
            _ev("ZION", "google_news", h, "ZION::notes"),
            _ev("ZION", "yfinance_news", h, "yf-1"),
        ])
        self.assertEqual(len(new), 1)

    def test_punctuation_and_case_differences_still_collapse(self):
        a = "First Horizon Announces Share Repurchase Program of $500 Million"
        b = "First Horizon announces share repurchase program of $500 million!"
        store.insert_events_returning_new([_ev("FHN", "businesswire", a, "bw-2")])
        second = store.insert_events_returning_new([_ev("FHN", "google_news", b, "gn-2")])
        self.assertEqual(len(second), 0)

    def test_distinct_headlines_not_collapsed(self):
        new = store.insert_events_returning_new([
            _ev("PNC", "businesswire", "PNC Financial Increases Quarterly Dividend", "bw-a"),
            _ev("PNC", "google_news", "PNC Financial Appoints New Chief Risk Officer", "gn-a"),
        ])
        self.assertEqual(len(new), 2, "different stories must both survive")

    def test_different_tickers_not_collapsed(self):
        h = "Reports Second Quarter 2026 Results"
        new = store.insert_events_returning_new([
            _ev("ABCB", "businesswire", "Ameris " + h, "bw-x"),
            _ev("SFBS", "businesswire", "ServisFirst " + h, "bw-y"),
        ])
        self.assertEqual(len(new), 2)

    def test_sec_8k_excluded_from_content_dedup(self):
        # 8-K headlines are generic ("8-K · Earnings / Results"); two DISTINCT
        # filings share a headline but must both survive (they dedup on
        # accession, not content).
        new = store.insert_events_returning_new([
            _ev("JPM", "sec_8k", "8-K · Earnings / Results", "0001-25-000001"),
            _ev("JPM", "sec_8k", "8-K · Earnings / Results", "0001-25-000002"),
        ])
        self.assertEqual(len(new), 2)

    def test_8k_and_wire_same_headline_coexist(self):
        # An 8-K is not collapsed against a wire release even if worded alike —
        # the 8-K is the primary filing and stays.
        store.insert_events_returning_new(
            [_ev("JPM", "businesswire", "JPMorgan Chase Declares Dividend", "bw-z")])
        new = store.insert_events_returning_new(
            [_ev("JPM", "sec_8k", "JPMorgan Chase Declares Dividend", "acc-1")])
        self.assertEqual(len(new), 1, "8-K is exempt from content dedup")


class TestNameMatchingCoverage(unittest.TestCase):
    def test_one_word_jpmorganchase_brand(self):
        self.assertIn("JPM", match_tickers(
            "JPMorganChase Expands Security and Resiliency Initiative to Canada"))

    def test_zions_brand_after_bancorporation_strip(self):
        self.assertEqual(_normalize_name("Zions Bancorporation"), "ZIONS")
        self.assertIn("ZION", match_tickers(
            "Zions enters written agreement with the Federal Reserve"))

    def test_incorporated_suffix_stripped(self):
        # Legal "Incorporated" suffix dropped so the SEC name matches the brand
        # used in headlines.
        self.assertEqual(_normalize_name("Comerica Incorporated"), "COMERICA")

    def test_bancorp_not_overstripped(self):
        # "Bancorp" (vs "Bancorporation") is intentionally preserved so common
        # names aren't shortened to a single generic token.
        self.assertEqual(_normalize_name("First Bancorp"), "FIRST BANCORP")


class TestDisambiguateUnit(unittest.TestCase):
    """_disambiguate scoring in isolation (no index build)."""

    def setUp(self):
        from data.events import wire_base as wb
        self.wb = wb

    def _hay(self, s):
        return self.wb._alnum_pad(s)

    def test_ticker_cue_wins(self):
        self.assertEqual(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First BanCorp (NYSE: FBP) reports")), "FBP")

    def test_geo_cue_wins(self):
        self.assertEqual(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First Bancorp, based in Maine, ...")), "FNLC")

    def test_no_cue_returns_none(self):
        self.assertIsNone(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First Bancorp reported results")))

    def test_conflicting_cues_tie_returns_none(self):
        # Both tickers present (e.g. a comparison piece) → can't tell → no tag.
        self.assertIsNone(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("FBP vs FNLC: which bank wins")))


class TestAmbiguousNameDisambiguation(unittest.TestCase):
    """Real index: 'First Bancorp' (FBP/FNLC, same name + different CIKs) is
    recovered via ticker/geo in the body, without regressing share-class
    siblings (First Niles) or distinguishable collisions (Citizens Holding)."""

    def test_only_same_name_diff_cik_is_ambiguous(self):
        from data.events import wire_base as wb
        wb.build_name_index()
        self.assertIn("FIRST BANCORP", wb._AMBIGUOUS_INDEX)
        self.assertEqual(set(wb._AMBIGUOUS_INDEX["FIRST BANCORP"]), {"FBP", "FNLC"})
        # Citizens (different resolved names) and share-class siblings must NOT
        # be treated as ambiguous.
        self.assertNotIn("CITIZENS", wb._AMBIGUOUS_INDEX)
        self.assertNotIn("FIRST NILES FINANCIAL", wb._AMBIGUOUS_INDEX)

    def test_fbp_recovered_by_ticker(self):
        self.assertEqual(
            match_tickers("First BanCorp to Announce Q2 2026 Results",
                          context="San Juan, Puerto Rico. First BanCorp (NYSE: FBP) today..."),
            ["FBP"])

    def test_fnlc_recovered_by_geo(self):
        self.assertIn("FNLC", match_tickers(
            "The First Bancorp Declares Quarterly Dividend",
            context="Damariscotta, Maine — The First Bancorp announced..."))

    def test_first_bancorp_no_cue_skips(self):
        # Ambiguous and nothing distinguishes them → tag NONE (never a guess).
        self.assertEqual(
            match_tickers("First Bancorp Reports Results",
                          context="First Bancorp reported results today."),
            [])

    def test_citizens_holding_not_regressed(self):
        self.assertEqual(match_tickers("Citizens Holding Company Declares Dividend"),
                         ["CIZN"])

    def test_share_class_sibling_collapsed(self):
        self.assertEqual(match_tickers("First Niles Financial Reports Earnings"),
                         ["FNFI"])


class TestWireTitleOnlyTagging(unittest.TestCase):
    """Wire adapters must tag on the TITLE only — a bank named in the body as an
    investor/underwriter/advisor is not the subject and must not be tagged."""

    def _run(self, items, universe):
        from data.events.globenewswire import GlobeNewswireAdapter
        # One mocked feed payload; the adapter calls fetch_rss once per feed URL
        # and dedups on guid, so returning the same list each call is fine.
        with patch("data.events.globenewswire.fetch_rss", return_value=items):
            return GlobeNewswireAdapter().poll(universe)

    def test_body_only_bank_mention_not_tagged(self):
        items = [
            RSSItem(
                title="Arcade Raises $60M to Build Autonomous AI Agents",
                summary="The round was led by JPMorgan Chase and Morgan Stanley.",
                link="https://globenewswire.com/arcade", published=NOW, guid="g1"),
            RSSItem(
                title="JPMorgan Chase Declares Dividend on Preferred Stock",
                summary="Routine quarterly declaration.",
                link="https://globenewswire.com/jpm", published=NOW, guid="g2"),
        ]
        evs = self._run(items, ["JPM", "MS"])
        tagged = {e.ticker for e in evs}
        self.assertNotIn("MS", tagged, "body-only investor mention must not tag MS")
        self.assertIn("JPM", tagged, "the bank's own titled release must be tagged")
        # The Arcade funding story must produce NO bank event at all.
        self.assertTrue(all("Arcade" not in e.headline for e in evs))


class TestPurgeRevalidatesNameMatches(unittest.TestCase):
    """_purge_junk_events re-runs the (now trap-aware) name matcher on stored
    wire/aggregator rows, so a historical mis-tag the matcher would no longer
    make ("First United" on a Century 21 PR) is cleaned out — while the bank's
    own correctly-tagged releases survive."""

    def setUp(self):
        _fresh_db()

    def test_corrected_mistag_purged_legit_kept(self):
        import jobs.poll_events as pe
        import data.events.wire_base as wb
        # Deterministic mini name index (avoids a live universe build).
        with patch.object(wb, "_NAME_INDEX", [("FIRST UNITED", "FUNC")]):
            store.insert_events_returning_new([
                _ev("FUNC", "businesswire",
                    "Century 21 Brand Expands Global Footprint With Opening of "
                    "First United Arab Emirates Office", "bw-junk",
                    url="https://www.businesswire.com/news/junk"),
                _ev("FUNC", "businesswire",
                    "First United Corporation Declares Quarterly Cash Dividend",
                    "bw-legit", url="https://www.businesswire.com/news/legit"),
            ])
            n = pe._purge_junk_events()
        self.assertEqual(n, 1, "only the mis-tagged row should be purged")
        from sqlalchemy import text
        with db._engine.connect() as c:
            kept = [r[0] for r in c.execute(text("SELECT headline FROM events")).all()]
        self.assertEqual(len(kept), 1)
        self.assertIn("Declares Quarterly Cash Dividend", kept[0])


class TestGoogleNewsRegulatoryCoverage(unittest.TestCase):
    def test_regulatory_event_passes_first_party_gate(self):
        from data.events.google_news import GoogleNewsAdapter
        items = [RSSItem(
            title="OCC fines Wells Fargo $250 million over compliance failures - Reuters",
            summary="", link="https://reuters.com/wf", published=NOW, guid="r1")]
        with patch("data.events.google_news.fetch_rss", return_value=items):
            evs = GoogleNewsAdapter()._fetch_ticker("WFC", NOW - timedelta(days=2))
        self.assertEqual(len(evs), 1, "regulatory action should pass the gate")
        self.assertEqual(evs[0].ticker, "WFC")


if __name__ == "__main__":
    unittest.main()
