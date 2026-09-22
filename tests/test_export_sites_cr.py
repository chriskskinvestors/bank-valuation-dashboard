"""Company-Reported statement exports (ui/financials_statements.py _cr_* /
_render_company_* renderers) on the shared .xlsx exporter (ui/export.py;
owner directive 2026-09-22: every data table gets an Export, built from the
RAW frame — never the display strings _cr_component renders).

Each test drives the real render function with a small fixture at the data
seam (the stitched-statement loader / composition cache / highlights engine),
captures the deferred workbook callable table_export hands st.download_button,
loads the bytes with openpyxl and asserts HAND-COMPUTED values: raw dollars
under ``$#,##0`` (not "$512.3M"), EPS under ``$#,##0.00``, share counts as the
raw count (not the /1e6 the screen shows), a fraction ratio exported ×100 as
percent units, "n/a" for a period the filing doesn't report (never 0), header
rows kept as label-only rows, and the Source sheet's Ticker / CIK / Latest
filing rows.

Stub discipline: the render module's own ``st`` binding is patched with a
PRIVATE stub per test (mock.patch.object) and the components.html sink is
patched on the shared stub module's attribute — nothing here swaps
sys.modules, so this module composes with every other suite under discovery
(see tests/test_export_sites_earnings.py for the pattern).

Run: python -m unittest tests.test_export_sites_cr
"""
from __future__ import annotations

import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from openpyxl import load_workbook  # noqa: E402


def _noop(*a, **k):
    return None


