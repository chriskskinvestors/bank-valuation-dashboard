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
    compute_upside_to_fair,
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

    def test_no_cap_on_the_multiple(self):
        """Owner decision 2026-08-20: the 2.5x backstop is gone — it made
        elite-return banks (BNY, TBBK, CASH…) read overvalued by construction
        and masked a corrupt input (KFFB's 1555% ROATCE from a filer XBRL
        error). Bad inputs are stopped at the source instead."""
        self.assertAlmostEqual(compute_fair_ptbv(33.1), (33.1 - 2.5) / 7.5,
                               places=12)      # TBBK: 4.08x, uncapped
        self.assertGreater(compute_fair_ptbv(40.0), 2.5)
        self.assertFalse(hasattr(dcf, "FAIR_PTBV_CAP"),
                         "FAIR_PTBV_CAP is back — the cap was dropped by "
                         "owner decision; re-introducing it silently re-hides "
                         "corrupt-input blowups")

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
        self.assertIsNone(compute_upside_to_fair(9.17, 
                                                 compute_fair_value_price(fair, 11.11)))

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


class TestUpsideToFairIsBounded(unittest.TestCase):
    """Finding (c): the old (fair_ptbv − actual_ptbv)/fair_ptbv put −785% on
    screen for low-return banks (31 of 364 below −100%). Upside to fair PRICE
    divides by the price, so −100% is the floor."""

    def test_hand_computed(self):
        self.assertAlmostEqual(compute_upside_to_fair(10.0, 12.5), 25.0, places=12)
        self.assertAlmostEqual(compute_upside_to_fair(10.0, 8.0), -20.0, places=12)

    def test_floor_is_minus_100(self):
        """Even a fair price of zero cannot read worse than −100%."""
        self.assertAlmostEqual(compute_upside_to_fair(9.17, 0.0), -100.0, places=12)

    def test_bsbk_shape_no_longer_explodes(self):
        """BSBK: price 9.17, fair multiple 0.18x x TBV 11.11 = fair ~$2.02.
        Old metric read -352%; the bounded one reads ~-78%."""
        fair_price = compute_fair_value_price(0.18, 11.11)
        v = compute_upside_to_fair(9.17, fair_price)
        self.assertGreater(v, -100.0)
        self.assertAlmostEqual(v, -78.2, places=1)

    def test_sign_convention_unchanged(self):
        """Positive still means undervalued — the peer 'most upside' chip and
        the higher_better/+15% threshold depend on it."""
        self.assertGreater(compute_upside_to_fair(10.0, 15.0), 0)
        self.assertLess(compute_upside_to_fair(15.0, 10.0), 0)

    def test_missing_inputs_are_none(self):
        self.assertIsNone(compute_upside_to_fair(None, 12.0))
        self.assertIsNone(compute_upside_to_fair(10.0, None))
        self.assertIsNone(compute_upside_to_fair(0.0, 12.0))


if __name__ == "__main__":
    unittest.main()
