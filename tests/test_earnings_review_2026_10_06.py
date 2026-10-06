"""Pins for the 2026-10-06 Earnings ground-truth review (EX-99.1 releases).

Every case uses the real figures the review found:
  P0-1  Results board / Last Reported scored GAAP-like FMP actuals against
        ADJUSTED consensus (JPM 2Q26 7.59 vs 5.59 = "+35.8%", release GAAP
        7.70 / ex-significant-items 6.14; HBAN 0.33 vs 0.3586 "Miss" while
        adjusted 0.39 beat; ZION; NTRS). A surprise now exists only on a
        CONFIRMED basis; adjusted-EPS extraction widened to the real forms.
  P1-1  CMTV 2Q26 revenue $16.04B (×1000) with no estimate — no upper bound.
  P1-2  Consensus-vs-Actual Q4 EPS = FY − 9M (ONB 0.56 vs reported 0.55).
  P2-2  "Last Qtr Avg Surprise" averaged the UPCOMING quarter's None.
  P2-3  PNFP Q4-25: wrong-entity release (legacy Synovus $1.22) replaced a
        correct FMP actual ($2.24 vs $2.26 est).
  P2-4  Zero consensus scored "inline".
  P2-5  EPS card cited XBRL for a release-anchored TTM.
  P2-6  Diluted EPS silently fell back to basic.

Run: python -m unittest tests.test_earnings_review_2026_10_06
"""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.earnings_results import build_results_rows, eps_basis, score_eps  # noqa: E402
from data.release_metrics import (adjusted_eps_stated,  # noqa: E402
                                  extract_release_metrics, extract_table_metrics)


# ── Real EX-99.1 text (2Q26 unless noted), verbatim ─────────────────────────
JPM_2Q26 = ("<p>JPMORGANCHASE REPORTS SECOND-QUARTER 2026 NET INCOME OF $21.2 "
            "BILLION ( $7.70 PER SHARE), NET INCOME EXCLUDING SIGNIFICANT ITEMS "
            "OF $16.9 BILLION ($6.14 PER SHARE) SECOND-QUARTER 2026 RESULTS 1 "
            "ROE 24% ROTCE 2 29%</p>")
HBAN_2Q26 = ("<p>◦ Excluding the after-tax impact of Notable Items as detailed "
             "in Table 2, adjusted EPS 1 was $0.39, higher by $0.02 from the "
             "prior quarter. ◦ The prior year quarter included Notable Items "
             "that decreased pre-tax earnings by $3 million. Excluding the "
             "impact from these items, adjusted EPS 1 was higher by $0.01 from "
             "the year ago quarter.</p>")
ONB_2Q26 = ('<p>Earnings per diluted common share ("EPS") of $0.65; record '
            'adjusted EPS 1 of $0.65</p>')
ZION_2Q26 = ("<p>Zions Bancorporation, N.A. reports 2Q26 Net Earnings of $452 "
             "million, diluted EPS of $3.05 (or $1.74 excluding notable items) "
             "compared with 2Q25 Net Earnings of $243 million, diluted EPS of "
             "$1.63 (or $1.58 excluding notable items), and 1Q26 Net Earnings "
             "of $232 million, diluted EPS of $1.56</p>")
NTRS_2Q26 = ("<p>Excluding notable items in the period, earnings per share "
             "increased 40%. Revenue increased 13% versus last year.</p>")
WAL_2Q26 = ("<p>“Earnings per share of $2.36, rose 6.3% from an adjusted EPS 2 "
            "of $2.22 in the prior quarter. Our results were driven by "
            "quarterly HFI loan growth.</p>")
AUB_2Q26 = ("<p>adjusted diluted operating earnings per common share (1) of "
            "$0.94 for the second quarter of 2026.</p>")
# A GAAP-only release (California First National Bancorp, Oct-2017 EX-99.1).
GAAP_ONLY = ("<p>Diluted earnings per share for the first quarter of fiscal "
             "2018 of $0.22 are up 17.1% from $0.19 per share for the prior "
             "year first quarter.</p>")


