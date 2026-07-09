"""
Pins the AUDIT-2026-07-02 P3 batch fixed/verified 2026-07-09:

1. ui/rate_sensitivity.py — the NIM-vs-slope chart's current-slope vline must
   be gated on `is not None`, never truthiness (an exactly-flat 0.0pp slope is
   a real, plottable value; truthiness silently dropped the marker).
   [Fixed earlier in daefb76; pinned here so it can't regress.]
4. ui/bank_detail.py — the ROATCE fallback (used when the engine's blended
   figure is absent) must subtract INTAN (total intangibles — the house TCE
   convention, P2 #24 owner decision), never INTANGW (goodwill only), so the
   fallback stays on the same basis as analysis/valuation.compute_roatce.
6. ui/macro.py — the upcoming-releases "Today" row highlight must key off the
   ET calendar date (rows are ET), not the server's UTC date, which rolls
   over at ~8pm ET and highlighted tomorrow's rows.
7. utils/formatting.format_value (the audit filed this under
   utils/chart_style.py, but the formatter consolidation moved it) — the
   "number" branch must tier by |value| so large NEGATIVE values scale
   (-2,500,000 → "-2.5M", not "-2,500,000.00"); plus utils/chart_style.py's
   module docstring must describe the LIGHT theme (it said "Premium dark").

Source-structure tests follow the tests/test_audit_regressions.py TestA12
pattern for UI render paths; format_value is pinned behaviorally with
hand-computed values.

Run: python -m unittest tests.test_ui_p3_guards
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_ROOT = Path(__file__).parent.parent


def _read(*parts):
    return (_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class TestVlineNoneGuard(unittest.TestCase):
    """Item 1 — NIM-vs-slope current-slope vline survives a 0.0pp slope."""

    def test_vline_guard_is_explicit_none_check(self):
        src = _read("ui", "rate_sensitivity.py")
        self.assertIn("fig.add_vline", src, "vline block moved/renamed?")
        before = src.split("fig.add_vline")[0]
        # The guard immediately preceding the add_vline call.
        guard = before[-300:]
        self.assertIn("current_slope is not None", guard,
                      "vline must be gated on `is not None`")
        # No bare-truthiness gate on current_slope anywhere (0.0 is falsy).
        self.assertIsNone(
            re.search(r"if current_slope\s*:", src),
            "bare `if current_slope:` truthiness drops the marker at 0.0")


class TestBankDetailRoatceFallbackBasis(unittest.TestCase):
    """Item 4 — ROATCE fallback on the INTAN basis, same as the engine."""

    def test_fallback_subtracts_total_intangibles(self):
        src = _read("ui", "bank_detail.py")
        marker = 'roatce_v = disp("roatce_blended")'
        self.assertIn(marker, src, "ROATCE fallback block moved/renamed?")
        block = src.split(marker)[1].split("performance = [")[0]
        self.assertIn('"INTAN"', block,
                      "fallback must subtract INTAN (total intangibles)")
        self.assertNotIn('"INTANGW"', block,
                         "INTANGW (goodwill only) breaks basis continuity "
                         "with analysis/valuation.compute_roatce")


class TestMacroTodayHighlightEt(unittest.TestCase):
    """Item 6 — calendar 'Today' highlight keyed to the ET date."""

    def test_today_computed_in_eastern_time(self):
        src = _read("ui", "macro.py")
        lines = [ln for ln in src.splitlines() if "today_iso =" in ln]
        self.assertEqual(len(lines), 1, "expected one today_iso assignment")
        self.assertIn('ZoneInfo("America/New_York")', lines[0],
                      "'Today' must be the ET date — rows are ET")
        # The old UTC-keyed form must be gone entirely.
        self.assertNotIn("_date.today()", src)


class TestFormatValueNegativeTiers(unittest.TestCase):
    """Item 7 — format_value 'number' tiers by |value|.

    Hand-computed: -2,500,000 / 1e6 = -2.5 → "-2.5M";
    -4,500 / 1e3 = -4.5 → "-4.5K". Before the fix both fell through the
    (un-abs'd) tier checks to "-2,500,000.00" / "-4,500.00".
    """

    def test_large_negative_scales_to_millions(self):
        from utils.formatting import format_value
        self.assertEqual(format_value(-2_500_000, "number"), "-2.5M")

    def test_negative_thousands_scale_to_k(self):
        from utils.formatting import format_value
        self.assertEqual(format_value(-4_500, "number"), "-4.5K")

    def test_positive_tiers_unchanged(self):
        from utils.formatting import format_value
        self.assertEqual(format_value(2_500_000, "number"), "2.5M")
        self.assertEqual(format_value(4_500, "number"), "4.5K")

    def test_small_negative_stays_plain(self):
        from utils.formatting import format_value
        self.assertEqual(format_value(-999, "number"), "-999.00")


class TestChartStyleDocstringLightTheme(unittest.TestCase):
    """Item 7 (second half) — the theme docstring says light, not dark."""

    def test_docstring_not_dark(self):
        import utils.chart_style as cs
        self.assertNotIn("dark", (cs.__doc__ or "").lower(),
                         "theme has been light since the styles.py migration")
        self.assertIn("Light", cs.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
