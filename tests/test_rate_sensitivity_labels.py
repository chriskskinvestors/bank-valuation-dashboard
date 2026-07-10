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
    """The pure label decision — explicit None checks, not truthiness.

    Sign convention (AUDIT #36, owner call 2026-07-09): the slope is 3M − 5Y
    (shorter tenor FIRST, matching Home/Macro), so an upward-sloping curve
    reads NEGATIVE (Steep) and an inverted one POSITIVE."""

    def test_none_slope_has_no_regime_label(self):
        # FRED outage: _load fallback sets the slope = None. Must NOT
        # produce any regime call — especially not "Inverted".
        self.assertIsNone(_slope_regime(None))

    def test_exactly_flat_zero_is_flat_not_inverted(self):
        # 0.0 is falsy — the old truthiness chain sent it to "Inverted".
        self.assertEqual(_slope_regime(0.0), "Flat")

    def test_positive_slope_is_inverted_short_minus_long(self):
        # 3M − 5Y = +1.25pp means short rates above long — inverted.
        self.assertEqual(_slope_regime(1.25), "Inverted")

    def test_steep_and_boundaries_short_minus_long(self):
        # Vocabulary under #36: < -0.5 Steep, |x| <= 0.5 Flat, else Inverted.
        self.assertEqual(_slope_regime(-1.0), "Steep")
        self.assertEqual(_slope_regime(-0.5), "Flat")
        self.assertEqual(_slope_regime(0.5), "Flat")
        self.assertEqual(_slope_regime(0.51), "Inverted")


class TestSlopeCardRendering(unittest.TestCase):
    """The card value string actually rendered by _render_rate_context."""

    def _slope_card_value(self, curve_3m_5y, t3m=None, t5=None):
        captured = {}

        def _capture(cards, **k):
            captured["cards"] = cards

        with patch("ui.source_trace.render_traceable_cards", _capture):
            _render_rate_context(None, t3m, t5, curve_3m_5y)
        slope_cards = [c for c in captured["cards"]
                       if c["label"] == "3M − 5Y Slope"]
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

    def test_positive_slope_renders_inverted(self):
        # 3M 5.3% vs 5Y 4.2% → 3M − 5Y = +1.10pp: inverted.
        val = self._slope_card_value(1.10, t3m=5.3, t5=4.2)
        self.assertEqual(val, "+1.10pp  (Inverted)")

    def test_negative_slope_renders_steep(self):
        val = self._slope_card_value(-1.10, t3m=3.1, t5=4.2)
        self.assertEqual(val, "-1.10pp  (Steep)")


class TestSpreadConventionShortMinusLong(unittest.TestCase):
    """(AUDIT-2026-07-02 #36, owner call) ONE spread convention everywhere:
    shorter tenor first (short − long), matching Home's 2Y−10Y/3M−10Y panes.
    Macro's board/chart and the recession factors previously showed the same
    spread with the OPPOSITE sign (raw FRED long−short)."""

    def test_macro_board_labels_and_negation(self):
        import ui.macro as M
        labels = {sid: label for _, sid, label, _ in M._RATE_BOARD}
        self.assertEqual(labels["T10Y2Y"], "2Y − 10Y")
        self.assertEqual(labels["T10Y3M"], "3M − 10Y")
        # the negate set is what flips the fetched series (values, deltas,
        # sparkline, z-score all downstream of it)
        self.assertEqual(M._SHORT_LONG_NEGATE, {"T10Y2Y", "T10Y3M"})

    def test_macro_spread_chart_flipped(self):
        src = (Path(__file__).parent.parent / "ui" / "macro.py").read_text(
            encoding="utf-8")
        block = src.split("def _fig_curve_spreads")[1].split("\ndef ")[0]
        self.assertIn('y=-d["value"]', block)          # series negated
        self.assertIn("above 0 = inverted", block)     # title matches the flip
        self.assertIn("y0=0, y1=4", block)             # shading on positive side
        self.assertIn("2Y − 10Y", block)
        self.assertNotIn('"10Y − 2Y"', block)

    def test_recession_factors_short_minus_long(self):
        import data.fred_client as F
        import pandas as pd
        vals = {"T10Y2Y": -0.75, "T10Y3M": -0.60}
        with patch.object(F, "latest_value",
                          side_effect=lambda s: vals.get(s)), \
             patch.object(F, "fetch_series",
                          return_value=pd.DataFrame(columns=["date", "value"])):
            out = F.recession_probability()
        text = " ".join(out["factors"])
        # raw FRED −0.75 (long−short) renders as short−long +0.75
        self.assertIn("2Y-10Y inverted +0.75pp", text)
        self.assertIn("3M-10Y inverted +0.60pp", text)
        self.assertNotIn("10Y-2Y", text)


class TestAssetBetaSliderScopedToConsumingTabs(unittest.TestCase):
    """(AUDIT P3, 2026-07-10) The 'Asset repricing speed' slider rendered as a
    global control but the DEFAULT (phased) tab ignored it — that tab derives
    its repricing pace from the FFIEC ladder with its own levers. The slider
    must render only when a consuming tab (Named Scenarios / Curve Matrix) is
    selected, so no visible control is silently disconnected."""

    def test_slider_gated_on_consuming_tabs(self):
        src = (Path(__file__).parent.parent / "ui" /
               "rate_sensitivity.py").read_text(encoding="utf-8")
        body = src.split("def render_rate_sensitivity")[1]
        pills = body.index("lazy_tabs(_rs_tabs")
        gate = body.index("if _rs_sel in (_rs_tabs[1], _rs_tabs[2])")
        slider = body.index("Asset repricing speed")
        # tabs are selected first, then the slider renders inside the gate
        self.assertLess(pills, gate)
        self.assertLess(gate, slider)
        # the phased pane call passes NO asset beta (it has its own levers)
        self.assertIn("_render_phased_scenarios(ticker, latest, hist, mode_key, custom_beta)",
                      body)


if __name__ == "__main__":
    unittest.main()
