"""Unit tests for the Analyst Coverage sub-tab's pure helpers.

Run: python -m unittest tests.test_analyst_coverage
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.analyst_coverage import (  # noqa: E402
    _upside_pct,
    _fmt_upside,
    _fmt_px,
    _grade_action_html,
)


class TestUpsidePct(unittest.TestCase):
    def test_hand_computed_positive(self):
        # target 55, price 50 → 55/50 - 1 = 10%
        self.assertAlmostEqual(_upside_pct(55.0, 50.0), 10.0)

    def test_hand_computed_negative(self):
        # target 45, price 50 → -10%
        self.assertAlmostEqual(_upside_pct(45.0, 50.0), -10.0)

    def test_missing_inputs_are_none(self):
        self.assertIsNone(_upside_pct(None, 50.0))
        self.assertIsNone(_upside_pct(55.0, None))
        self.assertIsNone(_upside_pct("55", 50.0))

    def test_nonpositive_price_or_target_is_none(self):
        # A zero/negative price is bad data, not 0% upside.
        self.assertIsNone(_upside_pct(55.0, 0.0))
        self.assertIsNone(_upside_pct(55.0, -1.0))
        self.assertIsNone(_upside_pct(0.0, 50.0))


class TestFormatting(unittest.TestCase):
    def test_fmt_px(self):
        self.assertEqual(_fmt_px(1234.5), "$1,234.50")
        self.assertEqual(_fmt_px(None), "n/a")
        self.assertEqual(_fmt_px("x"), "n/a")

    def test_fmt_upside_signs_and_colors(self):
        pos = _fmt_upside(10.04)
        self.assertIn("+10.0%", pos)
        self.assertIn("#059669", pos)  # green
        neg = _fmt_upside(-3.25)
        self.assertIn("-3.2%", neg)  # -3.25 banker-rounds to -3.2 under %.1f
        self.assertIn("#dc2626", neg)  # red
        self.assertEqual(_fmt_upside(None), "n/a")

    def test_zero_upside_renders_green_plus_zero(self):
        z = _fmt_upside(0.0)
        self.assertIn("+0.0%", z)
        self.assertIn("#059669", z)


class TestGradeActionHtml(unittest.TestCase):
    def test_upgrade_green_downgrade_red(self):
        self.assertIn("#059669", _grade_action_html("Upgrade"))
        self.assertIn("#dc2626", _grade_action_html("Downgrade"))

    def test_neutral_actions_plain(self):
        out = _grade_action_html("initialise")
        self.assertNotIn("#059669", out)
        self.assertNotIn("#dc2626", out)

    def test_empty_action_is_dash(self):
        self.assertEqual(_grade_action_html(None), "—")
        self.assertEqual(_grade_action_html(""), "—")

    def test_action_is_escaped(self):
        self.assertNotIn("<script>", _grade_action_html("<script>alert(1)</script>"))


if __name__ == "__main__":
    unittest.main()
