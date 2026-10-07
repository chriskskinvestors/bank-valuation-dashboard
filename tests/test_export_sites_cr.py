"""Company-Reported statement exports (ui/financials_statements.py _cr_* renderers;
ui/cr_statements.py income/balance pages) on the shared .xlsx exporter (ui/export.py;
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
        import ui.cr_statements
        import ui.export
        import ui.financials_statements
        cls.FS, cls.X = ui.financials_statements, ui.export
        cls.CS = ui.cr_statements        # the income/balance statement pages

    def setUp(self):
        self.calls = []          # [(label, data, kwargs)] from download_button
        self.htmls = []          # component html the screen table rendered
        fake_export_st = types.SimpleNamespace(
            download_button=lambda label, data, **k: self.calls.append((label, data, k)),
            container=lambda *a, **k: _Ctx())
        v1 = sys.modules["streamlit.components.v1"]
        stub = _StubSt()
        for p in (mock.patch.object(self.FS, "st", stub),
                  mock.patch.object(self.CS, "st", stub),
                  mock.patch.object(self.X, "st", fake_export_st),
                  mock.patch.object(v1, "html", lambda html, **k: self.htmls.append(html)),
                  mock.patch.object(self.FS, "get_bank_info", lambda t: INFO),
                  mock.patch.object(self.CS, "get_bank_info", lambda t: INFO)):
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
            self.CS._render_company_statement_annual(TICKER, "income", CIK, INFO)
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


class TestBareLabelEpsRows(_CrExportSite):
    """REVIEW-2026-09-24 P0-3: LARK and FBIZ label their EPS rows bare
    "Basic" / "Diluted" under an "Earnings per share" header. The label regex
    missed them and the dollar formatter printed 3.07 as "$3". The row's XBRL
    kind (carried by the stitch) now decides the format; the label is only the
    fallback for an untyped row."""

    STMT = {
        "statement": {
            "periods": ["2025-12-31", "2024-12-31"],
            "rows": [
                {"label": "Net earnings", "header": False, "kind": "monetary",
                 "values": [18_775_000, 12_977_000]},
                {"label": "EARNINGS PER SHARE (1):", "header": True, "values": []},
                {"label": "Basic", "header": False, "kind": "pershare",
                 "values": [3.09, 2.06]},
                {"label": "Diluted", "header": False, "kind": "pershare",
                 "values": [3.07, 2.05]},
                {"label": "Weighted average diluted", "header": False, "kind": "shares",
                 "values": [6_115_000, 6_090_000]},
            ],
        },
        "filings": [META_10K],
        "meta": META_10K,
    }

    def test_annual_bare_basic_diluted_render_as_eps(self):
        import data.sec_statements as S
        with mock.patch.object(S, "as_reported_statement_multiyear",
                               lambda cik, stype, n_years=5: self.STMT):
            self.CS._render_company_statement_annual(TICKER, "income", CIK, INFO)
        wb, ws, _kw = self._book()
        html = self.htmls[0]
        self.assertIn(">$3.07<", html)            # was ">$3<"
        self.assertIn(">$2.05<", html)
        self.assertIn(">$3.09<", html)
        self.assertNotIn(">$3<", html)
        self.assertIn(">6.1M<", html)             # typed share count, no "(in shares)"
        self.assertIn(">$18.8M<", html)           # dollar line unchanged
        g = self._grid(ws)
        diluted = next(r for r in g if r[0] == "Diluted")
        self.assertEqual(diluted, ["Diluted", 2.05, 3.07])
        row = [r[0] for r in g].index("Diluted") + 1
        self.assertEqual(ws.cell(row, 2).number_format, "$#,##0.00")
        shares_row = [r[0] for r in g].index("Weighted average diluted") + 1
        self.assertEqual(ws.cell(shares_row, 2).number_format, "#,##0")

    def test_quarterly_bare_diluted_renders_as_eps(self):
        import data.sec_statements as S
        stmt = {"statement": {"periods": ["Q2'26", "Q1'26"], "rows": [
                    {"label": "Diluted", "header": False, "kind": "pershare",
                     "values": [0.88, 0.83]}]},
                "filings": [META_10Q], "meta": META_10Q}
        with mock.patch.object(S, "as_reported_statement_multiquarter",
                               lambda cik, stype, n_quarters=12: stmt):
            self.CS._render_company_statement_quarterly(TICKER, "income", CIK, INFO)
        self.assertIn(">$0.88<", self.htmls[0])
        self.assertIn(">$0.83<", self.htmls[0])

    def test_line_kind_prefers_type_then_label(self):
        k = self.FS._cr_line_kind
        self.assertEqual(k({"label": "Basic", "kind": "pershare"}), "eps")
        self.assertEqual(k({"label": "Basic", "kind": "shares"}), "shares")
        # a typed monetary row wins over a misleading label
        self.assertEqual(k({"label": "Dividends paid per share class", "kind": "monetary"}),
                         "usd")
        # untyped (cached pre-fix statements, older filers): label fallback
        self.assertEqual(k({"label": "Diluted earnings per share"}), "eps")
        self.assertEqual(k({"label": "Diluted (in shares)"}), "shares")
        self.assertEqual(k({"label": "Net income"}), "usd")
        self.assertEqual(k({"label": "Ratio", "kind": "other"}), "usd")

    def test_onb_stated_value_common_stock_is_compact(self):
        # REVIEW-2026-09-24 P2-6: ONB's equity caption mentions "per share";
        # its XBRL kind is monetary, so it is $-compact, never "$389,662,000.00".
        import data.sec_statements as S
        label = ("Common stock, no par value, $1.00 per share stated value, 600,000 "
                 "shares authorized, 389,662 and 318,980 shares issued and outstanding")
        stmt = {"statement": {"periods": ["2025-12-31", "2024-12-31"], "rows": [
                    {"label": "Shareholders' equity", "header": True, "values": []},
                    {"label": label, "header": False, "kind": "monetary",
                     "values": [389_662_000.0, 318_980_000.0]}]},
                "filings": [META_10K], "meta": META_10K}
        with mock.patch.object(S, "as_reported_statement_multiyear",
                               lambda cik, stype, n_years=5: stmt):
            self.CS._render_company_statement_annual(TICKER, "balance", CIK, INFO)
        wb, ws, _kw = self._book()
        html = self.htmls[0]
        self.assertIn(">$389.7M<", html)
        self.assertIn(">$319.0M<", html)
        self.assertNotIn("389,662,000", html)
        row = next(r for r in self._grid(ws) if r[0] == label)
        self.assertEqual(row[1:], [318_980_000, 389_662_000])   # export: raw dollars

    def test_fmt_hand_values(self):
        f = self.FS._cr_fmt
        self.assertEqual(f("eps", 3.07), "$3.07")
        # Statement table: accounting parens (owner rule 2026-09-30), never "$-0.57".
        self.assertEqual(f("eps", -0.57), "($0.57)")
        self.assertEqual(f("shares", 66_900_000), "66.9M")
        self.assertEqual(f("usd", None), "")


class TestRestatedAndLegacyColumns(_CrExportSite):
    """REVIEW-2026-09-24 P1-6 (BBT): a restated cell shows the later value,
    marked, with the as-filed figure in its click-through; a line the
    restating filing did not re-report is n/a (never the superseded figure);
    a legacy-registrant column is marked in the header and named in a caption
    and on the export's Source sheet. Values are Beacon's real ones."""

    LEGACY = "Berkshire Hills Bancorp (legacy)"
    NOTE_NI = {"original": -50_240_000.0, "original_source": "10-Q filed 2025-11-10",
               "original_url": "https://www.sec.gov/q3.htm", "original_entity": "",
               "restated_in": "10-K filed 2026-03-02",
               "restated_url": "https://www.sec.gov/k25.htm", "not_re_reported": False}
    NOTE_PROV = {**NOTE_NI, "original": 87_496_000.0, "not_re_reported": True}
    NOTE_Q2 = {**NOTE_NI, "original": 30_366_000.0, "original_entity": LEGACY,
               "original_source": "10-Q filed 2025-08-11",
               "restated_in": "10-Q filed 2026-08-07"}
    STMT = {"statement": {
        "periods": ["Q3'25", "Q2'25", "Q2'24"],
        "period_entity": [None, None, LEGACY],
        "period_notes": ["Restated in 10-K filed 2026-03-02: lines it re-reported show "
                         "the restated value; lines it did not re-report are n/a.",
                         None, None],
        "rows": [
            {"label": "Provision for credit losses on loans", "header": False,
             "kind": "monetary", "values": [None, 6_997_000, None],
             "restated": [NOTE_PROV, None, None]},
            {"label": "Net income", "header": False, "kind": "monetary",
             "values": [-4_221_000, 22_026_000, 24_025_000],
             "restated": [NOTE_NI, NOTE_Q2, None]},
        ]},
        "filings": [META_10Q], "meta": META_10Q}

    def test_marks_click_through_captions_and_export(self):
        import json
        import re
        import data.sec_statements as S
        captions = []
        rec = _StubSt()
        rec.caption = lambda text, **k: captions.append(text)
        with mock.patch.object(S, "as_reported_statement_multiquarter",
                               lambda cik, stype, n_quarters=12: self.STMT), \
                mock.patch.object(self.CS, "st", rec):
            self.CS._render_company_statement_quarterly(TICKER, "income", CIK, INFO)
        wb, ws, _kw = self._book()
        html = self.htmls[0]
        # legacy column marked in the header (oldest → newest)
        self.assertIn(">Q2&#x27;24 ‡<", html)         # html-escaped apostrophe
        self.assertIn(">Q3&#x27;25<", html)
        # restated values shown, marked, clickable; the unrestated legacy cell is plain
        self.assertRegex(html, r'data-cid="r1c2">\(\$4\.2M\)\*<')
        self.assertRegex(html, r'data-cid="r1c1">\$22\.0M\*<')
        self.assertIn('<td class="val">$24.0M</td>', html)
        # the line the 10-K did not re-report: n/a, clickable, never $87.5M
        self.assertRegex(html, r'data-cid="r0c2">n/a\*<')
        self.assertNotIn("$87.5M<", html)
        cells = json.loads(re.search(r"const CELLS = (\{.*?\});\n", html).group(1))
        ni = cells["r1c2"]
        self.assertEqual(ni["terms"][0]["val"], "($50.2M)")
        self.assertEqual(ni["terms"][0]["sub"], "10-Q filed 2025-11-10")
        self.assertIn("restated in 10-K filed 2026-03-02", ni["op"])
        self.assertEqual(ni["link"], "https://www.sec.gov/k25.htm")
        self.assertIn(self.LEGACY, cells["r1c1"]["terms"][0]["sub"])
        self.assertIn("does not re-report this line", cells["r0c2"]["op"])
        self.assertEqual(cells["r0c2"]["terms"][0]["val"], "$87.5M")
        # captions name the legacy entity and the column caveat
        self.assertTrue(any("Q2'24" in c and self.LEGACY in c for c in captions))
        self.assertTrue(any(c.startswith("Q3'25: Restated in 10-K") for c in captions))
        # export: raw values; the superseded line is n/a; Source names the legacy column
        g = self._grid(ws)
        self.assertEqual(g[0], ["Line item", "Q2'24 ‡", "Q2'25", "Q3'25"])
        self.assertEqual(g[2], ["Net income", 24_025_000, 22_026_000, -4_221_000])
        self._assert_na(ws, "D2")
        self.assertEqual(self._source(wb)["Legacy-registrant columns (‡)"],
                         f"Q2'24 ‡: {self.LEGACY}")

    def test_trend_series_drop_the_legacy_columns(self):
        t = self.CS._trend_stmt(self.STMT["statement"])
        self.assertEqual(t["periods"], ["Q3'25", "Q2'25"])
        ni = next(r for r in t["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"], [-4_221_000, 22_026_000])
        plain = {"periods": ["Q1'26"], "rows": []}
        self.assertIs(self.CS._trend_stmt(plain), plain)


class TestQuarterlyBalanceSheet(_CrExportSite):
    """Site: _render_company_statement_quarterly (10-Q stitch, balance)."""

    def test_quarter_labels_and_underivable_quarter(self):
        import data.sec_statements as S
        with mock.patch.object(S, "as_reported_statement_multiquarter",
                               lambda cik, stype, n_quarters=12: BALANCE_QUARTERLY):
            self.CS._render_company_statement_quarterly(TICKER, "balance", CIK, INFO)
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