class _Ctx:
    """Context-manager placeholder (columns / container): every attribute is
    a no-op."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return _noop


class _StubSt:
    """Private streamlit stand-in for the Company-Reported render paths:
    unknown widgets are no-ops; layout widgets return contexts; radio returns
    the first option (the page's Annual default)."""

    session_state: dict = {}

    def __getattr__(self, name):
        return _noop

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    def container(self, *a, **k):
        return _Ctx()

    expander = spinner = popover = empty = container

    def radio(self, label, options=None, **k):
        return options[0] if options else None


TICKER = "TBK"
CIK = 12345
INFO = {"cik": CIK, "name": "Test Bancorp"}
META_10K = {"cik": CIK, "accession": "000012345-26-000010", "doc": "tbk-10k.htm",
            "date": "2026-02-27", "form": "10-K"}
SRC_10K = ("https://www.sec.gov/Archives/edgar/data/12345/000012345-26-000010/"
           "tbk-10k.htm")
META_10Q = {"cik": CIK, "accession": "000012345-25-000042", "doc": "tbk-10q.htm",
            "date": "2025-11-05", "form": "10-Q"}

# Multi-year income statement as data.sec_statements stitches it: periods and
# values NEWEST-FIRST, raw dollars (units de-scaled by the parser), EPS in
# $/share, share counts raw. FY2024 net income deliberately unreported.
INCOME_ANNUAL = {
    "statement": {
        "periods": ["2025-12-31", "2024-12-31"],
        "rows": [
            {"label": "Interest income", "header": True, "values": []},
            {"label": "Total interest income", "header": False,
             "values": [512_345_000, 468_900_000]},
            {"label": "Net income", "header": False, "values": [120_450_000, None]},
            {"label": "Per share data", "header": True, "values": []},
            {"label": "Diluted earnings per common share (in dollars per share)",
             "header": False, "values": [3.41, 3.05]},
            {"label": "Weighted average diluted shares (in shares)",
             "header": False, "values": [35_190_616, 36_004_120]},
        ],
    },
    "filings": [META_10K, {**META_10K, "date": "2025-02-28"}],
    "meta": META_10K,
}

# Discrete-quarter balance sheet: periods are already compact _q_label strings,
# newest-first; Q1'25 total assets not cleanly derivable.
BALANCE_QUARTERLY = {
    "statement": {
        "periods": ["Q3'25", "Q2'25", "Q1'25"],
        "rows": [
            {"label": "Assets", "header": True, "values": []},
            {"label": "Total assets", "header": False,
             "values": [1_050_000_000, 1_020_000_000, None]},
            {"label": "Total deposits", "header": False,
             "values": [900_000_000, 880_000_000, 870_000_000]},
        ],
    },
    "filings": [META_10Q, META_10Q, META_10Q],
    "meta": META_10Q,
}

# Loan composition as data.sec_composition returns it: {period: {"total",
# "rows": [(label, value, member)]}}, raw dollars. Filer dropped "Residential
# mortgage" and added "Consumer" in FY2025 (distinct members — never merged).
COMPOSITIONS = {
    "meta": META_10K,
    "loan": {
        "2025-12-31": {"total": 1_000_000_000, "rows": [
            ("Commercial real estate", 600_000_000, "tbk:CreMember"),
            ("Consumer", 400_000_000, "tbk:ConsumerMember")]},
        "2024-12-31": {"total": 900_000_000, "rows": [
            ("Commercial real estate", 550_000_000, "tbk:CreMember"),
            ("Residential mortgage", 350_000_000, "tbk:ResiMember")]},
    },
    "deposit": None,
}


class _CrExportSite(unittest.TestCase):
    """Binds the private stub onto ui.financials_statements, a download_button
    capture onto ui.export, and a recorder onto the components.html sink."""

    @classmethod
    def setUpClass(cls):
        import ui.export
        import ui.financials_statements
        cls.FS, cls.X = ui.financials_statements, ui.export

    def setUp(self):
        self.calls = []          # [(label, data, kwargs)] from download_button
        self.htmls = []          # component html the screen table rendered
        fake_export_st = types.SimpleNamespace(
            download_button=lambda label, data, **k: self.calls.append((label, data, k)),
            container=lambda *a, **k: _Ctx())
        v1 = sys.modules["streamlit.components.v1"]
        for p in (mock.patch.object(self.FS, "st", _StubSt()),
                  mock.patch.object(self.X, "st", fake_export_st),
                  mock.patch.object(v1, "html", lambda html, **k: self.htmls.append(html)),
                  mock.patch.object(self.FS, "get_bank_info", lambda t: INFO)):
            p.start()
            self.addCleanup(p.stop)

    # ── helpers ──────────────────────────────────────────────────────
    def _book(self):
        self.assertEqual(len(self.htmls), 1, "expected exactly one rendered table")
        self.assertEqual(len(self.calls), 1, "expected exactly one Export control")
        _label, data, kw = self.calls[0]
        self.assertTrue(callable(data), "workbook must build lazily, on click")
        wb = load_workbook(io.BytesIO(data()))
        return wb, wb.worksheets[0], kw

    @staticmethod
    def _grid(ws):
        return [[c.value for c in r] for r in ws.iter_rows()]

    @staticmethod
    def _source(wb):
        return dict((r[0].value, r[1].value) for r in wb["Source"].iter_rows())

    def _assert_na(self, ws, coord):
        self.assertEqual(ws[coord].value, "n/a")
        self.assertTrue(ws[coord].font.italic)

    def _assert_header_row(self, ws, row, label, ncols):
        """A section band survives as its label with every period n/a."""
        self.assertEqual(ws.cell(row, 1).value, label)
        for ci in range(2, ncols + 2):
            self.assertEqual(ws.cell(row, ci).value, "n/a")


class TestAnnualIncomeStatement(_CrExportSite):
    """Site: _render_company_statement_annual (10-K stitch, income)."""

    def test_raw_dollars_eps_shares_and_missing_year(self):
        import data.sec_statements as S
        with mock.patch.object(S, "as_reported_statement_multiyear",
                               lambda cik, stype, n_years=5: INCOME_ANNUAL):
            self.FS._render_company_statement_annual(TICKER, "income", CIK, INFO)
        wb, ws, kw = self._book()
        self.assertEqual(kw["file_name"], "TBK_cr_income_annual_2026-02-27.xlsx")
        self.assertEqual(kw["key"], "exp_cr_income_annual_TBK")
        g = self._grid(ws)
        # oldest → newest, exactly the screen's column labels
        self.assertEqual(g[0], ["Line item", "FY2024", "FY2025"])
        self._assert_header_row(ws, 2, "Interest income", 2)
        # dollar line: the RAW filing values, whole dollars — not "$468.9M"
        self.assertEqual(g[2], ["Total interest income", 468_900_000, 512_345_000])
        self.assertEqual(ws["B3"].number_format, "$#,##0")
        self.assertEqual(ws["C3"].number_format, "$#,##0")
        # FY2024 net income not reported → n/a, never 0
        self.assertEqual(g[3][0], "Net income")
        self._assert_na(ws, "B4")
        self.assertEqual((ws["C4"].value, ws["C4"].number_format), (120_450_000, "$#,##0"))
        self._assert_header_row(ws, 5, "Per share data", 2)
        # per-share line → $/share
        self.assertEqual(g[5], ["Diluted earnings per common share (in dollars per share)",
                                3.05, 3.41])
        self.assertEqual(ws["B6"].number_format, "$#,##0.00")
        self.assertEqual(ws["C6"].number_format, "$#,##0.00")
        # share count: the RAW count (screen shows 36.0M), integer format
        self.assertEqual(g[6], ["Weighted average diluted shares (in shares)",
                                36_004_120, 35_190_616])
        self.assertEqual(ws["B7"].number_format, "#,##0")
        self.assertEqual(ws["A3"].number_format, "General")      # label column
        self.assertEqual(ws.freeze_panes, "B2")
        # the screen still shows the compact forms — export and display diverge by design
        self.assertIn(">36.0M<", self.htmls[0])
        self.assertIn(">$468.9M<", self.htmls[0])
        src = self._source(wb)
        self.assertEqual(src["Ticker"], "TBK")
        self.assertEqual(src["Company"], "Test Bancorp")
        self.assertEqual(src["SEC CIK"], CIK)
        self.assertIn("Company Reported › Income Statement", src["Page"])
        self.assertTrue(src["Basis"].startswith("Annual"))
        self.assertEqual(src["Latest filing"], f"2026-02-27 — {SRC_10K}")
        self.assertIn("10-K", src["Source"])
        self.assertEqual(src["Periods"], "2 (FY2024 – FY2025)")
        self.assertIn("raw share counts", src["Value units"])
        self.assertIn("whole US dollars", src["Units"])


class TestQuarterlyBalanceSheet(_CrExportSite):
    """Site: _render_company_statement_quarterly (10-Q stitch, balance)."""

    def test_quarter_labels_and_underivable_quarter(self):
        import data.sec_statements as S
        with mock.patch.object(S, "as_reported_statement_multiquarter",
                               lambda cik, stype, n_quarters=12: BALANCE_QUARTERLY):
            self.FS._render_company_statement_quarterly(TICKER, "balance", CIK, INFO)
        wb, ws, kw = self._book()
        self.assertEqual(kw["file_name"], "TBK_cr_balance_quarterly_2025-11-05.xlsx")
        self.assertEqual(kw["key"], "exp_cr_balance_quarterly_TBK")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Line item", "Q1'25", "Q2'25", "Q3'25"])
        self._assert_header_row(ws, 2, "Assets", 3)
        self.assertEqual(g[2][0], "Total assets")
        self._assert_na(ws, "B3")                                  # not cleanly derivable
        self.assertEqual(g[2][2:], [1_020_000_000, 1_050_000_000])
        self.assertEqual(g[3], ["Total deposits", 870_000_000, 880_000_000, 900_000_000])
        for coord in ("C3", "D3", "B4", "C4", "D4"):
            self.assertEqual(ws[coord].number_format, "$#,##0")
        src = self._source(wb)
        self.assertEqual(src["Ticker"], "TBK")
        self.assertEqual(src["SEC CIK"], CIK)
        self.assertIn("Company Reported › Balance Sheet", src["Page"])
        self.assertTrue(src["Basis"].startswith("Quarterly"))
        self.assertNotIn("Q4 =", src["Basis"])        # the Q4 derivation note is income-only
        self.assertIn("2025-11-05", src["Latest filing"])
        self.assertIn("000012345-25-000042/tbk-10q.htm", src["Latest filing"])
        self.assertEqual(src["Periods"], "3 (Q1'25 – Q3'25)")


class TestLoanComposition(_CrExportSite):
    """Site: _render_company_composition (annual, loan) — categories unioned
    across years, blank where a category wasn't reported, Total per period."""

    def test_union_rows_total_and_unreported_category(self):
        with mock.patch.object(self.FS, "_compositions_cached", lambda cik: COMPOSITIONS):
            self.FS._render_company_composition(TICKER, "loan")
        wb, ws, kw = self._book()
        self.assertEqual(kw["file_name"], "TBK_cr_loan_composition_annual_2026-02-27.xlsx")
        self.assertEqual(kw["key"], "exp_cr_loan_composition_annual_TBK")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Line item", "FY2024", "FY2025"])
        # ordered by the latest year's value, categories absent latest trailing
        self.assertEqual(g[1], ["Commercial real estate", 550_000_000, 600_000_000])
        self.assertEqual(g[2][0], "Consumer")
        self._assert_na(ws, "B3")                                  # not reported FY2024
        self.assertEqual(g[2][2], 400_000_000)
        self.assertEqual(g[3][:2], ["Residential mortgage", 350_000_000])
        self._assert_na(ws, "C4")                                  # dropped in FY2025
        # Total = the filer's disclosed total each period (rows reconcile to it)
        self.assertEqual(g[4], ["Total", 900_000_000, 1_000_000_000])
        self.assertEqual(550_000_000 + 350_000_000, g[4][1])
        self.assertEqual(600_000_000 + 400_000_000, g[4][2])
        for coord in ("B2", "C2", "C3", "B4", "B5", "C5"):
            self.assertEqual(ws[coord].number_format, "$#,##0")
        self.assertEqual(len(g), 5)
        src = self._source(wb)
        self.assertEqual(src["Ticker"], "TBK")
        self.assertEqual(src["SEC CIK"], CIK)
        self.assertIn("Company Reported › Loan Composition", src["Page"])
        self.assertEqual(src["Latest filing"], f"2026-02-27 — {SRC_10K}")
        self.assertIn("inline XBRL", src["Source"])
        self.assertIn("reconcile", src["Source"])


class TestCreditQualityRatios(_CrExportSite):
    """Site: _render_credit_quality — pins the FRACTION → percent-unit
    conversion (0.0125 → 1.25 under 0.00"%") and the raw coverage multiple."""

    def test_fraction_rows_export_as_percent_units(self):
        # newest-first, as _cr_highlights_by_year returns them
        years = ["FY2025", "FY2024"]
        dicts = [
            {"acl": 10_000_000, "loans_gross": 800_000_000, "provision": 4_000_000,
             "reserves_loans": 0.0125, "npl_loans": 0.004, "nco_loans": None,
             "net_loans": 790_000_000},
            {"acl": 9_000_000, "loans_gross": None, "provision": -1_500_000,
             "reserves_loans": 0.0118, "npl_loans": None, "nco_loans": None,
             "net_loans": 760_000_000},
        ]
        with mock.patch.object(self.FS, "_cr_highlights_by_year",
                               lambda t, quarterly=False: (years, dicts, SRC_10K)):
            self.FS._render_credit_quality(TICKER)
        wb, ws, kw = self._book()
        self.assertEqual(kw["file_name"], "TBK_cr_credit_quality_annual.xlsx")
        self.assertEqual(kw["key"], "exp_cr_credit_quality_annual_TBK")
        g = self._grid(ws)
        self.assertEqual(g[0], ["Line item", "FY2024", "FY2025"])
        self._assert_header_row(ws, 2, "Allowance & Loans", 2)
        self.assertEqual(g[2], ["Allowance for credit losses", 9_000_000, 10_000_000])
        self.assertEqual(g[3][0], "Gross loans (net of unearned income)")
        self._assert_na(ws, "B4")
        self.assertEqual(g[3][2], 800_000_000)
        # a negative provision (reversal) stays a signed raw dollar figure
        self.assertEqual(g[4], ["Provision for credit losses", -1_500_000, 4_000_000])
        self.assertEqual(ws["B5"].number_format, "$#,##0")
        self._assert_header_row(ws, 6, "Asset-quality ratios", 2)
        # ACL / loans: 0.0118 → 1.18, 0.0125 → 1.25 (percent units, literal-% format)
        self.assertEqual(g[6][0], "ACL / loans")
        self.assertAlmostEqual(g[6][1], 1.18, places=9)
        self.assertAlmostEqual(g[6][2], 1.25, places=9)
        self.assertEqual(ws["B7"].number_format, '0.00"%"')
        self.assertEqual(g[7][0], "Nonaccrual / loans")
        self._assert_na(ws, "B8")
        self.assertAlmostEqual(g[7][2], 0.4, places=9)
        # nco_loans None every year → row dropped (matches the screen)
        self._assert_header_row(ws, 9, "Coverage", 2)
        # coverage = acl ÷ (npl_loans × net_loans) = 10e6 ÷ (0.004 × 790e6) = 3.1645569620x
        self.assertEqual(g[9][0], "ACL coverage of nonaccrual")
        self._assert_na(ws, "B10")
        self.assertAlmostEqual(g[9][2], 10_000_000 / (0.004 * 790_000_000), places=9)
        self.assertAlmostEqual(g[9][2], 3.164556962, places=8)
        self.assertEqual(ws["C10"].number_format, '0.00"x"')
        self.assertEqual(len(g), 10)
        src = self._source(wb)
        self.assertEqual(src["Ticker"], "TBK")
        self.assertEqual(src["SEC CIK"], CIK)
        self.assertIn("Company Reported › Credit Quality / Allowance", src["Page"])
        self.assertEqual(src["Latest filing"], SRC_10K)
        self.assertIn("percent units", src["Units"])
        self.assertIn("Multiples hold the raw ratio", src["Units"])


if __name__ == "__main__":
    unittest.main()
