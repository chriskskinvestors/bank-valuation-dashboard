"""P3 audit guards (docs/AUDIT-2026-07-02.md) — four cardinal-rule fixes.

Each test pins the EXACT failure with a synthetic fixture on which the OLD code
produced a plausible-wrong number and the NEW code returns None / {} (n/a) or
the correctly aligned value:

  1. extract_credit_quality: the composition-total fallback divided the
     current-quarter ACL by a PRIOR-FY loan total, unflagged. Now the stand-in
     is refused unless the composition total is dated at the filing's current
     period end.
  2. extract_rate_risk: the unrealized-loss/CET1 share took CET1 capital from
     max(cap) — a period that can DIFFER from the securities-marks period.
     Now CET1 must be tagged at the same period end, else the share is n/a.
  3. extract_nim_by_year's NII÷earning-assets fallback trusted a fragile
     (balance, interest, yield) positional triplet with no sanity band. Now the
     counts must match exactly and the computed NIM must land in (0, 10%).
  4. sec_earnings_8k._table_rows dropped empty cells, shifting a PRIOR period's
     value into the latest-quarter column when the current cell is blank. Now
     positions are preserved (blank value cell -> None -> n/a upstream).

Run: PYTHONIOENCODING=utf-8 python -m unittest tests.test_scraper_p3_guards -v
"""
import sys
import types
import unittest

# Streamlit stub (defensive: these data modules must import without a UI).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.sec_filing_scraper import (  # noqa: E402
    Fact, extract_credit_quality, extract_rate_risk, extract_nim_by_year,
)
from data.sec_earnings_8k import (  # noqa: E402
    _table_rows, _first_match, extract_earnings_figures, extract_reported_tbvps,
)


def _f(concept, value, period="2025-12-31", members=None, unit="usd"):
    return Fact(concept, value, period, None, members or {}, unit)


def _html(rows_html: str) -> bytes:
    return (f"<html><body><table>{rows_html}</table></body></html>").encode("utf-8")


