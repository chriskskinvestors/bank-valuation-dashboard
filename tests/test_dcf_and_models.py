"""
Value-asserting tests for the DCF engine, the earnings-normalization factor,
and the empirically calibrated NIM-model constants. Every expected value is
hand-computed — these are the formulas behind fair values shown to investors,
previously shipped with zero tests (audit P4).
"""
import sys
import types
import unittest

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from analysis.dcf import (  # noqa: E402
    terminal_value, present_value, run_fcfe_dcf, implied_irr, warranted_ptbv,
)


class TestGordonAndPV(unittest.TestCase):
    def test_terminal_value_single_growth_application(self):
        # TV_N = CF_{N+1} / (r − g): 1.04 / (0.10 − 0.04) = 17.3333…
        self.assertAlmostEqual(terminal_value(1.04, 10.0, 4.0), 17.333333, places=5)
        # The old double-growth bug would have produced 1.04*1.04/0.06 = 18.0267.

    def test_terminal_value_undefined_when_r_le_g(self):
        self.assertIsNone(terminal_value(1.0, 4.0, 4.0))
        self.assertIsNone(terminal_value(1.0, 3.0, 4.0))

    def test_present_value(self):
        # [1, 1] @ 10%: 1/1.1 + 1/1.21 = 1.7355372
        self.assertAlmostEqual(present_value([1.0, 1.0], 10.0), 1.7355372, places=6)


class TestWarrantedPtbv(unittest.TestCase):
    def test_gordon_identity(self):
        # (ROATCE − g) / (CoE − g): (12 − 4) / (10 − 4) = 1.3333
        v = warranted_ptbv(12.0, 10.0, 4.0)
        self.assertAlmostEqual(v, 8.0 / 6.0, places=5)

    def test_undefined_when_coe_le_g(self):
        self.assertIsNone(warranted_ptbv(12.0, 4.0, 4.0))


class TestFcfeDcf(unittest.TestCase):
    """Flat-bank fixture, every number hand-computed.

    base EPS $2, 0% growth ×5, zero loan growth (no growth capital), CoE 10%,
    terminal g 4%, ROATCE 12%:
      explicit FCFE = $2/yr ×5 → PV = 2 × (1 − 1.1^-5)/0.1 = 7.5815735
      terminal payout = 1 − 4/12 = 0.6666667  (sustainable-growth identity)
      terminal EPS (yr 6) = 2 × 1.04 = 2.08 ; CF6 = 2.08 × 2/3 = 1.3866667
      TV5 = 1.3866667 / 0.06 = 23.111111 ; PV(TV) = 23.111111/1.1^5 = 14.350179
      fair value = 7.5815735 + 14.350179 = 21.931752
    """
    PARAMS = dict(
        base_eps=2.0,
        eps_growth_rates=[0.0] * 5,
        payout_ratio=0.30,            # explicit-period payout (unused: no growth need)
        loan_growth_rates=[0.0] * 5,
        starting_loans_per_share=50.0,
        target_cet1_pct=10.0,
        cost_of_equity_pct=10.0,
        terminal_growth_pct=4.0,
        roatce_pct=12.0,
    )

    def test_fair_value_hand_computed(self):
        r = run_fcfe_dcf(**self.PARAMS)
        self.assertAlmostEqual(r["pv_explicit"], 7.5815735, places=4)
        self.assertAlmostEqual(r["terminal_payout_ratio_used"], 2.0 / 3.0, places=5)
        self.assertAlmostEqual(r["terminal_value"], 23.111111, places=3)
        self.assertAlmostEqual(r["pv_terminal"], 14.350179, places=3)
        self.assertAlmostEqual(r["fair_value_per_share"], 21.931752, places=3)

    def test_terminal_payout_uses_roatce_not_eps_growth(self):
        # The old category error derived payout from EPS growth (+1%): with 0%
        # growth that gave payout = 1 − 4/1 → clamped to 0 → near-zero terminal
        # value. With ROATCE=12 the identity gives 2/3.
        r = run_fcfe_dcf(**self.PARAMS)
        self.assertGreater(r["terminal_payout_ratio_used"], 0.6)

    def test_explicit_terminal_payout_respected(self):
        r = run_fcfe_dcf(**{**self.PARAMS, "terminal_payout_ratio": 0.5})
        self.assertAlmostEqual(r["terminal_payout_ratio_used"], 0.5, places=6)

    def test_growth_capital_reduces_fcfe(self):
        # 5% loan growth on $50 loans/share at 10% CET1 consumes capital:
        # year-1 new loans = 2.5 → capital need 0.25 → FCFE_1 = 2 − 0.25.
        r = run_fcfe_dcf(**{**self.PARAMS, "loan_growth_rates": [0.05] * 5})
        self.assertAlmostEqual(r["projected_fcfe"][0], 2.0 - 2.5 * 0.10, places=6)
        self.assertLess(r["fair_value_per_share"],
                        run_fcfe_dcf(**self.PARAMS)["fair_value_per_share"])