def _onb_table():
    """ONB 2Q26 non-GAAP table shape: split '$' cells, quarter + YTD block."""
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return ("<table>"
            + tr(["(in thousands, except per share data)", "Three Months Ended", "", ""])
            + tr(["", "June 30, 2026", "March 31, 2026", "December 31, 2025"])
            + tr(["EPS, diluted", "$", "0.65", "$", "0.59", "$", "0.55"])
            + tr(["Adjusted EPS, diluted", "$", "0.65", "$", "0.61", "$", "0.62"])
            + "</table>")


class TestAdjustedEpsExtraction(unittest.TestCase):
    """P0-1: eps_adj only matched 'Adjusted diluted earnings per share'."""

    def test_jpm_excluding_significant_items(self):
        m = extract_release_metrics(JPM_2Q26)
        self.assertEqual(m["eps_adj"], 6.14)
        self.assertTrue(adjusted_eps_stated(JPM_2Q26))

    def test_hban_adjusted_eps_footnote_form(self):
        # "$0.02 from the prior quarter" is a CHANGE, not the level.
        self.assertEqual(extract_release_metrics(HBAN_2Q26)["eps_adj"], 0.39)

    def test_onb_prose_and_table(self):
        self.assertEqual(extract_release_metrics(ONB_2Q26)["eps_adj"], 0.65)
        t = extract_table_metrics(_onb_table(), "2026-06-30")
        self.assertEqual(t["eps_adj"], 0.65)
        self.assertEqual(extract_table_metrics(_onb_table(), "2026-03-31")["eps_adj"],
                         0.61)

    def test_zion_current_not_prior_year(self):
        # 2Q25's "(or $1.58 …)" sits after "compared with" → excluded.
        self.assertEqual(extract_release_metrics(ZION_2Q26)["eps_adj"], 1.74)

    def test_aub_operating_form(self):
        self.assertEqual(extract_release_metrics(AUB_2Q26)["eps_adj"], 0.94)

    def test_wal_prior_quarter_adjusted_never_current(self):
        m = extract_release_metrics(WAL_2Q26)
        self.assertIsNone(m["eps_adj"])
        self.assertTrue(adjusted_eps_stated(WAL_2Q26))   # → basis unconfirmed

    def test_ntrs_mentions_ex_items_eps_without_a_figure(self):
        self.assertIsNone(extract_release_metrics(NTRS_2Q26)["eps_adj"])
        self.assertTrue(adjusted_eps_stated(NTRS_2Q26))

    def test_gaap_only_release(self):
        self.assertFalse(adjusted_eps_stated(GAAP_ONLY))
        self.assertIsNone(extract_release_metrics(GAAP_ONLY)["eps_adj"])

    def test_basic_row_never_adjusted_diluted(self):
        def tr(cells):
            return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        html = ("<table>" + tr(["($ per share)", "June 30, 2026", "March 31, 2026"])
                + tr(["Adjusted earnings per share - basic", "$0.81", "$0.95"])
                + "</table>")
        self.assertIsNone(extract_table_metrics(html, "2026-06-30")["eps_adj"])


def _row(eps_act, eps_est, metrics=None, stated=None, src=None, rel=True):
    r = {"ticker": "T", "date": "2026-07-14", "eps_act": eps_act,
         "eps_est": eps_est}
    if src:
        r["eps_act_src"] = src
    r["rel"] = ({"metrics": metrics or {}, "eps_adj_stated": stated}
                if rel else None)
    return r


