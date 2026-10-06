"""Pins for three verified follow-ups to the 2026-10-06 Earnings review.

  1. PNFP Q4-25 conflict row: the 2.02 8-K under merged CIK 2082866 is
     legacy Synovus ($1.22 GAAP, NI $171.1M, revenue $629.7M). #308 made the
     EPS cell "n/a (conflict)", but the expander / Last-Reported exhibit
     still rendered that filing's metrics as PNFP's. Now withheld.
  2. Revenue surprise basis: FMP's revenueActual is GAAP in a notable-items
     quarter (ZION 2Q26: NII $677M + noninterest income $460M = $1,137M incl.
     $252M of Visa/SBIC gains, vs an ex-items consensus — "+29.6%"; the
     release's "Adjusted taxable-equivalent revenue (non-GAAP)" is $878M).
     Scored only when the feed's figure is proven to be the release's
     adjusted (or, with no adjusted stated, reported) revenue within 0.5%.
     Figures below are each bank's real 2Q26 EX-99.1 rows and FMP's real
     2Q26 revenueActual / revenueEstimated.
  3. data/ir_provider skipped HBAN's 2Q26 release (0000049196-26-000060)
     once four later gated 8-Ks were filed (the candidate cap counted them
     and stopped before the 2.02).

Run: python -m unittest tests.test_earnings_followups_2026_10_06
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.earnings_results import revenue_basis, score_rev  # noqa: E402
from data.release_metrics import revenue_basis_facts  # noqa: E402


def _tr(cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _table(units, span, dates, rows):
    return ("<table>" + _tr([units, span] + [""] * (len(dates) - 1))
            + _tr([""] + dates)
            + "".join(_tr(r) for r in rows) + "</table>")


# ── Real 2Q26 EX-99.1 table rows (labels verbatim; values as printed) ───────
Q5 = ["June 30, 2026", "March 31, 2026", "December 31, 2025",
      "September 30, 2025", "June 30, 2025"]
ZION_2Q26 = (
    _table("(In millions)", "Three Months Ended", Q5, [
        ["Net interest income", "677", "662", "683", "672", "648"],
        ["Total noninterest income", "460", "187", "208", "189", "190"]])
    + _table("(Dollar amounts in millions)", "Three Months Ended", Q5, [
        ["Adjusted taxable-equivalent revenue (non-GAAP)",
         "878", "859", "879", "872", "837"],
        ["Pre-provision net revenue (PPNR) (non-GAAP)",
         "597", "298", "356", "345", "324"]])
    # The H1 twin table: unique period columns, so only the YTD-header skip
    # keeps its $1,737M from poisoning the quarter's $878M.
    + _table("(Dollar amounts in millions)", "Six Months Ended",
             ["June 30, 2026", "June 30, 2025"], [
        ["Adjusted taxable-equivalent revenue (non-GAAP)", "1,737", "1,637"]]))
ONB_2Q26 = _table("(dollars in thousands)", "Three Months Ended", Q5, [
    ["Net interest income", "578,988", "572,573", "580,832", "574,609", "514,790"],
    ["Total noninterest income", "153,564", "122,346", "109,759", "130,461",
     "132,517"],
    ["Total revenue (FTE) 2", "740,062", "702,768", "698,604", "713,045",
     "654,370"],
    ["Total adjusted revenue", "726,856", "702,693", "714,409", "713,038",
     "633,410"]])
WTFC_2Q26 = _table("(Dollars in thousands)", "Three Months Ended", Q5, [
    ["Net interest income", "597,366", "579,024", "583,874", "567,010", "546,694"],
    ["Net revenue (2)", "738,635", "713,166", "714,264", "697,837", "670,783"],
    ["Total non-interest income", "141,269", "134,142", "130,390", "130,827",
     "124,089"]])
MTB_2Q26 = _table("(Dollars in millions)", "Three Months Ended",
                  ["June 30, 2026", "March 31, 2026", "June 30, 2025"], [
    ["Net interest income", "1,792", "1,752", "1,713"],
    ["Noninterest income", "740", "689", "683"]])
# HBAN's tables carry split ordinal headers the period parser cannot prove,
# and it states an adjusted noninterest income: never confirmable.
HBAN_2Q26 = ("<p>Total revenue - FTE (1) $ 2,857 $ 2,592</p>"
             "<p>Total adjusted noninterest income (Non-GAAP) 785 682</p>")


def _rel(facts):
    return {"metrics": {}, "rev_basis": facts}


class TestRevenueBasisFacts(unittest.TestCase):
    def test_zion_adjusted_878_not_h1_and_reported_sum(self):
        f = revenue_basis_facts(ZION_2Q26, "2026-06-30")
        self.assertEqual(f["adjusted"], 878_000_000.0)
        self.assertEqual(f["reported"], [1_137_000_000.0])   # 677 + 460
        self.assertTrue(f["adj_stated"])

    def test_onb_adjusted_and_fte(self):
        f = revenue_basis_facts(ONB_2Q26, "2026-06-30")
        self.assertEqual(f["adjusted"], 726_856_000.0)
        self.assertIn(740_062_000.0, f["reported"])           # FTE total

    def test_wtfc_no_adjusted_stated(self):
        f = revenue_basis_facts(WTFC_2Q26, "2026-06-30")
        self.assertIsNone(f["adjusted"])
        self.assertFalse(f["adj_stated"])
        self.assertIn(738_635_000.0, f["reported"])

    def test_hban_adjusted_noninterest_income_marks_stated(self):
        f = revenue_basis_facts(HBAN_2Q26, "2026-06-30")
        self.assertEqual(f["reported"], [])
        self.assertTrue(f["adj_stated"])

    def test_adjusted_ppnr_is_not_adjusted_revenue(self):
        html = "<p>adjusted pre-provision net revenue of $332 million</p>"
        self.assertFalse(revenue_basis_facts(html, "2026-06-30")["adj_stated"])

    def test_no_qend_no_figures(self):
        self.assertEqual(revenue_basis_facts(ONB_2Q26, None)["reported"], [])


class TestRevenueSurpriseScoring(unittest.TestCase):
    def _score(self, act, est, html):
        row = {"rev_act": act, "rev_est": est,
               "rel": _rel(revenue_basis_facts(html, "2026-06-30"))}
        score_rev(row)
        return row

    def test_zion_gaap_feed_actual_never_scored(self):
        # The shipped "+29.6%": $1,137M (incl. Visa gains) vs ex-items est.
        row = self._score(1_137_000_000, 875_922_300, ZION_2Q26)
        self.assertEqual(row["rev_basis"], "unconfirmed")
        self.assertIsNone(row["rev_surprise"])

    def test_zion_adjusted_feed_actual_scored(self):
        row = self._score(878_000_000, 875_922_300, ZION_2Q26)
        self.assertEqual(row["rev_basis"], "adjusted")
        self.assertAlmostEqual(row["rev_surprise"], 0.2372, places=3)

    def test_onb_confirmed_adjusted(self):
        row = self._score(726_856_000, 716_252_700, ONB_2Q26)
        self.assertEqual(row["rev_basis"], "adjusted")
        self.assertAlmostEqual(row["rev_surprise"], 1.4804, places=3)

    def test_onb_reported_match_is_not_enough_when_adjusted_stated(self):
        row = self._score(740_062_000, 716_252_700, ONB_2Q26)   # FTE total
        self.assertEqual(row["rev_basis"], "unconfirmed")
        self.assertIsNone(row["rev_surprise"])

    def test_wtfc_confirmed_reported(self):
        row = self._score(738_635_000, 735_362_500, WTFC_2Q26)
        self.assertEqual(row["rev_basis"], "reported")
        self.assertAlmostEqual(row["rev_surprise"], 0.4450, places=3)

    def test_mtb_confirmed_reported_via_nii_plus_fees(self):
        row = self._score(2_532_000_000, 2_462_610_000, MTB_2Q26)
        self.assertEqual(row["rev_basis"], "reported")
        self.assertAlmostEqual(row["rev_surprise"], 2.8178, places=3)

    def test_hban_unconfirmable(self):
        row = self._score(2_857_000_000, 2_839_704_000, HBAN_2Q26)
        self.assertEqual(row["rev_basis"], "unconfirmed")
        self.assertIsNone(row["rev_surprise"])

    def test_half_percent_tolerance(self):
        base = {"rev_est": 1.0, "rel": _rel(
            {"reported": [], "adjusted": 100.0, "adj_stated": True})}
        self.assertEqual(revenue_basis({**base, "rev_act": 100.5}), "adjusted")
        self.assertEqual(revenue_basis({**base, "rev_act": 100.6}),
                         "unconfirmed")

    def test_no_release_pre_v23_or_release_fill_unconfirmed(self):
        self.assertIsNone(revenue_basis({"rev_act": None}))
        self.assertEqual(revenue_basis({"rev_act": 1.0, "rel": None}),
                         "unconfirmed")
        self.assertEqual(revenue_basis({"rev_act": 1.0, "rel": {"metrics": {}}}),
                         "unconfirmed")
        self.assertEqual(revenue_basis(
            {"rev_act": 1.0, "rev_act_src": "release",
             "rel": _rel({"reported": [1.0], "adjusted": None,
                          "adj_stated": False})}), "unconfirmed")

    def test_builder_no_longer_scores_unconfirmed_revenue(self):
        # Before: build_results_rows scored surprise_pct(act, est) outright.
        from datetime import date
        from data.earnings_results import build_results_rows
        fmp = [{"symbol": "ZION", "date": "2026-07-20", "time": "amc",
                "epsActual": 3.05, "epsEstimated": 1.57,
                "revenueActual": 1_137_000_000,
                "revenueEstimated": 875_922_300}]
        rows = build_results_rows(fmp, {"ZION"}, {}, date(2026, 7, 21))
        self.assertIsNone(rows[0]["rev_surprise"])
        self.assertIsNone(rows[0]["rev_basis"])

    def test_fill_scores_revenue_from_release_facts(self):
        from data.earnings_results import _fill_release_metrics
        row = {"ticker": "ONB", "date": "2026-07-22", "eps_act": 0.65,
               "eps_est": 0.626, "rev_act": 726_856_000,
               "rev_est": 716_252_700}
        rm = {"metrics": {"eps_adj": 0.65, "eps_diluted": 0.65},
              "eps_adj_stated": True, "filed_date": "2026-07-22",
              "qend": "2026-06-30", "url": "u",
              "rev_basis": revenue_basis_facts(ONB_2Q26, "2026-06-30")}
        with mock.patch("data.release_metrics.release_metrics", return_value=rm), \
                mock.patch("data.bank_mapping.get_cik", return_value=707179):
            _fill_release_metrics([row], max_workers=1)
        self.assertEqual(row["rev_basis"], "adjusted")
        self.assertAlmostEqual(row["rev_surprise"], 1.4804, places=3)

    def test_release_metrics_stores_rev_basis(self):
        import data.release_metrics as rm_mod
        src = Path(rm_mod.__file__).read_text(encoding="utf-8")
        self.assertIn('"rev_basis": revenue_basis_facts(rel["html"], qend)', src)


class TestRevenueBasisRendered(unittest.TestCase):
    def test_board_cell_flags_unconfirmed(self):
        from ui.earnings import _results_tr
        base = {"ticker": "ZION", "date": "2026-07-20", "eps_est": 1.57,
                "rev_act": 1_137_000_000, "rev_est": 875_922_300}
        html = _results_tr({**base, "rev_surprise": None,
                            "rev_basis": "unconfirmed"}, 14)
        self.assertIn("n/a (basis?)", html)
        self.assertIn("adjusted revenue", html)          # tooltip names the rule
        self.assertNotIn("+29.", html)
        ok = _results_tr({**base, "ticker": "ONB", "rev_act": 726_856_000,
                          "rev_est": 716_252_700, "rev_surprise": 1.48,
                          "rev_basis": "adjusted"}, 14)
        self.assertIn("+1.5%", ok)

    def test_exhibit_revenue_row_only_takes_a_reported_feed_figure(self):
        from ui.earnings import _rel_exhibit_rows
        rel = {"qend": "2026-06-30", "metrics": {}, "capital": {}}
        rows = {r["key"]: r for r in _rel_exhibit_rows(
            {"ticker": "ONB", "rev_act": 726_856_000, "rev_basis": "adjusted",
             "rel": rel})}
        self.assertIsNone(rows["total_revenue"]["cur"])   # adjusted ≠ reported row
        rows = {r["key"]: r for r in _rel_exhibit_rows(
            {"ticker": "WTFC", "rev_act": 738_635_000, "rev_basis": "reported",
             "rel": rel})}
        self.assertEqual(rows["total_revenue"]["cur"], 738_635_000)


# ── 1. PNFP conflict: the filing's figures never render ─────────────────────
PNFP_SYNOVUS = {"eps_diluted": 1.22, "total_revenue": 629_671_000.0,
                "nim": 3.41, "efficiency": 52.6, "roa": 1.15}


class TestConflictReleaseWithheld(unittest.TestCase):
    def _fill(self, row):
        from data.earnings_results import _fill_release_metrics
        rm = {"metrics": dict(PNFP_SYNOVUS), "eps_adj_stated": True,
              "capital": {"cet1_ratio": 10.9}, "prior_metrics": {"nim": 3.38},
              "prior_qend": "2025-09-30", "filed_date": row["date"],
              "qend": "2025-12-31", "url": "https://sec/pnfp-8k",
              "rev_basis": {"reported": [629_671_000.0], "adjusted": None,
                            "adj_stated": False}}
        with mock.patch("data.release_metrics.release_metrics", return_value=rm), \
                mock.patch("data.bank_mapping.get_cik", return_value=2082866):
            _fill_release_metrics([row], max_workers=1)
        return row

    def test_data_layer_strips_the_filings_figures(self):
        row = self._fill({"ticker": "PNFP", "date": "2026-01-21",
                          "eps_act": 2.24, "eps_est": 2.26, "rev_act": None,
                          "rev_est": 1_220_000_000})
        self.assertTrue(row["eps_conflict"])
        self.assertEqual(row["rel"], {"qend": "2025-12-31",
                                      "url": "https://sec/pnfp-8k",
                                      "withheld": "conflict"})
        self.assertIsNone(row["rev_act"])            # never Synovus' $629.7M
        self.assertNotIn("rev_act_src", row)
        self.assertIsNone(row["rev_surprise"])

    def test_expander_shows_flag_and_link_not_figures(self):
        from ui.earnings import _results_tr
        row = self._fill({"ticker": "PNFP", "date": "2026-01-21",
                          "eps_act": 2.24, "eps_est": 2.26, "rev_act": None,
                          "rev_est": None})
        html = _results_tr(row, 14)
        self.assertIn("release figures withheld", html)
        self.assertIn("the filing's figures conflict with the feed", html)
        self.assertIn('href="https://sec/pnfp-8k"', html)
        for leaked in ("1.22", "629", "3.41", "52.6", "10.9"):
            self.assertNotIn(leaked, html, leaked)

    def test_old_shape_conflict_row_still_withheld_in_ui(self):
        from ui.earnings import _rel_detail_tr
        row = {"ticker": "PNFP", "eps_conflict": True,
               "rel": {"qend": "2025-12-31", "metrics": dict(PNFP_SYNOVUS),
                       "capital": {}, "url": "https://sec/pnfp-8k"}}
        html = _rel_detail_tr(row, 14)
        self.assertIn("release figures withheld", html)
        self.assertNotIn("1.22", html)

    def test_last_reported_panel_withholds(self):
        import ui.earnings as ue

        class _Rec:
            def __init__(self):
                self.md = []

            def markdown(self, body, *a, **k):
                self.md.append(body)

            def __getattr__(self, name):
                return lambda *a, **k: None

        row = self._fill({"ticker": "PNFP", "date": "2026-01-21",
                          "eps_act": 2.24, "eps_est": 2.26, "rev_act": None,
                          "rev_est": None})
        rec = _Rec()
        with mock.patch.object(ue, "st", rec), \
                mock.patch("data.earnings_results.results_board",
                           return_value=[row]):
            ue._render_reported_panel("PNFP")
        html = " ".join(rec.md)
        self.assertIn("release figures withheld", html)
        self.assertIn("https://sec/pnfp-8k", html)
        self.assertNotIn("relx", html)                # no exhibit table
        for leaked in ("1.22", "629", "3.41", "52.6"):
            self.assertNotIn(leaked, html, leaked)


# ── 3. HBAN: the 2.02 behind four gated 8-Ks ───────────────────────────────
def _subs(forms, items, dates, accs):
    return {"filings": {"recent": {
        "form": forms, "items": items, "filingDate": dates,
        "accessionNumber": accs, "primaryDocument": ["d.htm"] * len(forms)}}}


# HBAN's real submissions `recent` 8-K block, newest first (2026-10-06).
HBAN_SUBS = _subs(
    ["8-K", "8-K", "8-K", "8-K", "8-K", "8-K"],
    ["5.02", "8.01,9.01", "7.01,9.01", "5.02,9.01", "8.01,9.01", "2.02,9.01"],
    ["2026-09-24", "2026-09-18", "2026-09-16", "2026-09-10", "2026-07-23",
     "2026-07-23"],
    ["0000049196-26-000081", "0000049196-26-000079", "0000049196-26-000077",
     "0000049196-26-000073", "0000049196-26-000062", "0000049196-26-000060"])


class TestEarningsCandidatesReachThe202(unittest.TestCase):
    def test_hban_202_still_reached(self):
        from data.ir_provider import _earnings_8k_candidates
        cands = _earnings_8k_candidates(HBAN_SUBS)
        self.assertEqual(cands[-1]["accession"], "000004919626000060")
        self.assertFalse(cands[-1]["gated"])
        self.assertEqual(sum(c["gated"] for c in cands), 4)

    def test_gated_cap_still_bounds_fetches(self):
        from data.ir_provider import _earnings_8k_candidates
        subs = _subs(["8-K"] * 7, ["9.01"] * 6 + ["2.02"],
                     [f"2026-09-{d:02d}" for d in range(20, 13, -1)],
                     [f"a-{i}" for i in range(7, 0, -1)])
        cands = _earnings_8k_candidates(subs)
        self.assertEqual([c["gated"] for c in cands],
                         [True, True, True, True, False])

    def test_latest_release_selects_hban_202(self):
        import json
        import data.ir_provider as ip
        heads = {  # real opening text of each exhibit
            "000004919626000079": "Exhibit 99.1 September 18, 2026 Analyst: "
                                  "Eric Wasserstrom",
            "000004919626000077": "hbanbarxpresentation_vf Abundant Green",
            "000004919626000062": "Exhibit 99.1 July 23, 2026 Analyst: Eric "
                                  "Wasserstrom",
            "000004919626000060": "Huntington Bancshares Incorporated Reports "
                                  "2026 Second-Quarter Earnings",
        }

        def fetch(cik, hit):
            h = heads.get(hit["accession"])
            return None if h is None else {
                "url": hit["accession"], "html": f"<p>{h}</p>",
                "filed_date": hit["filed_date"],
                "accession": hit["accession"], "form": hit["form"]}

        with mock.patch.object(ip, "_get",
                               return_value=json.dumps(HBAN_SUBS).encode()), \
                mock.patch.object(ip, "_fetch_ex99", side_effect=fetch):
            rel = ip.latest_earnings_release(49196)
        self.assertEqual(rel["accession"], "000004919626000060")


if __name__ == "__main__":
    unittest.main()
