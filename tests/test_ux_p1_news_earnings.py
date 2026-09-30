"""UX review 2026-09-24 — P1 items on the news / filings / earnings / branch
surfaces (docs/REVIEW-2026-09-24-ux.md, P1 table). Each class pins one
reviewed failure:

  P1-25  EDGAR index descriptions arrive entity-encoded ("JPMORGAN CHASE &amp;
         CO.") and rendered literally — unescape at parse, escape exactly once.
  P1-26  raw store keys shown as chips ("capital_raise", "fmp_news").
  P1-31  mis-attributed news: Venezuela story → HAPN, "WTFC Initiated Coverage
         by Wells Fargo" → WFC, "VKTX offering with Morgan Stanley" → MS.
  P1-24  "No consensus data for JPM yet" under a Consensus-EPS chart.
  P1-29  "Consensus Rating: None".
  P1-30  Earnings summary strip of zeros out of season.
  P1-27  branch map bounds dragged by stray points.
  P1-23  Branch "% of bank" / competitor shares without "%"; house tables.

No network, no DB: the name index is a fixed fixture and every fetch is
patched.
"""
import types
import unittest
from datetime import date
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

import pandas as pd  # noqa: E402

import data.events.wire_base as wb  # noqa: E402

# A fixed name index (longest-first, as build_name_index returns it) so the
# attribution tests never depend on the persisted universe snapshot.
_FIXTURE_INDEX = sorted([
    ("JPMORGAN CHASE", "JPM"), ("JPMORGAN", "JPM"),
    ("WELLS FARGO BANK", "WFC"), ("WELLS FARGO", "WFC"),
    ("MORGAN STANLEY", "MS"),
    ("FIFTH THIRD BANCORP", "FITB"),
    ("CITIGROUP", "C"), ("CITI", "C"),
    ("WINTRUST FINANCIAL", "WTFC"),
    # HAPN as the live index carries it: holdco "Happen, Inc." → "HAPPEN",
    # plus the brand alias "Happen Bank".
    ("HAPPEN BANK NATIONAL ASSOCIATION", "HAPN"), ("HAPPEN BANK", "HAPN"),
    ("HAPPEN", "HAPN"),
], key=lambda x: -len(x[0]))


class _FixtureIndex(unittest.TestCase):
    def setUp(self):
        self._saved = (wb._NAME_INDEX, wb._AMBIGUOUS_INDEX,
                       wb._NAME_LEADING_TOKENS)
        wb._NAME_INDEX = list(_FIXTURE_INDEX)
        wb._AMBIGUOUS_INDEX = {}
        wb._NAME_LEADING_TOKENS = None

    def tearDown(self):
        (wb._NAME_INDEX, wb._AMBIGUOUS_INDEX,
         wb._NAME_LEADING_TOKENS) = self._saved


# ── P1-31 ───────────────────────────────────────────────────────────────────
VENEZUELA = ("Trump Reportedly Told Venezuela's Leaders Elections Need To "
             "Happen. She Said Yes, But Not When.")
WTFC_BY_WFC = "WTFC Initiated Coverage by Wells Fargo -- Rating Set to Equal-We"
VKTX_WITH_MS = "VKTX Stock Offering Raised to $225M with Morgan Stanley and JPMo"


