"""
Pins two AUDIT-2026-07-02 P1 fixes in ui/peer_comparison.py:

  #8 — the "Biggest discount" highlight chip used mode="min" on ptbv_discount,
       which is positive-when-undervalued (analysis/valuation.py), so the chip
       crowned the MOST OVERVALUED bank. Must be "max"; every _HIGHLIGHTS
       entry's mode must match the metric's config.py color_rule direction.
  #9 — the radar chart plotted None metric values as 0 (worst-in-group,
       indistinguishable from genuinely worst) and computed its own
       strictly-below percentile instead of the shared Hazen
       compute_peer_percentile, so radar and table disagreed. Missing values
       must stay None (spoke omitted) and percentiles must match the shared
       helper exactly.

Run: python -m unittest tests.test_peer_comparison_fixes
"""
import sys
import types
import unittest

# Stub streamlit before importing ui modules (house pattern) —
# ui.peer_comparison applies @st.fragment at module load.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
sys.modules.setdefault("streamlit", _st)

from config import METRICS_BY_KEY  # noqa: E402
from analysis.peer_groups import compute_peer_percentile  # noqa: E402
from ui.peer_comparison import _HIGHLIGHTS, _radar_r_values  # noqa: E402


class TestHighlightDirections(unittest.TestCase):
    """Finding #8 — highlight chips must pick the best bank, not the worst."""

    def test_biggest_discount_is_max(self):
        """ptbv_discount is positive-when-undervalued → the biggest discount
        is the LARGEST value. mode="min" crowned the most overvalued bank."""
        entry = next((e for e in _HIGHLIGHTS if e[1] == "ptbv_discount"), None)
        self.assertIsNotNone(entry, "_HIGHLIGHTS lost its ptbv_discount entry")
        self.assertEqual(entry[2], "max")

    def test_biggest_discount_selection(self):
        """End-to-end on the selection rule _render_highlights uses: the chip
        winner is the bank with the LARGEST ptbv_discount."""
        peers = [
            {"ticker": "OVER", "ptbv_discount": -30.0},   # overvalued
            {"ticker": "FAIR", "ptbv_discount": 0.0},
            {"ticker": "CHEAP", "ptbv_discount": 25.0},   # most undervalued
        ]
        _label, mkey, mode = next(e for e in _HIGHLIGHTS
                                  if e[1] == "ptbv_discount")
        cand = [(p["ticker"], p.get(mkey)) for p in peers
                if isinstance(p.get(mkey), (int, float))]
        tk, val = (min if mode == "min" else max)(cand, key=lambda x: x[1])
        self.assertEqual(tk, "CHEAP")
        self.assertEqual(val, 25.0)

    def test_all_highlight_modes_match_color_rule(self):
        """Every highlight's min/max must agree with the metric's config.py
        color_rule (the same class of inversion as finding #8)."""
        expected_mode = {"higher_better": "max", "lower_better": "min"}
        for label, mkey, mode in _HIGHLIGHTS:
            m_def = METRICS_BY_KEY.get(mkey)
            self.assertIsNotNone(m_def, f"{label}: unknown metric {mkey!r}")
            rule = m_def.get("color_rule")
            self.assertIn(rule, expected_mode,
                          f"{label}: {mkey} has non-directional color_rule {rule!r}")
            self.assertEqual(
                mode, expected_mode[rule],
                f"{label}: mode {mode!r} inverted vs color_rule {rule!r} for {mkey}")


class TestRadarRValues(unittest.TestCase):
    """Finding #9 — radar percentiles: None stays None, Hazen matches table."""

    # metric_data rows as _render_peer_radar builds them ("values" omitted —
    # _radar_r_values only reads key / higher_better / numeric).
    METRIC_DATA = [
        {"key": "nim", "label": "NIM", "higher_better": True,
         "numeric": [2.0, 3.0, 4.0, 5.0]},
        {"key": "efficiency_ratio", "label": "Efficiency", "higher_better": False,
         "numeric": [50.0, 60.0, 70.0]},
    ]

    def test_none_never_becomes_zero(self):
        """A missing metric must yield None, not 0th percentile."""
        bank = {"ticker": "GAP", "nim": None}  # efficiency_ratio absent entirely
        r_values = _radar_r_values(bank, self.METRIC_DATA)
        self.assertEqual(r_values, [None, None])
        self.assertNotIn(0, r_values)

    def test_percentile_matches_shared_hazen(self):
        """The radar's r-value must equal compute_peer_percentile's output
        (inverted for lower-is-better), so radar and table agree."""
        bank = {"ticker": "MID", "nim": 3.0, "efficiency_ratio": 60.0}
        r_values = _radar_r_values(bank, self.METRIC_DATA)

        exp_nim = compute_peer_percentile(3.0, [2.0, 3.0, 4.0, 5.0])
        exp_eff = 100 - compute_peer_percentile(60.0, [50.0, 60.0, 70.0])
        self.assertEqual(r_values, [exp_nim, exp_eff])
        # Hand-computed Hazen ((below + 0.5*equal)/total * 100):
        # nim: (1 + 0.5)/4 = 37.5; efficiency: (1 + 0.5)/3 = 50 → inverted 50.
        self.assertEqual(r_values, [37.5, 50.0])

    def test_lower_better_inversion(self):
        """Best (lowest) efficiency lands near the TOP of the radar."""
        bank = {"ticker": "LEAN", "efficiency_ratio": 50.0}
        r_values = _radar_r_values(bank, self.METRIC_DATA)
        self.assertIsNone(r_values[0])  # no nim → omitted, not 0
        # Hazen for 50.0 in [50,60,70] = (0 + 0.5)/3 * 100 = 16.67 → inverted 83.33
        self.assertAlmostEqual(r_values[1], 100 - (0.5 / 3) * 100, places=9)
        self.assertGreater(r_values[1], 50)

    def test_ties_get_equal_percentile(self):
        """Tied banks must get the same r-value (Hazen tie handling — the old
        strictly-below formula already agreed here only by accident; pin it)."""
        md = [{"key": "nim", "label": "NIM", "higher_better": True,
               "numeric": [3.0, 3.0, 4.0]}]
        a = _radar_r_values({"ticker": "A", "nim": 3.0}, md)
        b = _radar_r_values({"ticker": "B", "nim": 3.0}, md)
        self.assertEqual(a, b)
        self.assertEqual(a, [compute_peer_percentile(3.0, [3.0, 3.0, 4.0])])


if __name__ == "__main__":
    unittest.main()
