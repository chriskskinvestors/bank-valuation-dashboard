"""(2026-08-20) Fair P/TBV audit — findings (a) and (b).

(a) Negative/zero ROATCE floored fair P/TBV at 0.0x, so the screen printed
    "Fair Price $0.00" for 20 going concerns (BANC, EGBN, BMRC, BAFN…). A
    loss-making bank with tangible book is not worth zero — the model is NOT
    APPLICABLE. Cardinal rule: n/a, never a plausible-wrong number.
(b) Two fair-value models coexisted: the screen used ROATCE/10 (Gordon with
    g=0) while Company → Valuation Model defaulted to (ROATCE−g)/(CoE−g) with
    g=2.5% — 38 of 364 banks differed >20% between pages. Both now run ONE
    model with ONE set of constants (analysis/dcf), and the Company page
    seeds ROATCE from the same input the screen uses.

Run: python -m unittest tests.test_fair_value_model
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from analysis import dcf  # noqa: E402
from analysis.valuation import (  # noqa: E402
    compute_fair_ptbv,
    compute_fair_value_price,
    compute_ptbv_discount,
)

COE, G = dcf.FAIR_VALUE_COE_PCT, dcf.FAIR_VALUE_TERMINAL_GROWTH_PCT


class TestOneModelBothSurfaces(unittest.TestCase):
    def test_screen_equals_company_page_for_same_inputs(self):
        """The screen's compute_fair_ptbv must reproduce the Company page's
        warranted_ptbv at the shared defaults — bit for bit (below the cap)."""
        for r in (5.0, 7.88, 10.0, 12.0, 15.0, 18.0):
            with self.subTest(roatce=r):
                self.assertAlmostEqual(
                    compute_fair_ptbv(r), dcf.warranted_ptbv(r, COE, G), places=12)

    def test_hand_computed_values(self):
        # (r − 2.5) / (10 − 2.5)
        self.assertAlmostEqual(compute_fair_ptbv(10.0), 1.0, places=12)
        self.assertAlmostEqual(compute_fair_ptbv(12.0), 9.5 / 7.5, places=12)
        self.assertAlmostEqual(compute_fair_ptbv(15.0), 12.5 / 7.5, places=12)
        self.assertAlmostEqual(compute_fair_ptbv(7.88), 5.38 / 7.5, places=12)

    def test_cap_is_the_shared_constant(self):
        self.assertEqual(compute_fair_ptbv(40.0), dcf.FAIR_PTBV_CAP)

    def test_constants_are_sane(self):
        self.assertGreater(COE, G)
        self.assertEqual(COE, 10.0)
        self.assertEqual(G, 2.5)


class TestNotApplicableIsNone(unittest.TestCase):
    def test_negative_roatce_is_none_not_zero(self):
        """The BANC/EGBN/BMRC shape: the old 0.0x floor printed $0.00."""
        self.assertIsNone(compute_fair_ptbv(-12.3))
        self.assertIsNone(compute_fair_ptbv(0.0))

    def test_roatce_at_or_below_growth_is_none(self):
        """Returns ≤ g make the Gordon multiple ≤ 0 — meaningless, so n/a
        (the old model printed 0.18x fair / −344% 'discount' here)."""
        self.assertIsNone(compute_fair_ptbv(G))
        self.assertIsNone(compute_fair_ptbv(1.82))

    def test_none_propagates_to_price_and_discount(self):
        fair = compute_fair_ptbv(-5.0)
        self.assertIsNone(compute_fair_value_price(fair, 11.11))
        self.assertIsNone(compute_ptbv_discount(0.83, fair))

    def test_just_above_growth_is_small_positive(self):
        v = compute_fair_ptbv(G + 0.75)
        self.assertAlmostEqual(v, 0.1, places=12)


class TestCompanyPageUsesSharedConstants(unittest.TestCase):
    """Structural: the Valuation Model sliders must default to the shared
    constants, not hardcoded literals — otherwise the two surfaces drift
    apart again the next time someone edits one of them."""

    def test_sliders_read_constants(self):
        src = (REPO / "ui/valuation_model.py").read_text(encoding="utf-8")
        self.assertIn("value=FAIR_VALUE_COE_PCT", src)
        self.assertIn("value=FAIR_VALUE_TERMINAL_GROWTH_PCT", src)
        # No stray hardcoded default for either slider
        self.assertIsNone(re.search(
            r'"Cost of equity \(%\)",\s*min_value=[^,]+,\s*max_value=[^,]+,'
            r'\s*value=10\.0', src))

    def test_page_seeds_from_holdco_first(self):
        src = (REPO / "ui/valuation_model.py").read_text(encoding="utf-8")
        self.assertIn("compute_roatce_holdco(sec)", src)


if __name__ == "__main__":
    unittest.main()