class TestNewsAttribution(_FixtureIndex):

    def test_venezuela_story_is_not_tagged_happen(self):
        # "Happen" is an English verb: a bare match needs a corporate suffix
        # or an exchange ticker now.
        self.assertIn("HAPPEN", wb._COMMON_NAME_WORDS)
        self.assertNotIn("HAPN", wb.match_tickers(VENEZUELA))

    def test_happen_bank_releases_still_tag_hapn(self):
        self.assertIn("Happen Bank", wb._BRAND_ALIASES["HAPN"])
        for h in ("Happen Bank Announces Quarterly Dividend",
                  "Happen, Inc. Reports Third Quarter 2026 Results",
                  "Happen Declares Dividend (NASDAQ: HAPN)"):
            self.assertIn("HAPN", wb.match_tickers(h), h)

    def test_bank_as_analyst_is_dropped(self):
        self.assertEqual(wb.match_tickers(WTFC_BY_WFC), ["WFC"])  # named...
        self.assertTrue(wb.is_junk_news(WTFC_BY_WFC, "WFC"))      # ...not subject
        self.assertTrue(wb.is_junk_news(
            "Wells Fargo Initiates Coverage of Wintrust Financial at Equal Weight",
            "WFC"))

    def test_bank_as_deal_counterparty_is_dropped(self):
        self.assertEqual(wb.match_tickers(VKTX_WITH_MS), ["MS"])
        self.assertTrue(wb.is_junk_news(VKTX_WITH_MS, "MS"))
        # every bank in a "with X and Y" list is a counterparty
        self.assertTrue(wb.is_junk_news(
            "VKTX Stock Offering Raised to $225M with Morgan Stanley and "
            "JPMorgan Chase", "JPM"))
        self.assertTrue(wb.is_junk_news(
            "Acme Notes Offering Underwritten by Morgan Stanley", "MS"))

    def test_true_positives_survive(self):
        for h, tk in (
                ("JPMorgan Chase stock falls 3.42 percent at the close", "JPM"),
                ("Fifth Third Bancorp Announces Redemption of Senior Notes", "FITB"),
                ("Citi Launches Premium Boost – Transforming How Clients Are "
                 "Rewarded for Their Banking Relationship", "C")):
            self.assertFalse(wb.is_junk_news(h, tk), h)
            self.assertFalse(wb.is_junk_news(h, tk, source="fmp_news"), h)
        self.assertEqual(wb.match_tickers(
            "JPMorgan Chase stock falls 3.42 percent at the close"), ["JPM"])
        self.assertEqual(wb.match_tickers(
            "Fifth Third Bancorp Announces Redemption of Senior Notes"), ["FITB"])

    def test_by_or_with_alone_is_not_enough(self):
        # The bank as ACQUIRER / merger party is its own news — keep.
        for h, tk in (
                ("Comerica to Be Acquired by Fifth Third Bancorp in Deal "
                 "Financed With Notes", "FITB"),
                ("Morgan Stanley Prices Offering of Senior Notes", "MS"),
                ("Local Bank Agrees to Merge with Fifth Third Bancorp", "FITB")):
            self.assertFalse(wb._is_counterparty_mention(h, tk), h)
        # Passive coverage: the named bank IS the covered subject.
        self.assertFalse(wb._is_counterparty_mention(
            "Wintrust Financial Initiated Coverage by Wells Fargo", "WTFC"))

    def test_display_recheck_drops_stale_google_mistag(self):
        from ui.recent_activity import _google_title_mistag
        self.assertTrue(_google_title_mistag(
            {"source": "google_news", "ticker": "HAPN", "headline": VENEZUELA}))
        self.assertFalse(_google_title_mistag(
            {"source": "google_news", "ticker": "JPM",
             "headline": "JPMorgan Chase stock falls 3.42 percent at the close"}))
        # only Google rows: their tag's sole evidence is the title match
        self.assertFalse(_google_title_mistag(
            {"source": "fmp_news", "ticker": "C", "headline": "Citi Launches X"}))


# ── P1-26 ───────────────────────────────────────────────────────────────────
class TestFeedChipLabels(unittest.TestCase):

    def test_tag_label_map_and_title_case_fallback(self):
        from ui.recent_activity import (tag_label, EVENT_TYPE_LABELS,
                                        SOURCE_LABELS)
        self.assertEqual(tag_label("capital_raise", EVENT_TYPE_LABELS), "Capital Raise")
        self.assertEqual(tag_label("capital_return", EVENT_TYPE_LABELS), "Capital Return")
        self.assertEqual(tag_label("fmp_news", SOURCE_LABELS), "FMP")
        self.assertEqual(tag_label("m_and_a", EVENT_TYPE_LABELS), "M&A")
        self.assertEqual(tag_label("some_new_type", EVENT_TYPE_LABELS), "Some New Type")

    def test_feed_row_never_shows_a_raw_key(self):
        from ui.recent_activity import _event_row
        html = _event_row({"event_type": "capital_raise", "source": "fmp_news",
                           "ticker": "C", "headline": "Citi prices notes",
                           "published_at": None, "url": None}, show_ticker=True)
        self.assertIn(">Capital Raise<", html)
        self.assertIn(">FMP<", html)
        self.assertNotIn("capital_raise", html)
        self.assertNotIn("fmp_news", html)


# ── P1-25 ───────────────────────────────────────────────────────────────────
_INDEX_HTML = (
    '<table class="tableFile"><tr><td>3</td>'
    '<td>JPMORGAN CHASE &amp; CO. EARNINGS RELEASE FINANCIAL SUPPLEMENT</td>'
    '<td><a href="/Archives/edgar/data/19617/000001961726000001/ex992.htm">'
    'ex992.htm</a></td><td>EX-99.2</td><td>1000</td></tr></table>')