class TestEpsBasisScoring(unittest.TestCase):
    """P0-1: a surprise only on a confirmed GAAP-vs-adjusted basis."""

    def test_jpm_gaap_like_feed_actual_never_scored(self):
        r = _row(7.59, 5.59, {"eps_adj": 6.14, "eps_diluted": 7.70}, True)
        score_eps(r)
        self.assertIsNone(r["eps_surprise"])          # was +35.8%
        self.assertEqual(r["eps_basis"], "unconfirmed")

    def test_hban_gaap_actual_never_a_miss(self):
        r = _row(0.33, 0.3586, {"eps_adj": 0.39, "eps_diluted": 0.33}, True)
        score_eps(r)
        self.assertIsNone(r["eps_surprise"])          # was -8.0% "Miss"

    def test_zion_and_ntrs(self):
        z = _row(3.05, 1.57, {"eps_adj": 1.74}, True)
        n = _row(4.23, 2.71, {}, True)                # stated, no figure
        score_eps(z); score_eps(n)
        self.assertIsNone(z["eps_surprise"])          # was +94.3%
        self.assertIsNone(n["eps_surprise"])          # was +56%
        self.assertEqual(n["eps_basis"], "unconfirmed")

    def test_onb_confirmed_adjusted_is_scored(self):
        r = _row(0.65, 0.626, {"eps_adj": 0.65, "eps_diluted": 0.65}, True)
        score_eps(r)
        self.assertEqual(r["eps_basis"], "adjusted")
        self.assertAlmostEqual(r["eps_surprise"], (0.65 - 0.626) / 0.626 * 100)

    def test_gaap_only_release_scores_on_gaap(self):
        # HWC 2Q26: FMP 1.55, est 1.55, release GAAP 1.55, no adjusted EPS.
        r = _row(1.55, 1.55, {"eps_diluted": 1.55}, False)
        score_eps(r)
        self.assertEqual(r["eps_basis"], "gaap_only")
        self.assertEqual(r["eps_surprise"], 0.0)
        # ...but a feed figure the GAAP-only release doesn't state is not.
        r2 = _row(1.62, 1.55, {"eps_diluted": 1.55}, False)
        self.assertEqual(eps_basis(r2), "unconfirmed")

    def test_no_release_or_pre_v22_extraction_is_unconfirmed(self):
        self.assertEqual(eps_basis(_row(1.0, 0.9, rel=False)), "unconfirmed")
        self.assertEqual(eps_basis(_row(1.0, 0.9, {"eps_diluted": 1.0}, None)),
                         "unconfirmed")

    def test_release_fill_sources(self):
        adj = _row(1.14, 1.14, {"eps_adj": 1.14}, True, src="release, adj.")
        gaap = _row(1.13, 1.10, {"eps_diluted": 1.13}, False, src="release, GAAP")
        score_eps(adj); score_eps(gaap)
        self.assertEqual(adj["eps_surprise"], 0.0)
        self.assertIsNone(gaap["eps_surprise"])       # † never scored
        self.assertIsNone(eps_basis({"eps_act": None}))

    def test_fill_scores_every_row_even_without_a_release(self):
        from data.earnings_results import _fill_release_metrics
        row = {"ticker": "T", "date": "2026-07-14", "eps_act": 7.59,
               "eps_est": 5.59, "eps_surprise": 35.8}
        with mock.patch("data.release_metrics.release_metrics", return_value=None), \
                mock.patch("data.bank_mapping.get_cik", return_value=1):
            _fill_release_metrics([row], max_workers=1)
        self.assertIsNone(row["eps_surprise"])
        self.assertEqual(row["eps_basis"], "unconfirmed")

    def test_fill_carries_eps_adj_stated(self):
        from data.earnings_results import _fill_release_metrics
        row = {"ticker": "JPM", "date": "2026-07-14", "eps_act": 7.59,
               "eps_est": 5.59, "rev_act": 57.347e9}
        rm = {"metrics": {"eps_adj": 6.14, "eps_diluted": 7.70},
              "eps_adj_stated": True, "filed_date": "2026-07-14",
              "qend": "2026-06-30", "url": "u"}
        with mock.patch("data.release_metrics.release_metrics", return_value=rm), \
                mock.patch("data.bank_mapping.get_cik", return_value=19617):
            _fill_release_metrics([row], max_workers=1)
        self.assertIs(row["rel"]["eps_adj_stated"], True)
        self.assertEqual(row["eps_act"], 7.59)        # within GAAP gap — kept
        self.assertIsNone(row["eps_surprise"])


