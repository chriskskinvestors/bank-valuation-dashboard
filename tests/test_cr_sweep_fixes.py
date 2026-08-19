"""
Regression tests for the 2026-08-19 full-universe sweep's parser fixes
(docs/COMPANY-REPORTED-PLAN.md §Phase 5 triage). Each class pins one fix:

F1 — lease-inclusive rollforward concepts: WFC-class filers tag their ONLY
     undimensioned charge-off/recovery/nonaccrual totals under
     ...AndNetInvestmentInLease... — the concept lists missed them, so a $2T
     bank showed blank NPL/NCO.
F2 — split-filer NIM: document-set filers (WFC/BNY) keep the MD&A
     average-balance table in a SIBLING part; the NIM scrape read only the
     primary shell (360KB, zero facts) and returned nothing.
F3 — one dead filing must not kill the multi-year stitch: NEWT's BDC-era
     10-Ks have no R-files (permanent 404) and aborted the whole walk even
     though its current filings parse.
F4 — freshness gate: a DEREGISTERED filer's decade-old statements
     (CCNB/FOTB/OSBK, last 10-K 2012-13) must stay n/a — rendering them as
     "Company Reported" beside live FDIC tabs would mislead. Current filers'
     full history is untouched (only the newest filing's age is gated).
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.sec_filing_scraper import (  # noqa: E402
    Fact,
    extract_asset_quality_nim,
    extract_npl_nco_by_year,
)


class TestLeaseInclusiveRollforward(unittest.TestCase):
    """F1 — WFC's real FY2025 shape, hand-computed: NCO = (5.195 − 1.201) /
    985.7 = 0.405%; NPL = 8.201 / 985.7 = 0.832%."""

    B = 1e9
    GROSS = ("us-gaap:FinancingReceivableAndNetInvestmentInLease"
             "ExcludingAccruedInterestBeforeAllowanceForCreditLoss")
    WO = ("us-gaap:FinancingReceivableAndNetInvestmentInLease"
          "ExcludingAccruedInterestAllowanceForCreditLossWriteoff")
    REC = ("us-gaap:FinancingReceivableAndNetInvestmentInLease"
           "ExcludingAccruedInterestAllowanceForCreditLossRecovery")
    NA = ("us-gaap:FinancingReceivableAndNetInvestmentInLease"
          "ExcludingAccruedInterestNonaccrual")

    def test_wfc_shape_recovers_npl_and_nco(self):
        facts = [
            Fact(self.GROSS, 985.7 * self.B, "2025-12-31", None, {}, "usd"),
            Fact(self.NA, 8.201 * self.B, "2025-12-31", None, {}, "usd"),
            Fact(self.WO, 5.195 * self.B, "2025-12-31", "2025-01-01", {}, "usd"),
            Fact(self.REC, 1.201 * self.B, "2025-12-31", "2025-01-01", {}, "usd"),
        ]
        out = extract_npl_nco_by_year(facts)
        self.assertAlmostEqual(out[2025]["npl_loans"], 8.201 / 985.7, places=6)
        self.assertAlmostEqual(out[2025]["nco_loans"],
                               (5.195 - 1.201) / 985.7, places=6)


class TestSplitFilerNim(unittest.TestCase):
    """F2 — the NIM scrape must scan every document part, first hit wins."""

    NIM_HTML = b"""<table>
      <tr><td></td><td>2025</td><td>2024</td></tr>
      <tr><td>Net interest margin</td><td>2.64%</td><td>2.73%</td></tr>
    </table>"""
    SHELL = b"<html><body>cover page only</body></html>"

    def test_nim_found_in_sibling_part(self):
        meta = {"cik": 1, "accession": "a", "doc": "d1.htm"}
        out = extract_asset_quality_nim([(meta, [], [self.SHELL, self.NIM_HTML])])
        self.assertAlmostEqual(out[2025]["nim"], 0.0264)
        self.assertAlmostEqual(out[2024]["nim"], 0.0273)

    def test_single_document_bytes_still_accepted(self):
        # Pre-fix callers pass bare bytes — must keep working.
        meta = {"cik": 1, "accession": "a", "doc": "d1.htm"}
        out = extract_asset_quality_nim([(meta, [], self.NIM_HTML)])
        self.assertAlmostEqual(out[2025]["nim"], 0.0264)


class _FakeHttp404(Exception):
    def __init__(self):
        super().__init__("404 Client Error")
        self.response = mock.Mock(status_code=404)


class TestMultiyearStitchResilience(unittest.TestCase):
    """F3/F4 — dead filings are skipped, not fatal; stale-only filers gate out."""

    def _run(self, metas, select_side_effect):
        import data.cache as dc
        import data.sec_statements as ss
        with mock.patch.object(ss, "_recent_10k_metas", return_value=metas), \
             mock.patch.object(ss, "_select_primary_rfile",
                               side_effect=select_side_effect), \
             mock.patch.object(dc, "get", return_value=None), \
             mock.patch.object(dc, "put"):
            return ss.as_reported_statement_multiyear(999999, "income", 5)

    @staticmethod
    def _meta(d, acc):
        return {"cik": 999999, "accession": acc, "date": d}

    def test_dead_filing_is_skipped_not_fatal(self):
        # Filing 2 404s (NEWT's BDC-era shape); filings 1 and 3 parse. The
        # stitch must keep both good filings instead of returning None.
        today = date.today()
        good = {"periods": ["Dec. 31, 2025", "Dec. 31, 2024"],
                "rows": [{"label": "NII", "header": False,
                          "values": [1.0, 2.0]}], "title": "income", "units_scale": 1}
        calls = iter([("f", good), _FakeHttp404(), ("f", good)])

        def side_effect(base, stype):
            v = next(calls)
            if isinstance(v, Exception):
                raise v
            return v
        metas = [self._meta((today - timedelta(days=100 * i)).isoformat(), f"a{i}")
                 for i in range(3)]
        res = self._run(metas, side_effect)
        self.assertIsNotNone(res, "one dead filing aborted the whole stitch")

    def test_stale_only_filer_gates_to_none(self):
        # Newest filing is 13 years old (deregistered) → n/a, never rendered.
        metas = [self._meta("2013-03-28", "old1"), self._meta("2012-03-29", "old2")]
        res = self._run(metas, AssertionError("must not fetch a gated filer"))
        self.assertIsNone(res)

    def test_current_filer_history_untouched(self):
        # Newest filing is recent; OLD comparatives in the window still stitch.
        today = date.today()
        good = {"periods": ["Dec. 31, 2025"],
                "rows": [{"label": "NII", "header": False, "values": [1.0]}],
                "title": "income", "units_scale": 1}
        metas = [self._meta((today - timedelta(days=90)).isoformat(), "new"),
                 self._meta("2020-03-01", "older")]
        res = self._run(metas, lambda base, stype: ("f", dict(good)))
        self.assertIsNotNone(res)


class TestHighlightsLabelVariants(unittest.TestCase):
    """F5 — the 2026-08-19 label harvest across 124 efficiency-missing banks:
    "before provision" NII lines, plural/"revenues" noninterest totals,
    BOKF-style "other operating" totals, and "net earnings" bottom lines must
    match (56 banks recovered). All values hand-computed from the fixture."""

    @staticmethod
    def _stmt(rows, periods=("Dec. 31, 2025",)):
        return {"statement": {"periods": list(periods),
                              "rows": [{"label": l, "header": False,
                                        "values": [v]} for l, v in rows],
                              "units_scale": 1, "title": "x"},
                "meta": {"cik": 1, "accession": "a", "doc": "d.htm",
                         "date": "2026-02-01"}}

    def test_variant_labels_fill_efficiency(self):
        inc = self._stmt([
            ("Net interest income before (recapture of) provision for credit losses", 460.0),
            ("Total noninterest revenues", 55.0),
            ("Total non-interest expenses", 237.0),
            ("Net earnings", 209.0),
        ])
        bal = self._stmt([("Total assets", 15000.0), ("Total deposits", 12000.0),
                          ("Total stockholders' equity", 2000.0)])
        import ui.financials_statements as fs
        import data.sec_statements as ss
        with mock.patch.object(fs, "get_bank_info", return_value={"cik": 1, "name": "T"}), \
             mock.patch.object(ss, "as_reported_statement_multiyear",
                               side_effect=lambda cik, st, n: inc if st == "income" else bal), \
             mock.patch("data.sec_filing_scraper.holdco_capital_for",
                        return_value=None), \
             mock.patch("data.sec_filing_scraper.company_asset_quality_nim",
                        return_value=None):
            years, dicts, _src = fs._cr_highlights_by_year("TEST")
        self.assertEqual(years, ["FY2025"])
        d = dicts[0]
        self.assertEqual(d["nii"], 460.0)
        self.assertEqual(d["noninterest_income"], 55.0)
        self.assertEqual(d["noninterest_expense"], 237.0)
        self.assertEqual(d["net_income"], 209.0)
        self.assertAlmostEqual(d["efficiency"], 237.0 / (460.0 + 55.0), places=6)

    def test_loss_first_ni_and_reversed_assets_total_match(self):
        # Round 2 of the harvest: "Net (loss) income" (13 banks) and the SEC
        # standard-label "Assets, Total" reversed word order (ASB/EFSC/PRK).
        inc = self._stmt([
            ("Net interest income", 460.0),
            ("Total noninterest income", 55.0),
            ("Total noninterest expense", 237.0),
            ("Net (loss) income", 209.0),
        ])
        bal = self._stmt([("Assets, Total", 15000.0), ("Total deposits", 12000.0),
                          ("Total stockholders' equity", 2000.0)])
        import ui.financials_statements as fs
        import data.sec_statements as ss
        with mock.patch.object(fs, "get_bank_info", return_value={"cik": 1, "name": "T"}), \
             mock.patch.object(ss, "as_reported_statement_multiyear",
                               side_effect=lambda cik, st, n: inc if st == "income" else bal), \
             mock.patch("data.sec_filing_scraper.holdco_capital_for",
                        return_value=None), \
             mock.patch("data.sec_filing_scraper.company_asset_quality_nim",
                        return_value=None):
            _years, dicts, _src = fs._cr_highlights_by_year("TEST")
        d = dicts[0]
        self.assertEqual(d["net_income"], 209.0)
        self.assertEqual(d["total_assets"], 15000.0)
        self.assertAlmostEqual(d["roaa"], 209.0 / 15000.0, places=6)

    def test_after_provision_line_never_matches(self):
        # The after-provision figure is NOT net interest income — a filer
        # tagging ONLY that line must yield n/a, never a wrong efficiency.
        inc = self._stmt([
            ("Net interest income after provision for credit losses", 420.0),
            ("Total noninterest income", 55.0),
            ("Total noninterest expense", 237.0),
            ("Net income", 209.0),
        ])
        bal = self._stmt([("Total assets", 15000.0), ("Total deposits", 12000.0),
                          ("Total stockholders' equity", 2000.0)])
        import ui.financials_statements as fs
        import data.sec_statements as ss
        with mock.patch.object(fs, "get_bank_info", return_value={"cik": 1, "name": "T"}), \
             mock.patch.object(ss, "as_reported_statement_multiyear",
                               side_effect=lambda cik, st, n: inc if st == "income" else bal), \
             mock.patch("data.sec_filing_scraper.holdco_capital_for",
                        return_value=None), \
             mock.patch("data.sec_filing_scraper.company_asset_quality_nim",
                        return_value=None):
            years, dicts, _src = fs._cr_highlights_by_year("TEST")
        d = dicts[0]
        self.assertIsNone(d["nii"])
        self.assertIsNone(d["efficiency"])


if __name__ == "__main__":
    unittest.main()
