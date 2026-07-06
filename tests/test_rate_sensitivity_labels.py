"""
Pins AUDIT-2026-07-02 finding #16 (ui/rate_sensitivity.py):

The 5Y−3M curve-slope card's regime label used a truthiness chain
(`curve_5y_3m and ...`), so BOTH the None fallback (set on any FRED
exception) AND an exactly-flat 0.00pp slope fell through to "Inverted".
During a FRED outage the card rendered "— (Inverted)" — a confident
wrong regime label. House rule: n/a-over-guess — an outage renders as
unavailable, never a regime call.

Pinned here via the extracted pure helper `_slope_regime` plus the
rendered card value from `_render_rate_context`:
  1. slope None  -> no regime label at all; card shows bare "—"
  2. slope 0.0   -> "Flat" (the card's existing |slope| <= 0.5 vocabulary)
  3. negative slope beyond -0.5 -> "Inverted" still works

Run: python -m unittest tests.test_rate_sensitivity_labels
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub streamlit before importing ui modules (house pattern —
# see tests/test_sec_8k_adapter.py / tests/test_render_smoke.py).
_st = types.ModuleType("streamlit")


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return _noop


def _noop(*a, **k):
    return None


def _cache(*a, **k):
    if a and callable(a[0]):
        return a[0]
    return lambda f: f


_st.cache_data = _cache
_st.cache_resource = _cache
_st.fragment = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.columns = lambda spec, **k: [
    _Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
_st.container = lambda *a, **k: _Ctx()
_st.popover = lambda *a, **k: _Ctx()
_st.expander = lambda *a, **k: _Ctx()
_st.spinner = lambda *a, **k: _Ctx()
_st.session_state = {}
_st.query_params = {}
for _name in ("markdown", "caption", "write", "info", "warning", "error",
              "divider", "metric", "dataframe", "plotly_chart", "button",
              "download_button", "toggle", "rerun", "html", "subheader"):
    setattr(_st, _name, _noop)
_st.checkbox = lambda *a, **k: bool(k.get("value", False))
_st.radio = lambda label, options=None, **k: (options[0] if options else None)
_st.selectbox = lambda label, options=None, **k: (options[0] if options else None)
_st.segmented_control = lambda label, options=None, **k: (
    k["default"] if k.get("default") is not None
    else (options[0] if options else None))
_st.slider = lambda *a, **k: k.get("value", 0)
_st.tabs = lambda labels, **k: [_Ctx() for _ in labels]
_comp_pkg = types.ModuleType("streamlit.components")
_comp_v1 = types.ModuleType("streamlit.components.v1")
_comp_v1.html = _noop
_comp_pkg.v1 = _comp_v1
_st.components = _comp_pkg
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _comp_pkg)
sys.modules.setdefault("streamlit.components.v1", _comp_v1)

from ui.rate_sensitivity import _render_rate_context, _slope_regime  # noqa: E402


class TestSlopeRegime(unittest.TestCase):
    """The pure label decision — explicit None checks, not truthiness."""

    def test_none_slope_has_no_regime_label(self):
        # FRED outage: _load fallback sets curve_5y_3m = None. Must NOT
        # produce any regime call — especially not "Inverted".
        self.assertIsNone(_slope_regime(None))

    def test_exactly_flat_zero_is_flat_not_inverted(self):
        # 0.0 is falsy — the old truthiness chain sent it to "Inverted".
        self.assertEqual(_slope_regime(0.0), "Flat")

    def test_negative_slope_still_inverted(self):
        self.assertEqual(_slope_regime(-1.25), "Inverted")

    def test_steep_and_boundaries_unchanged(self):
        # Existing vocabulary: > 0.5 Steep, |x| <= 0.5 Flat, else Inverted.
        self.assertEqual(_slope_regime(1.0), "Steep")
        self.assertEqual(_slope_regime(0.5), "Flat")
        self.assertEqual(_slope_regime(-0.5), "Flat")
        self.assertEqual(_slope_regime(-0.51), "Inverted")


class TestSlopeCardRendering(unittest.TestCase):
    """The card value string actually rendered by _render_rate_context."""

    def _slope_card_value(self, curve_5y_3m, t3m=None, t5=None):
        captured = {}

        def _capture(cards, **k):
            captured["cards"] = cards

        with patch("ui.source_trace.render_traceable_cards", _capture):
            _render_rate_context(None, t3m, t5, curve_5y_3m)
        slope_cards = [c for c in captured["cards"]
                       if c["label"] == "5Y − 3M Slope"]
        self.assertEqual(len(slope_cards), 1)
        return slope_cards[0]["value"]

    def test_outage_renders_dash_without_regime(self):
        val = self._slope_card_value(None)
        self.assertEqual(val, "—")
        self.assertNotIn("Inverted", val)
        self.assertNotIn("(", val)  # no regime parenthetical at all

    def test_zero_slope_renders_flat(self):
        val = self._slope_card_value(0.0, t3m=4.0, t5=4.0)
        self.assertEqual(val, "+0.00pp  (Flat)")
        self.assertNotIn("Inverted", val)

    def test_negative_slope_renders_inverted(self):
        val = self._slope_card_value(-1.10, t3m=5.3, t5=4.2)
        self.assertEqual(val, "-1.10pp  (Inverted)")


if __name__ == "__main__":
    unittest.main()