class TestWrongEntityReleaseGuard(unittest.TestCase):
    """P2-3: PNFP Q4-25 — the 8-K under merged CIK 2082866 is legacy Synovus
    ($1.22 diluted); FMP's $2.24 is Pinnacle's, est $2.26."""

    def _fill(self, row, metrics):
        from data.earnings_results import _fill_release_metrics
        rm = {"metrics": metrics, "eps_adj_stated": True,
              "filed_date": row["date"], "qend": "2025-12-31", "url": "u"}
        with mock.patch("data.release_metrics.release_metrics", return_value=rm), \
                mock.patch("data.bank_mapping.get_cik", return_value=2082866):
            _fill_release_metrics([row], max_workers=1)
        return row

    def test_pnfp_consensus_consistent_feed_is_not_swapped(self):
        row = self._fill({"ticker": "PNFP", "date": "2026-01-21",
                          "eps_act": 2.24, "eps_est": 2.26},
                         {"eps_diluted": 1.22, "total_revenue": 629_671_000.0})
        self.assertIsNone(row["eps_act"])             # never Synovus' 1.22
        self.assertTrue(row["eps_conflict"])
        self.assertNotIn("eps_act_src", row)
        self.assertIsNone(row["eps_surprise"])

    def test_npb_junk_feed_still_replaced(self):
        # NPB 2026-07-22: FMP $0.09 vs $0.677 est — FMP is the junk side.
        row = self._fill({"ticker": "NPB", "date": "2026-07-21",
                          "eps_act": 0.09, "eps_est": 0.677},
                         {"eps_diluted": 0.60})
        self.assertEqual(row["eps_act"], 0.60)
        self.assertEqual(row["eps_act_src"], "release, GAAP")
        self.assertNotIn("eps_conflict", row)

    def test_no_estimate_cannot_adjudicate(self):
        row = self._fill({"ticker": "T", "date": "2026-07-21",
                          "eps_act": 0.09, "eps_est": None},
                         {"eps_diluted": 0.60})
        self.assertIsNone(row["eps_act"])
        self.assertTrue(row["eps_conflict"])


