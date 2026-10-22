"""(2026-10-22) Stock splits declared after the balance sheet —
data/sec_facts_overlay.{split_entries,resolve_stock_split,overlay_stock_split},
data/sec_client._apply_stock_split, analysis/valuation._release_split_scale
+ the release scaling in _resolve_tbvps/_resolve_bvps/_resolve_eps,
analysis/release_eps.composite_ttm_eps(prior_scale), the release TBVPS
reader's same-period book anchor, and the card labels.

SFBS (ServisFirst): a 2-for-1 split declared 2026-07-20, effective
2026-08-21, after the 2026-06-30 balance sheet of its Q2 10-Q
(0001171843-26-005359). The filing's figures — 54,671,023 shares, TBV
$35.93, TTM EPS $5.87 — are pre-split while the $38 quote is post-split:
the card read P/TBV 1.06x (2.12x true) and, once the post-split Q3 release
(filed 2026-10-19: TBVPS $18.51, BVPS $18.63, diluted EPS $0.80) arrived,
the ±15% gate called the bank's own figure a conflict and served $35.93.
The 10-Q tags the split as a subsequent event —
StockholdersEquityNoteStockSplitConversionRatio1 = 2 dated 2026-07-20 on
SubsequentEventTypeAxis beside CommonStockSharesOutstanding 109,300,000 —
dimensioned, so companyfacts drops it; the overlay reads the instance.

Hand-computed expectations: 54,671,023 × 2 = 109,342,046; 5.87 ÷ 2 =
2.935; 1.14 ÷ 2 = 0.57; TBV 1,964,300,000 ÷ 109,342,046 = 17.9647.

Run: python -m unittest tests.test_stock_split
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import analysis.valuation as va  # noqa: E402
from analysis import release_eps  # noqa: E402
from data import sec_client  # noqa: E402
from data import sec_earnings_8k as se8k  # noqa: E402
from data import sec_facts_overlay as ov  # noqa: E402
from ui import bank_detail as bd  # noqa: E402

SUB = {"SubsequentEventTypeAxis": "SubsequentEventMember"}
BS_END = "2026-06-30"
DECL = "2026-07-20"
PRE_SHARES = 54_671_023.0
POST_SHARES = 109_300_000.0


def _sfbs_entries():
    return [
        {"concept": ov._SPLIT_RATIO, "members": dict(SUB), "end": DECL, "val": 2.0},
        {"concept": "CommonStockSharesIssued", "members": dict(SUB), "end": DECL,
         "val": POST_SHARES},
        {"concept": "CommonStockSharesOutstanding", "members": dict(SUB), "end": DECL,
         "val": POST_SHARES},
    ]


class TestResolveStockSplit(unittest.TestCase):
    def test_sfbs_subsequent_event_split(self):
        self.assertEqual(ov.resolve_stock_split(_sfbs_entries(), BS_END),
                         {"ratio": 2.0, "date": DECL, "post_shares": POST_SHARES})

    def test_ratio_on_or_before_the_balance_sheet_is_already_in_the_figures(self):
        ents = _sfbs_entries()
        for e in ents:
            e["end"] = BS_END
        self.assertIsNone(ov.resolve_stock_split(ents, BS_END))
        for e in ents:
            e["end"] = "2026-05-01"
        self.assertIsNone(ov.resolve_stock_split(ents, BS_END))

    def test_conflicting_ratios_on_one_date_never_guess(self):
        ents = _sfbs_entries() + [
            {"concept": ov._SPLIT_RATIO, "members": {}, "end": DECL, "val": 3.0}]
        self.assertIsNone(ov.resolve_stock_split(ents, BS_END))

    def test_unit_ratio_and_no_ratio(self):
        ents = [{"concept": ov._SPLIT_RATIO, "members": {}, "end": DECL, "val": 1.0}]
        self.assertIsNone(ov.resolve_stock_split(ents, BS_END))
        self.assertIsNone(ov.resolve_stock_split([], BS_END))

    def test_post_shares_optional_and_reverse_split(self):
        ents = [{"concept": ov._SPLIT_RATIO, "members": {}, "end": DECL, "val": 0.1}]
        self.assertEqual(ov.resolve_stock_split(ents, BS_END),
                         {"ratio": 0.1, "date": DECL, "post_shares": None})

    def test_latest_declaration_decides(self):
        ents = _sfbs_entries() + [
            {"concept": ov._SPLIT_RATIO, "members": {}, "end": "2026-07-01", "val": 1.05}]
        self.assertEqual(ov.resolve_stock_split(ents, BS_END)["ratio"], 2.0)


class TestOverlayStockSplit(unittest.TestCase):
    SLIM = {"facts": {"StockholdersEquity": {"units": {"USD": [
        {"end": BS_END, "val": 1_970_000_000, "form": "10-Q",
         "filed": "2026-08-07", "fy": 2026, "fp": "Q2"}]}}}}
    META = {"accession": "000117184326005359", "doc": "sfbs20260630_10q.htm",
            "date": "2026-08-07", "form": "10-Q", "cik": 1430723}

    def test_record_attached_from_the_instance(self):
        with patch.object(sec_client, "_balance_sheet_date", return_value=BS_END), \
                patch("data.sec_filing_scraper.latest_filing", return_value=self.META), \
                patch.object(ov, "split_entries", return_value=_sfbs_entries()):
            out = ov.overlay_stock_split(1430723, self.SLIM)
        self.assertEqual(out["_stock_split"], {
            "ratio": 2.0, "date": DECL, "post_shares": POST_SHARES,
            "accession": "000117184326005359", "form": "10-Q"})
        self.assertNotIn("_stock_split", self.SLIM)      # never written back

    def test_no_split_is_the_same_blob(self):
        with patch.object(sec_client, "_balance_sheet_date", return_value=BS_END), \
                patch("data.sec_filing_scraper.latest_filing", return_value=self.META), \
                patch.object(ov, "split_entries", return_value=[]):
            self.assertIs(ov.overlay_stock_split(1430723, self.SLIM), self.SLIM)
        empty = {}
        self.assertIs(ov.overlay_stock_split(1430723, empty), empty)

    def test_hooked_into_sec_client_overlay(self):
        src = (REPO / "data/sec_client.py").read_text(encoding="utf-8")
        i = src.index("def _overlay(")
        body = src[i:src.index("def fetch_company_facts_ok")]
        self.assertIn("overlay_stock_split(cik, slim)", body)


class TestApplyStockSplit(unittest.TestCase):
    REC = {"ratio": 2.0, "date": DECL, "post_shares": POST_SHARES}

    def _result(self):
        return {"shares_outstanding": PRE_SHARES,
                "shares_for_market_cap": 54_672_510.0,
                "eps": 5.87, "eps_ttm": 5.87, "eps_latest_period": 1.47,
                "eps_basic": 5.90, "eps_basic_ttm": 5.90, "eps_basic_latest_period": 1.48,
                "dividends_per_share": 1.14, "dividends_per_share_ttm": 1.14,
                "dividends_per_share_latest_period": 0.335}

    def test_sfbs_counts_up_per_share_down(self):
        r = self._result()
        sec_client._apply_stock_split(r, {"_stock_split": self.REC}, BS_END,
                                      {"shares_outstanding": BS_END,
                                       "shares_for_market_cap": "2026-08-04"})
        self.assertEqual(r["shares_outstanding"], 109_342_046.0)
        self.assertEqual(r["shares_for_market_cap"], 109_345_020.0)
        self.assertAlmostEqual(r["eps"], 2.935)
        self.assertAlmostEqual(r["eps_ttm"], 2.935)
        self.assertAlmostEqual(r["eps_latest_period"], 0.735)
        self.assertAlmostEqual(r["eps_basic"], 2.95)
        self.assertAlmostEqual(r["dividends_per_share"], 0.57)
        self.assertAlmostEqual(r["dividends_per_share_ttm"], 0.57)
        self.assertAlmostEqual(r["dividends_per_share_latest_period"], 0.1675)
        self.assertEqual(r["stock_split"]["ratio"], 2.0)
        self.assertEqual(r["stock_split"]["date"], DECL)
        self.assertIn("shares_outstanding", r["stock_split"]["applied"])
        # Per-share book value off the restated count: 1,964.3M TCE.
        self.assertAlmostEqual(1_964_300_000 / r["shares_outstanding"], 17.9647, places=4)

    def test_a_count_already_post_split_is_left_alone(self):
        r = self._result()
        r["shares_for_market_cap"] = 109_310_000.0     # a post-split cover count
        sec_client._apply_stock_split(r, {"_stock_split": self.REC}, BS_END,
                                      {"shares_outstanding": BS_END,
                                       "shares_for_market_cap": "2026-09-01"})
        self.assertEqual(r["shares_outstanding"], 109_342_046.0)
        self.assertEqual(r["shares_for_market_cap"], 109_310_000.0)
        self.assertNotIn("shares_for_market_cap", r["stock_split"]["applied"])

    def test_without_post_shares_the_count_date_decides(self):
        rec = {"ratio": 2.0, "date": DECL, "post_shares": None}
        r = self._result()
        r["shares_for_market_cap"] = 109_310_000.0
        sec_client._apply_stock_split(r, {"_stock_split": rec}, BS_END,
                                      {"shares_outstanding": BS_END,
                                       "shares_for_market_cap": "2026-09-01"})
        self.assertEqual(r["shares_outstanding"], 109_342_046.0)
        self.assertEqual(r["shares_for_market_cap"], 109_310_000.0)   # dated after

    def test_no_record_or_split_inside_the_period_is_a_no_op(self):
        r = self._result()
        sec_client._apply_stock_split(r, {}, BS_END, {"shares_outstanding": BS_END})
        self.assertEqual(r, self._result())
        self.assertNotIn("stock_split", r)
        rec = {"ratio": 2.0, "date": "2026-06-15", "post_shares": POST_SHARES}
        sec_client._apply_stock_split(r, {"_stock_split": rec}, BS_END,
                                      {"shares_outstanding": BS_END})
        self.assertEqual(r, self._result())

    def test_restatement_precedes_the_per_share_block(self):
        src = (REPO / "data/sec_client.py").read_text(encoding="utf-8")
        i = src.index("def get_latest_fundamentals(")
        body = src[i:]
        self.assertLess(body.index("_apply_stock_split(result, facts"),
                        body.index('result["book_value_per_share"] = common_equity / shares'))
        # The restated eps/dividends keys are all set before the call.
        for k in sec_client._SPLIT_PER_SHARE_KEYS:
            if k in ("eps_ttm", "eps_basic_ttm"):
                continue
            self.assertLess(body.index(f'result["{k}"]') if f'result["{k}"]' in body
                            else body.index(f'"{k.split("_")[0]}"'),
                            body.index("_apply_stock_split(result, facts"), k)


class TestReleaseSplitScale(unittest.TestCase):
    SPLIT = {"ratio": 2.0, "date": DECL}

    def _scale(self, f8k):
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch("data.sec_earnings_8k._latest_earnings_8k", return_value=f8k):
            return va._release_split_scale("SFBS", self.SPLIT)

    def test_release_on_the_declaration_day_is_pre_split(self):
        # SFBS's Q2 release was furnished 2026-07-20, the day of the announcement.
        self.assertEqual(self._scale({"date": DECL, "accession": "x"}), 2.0)
        self.assertEqual(self._scale({"date": "2026-07-01", "accession": "x"}), 2.0)

    def test_release_after_the_declaration_is_post_split(self):
        self.assertEqual(self._scale({"date": "2026-10-19", "accession": "y"}), 1.0)

    def test_no_release_no_split_or_failure_is_unscaled(self):
        self.assertEqual(self._scale(None), 1.0)
        self.assertEqual(va._release_split_scale("SFBS", None), 1.0)
        with patch("data.bank_mapping.get_data_cik", side_effect=RuntimeError("x")):
            self.assertEqual(va._release_split_scale("SFBS", self.SPLIT), 1.0)


class TestResolversScaleAPreSplitRelease(unittest.TestCase):
    """A pre-split release is gated on ITS basis (anchors × ratio) and served
    ÷ ratio; a post-split release (scale 1.0) is gated as-is."""

    def test_tbvps_pre_split_release(self):
        seen = {}

        def fake(cik, reconstructed=None, bvps=None):
            seen.update(reconstructed=reconstructed, bvps=bvps)
            return 35.94, "ok"                       # the Q2 release's own figure
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_tbvps_status", side_effect=fake):
            v, src, conflict = va._resolve_tbvps("SFBS", 17.9647, 18.0893,
                                                 sec_as_of=BS_END, release_scale=2.0)
        self.assertAlmostEqual(seen["reconstructed"], 35.9294)
        self.assertAlmostEqual(seen["bvps"], 36.1786)
        self.assertEqual((round(v, 2), src, conflict), (17.97, "reported_8k", False))

    def test_tbvps_post_split_release_unscaled(self):
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_tbvps_status",
                      return_value=(18.51, "ok")) as m:
            v, src, _ = va._resolve_tbvps("SFBS", 17.9647, 18.0893,
                                          sec_as_of=BS_END, release_scale=1.0)
        self.assertEqual(m.call_args.kwargs["reconstructed"], 17.9647)
        self.assertEqual((v, src), (18.51, "reported_8k"))

    def test_bvps_pre_split_release(self):
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch.object(va, "_earnings_8k_predates", return_value=False), \
                patch("data.sec_earnings_8k.reported_bvps_status",
                      return_value=(36.20, "ok")) as m:
            v, src, _ = va._resolve_bvps("SFBS", 18.0893, 17.9647,
                                         sec_as_of=BS_END, release_scale=2.0)
        self.assertAlmostEqual(m.call_args.kwargs["reconstructed"], 36.1786)
        self.assertAlmostEqual(m.call_args.kwargs["tbvps"], 35.9294)
        self.assertEqual((v, src), (18.10, "reported_8k"))

    def test_eps_post_split_release_scales_the_prior_quarters(self):
        seen = {}

        def fake(cik, prior_scale=1.0):
            seen["prior_scale"] = prior_scale
            return 3.13, "2026-09-30", {"release": {"qend": "2026-09-30", "eps": 0.80},
                                        "quarters": {"a": 0.775, "b": 0.795, "c": 0.765}}
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch.object(va, "_release_newer_than_filings", return_value=True), \
                patch("analysis.release_eps.composite_ttm_eps", side_effect=fake):
            v, src, conflict = va._resolve_eps("SFBS", 2.935, BS_END, release_scale=1.0,
                                               split={"ratio": 2.0, "date": DECL})
        self.assertEqual(seen["prior_scale"], 2.0)
        self.assertEqual((v, src, conflict), (3.13, "release_ttm", False))

    def test_eps_pre_split_release_scales_the_whole_composite(self):
        def fake(cik, prior_scale=1.0):
            self.assertEqual(prior_scale, 1.0)
            return 5.87, "2026-06-30", {"release": {"qend": "2026-06-30", "eps": 1.47},
                                        "quarters": {"a": 1.45, "b": 1.50, "c": 1.45}}
        with patch("data.bank_mapping.get_data_cik", return_value=1430723), \
                patch.object(va, "_release_newer_than_filings", return_value=True), \
                patch("analysis.release_eps.composite_ttm_eps", side_effect=fake):
            v, src, _ = va._resolve_eps("SFBS", 2.935, "2026-03-31", release_scale=2.0,
                                        split={"ratio": 2.0, "date": DECL})
        self.assertAlmostEqual(v, 2.935)
        self.assertEqual(src, "release_ttm")


class TestCompositePriorScale(unittest.TestCase):
    """analysis/release_eps._assemble: SFBS 3Q26 — release $0.80 (post-split)
    + Company-Reported Q2'26 1.55, Q1'26 1.59, Q4'25 1.53 (pre-split) read
    5.47; with prior_scale 2 the priors are 0.775 + 0.795 + 0.765 → 3.135."""
    RM = {"qend": "2026-09-30", "accession": "000117184326006691",
          "metrics": {"eps_diluted": 0.80}}

    def _stmt(self):
        labels = [lab for lab, _ in release_eps._prior_quarters("2026-09-30", 3)]
        return {"statement": {"periods": labels, "rows": [
            {"label": "Diluted earnings per share (in dollars per share)",
             "values": [1.55, 1.59, 1.53]}]}}

    def test_prior_quarters_divided(self):
        with patch("data.sec_statements.as_reported_statement_multiquarter",
                   return_value=self._stmt()):
            v, qend, comps = release_eps._assemble(1430723, self.RM, "2026-09-30",
                                                   prior_scale=2.0)
            v1, _, comps1 = release_eps._assemble(1430723, self.RM, "2026-09-30")
        self.assertAlmostEqual(v, 3.135)
        self.assertEqual(qend, "2026-09-30")
        self.assertEqual(sorted(comps["quarters"].values()), [0.765, 0.775, 0.795])
        self.assertAlmostEqual(v1, 5.47)
        self.assertEqual(sorted(comps1["quarters"].values()), [1.53, 1.55, 1.59])

    def test_memo_keyed_by_scale(self):
        release_eps._MEMO.pop(1430723, None)
        self.addCleanup(release_eps._MEMO.pop, 1430723, None)
        with patch("data.release_metrics.release_metrics", return_value=self.RM), \
                patch.object(release_eps, "_expected_release_qend",
                             return_value="2026-09-30"), \
                patch("data.sec_statements.as_reported_statement_multiquarter",
                      return_value=self._stmt()):
            a = release_eps.composite_ttm_eps(1430723, prior_scale=2.0)[0]
            b = release_eps.composite_ttm_eps(1430723)[0]
        self.assertAlmostEqual(a, 3.135)
        self.assertAlmostEqual(b, 5.47)


def _tr(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _doc(*rows):
    return ("<html><body><table>" + "".join(rows)
            + "</table></body></html>").encode("utf-8")


class TestReleaseTbvpsSamePeriodBookAnchor(unittest.TestCase):
    """SFBS 3Q26 release: TBVPS $18.51 against the Q2 reconstruction BVPS
    $18.09 failed tangible < book ("not_disclosed") although its own book
    row prints $18.63 above it. The release's same-period book value
    satisfies the gate; without a book row the prior-quarter anchor still
    rejects (unchanged)."""
    HTML = _doc(_tr("Book value per common share", "$", "18.63", "$", "18.09"),
                _tr("Tangible book value per common share", "$", "18.51", "$", "17.96"))

    def test_release_book_above_the_candidate_passes(self):
        self.assertEqual(se8k.extract_reported_tbvps_status(
            self.HTML, reconstructed=17.9647, bvps=18.0893), (18.51, "ok"))

    def test_without_a_release_book_row_the_callers_anchor_rejects(self):
        html = _doc(_tr("Tangible book value per common share", "$", "18.51", "$", "17.96"))
        self.assertEqual(se8k.extract_reported_tbvps_status(
            html, reconstructed=17.9647, bvps=18.0893), (None, "not_disclosed"))

    def test_release_book_below_the_candidate_still_rejects(self):
        html = _doc(_tr("Book value per common share", "$", "18.40", "$", "18.09"),
                    _tr("Tangible book value per common share", "$", "18.51", "$", "17.96"))
        self.assertEqual(se8k.extract_reported_tbvps_status(
            html, reconstructed=17.9647, bvps=18.0893), (None, "not_disclosed"))

    def test_reader_key_bumped(self):
        self.assertEqual(se8k._REPORTED_TBVPS_CKEY_V, "v17")


class TestCardLabels(unittest.TestCase):
    def test_restated_reconstruction_is_labelled(self):
        row = {"stock_split": {"ratio": 2.0, "date": DECL}, "tbvps_source": "reconstructed",
               "bvps_source": "reconstructed", "eps_source": "reconstructed"}
        self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                         "TBV / Share (2:1 split-adjusted)")
        self.assertEqual(bd._ps_label(row, "BV / Share", "bvps_source"),
                         "BV / Share (2:1 split-adjusted)")
        self.assertEqual(bd._eps_label(row), "EPS (TTM, 2:1 split-adjusted)")

    def test_release_figures_keep_their_provenance(self):
        row = {"stock_split": {"ratio": 2.0, "date": DECL}, "tbvps_source": "reported_8k",
               "bvps_source": "reported_8k", "eps_source": "release_ttm"}
        self.assertEqual(bd._ps_label(row, "TBV / Share", "tbvps_source"),
                         "TBV / Share (co. release)")
        self.assertEqual(bd._eps_label(row), "EPS (TTM, co. release)")

    def test_reverse_split_and_no_split(self):
        self.assertEqual(bd._split_label({"stock_split": {"ratio": 0.1}}), "1:10")
        self.assertEqual(bd._split_label({"stock_split": {"ratio": 1.05}}), "1.05:1")
        self.assertIsNone(bd._split_label({}))
        self.assertEqual(bd._ps_label({"tbvps_source": "reconstructed"}, "TBV / Share",
                                      "tbvps_source"), "TBV / Share")


if __name__ == "__main__":
    unittest.main()