class TestEdgarTitleEntities(unittest.TestCase):

    def _exhibits(self):
        import ui.key_exhibits as ke
        info = {"cik": 19617, "recent_filings": [
            {"form": "8-K", "date": "2026-07-14",
             "accession": "0000019617-26-000001"}]}
        resp = types.SimpleNamespace(text=_INDEX_HTML)
        with mock.patch.object(ke, "get_filing_info", lambda cik, **k: info), \
                mock.patch("data.http.get_with_retry", lambda *a, **k: resp):
            return ke.fetch_key_exhibits(19617)

    def test_description_is_unescaped_at_parse(self):
        ex = self._exhibits()
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]["description"],
                         "JPMORGAN CHASE & CO. EARNINGS RELEASE FINANCIAL SUPPLEMENT")

    def test_exhibit_table_escapes_exactly_once(self):
        from ui.key_exhibits import _exhibit_table
        for desc in ("JPMORGAN CHASE & CO. ER",       # parsed (post-fix)
                     "JPMORGAN CHASE &amp; CO. ER"):  # cached pre-fix row
            html = _exhibit_table([{
                "form": "8-K", "filed": "2026-07-14", "type": "EX-99.2",
                "family": "Press Release / Presentation", "color": "#0891b2",
                "description": desc, "url": "https://www.sec.gov/x.htm"}])
            self.assertIn("JPMORGAN CHASE &amp; CO. ER", html)
            self.assertNotIn("&amp;amp;", html)
            self.assertIn('class="ksk-grid', html)       # house table (P1-23)

    def test_recent_documents_menu_escapes_exactly_once(self):
        from ui.recent_documents import _exhibit_menu
        for desc in ("JPMORGAN CHASE & CO. ER", "JPMORGAN CHASE &amp; CO. ER"):
            html = _exhibit_menu({"url": "https://www.sec.gov/x.htm",
                                  "description": desc}, "JPM", "Press Release (PR)")
            self.assertIn("JPMORGAN CHASE &amp; CO. ER", html)
            self.assertNotIn("&amp;amp;", html)


# ── P1-24 / P1-29 / P1-30 ──────────────────────────────────────────────────
class TestEarningsCopy(unittest.TestCase):

    def test_consensus_rating_none_is_absent(self):
        from ui.earnings import _rec_label
        for rec in (None, "None", "none", "", "  "):
            self.assertEqual(_rec_label(rec), "—", repr(rec))
        self.assertEqual(_rec_label("strong_buy"), "Strong Buy")
        self.assertEqual(_rec_label("hold"), "Hold")

    def test_no_uploaded_estimates_note(self):
        from ui.earnings import _no_uploaded_estimates_note
        self.assertEqual(
            _no_uploaded_estimates_note("JPM", True),
            "No uploaded broker estimates for JPM — street consensus above "
            "is from market data.")
        # never claims a street consensus that didn't render
        self.assertNotIn("street consensus",
                         _no_uploaded_estimates_note("BSBK", False))


class TestEarningsSummaryStrip(unittest.TestCase):

    def _rows(self, **over):
        from ui.earnings import _earnings_summary_rows
        kw = dict(cal_failed=False, upcoming_7=0, upcoming_14=1,
                  upcoming=[(date(2026, 10, 14), "MTB")],
                  banks_with_consensus=1, beats=0, misses=0, inlines=0,
                  avg_surprise=None)
        kw.update(over)
        return dict(_earnings_summary_rows(**kw))

    def test_out_of_season_hides_zero_kpis_and_names_next_report(self):
        rows = self._rows()
        self.assertNotIn("Reporting This Week", rows)
        self.assertNotIn("Total Metrics Compared", rows)
        self.assertNotIn("Beat Rate", rows)
        self.assertNotIn("Last Qtr Avg Surprise", rows)
        self.assertIn("2026-10-14", rows["Next Report"])
        self.assertIn("(MTB)", rows["Next Report"])
        self.assertEqual(rows["Banks w/ Consensus"], "1")

    def test_next_report_picks_earliest_date_and_counts_ties(self):
        rows = self._rows(upcoming=[(date(2026, 10, 20), "JPM"),
                                    (date(2026, 10, 14), "WFC"),
                                    (date(2026, 10, 14), "MTB")])
        self.assertIn("2026-10-14", rows["Next Report"])
        self.assertIn("(MTB +1)", rows["Next Report"])

    def test_in_season_values_render(self):
        rows = self._rows(upcoming_7=3, upcoming_14=5, beats=6, misses=2,
                          inlines=2, avg_surprise=2.5)
        self.assertTrue(rows["Reporting This Week"].startswith("3 "))
        self.assertNotIn("Next Report", rows)
        self.assertTrue(rows["Total Metrics Compared"].startswith("10 "))
        self.assertTrue(rows["Beat Rate"].startswith("60% "))
        self.assertTrue(rows["Last Qtr Avg Surprise"].startswith("+2.5% "))

    def test_calendar_outage_says_so_never_zero(self):
        rows = self._rows(cal_failed=True, upcoming=[], banks_with_consensus=0)
        self.assertIn("calendar feed unavailable", rows["Reporting This Week"])
        self.assertTrue(rows["Reporting This Week"].startswith("—"))
        self.assertNotIn("n/a", rows["Reporting This Week"])
        self.assertNotIn("Banks w/ Consensus", rows)

    def test_nothing_to_show_is_empty(self):
        self.assertEqual(self._rows(upcoming=[], upcoming_14=0,
                                    banks_with_consensus=0), {})