class TestRevenueUpperBound(unittest.TestCase):
    """P1-1: CMTV 2Q26 revenueActual $16,042,610,000 (×1000), no estimate."""
    TODAY = date(2026, 7, 22)
    CMTV = {"symbol": "CMTV", "date": "2026-07-21", "epsActual": 0.84,
            "revenueActual": 16_042_610_000}

    def test_cmtv_x1000_revenue_blanked_vs_assets(self):
        rows = build_results_rows([self.CMTV], {"CMTV"}, {}, self.TODAY,
                                  assets_by_ticker={"CMTV": 1_234_804_000.0})
        self.assertIsNone(rows[0]["rev_act"])
        self.assertIsNone(rows[0]["rev_surprise"])
        # The ÷1000 figure ($16.04M on $1.23B = 1.3%) is a real quarter.
        ok = dict(self.CMTV, revenueActual=16_042_610)
        rows = build_results_rows([ok], {"CMTV"}, {}, self.TODAY,
                                  assets_by_ticker={"CMTV": 1_234_804_000.0})
        self.assertEqual(rows[0]["rev_act"], 16_042_610)

    def test_no_assets_and_no_estimate_renders_na(self):
        rows = build_results_rows([self.CMTV], {"CMTV"}, {}, self.TODAY)
        self.assertEqual(len(rows), 1)                # still a reported row
        self.assertIsNone(rows[0]["rev_act"])

    def test_estimate_present_skips_upper_bound(self):
        # MS 2Q26: $21.35B on its single-cert $391B bank-sub (5.5%) — the
        # 0.2-5x consensus guard owns rows with an estimate.
        fmp = [{"symbol": "MS", "date": "2026-07-15", "epsActual": 2.1,
                "revenueActual": 21_348_000_000,
                "revenueEstimated": 19_674_830_000}]
        rows = build_results_rows(fmp, {"MS"}, {}, self.TODAY,
                                  assets_by_ticker={"MS": 391_305_000_000.0})
        self.assertEqual(rows[0]["rev_act"], 21_348_000_000)

    def test_build_anchors_assets_by_resolver_cert(self):
        """CMTV has NO universe-snapshot cert; the lookup-time resolver gives
        6271 ($1.23B). The build must use the resolver (stale snapshot certs
        anchored CARV/UNB to $115M/$251M charters)."""
        import data.earnings_results as er
        from data import cache as _cache
        uni = {"CMTV": {"share_class": "common", "fdic_cert": None}}
        with mock.patch("data.fmp_client.get_earnings_calendar",
                        return_value=[self.CMTV]), \
                mock.patch("data.bank_universe.get_universe", return_value=uni), \
                mock.patch("data.bank_mapping.get_fdic_cert", return_value=6271), \
                mock.patch.object(_cache, "served_snapshot",
                                  return_value={"6271": 1_234_804_000.0}), \
                mock.patch("data.events.store.get_events_by_type", return_value=[]), \
                mock.patch.object(er, "_fill_price_reactions"), \
                mock.patch.object(er, "_fill_release_metrics"), \
                mock.patch.object(er, "_fill_fdic_history"), \
                mock.patch.object(er, "_fill_sec_history"), \
                mock.patch.object(er, "date") as d:
            d.today.return_value = self.TODAY
            d.fromisoformat = date.fromisoformat
            rows = er._build_results_board(30)
        self.assertEqual([r["ticker"] for r in rows], ["CMTV"])
        self.assertIsNone(rows[0]["rev_act"])

    def test_board_key_bumped(self):
        import data.earnings_results as er
        self.assertEqual(er._board_key(30), "earnings_results_board_v13:30")


def _facts(concepts):
    return {"facts": {"us-gaap": {
        c: {"units": {u: list(es)}} for c, (u, es) in concepts.items()}}}