def _row(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


M = 1e6
B = 1e9


# ── 1. Credit quality: composition loan total must match the ACL period ───────
class TestCompLoanTotalPeriodGate(unittest.TestCase):
    """A filer tagging loans only by segment renders via the composition total —
    but ONLY when that total is dated at the filing's current period. OLD code
    divided the 2025-12-31 ACL ($106M) by a 2024-12-31 composition total
    ($6,000M) and shipped acl_to_loans = 1.77% — plausible (inside the
    [0.2%, 6%] gate), wrong period. NEW: period mismatch -> {} (n/a)."""

    SEG = "us-gaap:FinancingReceivablePortfolioSegmentAxis"
    COLL = "us-gaap:LoansAndLeasesReceivableCollectivelyEvaluatedForAllowance"

    def _facts(self):
        # No undimensioned ACL or gross loans — only the segment CECL split;
        # the period is anchored by the undimensioned Assets tag (2025-12-31).
        return [
            _f("us-gaap:Assets", 10_000 * M),
            _f(self.COLL, 80 * M, members={self.SEG: "x:CommercialMember"}),
            _f(self.COLL, 26 * M, members={self.SEG: "x:ConsumerMember"}),
        ]

    def test_prior_fy_composition_total_refused(self):
        out = extract_credit_quality(
            self._facts(), comp_loan_total=6_000 * M,
            comp_loan_period="2024-12-31")     # PRIOR FY vs 2025-12-31 ACL
        self.assertEqual(out, {})              # n/a, never a cross-period ratio

    def test_same_period_composition_total_still_renders(self):
        # The guard must not over-block: a matching period end renders exactly
        # as before (ACL 106 / loans 8,158 = 1.30%).
        out = extract_credit_quality(
            self._facts(), comp_loan_total=8_158 * M,
            comp_loan_period="2025-12-31")
        d = out["2025-12-31"]
        self.assertAlmostEqual(d["loans_gross"], 8_158 * M)
        self.assertAlmostEqual(d["acl"], 106 * M)
        self.assertAlmostEqual(d["acl_to_loans"], 106 / 8158, places=6)


# ── 2. Rate risk: CET1 must be tagged at the securities-marks period ─────────
class TestRateRiskCet1PeriodMatch(unittest.TestCase):
    """The unrealized total is measured at the securities period (2025-12-31).
    OLD code denominated it by cap[max(cap)] — here CET1 tagged ONLY at
    2024-12-31 — shipping unrealized_to_cet1 = -3.5/13.0 = -26.9% built from
    two different dates. NEW: no same-period CET1 -> the share is None."""

    AC_A = "us-gaap:DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestAfterAllowanceForCreditLoss"
    FV_A = "us-gaap:DebtSecuritiesAvailableForSaleExcludingAccruedInterest"
    EQ = "us-gaap:StockholdersEquity"
    CET1 = "us-gaap:CommonEquityTierOneCapital"

    def _sec_facts(self):
        # AFS: cost 230B, fair 226.5B -> net unrealized -3.5B; equity 178B.
        return [_f(self.AC_A, 230 * B), _f(self.FV_A, 226.5 * B),
                _f(self.EQ, 178 * B)]

    def test_prior_period_cet1_not_mixed(self):
        facts = self._sec_facts() + [_f(self.CET1, 13.0 * B, period="2024-12-31")]
        d = extract_rate_risk(facts)["2025-12-31"]
        self.assertIsNone(d["cet1_capital"])         # wrong-date CET1 refused
        self.assertIsNone(d["unrealized_to_cet1"])   # n/a, never a mixed ratio
        # The same-period equity share still renders.
        self.assertAlmostEqual(d["unrealized_to_equity"], -3.5 / 178, places=4)

    def test_same_period_cet1_still_used(self):
        facts = self._sec_facts() + [_f(self.CET1, 13.0 * B, period="2025-12-31")]
        d = extract_rate_risk(facts)["2025-12-31"]
        self.assertAlmostEqual(d["cet1_capital"], 13.0 * B)
        self.assertAlmostEqual(d["unrealized_to_cet1"], -3.5 / 13.0, places=4)


# ── 3. NIM fallback: exact triplet counts + 0–10% plausibility band ───────────
class TestNimFallbackGuards(unittest.TestCase):
    """The NII ÷ avg-earning-assets fallback reads the earning-assets row as
    (avg balance, interest, yield) per year — a positional assumption."""

    @staticmethod
    def _table(ea_cells, nii_cells):
        # Caption mentions 'net interest margin' (the table gate) but there is
        # NO parseable NIM row, forcing the compute fallback.
        return (b"<table>"
                b"<tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>"
                b"<tr><td>Yield and net interest margin summary</td></tr>"
                + ("<tr><td>Total interest-earning assets</td>"
                   + "".join(f"<td>{c}</td>" for c in ea_cells)
                   + "</tr>").encode()
                + ("<tr><td>Net interest income</td>"
                   + "".join(f"<td>{c}</td>" for c in nii_cells)
                   + "</tr>").encode()
                + b"</table>")

    _EA_OK = ["24,836,731", "1,398,314", "5.63",
              "23,968,054", "1,382,114", "5.77",
              "23,259,072", "1,284,215", "5.52"]

    def test_out_of_band_computed_nim_is_na(self):
        # A mis-scaled NII (same magnitude as the balances) computes an ~80%
        # "margin". OLD code shipped nims[2025] = 0.805; NEW: out of the 0–10%
        # band -> {} (n/a).
        html = self._table(self._EA_OK, ["20,000,000", "19,000,000", "18,500,000"])
        self.assertEqual(extract_nim_by_year(html), {})

    def test_extra_figure_shifts_triplet_is_na(self):
        # A leading 'Change' figure (5.2) makes 10 numbers for 3 years. OLD code
        # (len >= 3*years) indexed ea = [5.2, 5.77, 1,284,215] and shipped
        # nims[2025] = 940,712 / 5.2 — garbage. NEW: exact-count gate -> {}.
        html = self._table(["5.2"] + self._EA_OK,
                           ["940,712", "853,020", "838,824"])
        self.assertEqual(extract_nim_by_year(html), {})

    def test_plausible_fallback_still_computes(self):
        # Control: the well-formed triplet still computes (3.79% in band).
        html = self._table(self._EA_OK, ["940,712", "853,020", "838,824"])
        out = extract_nim_by_year(html)
        self.assertAlmostEqual(out[2025], 940712 / 24836731, places=6)
        self.assertAlmostEqual(out[2024], 853020 / 23968054, places=6)
        self.assertAlmostEqual(out[2023], 838824 / 23259072, places=6)


# ── 4. _table_rows: a blank latest-quarter cell must stay None ────────────────
class TestTableRowsColumnAlignment(unittest.TestCase):
    """OLD _table_rows dropped empty cells, so a row whose latest-quarter cell
    is blank served the PRIOR period's value as current (nums[0])."""

    _TWO_QTR = (
        _row("", "2026", "2025")                          # year header
        + _row("Total assets", "28,109,935", "27,500,000")
        + _row("Net interest margin", "", "3.56")          # blank CURRENT qtr
        + _row("Return on average assets", "1.62", "1.55")
    )

    def test_blank_latest_quarter_cell_is_none_not_shifted(self):
        rows = _table_rows(_html(self._TWO_QTR))
        by_label = dict(rows)
        # OLD: nums == [3.56] -> _first_match returned 3.56 (prior quarter as
        # current). NEW: position preserved -> [None, 3.56] -> n/a.
        self.assertEqual(by_label["net interest margin"], [None, 3.56])
        self.assertIsNone(_first_match(rows, {"net interest margin"}))
        # Fully populated rows are untouched.
        self.assertEqual(by_label["return on average assets"], [1.62, 1.55])
        self.assertEqual(by_label["total assets"], [28109935.0, 27500000.0])

    def test_extract_earnings_figures_blank_current_is_na(self):
        anchor = {"total_assets": 28_109_935_000.0}
        out = extract_earnings_figures(_html(self._TWO_QTR), anchor)
        self.assertIsNone(out["nim"])                       # blank -> n/a
        self.assertAlmostEqual(out["roaa"], 1.62)           # populated -> kept
        self.assertAlmostEqual(out["total_assets"], 28_109_935_000.0)

    def test_decoration_and_spacer_columns_still_dropped(self):
        # '$' columns and always-empty spacer columns carry no number in ANY
        # row -> dropped for every row alike (no spurious Nones).
        html = _html(
            _row("Total assets", "$", "28,109,935", "", "$", "27,000,000")
            + _row("Total deposits", "$", "22,636,740", "", "$", "21,900,000")
        )
        by_label = dict(_table_rows(html))
        self.assertEqual(by_label["total assets"], [28109935.0, 27000000.0])
        self.assertEqual(by_label["total deposits"], [22636740.0, 21900000.0])

    def test_tbvps_blank_current_quarter_rejected(self):
        # OLD: the blank current cell was dropped, nums[0] = 42.68 (the PRIOR
        # quarter's TBVPS), which ties the reconstruction and tangible<book ->
        # served as current. NEW: None current cell -> None (n/a).
        html = _html(
            _row("", "2026", "2025")
            + _row("Book value per common share", "53.05", "52.10")
            + _row("Tangible book value per common share", "", "42.68")
        )
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10))


if __name__ == "__main__":
    unittest.main()