class TestImpliedIrr(unittest.TestCase):
    def test_irr_recovers_cost_of_equity(self):
        # Price the bank exactly at its 10%-CoE fair value → IRR ≈ 10%.
        fv = run_fcfe_dcf(**TestFcfeDcf.PARAMS)["fair_value_per_share"]
        irr = implied_irr(fv, dict(TestFcfeDcf.PARAMS))
        self.assertIsNotNone(irr)
        self.assertAlmostEqual(irr, 10.0, delta=0.2)

    def test_out_of_bracket_returns_none_not_sentinel(self):
        # Absurdly cheap price → IRR above 30%: must be None now, never a fake
        # 30.0 that reads like a solved value.
        self.assertIsNone(implied_irr(0.01, dict(TestFcfeDcf.PARAMS)))
        # Absurdly expensive → below 3%: also None (was a fake 2.0).
        self.assertIsNone(implied_irr(10_000.0, dict(TestFcfeDcf.PARAMS)))


class TestNormalizationFactor(unittest.TestCase):
    """Regression for the shipped Carter Bankshares false-positive: a one-time
    quarterly spike must shrink the factor; steady earners must be untouched."""

    @staticmethod
    def _hist(quarterly_ni):
        """Build FDIC-style records (newest first) with YTD NETINC."""
        recs = []
        # Two years of quarters, newest first: construct YTD per calendar year.
        years = [2026, 2026, 2025, 2025, 2025, 2025, 2024, 2024]
        quarters = [2, 1, 4, 3, 2, 1, 4, 3]
        ytd = {}
        rows = list(zip(years, quarters, quarterly_ni))
        # build YTD from oldest forward
        ytd_map = {}
        running = {}
        for y, q, ni in sorted(rows, key=lambda r: (r[0], r[1])):
            running.setdefault(y, 0)
            running[y] += ni
            ytd_map[(y, q)] = running[y]
        for y, q, _ni in rows:
            recs.append({"REPDTE": f"{y}-{q*3:02d}-30", "NETINC": ytd_map[(y, q)]})
        return recs

    def test_steady_earner_factor_is_one(self):
        from analysis.valuation import _normalized_earnings_factor
        hist = self._hist([100] * 8)
        self.assertAlmostEqual(_normalized_earnings_factor(hist), 1.0, places=6)

    def test_one_time_spike_shrinks_factor(self):
        from analysis.valuation import _normalized_earnings_factor
        # Latest quarter 5× the norm (the Carter pattern).
        hist = self._hist([500, 100, 100, 100, 100, 100, 100, 100])
        f = _normalized_earnings_factor(hist)
        self.assertLess(f, 0.85, "spike must trip the distortion threshold")
        self.assertGreaterEqual(f, 0.2, "floor must hold")


class TestCalibratedConstantsPinned(unittest.TestCase):
    """The empirically recalibrated constants (tools/recalibrate_constants.py
    sweep: RMSE/bias documented in analysis/rate_sensitivity.py). A drive-by
    edit must FAIL here and force a re-run of the calibration sweep."""

    def test_constants(self):
        import analysis.rate_sensitivity as rs
        self.assertEqual(rs._DEFAULT_FLOATING_LOAN_SHARE, 0.27)
        self.assertEqual(rs._MIX_SHIFT_PER_100BPS, 0.04)
        self.assertEqual(rs.DEFAULT_BETA_IB_CORE, 0.40)
        self.assertEqual(rs.TEXTBOOK_INT_BEARING_BETA, 0.50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
