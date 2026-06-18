"""
Render smoke test: UI sections must render with POPULATED data.

Pins the 2026-06-12 production crash: ui/home.py called _esc() without
importing it. Local verification only exercised the empty branch (no
topic news locally), so the NameError shipped and crashed Home for
every user. This test stubs streamlit and feeds the renderers fake
populated data — including HTML-special characters — so the code paths
that only run with real content actually execute.

Run: python tests/test_render_smoke.py
CI: runs in the deploy workflow BEFORE the container build.
"""
from __future__ import annotations

import sys
import types
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
warnings.filterwarnings("ignore")


def _install_streamlit_stub():
    """Minimal no-op streamlit so render functions execute headlessly."""
    st = types.ModuleType("streamlit")

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getattr__(self, name):
            return _noop

    def _noop(*a, **k):
        return None

    def _columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    def _cache(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

    st.cache_data = _cache
    st.cache_resource = _cache
    st.columns = _columns
    st.container = lambda *a, **k: _Ctx()
    st.popover = lambda *a, **k: _Ctx()
    st.expander = lambda *a, **k: _Ctx()
    st.spinner = lambda *a, **k: _Ctx()
    st.session_state = {}
    st.query_params = {}
    for name in ("markdown", "caption", "write", "info", "warning", "error",
                 "divider", "metric", "dataframe", "plotly_chart", "button",
                 "download_button", "toggle", "rerun", "html"):
        setattr(st, name, _noop)
    st.checkbox = lambda *a, **k: bool(k.get("value", False))
    st.radio = lambda label, options=None, **k: (options[0] if options else None)
    # Above-the-fold native selector widgets (segmented_control / selectbox)
    # and the @st.fragment decorator the redesigned Home grid uses.
    st.segmented_control = lambda label, options=None, **k: (
        k["default"] if k.get("default") is not None
        else (options[0] if options else None))
    st.selectbox = lambda label, options=None, **k: (options[0] if options else None)
    st.fragment = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    st.subheader = _noop
    # streamlit.components.v1 — iframe tables (financials_statements,
    # capital walk) import it; html is replaced per-test to capture output.
    comp_pkg = types.ModuleType("streamlit.components")
    comp_v1 = types.ModuleType("streamlit.components.v1")
    comp_v1.html = _noop
    comp_pkg.v1 = comp_v1
    st.components = comp_pkg
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = comp_pkg
    sys.modules["streamlit.components.v1"] = comp_v1
    # ui.financials_statements binds `st`/`components` at import time. Each call
    # here builds FRESH stub module objects, so a render module imported under
    # an earlier class's stub would stay pinned to it — and later classes' own
    # per-test html capture (patched onto THIS stub) would never reach the
    # render path, producing empty output. Reload it so its module-level
    # streamlit bindings always track the current stub. (Makes the test classes
    # order-independent regardless of which one imports the module first.)
    if "ui.financials_statements" in sys.modules:
        import importlib
        importlib.reload(sys.modules["ui.financials_statements"])
    return st


# Populated above-the-fold feed rows with HTML-hostile content so the
# _esc()-escaping loop in _af_feed_table actually executes — that loop is the
# exact code shape that crashed production on 2026-06-12 (NameError: _esc
# used without import; local checks only ever hit the empty branch).
FAKE_FEED = [
    {"tag": "M&A", "cls": "ma", "tk": "BANR",
     "head": 'Acme <b>"A&B"</b> to acquire rival & expand footprint',
     "ts": "2026-06-15T09:00:00"},
    {"tag": "8-K", "cls": "k", "tk": None,
     "head": "Q2 results <released> & 2026 outlook raised", "ts": None},
]

# Macro prints for the Calendar pane: name is HTML-hostile and each carries
# the scheduled time the redesign added (8:30 ET prints / 2:00 ET FOMC).
FAKE_PRINTS = [
    {"date": "2026-06-17", "name": "FOMC Decision <hawkish>", "kind": "fomc",
     "importance": "high", "time": "2:00 ET"},
    {"date": "2026-06-13", "name": "CPI <YoY> & Core", "kind": "print",
     "importance": "high", "time": "8:30 ET"},
]


class TestHomeRendersPopulated(unittest.TestCase):
    """The above-the-fold panes must survive real-shaped, HTML-hostile
    content. Pins the 2026-06-12 production crash: home called _esc() without
    importing it, and local verification only hit the empty branch. The
    redesign's _af_feed_table has the same shape — it _esc()-escapes every
    populated row — so we drive it with hostile items and demand the NameError
    when the import is removed. The pin calls the pane DIRECTLY: the page entry
    _render_above_fold wraps panes in _af_safe's try/except, which would
    otherwise swallow the very NameError this test exists to catch."""

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import importlib
        import ui.home
        cls.home = importlib.reload(ui.home)

    def test_feed_pane_escapes_populated_content(self):
        self.home._af_feed_items = lambda w: list(FAKE_FEED)
        h = self.home._af_feed_table(["BANR"])
        self.assertIn("&amp;", h)            # & escaped
        self.assertIn("&lt;b&gt;", h)        # <b> escaped, not interpreted
        self.assertNotIn("<b>", h)
        self.assertIn("acquire rival", h)    # both rows rendered

    def test_calendar_pane_with_prints(self):
        import data.estimates as est
        import data.macro_calendar as mc
        saved = (est.fetch_earnings_calendar, mc.get_upcoming_prints)
        try:
            est.fetch_earnings_calendar = lambda w: []
            mc.get_upcoming_prints = lambda days=7: list(FAKE_PRINTS)
            h = self.home._af_calendar_table([])
        finally:
            est.fetch_earnings_calendar, mc.get_upcoming_prints = saved
        self.assertIn("8:30 ET", h)          # redesign's scheduled-time column
        self.assertIn("2:00 ET", h)
        self.assertIn("&lt;YoY&gt;", h)      # macro name HTML-escaped
        self.assertNotIn("<YoY>", h)

    def test_above_fold_integration_renders(self):
        # End-to-end grid assembly. Network sources are mocked off; every
        # other pane reads local cache/snapshots, and _af_safe isolates any
        # failure into its own error pane — the call itself must not raise.
        import data.fmp_client as fmp
        self.home._af_feed_items = lambda w: list(FAKE_FEED)
        self.home._fred_points = lambda sid: (None, None, None)
        saved = (fmp.get_history, fmp.get_quote_batch)
        try:
            fmp.get_history = lambda *a, **k: None
            fmp.get_quote_batch = lambda *a, **k: {}
            self.home._render_above_fold([], ["BANR"])
        finally:
            fmp.get_history, fmp.get_quote_batch = saved

    def test_pins_the_esc_regression(self):
        # Removing the _esc import must reproduce the production crash on a
        # POPULATED feed pane — proves the escaping lines actually execute.
        self.home._af_feed_items = lambda w: list(FAKE_FEED)
        saved = self.home._esc
        try:
            del self.home._esc
            with self.assertRaises(NameError):
                self.home._af_feed_table(["BANR"])
        finally:
            self.home._esc = saved


class TestCapitalWalkRendersPopulated(unittest.TestCase):
    """RC-R capital walk (ui/capital_dynamics._render_rcr_capital_walk) must
    render with POPULATED stored detail — the table loop, click-through
    builders and ratio math actually execute. Values are Banner Bank's filed
    12/31/2025 call report ($000), the same fixture as
    tests/test_audit_regressions.TestRcrCapitalDetail, in the stored-dict
    shape get_rcr_capital_detail / get_stored_rcr_detail return."""

    BANNER_DETAIL = {
        "reporting_period": "12/31/2025", "rssd_id": 352772,
        "common_stock_surplus": None, "retained_earnings": None,
        "aoci": None, "cet1_minority_interest": None,
        "cet1_before_adjustments": 1_951_461.0,
        "goodwill_deduction": 370_753.0,
        "other_intangibles_deduction": 2_237.0,
        "dta_deduction": 6_912.0,
        "aoci_adj_unrealized_afs": -213_012.0,
        "aoci_adj_afs_preferred": None, "aoci_adj_cash_flow_hedges": None,
        "aoci_adj_pension": None, "aoci_adj_htm": None,
        "cet1": 1_784_571.0,
        "additional_tier1": 0.0,
        "tier1": 1_784_571.0,
        "t2_instruments": 0.0,
        "t2_nonqualifying_instruments": None, "t2_minority_interest": None,
        "t2_allowance": 173_048.0,
        "tier2": 173_048.0,
        "total_capital": 1_957_619.0,
        "rwa": 13_841_345.0,
        # Derived walk lines (get_rcr_capital_detail semantics)
        "intangibles_deduction": 372_990.0,
        "aoci_adjustment": 213_012.0,
        "other_cet1_adjustments": 0.0,
        "t2_other": 0.0,
    }

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.capital_dynamics
        cls.cd = ui.capital_dynamics

    def _render(self, stored):
        """Run the walk against a fake store; returns captured iframe HTML
        and st.info messages."""
        comp_v1 = sys.modules["streamlit.components.v1"]
        st = sys.modules["streamlit"]
        import data.call_report_store as crs
        import data.bank_mapping as bm
        captured_html, infos = [], []
        saved = (comp_v1.html, st.info, crs.get_stored_rcr_detail,
                 bm.get_name, self.cd.get_fdic_cert)
        try:
            comp_v1.html = lambda html, **k: captured_html.append(html)
            st.info = lambda msg, *a, **k: infos.append(msg)
            crs.get_stored_rcr_detail = lambda cert, quarters=8: list(stored)
            bm.get_name = lambda t: "Banner Bank"
            self.cd.get_fdic_cert = lambda t: 28489
            self.cd._render_rcr_capital_walk("BANR")
        finally:
            (comp_v1.html, st.info, crs.get_stored_rcr_detail,
             bm.get_name, self.cd.get_fdic_cert) = saved
        return captured_html, infos

    def test_walk_renders_banner_values(self):
        html, infos = self._render([dict(self.BANNER_DETAIL)])
        self.assertEqual(len(html), 1, "walk table iframe was not rendered")
        self.assertEqual(infos, [])
        h = html[0]
        # Provenance label is non-negotiable (bank-sub vs holdco).
        self.assertIn("bank subsidiary (call report)", h)
        # Cell values: derived intangibles line ($372,990k → $373.0M) and a
        # filed total — proves the row loop and builders actually ran.
        self.assertIn("$373.0M", h)          # less: intangibles
        self.assertIn("372,990", h)          # click-through term ($000)
        self.assertIn("$1.96B", h)           # total capital 1,957,619k
        # Computed ratios = component ÷ RWA × 100 (hand-checked):
        # 1,784,571 ÷ 13,841,345 = 12.89%; 1,957,619 ÷ 13,841,345 = 14.14%
        self.assertIn("12.89%", h)
        self.assertIn("14.14%", h)
        # Click-through provenance: verified RC-R item + MDRM code present.
        self.assertIn("item 5 (MDRM P840)", h)
        self.assertIn("MDRM P841", h)
        # AOCI add-back is positive (losses added back), never the raw −213,012.
        self.assertIn("$213.0M", h)
        # Column header is the quarter label, newest right.
        self.assertIn("Q4 '25", h)

    def test_no_stored_rows_renders_note_not_empty_table(self):
        html, infos = self._render([])
        self.assertEqual(html, [], "must not render a table with no data")
        self.assertTrue(any("RC-R capital walk unavailable" in m for m in infos),
                        f"expected the honest-gap note, got: {infos}")


class TestHoldcoCapitalRendersPopulated(unittest.TestCase):
    """The SEC-sourced holding-company capital block
    (ui/capital_dynamics._render_holdco_capital) must render the $ capital
    amounts alongside the four ratios, and the regulatory-capital WALK only
    when the CET1 build reconciles. Values are Regions-shaped (FY2025 10-K):
    CET1 $13.49B / 10.89%, Tier 1 $14.86B / 11.99%, Total $17.20B / 13.89%,
    leverage 9.68%, RWA $123.9B."""

    META = {"form": "10-K", "date": "2026-02-24", "accession": "acc", "doc": "rf.htm"}
    # A reconciling walk: common 19.00B − intangibles (5.733+0.140) ≈ CET1
    # band; AOCI retained (opt-in). Sub-debt 1.00B tagged.
    CAP = {"2025-12-31": {
        "cet1_ratio": 0.1089, "t1_ratio": 0.1199, "total_ratio": 0.1389,
        "lev_ratio": 0.0968, "cet1_cap": 13.49e9, "t1_cap": 14.859e9,
        "tier2_cap": 2.346e9, "total_cap": 17.205e9, "rwa": 123.9e9,
        "_anchored": True, "_walk_reconciles": True,
        "_walk": {"common_equity": 19.00e9, "goodwill": 5.733e9,
                  "other_intangibles": 0.140e9, "aoci": -1.535e9,
                  "subordinated_debt": 1.00e9, "intangibles": 5.873e9,
                  "aoci_treatment": "included"}}}

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.capital_dynamics
        cls.cd = ui.capital_dynamics

    def _render(self, res):
        # Patch the streamlit module ui.capital_dynamics is BOUND to (cd.st) —
        # not sys.modules["streamlit"], which may be a newer stub object after
        # another test class reinstalled it (see _install_streamlit_stub).
        st = self.cd.st
        import data.sec_filing_scraper as sfs
        md = []
        saved = (st.markdown, st.caption, st.subheader,
                 self.cd.get_cik, self.cd.get_fdic_cert, sfs.holdco_capital_for)
        try:
            st.markdown = lambda s, *a, **k: md.append(str(s))
            st.caption = lambda s, *a, **k: md.append(str(s))
            st.subheader = lambda s, *a, **k: md.append(str(s))
            self.cd.get_cik = lambda t: 1281761
            self.cd.get_fdic_cert = lambda t: 12368
            sfs.holdco_capital_for = lambda cik, cert=None: res
            self.cd._render_holdco_capital("RF")
        finally:
            (st.markdown, st.caption, st.subheader,
             self.cd.get_cik, self.cd.get_fdic_cert,
             sfs.holdco_capital_for) = saved
        return "\n".join(md)

    def test_amounts_and_ratios_render(self):
        h = self._render({"meta": self.META, "capital": self.CAP})
        # The four ratios.
        self.assertIn("10.89%", h)
        self.assertIn("11.99%", h)
        self.assertIn("13.89%", h)
        self.assertIn("9.68%", h)
        # The $ capital amounts alongside the ratios (the TASK-1 confirmation).
        self.assertIn("$13.49B", h)    # CET1 capital
        self.assertIn("$14.86B", h)    # Tier 1 capital
        self.assertIn("$2.35B", h)     # Tier 2 capital
        self.assertIn("$17.20B", h)    # Total capital
        self.assertIn("$123.90B", h)   # RWA

    def test_walk_renders_when_reconciled(self):
        h = self._render({"meta": self.META, "capital": self.CAP})
        self.assertIn("Regulatory capital walk", h)
        self.assertIn("Total common equity", h)
        self.assertIn("$19.00B", h)                 # common equity
        # Additional Tier 1 = t1 − cet1 = 14.859 − 13.49 = 1.369 → $1.37B.
        self.assertIn("$1.37B", h)
        # Other Tier 2 = tier2 − sub-debt = 2.346 − 1.00 = 1.346 → $1.35B.
        self.assertIn("$1.35B", h)
        # AOCI retained (opt-in) → no removal step.
        self.assertIn("in CET1", h)

    def test_walk_na_when_not_reconciled(self):
        cap = {"2025-12-31": dict(self.CAP["2025-12-31"],
                                  _walk_reconciles=False)}
        h = self._render({"meta": self.META, "capital": cap})
        # Ratios + amounts still render; the walk does not.
        self.assertIn("$13.49B", h)
        self.assertNotIn("Total common equity", h)
        self.assertIn("does not tag a machine-readable", h)


class TestIncomeStatementRiRendersPopulated(unittest.TestCase):
    """SNL Income Statement RI-E sub-block + FTE NII rows must render with
    POPULATED stored detail — the spec augmentation, new cell kinds and FTE
    math actually execute. Fixtures are Banner-shaped (cert 28489,
    12/31/2025): Schedule RI tax-exempt income RIAD4313 = 15,532 /
    RIAD4507 = 14,865 ($000) → FTE adjustment = 30,397 × 0.21/0.79 =
    8,080.2 ($000, hand-computed); RI-E itemizes only data processing
    (C017 = 30,787) plus one labeled income write-in ('Merchant Fee
    Income', 2,186) — every other preprinted line below threshold (None)."""

    BANNER_RI = {
        "reporting_period": "12/31/2025", "rssd_id": 352772,
        "tax_exempt_loan_income": 15_532.0,
        "tax_exempt_loan_income_usd": 15_532_000.0,
        "tax_exempt_sec_income": 14_865.0,
        "tax_exempt_sec_income_usd": 14_865_000.0,
    }

    BANNER_RIE = {
        "reporting_period": "12/31/2025", "rssd_id": 352772,
        "data_processing": 30_787.0, "data_processing_usd": 30_787_000.0,
        "marketing_professional": None, "directors_fees": None,
        "printing_supplies": None, "postage": None, "legal": None,
        "fdic_assessments": None, "accounting_auditing": None,
        "consulting_advisory": None, "atm_interchange": None,
        "telecommunications": None,
        "income_writeins": [{"label": "Merchant Fee Income",
                             "value": 2_186.0, "value_usd": 2_186_000.0}],
        "expense_writeins": [],
    }

    # One FY2025 record; income fields are filed YTD (full year at Dec 31),
    # matching the RI/RI-E YTD convention. NII = 700,000 − 200,000 = 500,000
    # → NII (FTE) = 500,000 + 8,080.2 = 508,080.2 ($000) → $508.1M.
    HIST_ROW = {
        "REPDTE": "2025-12-31", "INTINC": 700_000, "EINTEXP": 200_000,
        "NONII": 80_000, "NONIX": 300_000, "ESAL": 150_000,
        "EOTHNINT": 60_000, "NETINC": 90_000,
    }

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.financials_statements
        cls.fs = ui.financials_statements

    def _render(self, ri_rows, rie_rows):
        """Render the income statement against fake FDIC history + RI store;
        returns captured iframe HTML list."""
        import pandas as pd
        comp_v1 = sys.modules["streamlit.components.v1"]
        import data.call_report_store as crs
        import data.fdic_client as fc
        captured = []
        saved = (comp_v1.html, self.fs.get_bank_info,
                 fc.get_historical_financials,
                 crs.get_stored_ri_detail, crs.get_stored_rie_detail)
        try:
            comp_v1.html = lambda html, **k: captured.append(html)
            self.fs.get_bank_info = lambda t: {
                "name": "Banner Bank", "fdic_cert": 28489, "cik": None}
            fc.get_historical_financials = (
                lambda cert, quarters=36: pd.DataFrame([dict(self.HIST_ROW)]))
            crs.get_stored_ri_detail = lambda cert, quarters=8: list(ri_rows)
            crs.get_stored_rie_detail = lambda cert, quarters=8: list(rie_rows)
            self.fs.render_income_statement("BANR")
        finally:
            (comp_v1.html, self.fs.get_bank_info,
             fc.get_historical_financials,
             crs.get_stored_ri_detail, crs.get_stored_rie_detail) = saved
        return captured

    def test_rie_and_fte_render_banner_values(self):
        html = self._render([dict(self.BANNER_RI)], [dict(self.BANNER_RIE)])
        self.assertEqual(len(html), 1, "statement iframe was not rendered")
        h = html[0]
        # RI-E itemized line: data processing 30,787 ($000) → $30.8M cell +
        # the $000 click-through term.
        self.assertIn("Data processing expenses", h)
        self.assertIn("$30.8M", h)
        self.assertIn("30,787", h)
        # Below-threshold preprinted lines render n/a with the reason — and
        # appear at all because SOME line was itemized.
        self.assertIn("Telecommunications expense", h)
        self.assertIn(">n/a<", h)
        self.assertIn("below the RI-E itemization threshold", h)
        # Labeled income write-in (bank's own filed text).
        self.assertIn("Merchant Fee Income", h)
        self.assertIn("2,186", h)
        # FTE adjustment: 30,397 × 0.21/0.79 = 8,080.2 → $8.1M cell, 8,080
        # ($000) term, labeled with the statutory rate.
        self.assertIn("FTE adjustment", h)
        self.assertIn("$8.1M", h)
        self.assertIn("8,080", h)
        self.assertIn("statutory 21% federal rate", h)
        # NII (FTE) = 500,000 + 8,080.2 = 508,080.2 → $508.1M.
        self.assertIn("Net interest income (FTE)", h)
        self.assertIn("$508.1M", h)
        # Provenance names the schedules.
        self.assertIn("Schedule RI-E", h)
        self.assertIn("RIAD4313", h)
        self.assertIn("RIAD4507", h)

    def test_no_rie_stored_means_no_subblock(self):
        # Bank itemized nothing → no wall of n/a; FTE rows stay (dead cells).
        html = self._render([], [])
        self.assertEqual(len(html), 1)
        h = html[0]
        self.assertNotIn("Data processing expenses", h)
        self.assertNotIn("Merchant Fee Income", h)
        self.assertIn("FTE adjustment", h)

    def test_tax_exempt_not_reported_is_na_not_zero(self):
        # RI detail present but both tax-exempt components absent → n/a with
        # the reason, never a computed $0.
        ri = {"reporting_period": "12/31/2025", "rssd_id": 352772,
              "tax_exempt_loan_income": None, "tax_exempt_sec_income": None}
        html = self._render([ri], [dict(self.BANNER_RIE)])
        h = html[0]
        self.assertIn("tax-exempt income not reported", h)
        self.assertNotIn("$8.1M", h)


class TestBalanceSheetRendersPopulated(unittest.TestCase):
    """SNL Balance Sheet (_BALANCE) must render with POPULATED FDIC history
    across multiple years — the new computed kinds (diff reserve, sum
    subtotals, htm, otherint, residual Other Assets / Other Liabilities,
    growth) and the n/a kind all execute. Fixtures are Banner-shaped
    (cert 28489), two FY columns so the YoY growth rows compute. The newest
    column is hand-checked below."""

    # FY2025 (12/31/2025) Banner Bank call report, $000 (live-verified).
    BANR_FY25 = {
        "REPDTE": "2025-12-31",
        "CHBAL": 422_640, "CHBALI": 239_868, "FREPO": 0, "TRADE": 0,
        "SCAF": 2_016_261, "SCHA": 961_487, "SC": 2_977_863,
        "LNLSGR": 11_764_589, "LNLSNET": 11_604_313, "LNATRESR": 1.3624,
        "ORE": 5_578, "INTAN": 387_214, "INTANGW": 373_121, "INTANMSR": 11_498,
        "MSA": 47_460, "BKPREM": 141_799, "ASSET": 16_347_870,
        "DEP": 13_812_149, "OTHBFHLB": 150_000, "SUBND": 0, "LIAB": 14_396_409,
        "EQPP": 0, "EQTOT": 1_951_461,
    }
    # FY2024 prior column (only the growth-rate fields need to be present).
    BANR_FY24 = {
        "REPDTE": "2024-12-31",
        "CHBAL": 400_000, "CHBALI": 230_000, "FREPO": 0, "TRADE": 0,
        "SCAF": 2_100_000, "SCHA": 1_000_000, "SC": 3_100_000,
        "LNLSGR": 11_386_000, "LNLSNET": 11_230_000, "LNATRESR": 1.37,
        "ORE": 6_000, "INTAN": 390_000, "INTANGW": 373_121, "INTANMSR": 12_000,
        "MSA": 48_000, "BKPREM": 145_000, "ASSET": 16_210_000,
        "DEP": 13_590_000, "OTHBFHLB": 100_000, "SUBND": 0, "LIAB": 14_260_000,
        "EQPP": 0, "EQTOT": 1_950_000,
    }

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.financials_statements
        cls.fs = ui.financials_statements

    def _render(self, hist_rows):
        import pandas as pd
        comp_v1 = self.fs.components
        st = self.fs.st
        import data.fdic_client as fc
        captured = []
        saved = (comp_v1.html, st.radio, self.fs.get_bank_info,
                 fc.get_historical_financials)
        try:
            comp_v1.html = lambda html, **k: captured.append(html)
            st.radio = lambda label, options=None, **k: "Annual"
            self.fs.get_bank_info = lambda t: {
                "name": "Banner Bank", "fdic_cert": 28489, "cik": None}
            fc.get_historical_financials = (
                lambda cert, quarters=36:
                pd.DataFrame([dict(r) for r in hist_rows]))
            self.fs.render_balance_sheet("BANR")
        finally:
            (comp_v1.html, st.radio, self.fs.get_bank_info,
             fc.get_historical_financials) = saved
        return captured

    def test_balance_sheet_renders_banner_values(self):
        html = self._render([dict(self.BANR_FY24), dict(self.BANR_FY25)])
        self.assertEqual(len(html), 1, "balance-sheet iframe was not rendered")
        h = html[0]
        # Computed reserve = LNLSGR − LNLSNET = 11,764,589 − 11,604,313 =
        # 160,276 ($000) → $160.3M.
        self.assertIn("Loan Loss Reserve", h)
        # Reserve = LNLSGR − LNLSNET = 11,764,589 − 11,604,313 = 160,276 ($000),
        # rendered in the table's compact form (like the Income Statement tab).
        self.assertIn("$160.3M", h)
        # Computed Other Assets residual = ASSET − itemized displayed fields;
        # FY2025 residual = $761.1M (positive). (The click-through carries the
        # full formula; we assert the displayed value here — robust against the
        # Unicode minus/÷ glyphs the popup formula uses.)
        self.assertIn("Other Assets", h)
        self.assertIn("$761.1M", h)
        # HTM = filed SCHA (not the SC−SCAF−TRADE residual); the click-through
        # carries the source field and its raw $000 value (961,487).
        self.assertIn("Held to Maturity Securities", h)
        self.assertIn("FDIC field SCHA", h)
        self.assertIn("961,487", h)
        # Subtotal sums render compact; FY2025 cash+securities = $3.40B.
        self.assertIn("» Total Cash & Securities", h)
        self.assertIn("$3.40B", h)
        # Growth rows: first column n/a (no prior), newest column computed.
        self.assertIn("Asset Growth", h)
        self.assertIn("no prior period in view", h)
        # Asset Growth = (16,347,870 / 16,210,000 − 1) × 100 = 0.85%.
        self.assertIn("0.85%", h)
        # n/a lines carry the reason (never a $0).
        self.assertIn("Other Securities", h)
        self.assertIn("not in the FDIC SDI feed", h)
        self.assertIn("Tot Acc Other Comprehensive Inc", h)
        self.assertIn("EQUPTOT is not AOCI", h)
        # Average Balances (FFIEC RC-K) is deferred and NOT shown on this tab.
        self.assertNotIn("Average Balances", h)
        # Common equity = EQTOT − EQPP.
        self.assertIn("Common Equity (incl. NCI)", h)
        # Intangibles breakdown reconciles ON THE FACE of the table: the
        # mortgage-servicing intangible (INTANMSR) gets its own line so
        # Goodwill + CDI + MSR-intangible + Other = » Total Intangible Assets.
        # FY2024 column: INTANGW 373,121 + INTANMSR 12,000 + Other (390,000−
        # 373,121−12,000 = 4,879) = 390,000 = INTAN ($373.1M + $12.0M + $4.9M
        # = $390.0M). FY2025 column carries INTANMSR 11,498 -> $11.5M.
        self.assertIn("Mortgage Servicing Intangible", h)
        self.assertIn("FDIC field INTANMSR", h)   # direct-field source link
        self.assertIn("$12.0M", h)                # INTANMSR, FY2024 column
        self.assertIn("$11.5M", h)                # INTANMSR, FY2025 column
        self.assertIn("$390.0M", h)               # » Total Intangible Assets (INTAN), FY2024

    def test_negative_residual_is_na_not_negative_plug(self):
        # Itemized asset lines forced to exceed ASSET → Other Assets must be
        # n/a + flag, never a silent negative plug. The cell value reads "n/a";
        # the click-through explains why ("itemized lines exceed total") and
        # shows the would-be-negative magnitude only as the explanation — never
        # as the displayed value.
        bad = dict(self.BANR_FY25)
        bad["ASSET"] = 1_000_000   # $1.0B — far below the ~$15.6B itemized sum
        html = self._render([dict(self.BANR_FY24), bad])
        h = html[0]
        self.assertIn("itemized lines exceed total", h)


class TestPerformanceComputedLines(unittest.TestCase):
    """The Performance-Analysis computed kinds added 2026-06-14 (ROACE, ROATE,
    net-opex/assets, cost of funds, cost of debt, residual other-asset yield).
    Driven through the real render path with a single Q1 column whose fields
    were probed LIVE from the FDIC SDI API (Banner cert 28489, 2026-03-31);
    each ratio is hand-checked below and several cross-validate against FDIC's
    own reported ratios (ROACE here == reported ROE since EQPP=0)."""

    # Banner Q1-2026 call report ($000), fields probed live from FDIC SDI.
    BANR_Q1 = {
        "REPDTE": "2026-03-31",
        "NETINC": 56_492, "EQTOT": 1_952_235, "EQPP": 0, "INTAN": 386_768,
        "NONIX": 101_010, "NONII": 20_143, "ASSET": 16_338_071,
        "EINTEXP": 46_670, "EDEP": 45_934, "INTINC": 197_818,
        "ILNDOM": 173_703, "ISC": 22_448, "SC": 2_979_219, "LNLSGR": 11_741_404,
        "DEP": 13_928_915, "FREPP": 115_723, "OTHBFHLB": 0, "ESUBND": 0,
        "ERNAST": 14_819_352,
    }
    # A second bank WITH preferred stock — ROACE must be n/a (needs RI-A divs).
    PFD_Q1 = dict(BANR_Q1, EQPP=200_000)

    _SPEC = [("Perf", [
        ("Return on avg common equity (ROACE)", "roace"),
        ("Return on avg tangible equity (ROATE)", "roate"),
        ("Net operating expense / avg assets", "netopex"),
        ("Cost of funds", "costfunds"),
        ("Cost: borrowings / debt", "costdebt"),
    ])]

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.financials_statements
        cls.fs = ui.financials_statements

    def _render(self, rows):
        import pandas as pd
        comp_v1 = self.fs.components
        st = self.fs.st
        import data.fdic_client as fc
        captured = []
        saved = (comp_v1.html, st.radio, self.fs.get_bank_info,
                 fc.get_historical_financials)
        try:
            comp_v1.html = lambda html, **k: captured.append(html)
            st.radio = lambda label, options=None, **k: "Quarterly"  # show Q1
            self.fs.get_bank_info = lambda t: {
                "name": "Banner Bank", "fdic_cert": 28489, "cik": None}
            fc.get_historical_financials = (
                lambda cert, quarters=36: pd.DataFrame([dict(r) for r in rows]))
            # Custom spec keeps the test to the new kinds (no SEC/FFIEC loads).
            self.fs.render_statement("BANR", "perftest", "Perf", self._SPEC)
        finally:
            (comp_v1.html, st.radio, self.fs.get_bank_info,
             fc.get_historical_financials) = saved
        return captured

    def test_computed_ratios_match_probed_values(self):
        h = self._render([dict(self.BANR_Q1)])[0]
        # Single Q1 column: avg balance = period-end, annualization factor = 4.
        # (FDIC's reported ROE 11.58% uses period-AVERAGE equity; period-end
        # here gives 11.57% — within a few bps, confirming the formula.)
        self.assertIn("11.57%", h)   # ROACE = 56,492*4 / 1,952,235
        self.assertIn("14.43%", h)   # ROATE = 56,492*4 / (1,952,235 - 386,768)
        self.assertIn("1.98%", h)    # Net opex = (101,010-20,143)*4 / 16,338,071
        self.assertIn("1.33%", h)    # Cost of funds = 46,670*4 / (13,928,915+115,723)
        self.assertIn("2.54%", h)    # Cost of debt = (46,670-45,934)*4 / 115,723

    def test_cost_of_debt_na_when_rate_implausible(self):
        # Borrowings shrink to a sliver at period-end → the real interest figure
        # over a tiny balance implies a double-digit "cost of debt" that isn't
        # real. Outside the 0-8% band it must render n/a, not a wrong number.
        bad = dict(self.BANR_Q1, FREPP=20_000)   # 736*4 / 20,000 = 14.7% > 8%
        h = self._render([bad])[0]
        self.assertIn("period-end borrowings make the rate unreliable", h)

    def test_roace_na_when_preferred_outstanding(self):
        # With preferred equity, NI-to-common needs RI-A preferred dividends we
        # don't carry → n/a + flag, never NI/common-equity (which overstates).
        h = self._render([dict(self.PFD_Q1)])[0]
        self.assertIn("preferred dividends not in FDIC SDI feed", h)

    def test_per_share_lines_wire_through(self):
        # Pin the new SEC per-share spec wiring (a key typo would silently blank
        # a line). The VALUES are verified separately against Regions' live SEC
        # XBRL (Basic EPS 2.51, avg diluted 963,000,000 — exact to the filing);
        # here we feed those known values and assert each line renders them.
        import pandas as pd
        import ui.financial_highlights as fh
        PS = {"basic_eps": 2.51, "eps": 2.49, "eps_before_amort": 2.52,
              "avg_diluted_shares": 963_000_000, "shares": 941_907_925,
              "bvps": 17.89, "tbvps": 11.27, "dps": 0.65}
        spec = [("PS", [
            ("Basic EPS", "ps", "basic_eps"),
            ("Diluted EPS before amortization", "ps", "eps_before_amort"),
            ("Avg diluted shares (actual)", "shares", "avg_diluted_shares"),
            ("Common shares outstanding (actual)", "shares", "shares"),
        ])]
        comp_v1 = self.fs.components
        st = self.fs.st
        import data.fdic_client as fc
        captured = []
        saved = (comp_v1.html, st.radio, self.fs.get_bank_info,
                 fc.get_historical_financials, fh._per_share_for_ends)
        try:
            comp_v1.html = lambda html, **k: captured.append(html)
            st.radio = lambda *a, **k: "Annual"
            self.fs.get_bank_info = lambda t: {
                "name": "Regions", "fdic_cert": 12368, "cik": "1281761"}
            fc.get_historical_financials = (
                lambda cert, quarters=36:
                pd.DataFrame([{"REPDTE": "2025-12-31", "ASSET": 1}]))
            fh._per_share_for_ends = lambda cik, ends, quarterly=False: {e: PS for e in ends}
            self.fs.render_statement("RF", "psw", "PS", spec, with_persh=True)
        finally:
            (comp_v1.html, st.radio, self.fs.get_bank_info,
             fc.get_historical_financials, fh._per_share_for_ends) = saved
        h = captured[0]
        self.assertIn("2.51", h)           # Basic EPS
        self.assertIn("2.52", h)           # Diluted EPS before amortization
        self.assertIn("963,000,000", h)    # Avg diluted shares
        self.assertIn("941,907,925", h)    # Common shares outstanding


class TestTableExports(unittest.TestCase):
    """Design-system decision #12: every data table gets an Export action.
    Pins the table_export contract (CSV bytes, .csv filename, widget key)
    and exercises one of the new call sites (peer_rank leaderboard) with
    populated data, asserting the exported CSV carries the UNFORMATTED
    numeric values."""

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.chrome
        cls.chrome = ui.chrome

    def _capture_downloads(self):
        """Patch download_button on the st module ui.chrome is bound to."""
        calls = []
        saved = self.chrome.st.download_button
        self.chrome.st.download_button = (
            lambda label, data, **k: calls.append((label, data, k)))
        return calls, saved

    def test_table_export_emits_csv_download(self):
        import pandas as pd
        calls, saved = self._capture_downloads()
        try:
            df = pd.DataFrame({"Ticker": ["BANR"], "NPL Ratio": [0.42]})
            self.chrome.table_export(df, "peers_BANR", key="exp_peers_BANR")
        finally:
            self.chrome.st.download_button = saved
        self.assertEqual(len(calls), 1, "table_export did not render a button")
        label, data, kw = calls[0]
        self.assertEqual(label, "Export")
        self.assertEqual(kw["file_name"], "peers_BANR.csv")
        self.assertEqual(kw["key"], "exp_peers_BANR")
        self.assertEqual(kw["mime"], "text/csv")
        self.assertIn("BANR", data)
        self.assertIn("0.42", data)

    def test_peer_rank_leaderboard_exports_numeric_csv(self):
        import ui.peer_rank as pr
        calls, saved = self._capture_downloads()
        saved_grp, saved_name = pr.get_peer_group_for_bank, pr.get_name
        try:
            pr.get_peer_group_for_bank = lambda t, m, mode="size": m
            pr.get_name = lambda t: f"{t} Bancorp"
            cohort = [{"ticker": "BANR", "npl_ratio": 0.31},
                      {"ticker": "EWBC", "npl_ratio": 0.55}]
            pr._render_leaderboard("BANR", cohort, "npl_ratio", "size")
        finally:
            pr.get_peer_group_for_bank, pr.get_name = saved_grp, saved_name
            self.chrome.st.download_button = saved
        self.assertEqual(len(calls), 1, "leaderboard export was not rendered")
        _label, data, kw = calls[0]
        self.assertEqual(kw["file_name"], "peer_leaderboard_BANR_npl_ratio.csv")
        self.assertEqual(kw["key"], "exp_peer_leaderboard_BANR_npl_ratio")
        # npl_ratio is lower-is-better → BANR (0.31) ranks #1; values are the
        # raw numerics, not display strings like "0.31%".
        lines = data.strip().splitlines()
        self.assertEqual(lines[0], "Rank,Ticker,Bank,Value")
        self.assertEqual(lines[1], "1,BANR,BANR Bancorp,0.31")
        self.assertEqual(lines[2], "2,EWBC,EWBC Bancorp,0.55")


class TestPerformanceDepositCostRendersPopulated(unittest.TestCase):
    """Performance Analysis deposit-cost split (Schedule RI 2.a / RC-K
    stored detail) must render with POPULATED stored quarters — the
    date-keyed join, YTD de-cumulation, FY quarterly-average mean and the
    reconciliation gate actually execute. Hand-computed pins:
      Q2 CD rate = (30,000 − 14,000) ÷ 1,520,000 × 4 × 100 = 4.21%;
      FY CD rate = 54,368 ÷ mean(1,500,000; 1,520,000; 1,540,000;
      1,539,845 = 1,524,961.25) × 100 = 3.5652% → 3.57%."""

    STORED = [   # newest-first, the store's order
        {"reporting_period": "12/31/2025", "rssd_id": 352772,
         "int_cds": 54_368.0, "avg_cds": 1_539_845.0,
         "int_other_ib": 148_172.0, "avg_other_ib": 7_897_276.0,
         "reconciles": True},
        {"reporting_period": "09/30/2025", "rssd_id": 352772,
         "int_cds": 40_000.0, "avg_cds": 1_540_000.0,
         "int_other_ib": 105_000.0, "avg_other_ib": 7_200_000.0,
         "reconciles": True},
        {"reporting_period": "06/30/2025", "rssd_id": 352772,
         "int_cds": 30_000.0, "avg_cds": 1_520_000.0,
         "int_other_ib": 65_000.0, "avg_other_ib": 7_100_000.0,
         "reconciles": True},
        {"reporting_period": "03/31/2025", "rssd_id": 352772,
         "int_cds": 14_000.0, "avg_cds": 1_500_000.0,
         "int_other_ib": 30_000.0, "avg_other_ib": 7_000_000.0,
         "reconciles": True},
    ]

    HIST = [{"REPDTE": d} for d in
            ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")]

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import ui.financials_statements
        cls.fs = ui.financials_statements

    def _render(self, hist_rows, stored, period="Annual"):
        """Render Performance Analysis against fake FDIC history + stored
        deposit-cost rows; returns captured iframe HTML list. Patches the
        module objects ui.financials_statements is BOUND to (fs.components,
        fs.st) — each _install_streamlit_stub() call rebuilds sys.modules
        stubs, so the entries there can be newer objects than fs's."""
        import pandas as pd
        comp_v1 = self.fs.components   # `import streamlit.components.v1 as components`
        st = self.fs.st
        import data.call_report_store as crs
        import data.fdic_client as fc
        captured = []
        saved = (comp_v1.html, st.radio, self.fs.get_bank_info,
                 fc.get_historical_financials,
                 crs.get_stored_deposit_cost_detail)
        try:
            comp_v1.html = lambda html, **k: captured.append(html)
            st.radio = lambda label, options=None, **k: period
            self.fs.get_bank_info = lambda t: {
                "name": "Banner Bank", "fdic_cert": 28489, "cik": None}
            fc.get_historical_financials = (
                lambda cert, quarters=36:
                pd.DataFrame([dict(r) for r in hist_rows]))
            crs.get_stored_deposit_cost_detail = (
                lambda cert, quarters=8: [dict(r) for r in stored])
            self.fs.render_performance_analysis("BANR")
        finally:
            (comp_v1.html, st.radio, self.fs.get_bank_info,
             fc.get_historical_financials,
             crs.get_stored_deposit_cost_detail) = saved
        return captured

    def test_quarterly_decumulation_renders_hand_checked_rates(self):
        html = self._render(self.HIST, self.STORED, period="Quarterly")
        self.assertEqual(len(html), 1, "statement iframe was not rendered")
        h = html[0]
        # Block + provenance labels.
        self.assertIn("bank subsidiary (call report)", h)
        self.assertIn("Cost of CDs (%)", h)
        self.assertIn("Cost of other interest-bearing deposits (%)", h)
        # Q2 CD rate (de-cumulated): (30,000 − 14,000) ÷ 1,520,000 × 400
        # = 4.2105% → 4.21%. Q1 uses YTD directly: 14,000 ÷ 1,500,000 ×
        # 400 = 3.73%; Q2 other-IB: 35,000 ÷ 7,100,000 × 400 = 1.97%.
        self.assertIn("4.21%", h)
        self.assertIn("3.73%", h)
        self.assertIn("1.97%", h)
        # Click-through: full de-cumulation formula + both code sets. The
        # cells dict is embedded via json.dumps (ensure_ascii) — compare
        # against the same escaped form.
        import json as _json
        formula = "(YTD_q − YTD_q−1) ÷ avg_q × 4 × 100"
        self.assertIn(_json.dumps(formula)[1:-1], h)
        self.assertIn("RIADHK03 + RIADHK04", h)
        self.assertIn("RCONHK16 + RCONHK17", h)
        self.assertIn("RIAD4508 + RIAD0093", h)
        self.assertIn("RCON3485 + RCONB563", h)
        # Component $ rows cite the schedules; filed $000 terms present.
        self.assertIn("Schedule RI item 2.a", h)
        self.assertIn("Schedule RC-K", h)
        self.assertIn("54,368", h)
        self.assertIn("1,539,845", h)
        # CDR facsimile link (the house click-through doc pattern).
        self.assertIn("cdr.ffiec.gov", h)

    def test_annual_fy_rate_uses_mean_of_quarterly_averages(self):
        html = self._render(self.HIST, self.STORED, period="Annual")
        h = html[0]
        # FY CD rate = 54,368 ÷ 1,524,961.25 × 100 = 3.5652% → 3.57% —
        # NEVER 54,368 ÷ the Q4-only average (3.53%).
        self.assertIn("3.57%", h)
        self.assertNotIn("3.53%", h)
        self.assertIn("1,524,961", h)   # the mean, in the click-through
        self.assertIn("mean of the four quarterly RC-K averages", h)

    def test_missing_prior_quarter_is_na_never_raw_ytd(self):
        # Only Q3 stored: Q3 can't de-cumulate (no Q2 row) → n/a + reason;
        # raw YTD ÷ avg (40,000 ÷ 1,540,000 × 400 = 10.39%) must NOT show.
        hist = [{"REPDTE": "2025-06-30"}, {"REPDTE": "2025-09-30"}]
        html = self._render(hist, [dict(self.STORED[1])], period="Quarterly")
        h = html[0]
        self.assertIn("prior quarter not ingested", h)
        self.assertIn("cannot de-cumulate YTD", h)
        self.assertNotIn("10.39%", h)

    def test_annual_incomplete_quarters_is_na(self):
        # Only Q4 stored → FY mean impossible → n/a + reason; never the
        # Q4-only-average rate (3.53%).
        html = self._render(self.HIST, [dict(self.STORED[0])],
                            period="Annual")
        h = html[0]
        self.assertIn("incomplete quarterly average history", h)
        self.assertNotIn("3.53%", h)

    def test_reconciles_false_renders_na_split(self):
        # A row whose components don't reconcile must never display ANY
        # split number — rates and $ components all n/a with the reason.
        bad = dict(self.STORED[0]); bad["reconciles"] = False
        html = self._render([{"REPDTE": "2025-12-31"}], [bad],
                            period="Quarterly")
        h = html[0]
        self.assertIn("components do not reconcile to total interest expense", h)
        self.assertNotIn("54,368", h)
        self.assertNotIn("1,539,845", h)
        self.assertNotIn("3.53%", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