# ── P1-27 / P1-23 (branches) ────────────────────────────────────────────────
class TestBranchMapAndTables(unittest.TestCase):

    def test_map_extent_ignores_stray_outliers(self):
        from ui.branch_analytics import _map_extent
        from ui.geo_view import _fit_viewport
        lats = [30 + i * 0.15 for i in range(100)] + [0.0]      # 30..44.85 + (0,0)
        lngs = [-120 + i * 0.5 for i in range(100)] + [0.0]     # -120..-70.5
        center, _zoom = _fit_viewport(*_map_extent(lats, lngs))
        self.assertTrue(25 < center["lat"] < 45, center)
        self.assertTrue(-110 < center["lon"] < -85, center)    # centered on the US
        # the untrimmed extent is what dragged the view into the Atlantic
        raw_center, _ = _fit_viewport(pd.Series(lats), pd.Series(lngs))
        self.assertGreater(raw_center["lon"], -65)

    def test_small_roster_keeps_every_branch(self):
        from ui.branch_analytics import _map_extent
        la, lo = _map_extent([37.8, 37.9, 10.0], [-122.3, -122.1, -60.0])
        self.assertEqual((float(la.min()), float(lo.max())), (10.0, -60.0))

    def _roster(self):
        return pd.DataFrame([
            ("Main Office", "1 Main St", "Oakland", "CA", "Alameda", "SF", 300_000,
             "06001", 2025, 111, 37.80, -122.27),
            ("Elm Branch", "2 Elm St", "Berkeley", "CA", "Alameda", "SF", 100_000,
             "06001", 2025, 111, 37.87, -122.27),
            ("Walnut Branch", "3 Walnut Blvd", "Walnut Creek", "CA", "Contra Costa",
             "SF", None, "06013", 2025, 111, 37.90, -122.06),
        ], columns=["branch_name", "address", "city", "state", "county",
                    "msa_name", "deposits", "stcntybr", "year", "cert", "lat",
                    "lng"]), []

    def test_branch_list_share_carries_percent_and_absent_is_dash(self):
        import ui.branch_analytics as ba
        shown = []
        fake_st = types.SimpleNamespace(
            markdown=lambda *a, **k: None, caption=lambda *a, **k: None,
            dataframe=lambda df, **k: shown.append(df))
        with mock.patch.object(ba, "st", fake_st), \
                mock.patch.object(ba, "_roster", lambda cert: self._roster()), \
                mock.patch.object(ba, "get_fdic_cert", lambda t: 111), \
                mock.patch.object(ba, "get_bank_info", lambda t: None), \
                mock.patch.object(ba, "table_export", lambda *a, **k: None):
            ba.render_branch_list("TST")
        self.assertEqual(list(shown[0]["% of bank"]), ["75.00%", "25.00%", "—"])

    def test_branch_competitors_is_a_house_table_with_percent(self):
        import ui.branch_analytics as ba
        tables = []
        county = pd.DataFrame([
            {"owner_key": "TST", "cert": 111, "bank_name": "Test Bank",
             "ticker": "TST", "n_branches": 2, "total_deposits": 400_000.0},
            {"owner_key": "c222", "cert": 222, "bank_name": "Other Bank",
             "ticker": None, "n_branches": 1, "total_deposits": 100_000.0}])
        fake_st = types.SimpleNamespace(markdown=lambda *a, **k: None,
                                        caption=lambda *a, **k: None)
        with mock.patch.object(ba, "st", fake_st), \
                mock.patch.object(ba, "_roster", lambda cert: self._roster()), \
                mock.patch.object(ba, "_footprint_participants",
                                  lambda c, y: {}),                 mock.patch.object(ba, "_county_banks", lambda f, y: county), \
                mock.patch.object(ba, "get_fdic_cert", lambda t: 111), \
                mock.patch.object(ba, "get_bank_info", lambda t: {"name": "Test"}), \
                mock.patch.object(ba, "table_export", lambda *a, **k: None), \
                mock.patch.object(ba, "ksk_table",
                                  lambda df, **k: tables.append((df, k))):
            ba.render_branch_competitors("TST")
        df, kw = tables[0]
        self.assertEqual(list(df["% of footprint deposits"]), ["80.00%", "20.00%"])
        self.assertIn('href="?s=Company&bank=TST"', df["Ticker"].iloc[0])
        self.assertEqual(df["Ticker"].iloc[1], "")               # private bank
        self.assertEqual(kw.get("html_cols"), ("Ticker",))


if __name__ == "__main__":
    unittest.main()
