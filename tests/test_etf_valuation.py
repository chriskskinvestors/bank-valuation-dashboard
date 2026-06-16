"""
Tests for data/etf_valuation.py — the ETF look-through valuation blender
behind Market & Macro's Bank Sector VALUATION block (HOME-MACRO-PLAN.md §2).

Pins the aggregation math (no network): harmonic weighting for P/E and P/TBV,
weighted-average for dividend yield, P/TBV derived from P/B·BVPS/TBVPS, and
the coverage handling that excludes holdings missing a metric (rather than
distorting the blend).
"""
import unittest

from data.etf_valuation import blend_valuation, parse_holdings


class TestBlendValuation(unittest.TestCase):
    HOLDINGS = [("AAA", 60.0), ("BBB", 40.0)]
    METRICS = {
        # ptbv_A = 2 * 50/40 = 2.5 ; dy 3.0%
        "AAA": {"pe_ratio": 10.0, "pb_ratio": 2.0, "bvps": 50.0, "tbvps": 40.0,
                "dividend_yield": 3.0},
        # ptbv_B = 1 * 20/20 = 1.0 ; dy 1.0%
        "BBB": {"pe_ratio": 20.0, "pb_ratio": 1.0, "bvps": 20.0, "tbvps": 20.0,
                "dividend_yield": 1.0},
    }

    def test_harmonic_pe(self):
        # 1 / (0.6/10 + 0.4/20) = 1/0.08 = 12.5  (weights as % cancel in ratio)
        b = blend_valuation(self.HOLDINGS, self.METRICS)
        # Σw / Σ(w/pe) = 100 / (60/10 + 40/20) = 100 / 8 = 12.5
        self.assertAlmostEqual(b["pe"], 12.5, places=6)
        self.assertEqual(b["n_pe"], 2)

    def test_harmonic_ptbv(self):
        # Σw / Σ(w/ptbv) = 100 / (60/2.5 + 40/1.0) = 100 / 64 = 1.5625
        b = blend_valuation(self.HOLDINGS, self.METRICS)
        self.assertAlmostEqual(b["ptbv"], 1.5625, places=6)
        self.assertEqual(b["n_ptbv"], 2)

    def test_weighted_dividend_yield(self):
        # (60*3 + 40*1) / 100 = 2.2
        b = blend_valuation(self.HOLDINGS, self.METRICS)
        self.assertAlmostEqual(b["dividend_yield"], 2.2, places=6)
        self.assertEqual(b["n_dy"], 2)

    def test_excludes_missing_and_nonpositive(self):
        holdings = [("AAA", 50.0), ("BBB", 30.0), ("CCC", 20.0)]
        metrics = {
            "AAA": {"pe_ratio": 10.0, "pb_ratio": 2.0, "bvps": 50.0, "tbvps": 40.0,
                    "dividend_yield": 4.0},
            # BBB has a negative P/E (loss-maker) and negative tangible book →
            # excluded from P/E and P/TBV, but its yield still counts.
            "BBB": {"pe_ratio": -5.0, "pb_ratio": 1.5, "bvps": 10.0, "tbvps": -2.0,
                    "dividend_yield": 2.0},
            # CCC missing entirely.
        }
        b = blend_valuation(holdings, metrics)
        # Only AAA contributes to P/E and P/TBV.
        self.assertAlmostEqual(b["pe"], 10.0, places=6)
        self.assertEqual(b["n_pe"], 1)
        self.assertAlmostEqual(b["ptbv"], 2.0 * 50.0 / 40.0, places=6)
        self.assertEqual(b["n_ptbv"], 1)
        # Yield: AAA + BBB carry it (CCC missing) → (50*4 + 30*2)/(80) = 3.25
        self.assertAlmostEqual(b["dividend_yield"], (50 * 4 + 30 * 2) / 80.0, places=6)
        self.assertEqual(b["n_dy"], 2)

    def test_all_missing(self):
        b = blend_valuation([("AAA", 100.0)], {})
        self.assertIsNone(b["pe"])
        self.assertIsNone(b["ptbv"])
        self.assertIsNone(b["dividend_yield"])

    def test_empty_holdings(self):
        b = blend_valuation([], {})
        self.assertEqual(b["n_holdings"], 0)
        self.assertIsNone(b["pe"])


class TestParseHoldings(unittest.TestCase):
    # Real shapes seen across issuers (SSGA, Invesco, First Trust).
    ROWS = [
        {"asset": "EWBC", "isin": "US27579R1041", "securityCusip": "27579R104",
         "weightPercentage": 1.65},
        {"asset": "AGPXX", "isin": "US8252528851", "securityCusip": "825252885",
         "weightPercentage": 0.078},  # money-market fund — has ISIN, survives
        {"asset": "", "isin": "", "securityCusip": "",
         "weightPercentage": 0.19},   # index future — blank ticker, dropped
        {"asset": "$USD", "isin": "", "securityCusip": "",
         "weightPercentage": 0.33},   # First Trust cash — no ident, dropped
        {"asset": "", "isin": "", "securityCusip": "924QSGII3",
         "weightPercentage": 0.18},   # SSGA money-market — blank ticker, dropped
    ]

    def test_keeps_real_securities_only(self):
        out = parse_holdings(self.ROWS)
        tickers = [t for t, _ in out]
        self.assertEqual(tickers, ["EWBC", "AGPXX"])  # future / $USD / blank dropped
        self.assertAlmostEqual(dict(out)["EWBC"], 1.65)

    def test_empty_and_garbage(self):
        self.assertEqual(parse_holdings([]), [])
        self.assertEqual(parse_holdings(None), [])
        self.assertEqual(parse_holdings(["x", 5, {"asset": "AAA"}]), [])  # no ident/weight


if __name__ == "__main__":
    unittest.main()
