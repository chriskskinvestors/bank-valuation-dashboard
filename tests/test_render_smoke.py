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
    sys.modules["streamlit"] = st
    return st


FAKE_NEWS = [{
    "headline": 'Bank <b>"A&B"</b> beats — NII up 5% & guides higher',
    "url": "https://example.com/a?x=1&y=2",
    "source_name": "Reuters <wire>",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