def _e(start, end, val, form="10-Q", filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


class TestPeriodEps(unittest.TestCase):
    """P1-2 / P2-6 — data/sec_period.fundamentals_for_period."""

    def _run(self, facts, period):
        import data.sec_period as sp
        with mock.patch("data.sec_client.fetch_company_facts", return_value=facts):
            return sp.fundamentals_for_period(123, period)

    # ONB's real companyfacts (CIK 707179): 9M 1.23, Q3 0.46, FY 1.79.
    ONB = {"EarningsPerShareDiluted": ("USD/shares", [
        _e("2025-01-01", "2025-09-30", 1.23, filed="2025-10-29"),
        _e("2025-07-01", "2025-09-30", 0.46, filed="2025-10-29"),
        _e("2025-01-01", "2025-12-31", 1.79, form="10-K", filed="2026-02-19")]),
        "EarningsPerShareBasic": ("USD/shares", [
        _e("2025-01-01", "2025-09-30", 1.24, filed="2025-10-29"),
        _e("2025-07-01", "2025-09-30", 0.46, filed="2025-10-29"),
        _e("2025-01-01", "2025-12-31", 1.80, form="10-K", filed="2026-02-19")]),
        "NetIncomeLoss": ("USD", [
        _e("2025-01-01", "2025-09-30", 480e6, filed="2025-10-29"),
        _e("2025-01-01", "2025-12-31", 696e6, form="10-K", filed="2026-02-19")])}

    def test_onb_q4_eps_is_na_not_fy_minus_9m(self):
        a = self._run(_facts(self.ONB), "2025Q4")
        self.assertNotIn("eps", a)                    # was 0.56 (reported 0.55)
        self.assertEqual(a["net_income"], 216e6)      # flows still FY − 9M

    def test_jpm_q4_eps_is_na(self):
        jpm = {"EarningsPerShareDiluted": ("USD/shares", [
            _e("2025-01-01", "2025-09-30", 15.38, filed="2025-11-04"),
            _e("2025-01-01", "2025-12-31", 20.02, form="10-K", filed="2026-02-13")])}
        self.assertIsNone(self._run(_facts(jpm), "2025Q4"))   # was 4.64 vs 4.63

    def test_tagged_quarter_and_fy_still_served(self):
        self.assertEqual(self._run(_facts(self.ONB), "2025Q3")["eps"], 0.46)
        self.assertEqual(self._run(_facts(self.ONB), "2025")["eps"], 1.79)
        q4 = dict(self.ONB)
        q4["EarningsPerShareDiluted"] = ("USD/shares", list(self.ONB[
            "EarningsPerShareDiluted"][1]) + [
            _e("2025-10-01", "2025-12-31", 0.55, form="10-K", filed="2026-02-19")])
        a = self._run(_facts(q4), "2025Q4")
        self.assertEqual(a["eps"], 0.55)              # directly-tagged Q4
        self.assertNotIn("eps_basis", a)

    def test_basic_only_filer_is_labeled(self):
        # CIK 894671 tags only EarningsPerShareBasic (2025Q3 0.64).
        f = _facts({"EarningsPerShareBasic": ("USD/shares", [
            _e("2025-07-01", "2025-09-30", 0.64, filed="2025-11-10")])})
        a = self._run(f, "2025Q3")
        self.assertEqual(a["eps"], 0.64)
        self.assertEqual(a["eps_basis"], "basic")
        from data.consensus import compare_consensus_to_actual
        c = compare_consensus_to_actual(
            {"metrics": [{"key": "eps", "name": "EPS", "value": 0.60,
                          "unit": "$"}]}, a)
        self.assertTrue(c[0]["metric_name"].endswith("(basic)"))
        c2 = compare_consensus_to_actual(
            {"metrics": [{"key": "eps", "name": "EPS", "value": 0.60,
                          "unit": "$"}]}, {"eps": 0.64})
        self.assertNotIn("basic", c2[0]["metric_name"])


class TestZeroConsensus(unittest.TestCase):
    """P2-4: a zero consensus has no percent base — never 'inline'."""

    def test_zero_consensus_is_na(self):
        from data.consensus import compare_consensus_to_actual
        c = compare_consensus_to_actual(
            {"metrics": [{"key": "eps", "name": "EPS", "value": 0.0,
                          "unit": "$"}]}, {"eps": 0.05})[0]
        self.assertIsNone(c["delta_pct"])
        self.assertEqual(c["beat_miss"], "n/a")
        self.assertAlmostEqual(c["delta"], 0.05)


class TestLastQtrAvgSurprise(unittest.TestCase):
    """P2-2: earnings_history[0] is the UPCOMING quarter (surprise None)."""

    def test_reads_most_recent_reported_quarter(self):
        import ui.earnings as ue
        from data import cache as _cache
        est = {"JPM": {"earnings_history": [
                   {"date": "2026-10-13", "surprise_pct": None},
                   {"date": "2026-07-14", "surprise_pct": 35.8},
                   {"date": "2026-04-14", "surprise_pct": 8.6}]},
               "ONB": {"earnings_history": [
                   {"date": "2026-10-21", "surprise_pct": None},
                   {"date": "2026-07-22", "surprise_pct": 3.8}]},
               "NONE": {"earnings_history": [{"surprise_pct": None}]}}
        keys = []

        def served(key, ttl, build):
            keys.append(key)
            return build()
        with mock.patch.object(_cache, "served_snapshot", served), \
                mock.patch("data.estimates.fetch_all_estimates", return_value=est):
            v = ue._avg_eps_surprise_cached(("JPM", "ONB", "NONE"))
        self.assertAlmostEqual(v, (35.8 + 3.8) / 2)   # was None
        self.assertEqual(keys, ["earnings_avg_eps_surprise_v2"])


class TestEpsCardProvenance(unittest.TestCase):
    """P2-5: the EPS card cited XBRL for a release-anchored TTM."""

    def test_branches_on_eps_source(self):
        from ui.earnings import _eps_card_calc
        rel = _eps_card_calc("release_ttm", "$8.44", "JPM", None)
        otc = _eps_card_calc("release_ttm_otc", "$2.10", "FDVA", None)
        sec = _eps_card_calc("reconstructed", "$8.44", "JPM",
                             {"label": "10-Q 2026-06-30", "url": "https://sec/x"})
        self.assertNotIn("XBRL", rel["ref"])
        self.assertIn("earnings release", rel["source"])
        self.assertIn("releases", otc["source"])
        self.assertEqual(sec["ref"], "XBRL EarningsPerShareDiluted")
        self.assertEqual(sec["link"], "https://sec/x")


class TestUnscoredFlagRendered(unittest.TestCase):
    """P0-1 / P2-3 UI: an unscored surprise says why — never a bare '—'."""

    def test_board_cells(self):
        from ui.earnings import _results_tr
        base = {"ticker": "JPM", "date": "2026-07-14", "eps_est": 5.59}
        basis = _results_tr({**base, "eps_act": 7.59, "eps_surprise": None,
                             "eps_basis": "unconfirmed"}, 14)
        self.assertIn("n/a (basis?)", basis)
        self.assertIn("GAAP vs", basis)               # tooltip names the rule
        self.assertNotIn("+35.8%", basis)
        conflict = _results_tr({"ticker": "PNFP", "date": "2026-01-21",
                                "eps_est": 2.26, "eps_act": None,
                                "eps_conflict": True}, 14)
        self.assertIn("n/a (conflict)", conflict)
        scored = _results_tr({**base, "ticker": "ONB", "eps_act": 0.65,
                              "eps_est": 0.626, "eps_surprise": 3.83,
                              "eps_basis": "adjusted"}, 14)
        self.assertIn("+3.8%", scored)

    def test_last_reported_panel(self):
        import ui.earnings as ue

        class _Rec:
            def __init__(self):
                self.md = []

            def markdown(self, body, *a, **k):
                self.md.append(body)

            def __getattr__(self, name):
                return lambda *a, **k: None

        jpm = {"ticker": "JPM", "date": "2026-07-14", "when": "Before open",
               "eps_act": 7.59, "eps_est": 5.59, "eps_surprise": None,
               "eps_basis": "unconfirmed", "rev_act": 57.347e9,
               "rev_est": 50.72064e9, "rev_surprise": 13.06,
               "rel": {"qend": "2026-06-30",
                       "metrics": {"eps_adj": 6.14, "eps_diluted": 7.70},
                       "capital": {}, "prior_metrics": {}, "yoy_metrics": {},
                       "eps_adj_stated": True, "url": "u"}}
        pnfp = {"ticker": "PNFP", "date": "2026-01-21", "eps_act": None,
                "eps_est": 2.26, "eps_conflict": True, "eps_surprise": None}
        for row, want in ((jpm, "basis unconfirmed (GAAP vs adjusted)"),
                          (pnfp, "feed vs release conflict")):
            rec = _Rec()
            with mock.patch.object(ue, "st", rec), \
                    mock.patch("data.earnings_results.results_board",
                               return_value=[row]):
                ue._render_reported_panel(row["ticker"])
            html = " ".join(rec.md)
            self.assertIn(want, html)
            self.assertNotIn("+35.8%", html)
        self.assertIn("n/a vs &#36;2.26 est", html)        # PNFP EPS cell


if __name__ == "__main__":
    unittest.main()
