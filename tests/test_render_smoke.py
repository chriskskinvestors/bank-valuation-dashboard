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
    return st


# Must survive curation (reputable source + relevance keyword) so the
# HTML-escaping row loop actually executes — that loop is the code that
# crashed production on 2026-06-12.
FAKE_NEWS = [{
    "headline": 'Fed officials <b>"A&B"</b> signal rates & inflation outlook',
    "url": "https://example.com/a?x=1&y=2",
    "source_name": "Reuters",
    "published_at": "2026-06-12T09:00:00",
}]

FAKE_PRINTS = [
    {"date": "2026-06-13", "name": "CPI <YoY> & Core", "kind": "print",
     "importance": "high"},
    {"date": "2026-06-17", "name": "FOMC Decision", "kind": "fomc",
     "importance": "high"},
]


class TestHomeRendersPopulated(unittest.TestCase):
    """Home sections must survive real-shaped, HTML-hostile content."""

    @classmethod
    def setUpClass(cls):
        _install_streamlit_stub()
        import data.events as ev
        ev.get_topic_news = lambda cat, hours=24, limit=6: list(FAKE_NEWS)
        import data.macro_calendar as mc
        mc.get_upcoming_prints = lambda days=7: list(FAKE_PRINTS)
        import importlib
        import ui.home
        cls.home = importlib.reload(ui.home)
        cls.home._collect_earnings_alerts = lambda w: []

    def test_overnight_breaking_with_items(self):
        self.home._render_overnight_breaking()

    def test_rates_strip_with_values(self):
        # Populated FRED points + KRE quote — no live calls
        self.home._fred_points = lambda sid: (4.25, 4.23, 4.10)
        import data.price_cache_store as pcs
        pcs.get_prices = lambda tickers, max_age_s=None: {
            "KRE": {"price": 73.10, "prev_close": 72.35, "change_pct": 1.04}}
        self.home._render_rates_strip()

    def test_todays_calendar_with_prints(self):
        self.home._render_todays_calendar([])

    def test_pins_the_esc_regression(self):
        # Removing the _esc import must reproduce the production crash —
        # proves this test actually exercises the failing lines.
        saved = self.home._esc
        try:
            del self.home._esc
            with self.assertRaises(NameError):
                self.home._render_overnight_breaking()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
