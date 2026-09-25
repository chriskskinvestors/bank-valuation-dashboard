"""
(P0-7, 2026-09-24) Historical YoY growth must be n/a, not 0.0, when it cannot
be derived — and the year-ago quarter must be found by date, not by position.

The bug (analysis/rate_sensitivity.compute_historical_growth_rates):
  • `_growth` ran through `_safe(val, default=0.0)`, so a year-ago balance of
    None became v0 = 0 → "0.0% growth", and a latest balance of None became
    v1 = 0 → −100%. The Rate Sensitivity tab then printed "Historical YoY:
    deposits +0.0%" and the volume projection used the 0 as its growth
    assumption.
  • `year_ago = fdic_hist[4]` is positional: with one quarter missing from
    the history the "YoY" pair spanned five quarters.

Fix: None for any underivable field (endpoint absent, non-positive base,
year-ago quarter absent); year-ago matched on REPDTE; adjust_growth_for_rates
keeps None as None; the projection substitutes the documented default (hold
flat) and reports which fields it defaulted so the UI can label them.

Pins (pure, no network) — every value hand-computed in the fixtures below.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.rate_sensitivity import (  # noqa: E402
    adjust_growth_for_rates,
    apply_rate_scenario_phased,
    compute_historical_growth_rates,
    run_rate_sensitivity_phased,
)


def _q(repdte, loans, dep, ernast, sc, asset):
    return {"REPDTE": repdte, "LNLSNET": loans, "DEP": dep, "ERNAST": ernast,
            "SC": sc, "ASSET": asset}


# Newest-first. Latest vs the true year-ago (2024-12-31):
#   loans      (1,100,000 − 1,000,000) / 1,000,000 = 0.10
#   deposits   year-ago DEP absent → None
#   EA         (1,600,000 − 1,500,000) / 1,500,000 = 0.0666…
#   securities (400,000 − 380,000) / 380,000 = 0.0526315…
CONTIGUOUS = [
    _q("20251231", 1_100_000, 1_300_000, 1_600_000, 400_000, 1_800_000),
    _q("20250930", 1_080_000, 1_280_000, 1_580_000, 395_000, 1_780_000),
    _q("20250630", 1_050_000, 1_260_000, 1_550_000, 390_000, 1_750_000),
    _q("20250331", 1_020_000, 1_240_000, 1_520_000, 385_000, 1_720_000),
    _q("20241231", 1_000_000, None,      1_500_000, 380_000, 1_700_000),
]


class TestNoneNeverZero(unittest.TestCase):
    def test_absent_year_ago_field_is_none_others_hand_computed(self):
        g = compute_historical_growth_rates(CONTIGUOUS)
        self.assertIsNotNone(g)
        self.assertIsNone(g["deposits_growth"])            # was 0.0
        self.assertAlmostEqual(g["loans_growth"], 0.10, places=12)
        self.assertAlmostEqual(g["earning_assets_growth"], 100_000 / 1_500_000, places=12)
        self.assertAlmostEqual(g["securities_growth"], 20_000 / 380_000, places=12)

    def test_absent_latest_field_is_none_not_minus_100pct(self):
        hist = [dict(CONTIGUOUS[0], LNLSNET=None)] + CONTIGUOUS[1:]
        self.assertIsNone(compute_historical_growth_rates(hist)["loans_growth"])

    def test_earning_assets_falls_back_to_total_assets_then_none(self):
        # ERNAST absent on the latest record → ASSET pair:
        #   (1,800,000 − 1,700,000) / 1,700,000 = 0.0588235…
        hist = [dict(CONTIGUOUS[0], ERNAST=None)] + CONTIGUOUS[1:]
        self.assertAlmostEqual(
            compute_historical_growth_rates(hist)["earning_assets_growth"],
            100_000 / 1_700_000, places=12)
        # Neither derivable → None.
        hist = [dict(CONTIGUOUS[0], ERNAST=None, ASSET=None)] + CONTIGUOUS[1:]
        self.assertIsNone(compute_historical_growth_rates(hist)["earning_assets_growth"])

    def test_fewer_than_five_quarters_is_still_none_overall(self):
        self.assertIsNone(compute_historical_growth_rates(CONTIGUOUS[:4]))
        self.assertIsNone(compute_historical_growth_rates(None))


class TestYearAgoByDate(unittest.TestCase):
    def test_gap_resolves_year_ago_by_repdte_not_index(self):
        # 2025-06-30 missing: hist[4] is 2024-09-30 (loans 800,000), the true
        # year-ago 2024-12-31 (loans 1,000,000) sits at hist[3].
        #   correct   = (1,100,000 − 1,000,000) / 1,000,000 = 0.10
        #   positional = (1,100,000 − 800,000) / 800,000 = 0.375 (wrong)
        hist = [
            CONTIGUOUS[0],
            CONTIGUOUS[1],
            CONTIGUOUS[3],
            _q("20241231", 1_000_000, 1_200_000, 1_500_000, 380_000, 1_700_000),
            _q("20240930",   800_000, 1_100_000, 1_400_000, 370_000, 1_600_000),
        ]
        g = compute_historical_growth_rates(hist)
        self.assertAlmostEqual(g["loans_growth"], 0.10, places=12)
        self.assertAlmostEqual(g["deposits_growth"], 100_000 / 1_200_000, places=12)

    def test_true_year_ago_quarter_absent_is_none(self):
        # 2024-12-31 absent: five records, but no record 4 quarters before
        # the latest → nothing to derive from, every field None.
        hist = CONTIGUOUS[:4] + [
            _q("20240930", 800_000, 1_100_000, 1_400_000, 370_000, 1_600_000)]
        g = compute_historical_growth_rates(hist)
        self.assertIsNotNone(g)
        self.assertEqual(set(g.values()), {None})

    def test_repdte_formats_match(self):
        # ISO strings (warm-cache round trip) and Timestamps resolve the same
        # year-ago pair as 'YYYYMMDD'.
        import pandas as pd
        iso = [dict(r, REPDTE=f"{r['REPDTE'][:4]}-{r['REPDTE'][4:6]}-{r['REPDTE'][6:]}")
               for r in CONTIGUOUS]
        ts = [dict(r, REPDTE=pd.Timestamp(r["REPDTE"])) for r in CONTIGUOUS]
        for hist in (iso, ts):
            self.assertAlmostEqual(
                compute_historical_growth_rates(hist)["loans_growth"], 0.10, places=12)


class TestConsumersKeepNone(unittest.TestCase):
    BASE = {"loans_growth": None, "deposits_growth": 0.04,
            "earning_assets_growth": None, "securities_growth": 0.03}

    def test_adjust_growth_keeps_none(self):
        adj = adjust_growth_for_rates(self.BASE, 100)
        self.assertIsNone(adj["loans_growth"])
        self.assertIsNone(adj["earning_assets_growth"])
        # +100 bps: deposits 0.04 − 0.01 = 0.03; securities 0.03 + 0.005 = 0.035
        self.assertAlmostEqual(adj["deposits_growth"], 0.03, places=12)
        self.assertAlmostEqual(adj["securities_growth"], 0.035, places=12)

    # Balance-sheet inputs in raw dollars (as build_rate_sensitivity_inputs
    # returns them); deposits omitted so the cost-of-funds branch is trivial.
    INPUTS = {"earning_asset_yield_pct": 5.0, "cost_of_int_bearing_pct": 2.0,
              "current_nim_pct": 3.0, "earning_assets_usd": 1_000_000_000.0,
              "securities_share": 0.2, "loans_share": 0.8, "effective_tax_rate": 0.21}

    def test_projection_defaults_none_ea_growth_to_flat_and_reports_it(self):
        r = apply_rate_scenario_phased(
            self.INPUTS, 100, deposit_beta_int_bearing=0.4, horizon_years=3,
            base_growth_rates=self.BASE, apply_volume_effects=True)
        self.assertEqual(r["growth_rates_defaulted"], ["earning_assets_growth"])
        self.assertIsNone(r["growth_rates_used"]["earning_assets_growth"])
        # Held flat: every year's EA equals the year-0 base.
        for y in r["years"]:
            self.assertEqual(y["earning_assets_usd"], 1_000_000_000.0)

    def test_projection_control_derivable_ea_growth_compounds(self):
        # +100 bps adjusts EA growth by 1 × (−0.02 × 0.7 + 0.005 × 0.3) = −0.0125
        # → 0.05 − 0.0125 = 0.0375; year N EA = base × 1.0375^N.
        base = dict(self.BASE, earning_assets_growth=0.05)
        r = apply_rate_scenario_phased(
            self.INPUTS, 100, deposit_beta_int_bearing=0.4, horizon_years=3,
            base_growth_rates=base, apply_volume_effects=True)
        self.assertEqual(r["growth_rates_defaulted"], [])
        self.assertAlmostEqual(r["growth_rates_used"]["earning_assets_growth"], 0.0375,
                               places=12)
        for y in r["years"]:
            self.assertAlmostEqual(y["earning_assets_usd"],
                                   1_000_000_000.0 * 1.0375 ** y["year"], places=3)

    def test_volume_effects_off_reports_nothing_defaulted(self):
        r = apply_rate_scenario_phased(
            self.INPUTS, 100, deposit_beta_int_bearing=0.4,
            base_growth_rates=self.BASE, apply_volume_effects=False)
        self.assertEqual(r["growth_rates_defaulted"], [])
        self.assertIsNone(r["growth_rates_used"])

    def test_run_phased_surfaces_none_and_defaulted_fields(self):
        # Year-ago ERNAST and ASSET both absent → EA growth underivable.
        latest = dict(CONTIGUOUS[0], INTINCY=5.0, INTEXPY=2.0, NIMY=3.0,
                      DEPIDOM=900_000, DEPNIDOM=400_000, BRO=0)
        hist = [latest] + CONTIGUOUS[1:4] + [
            dict(CONTIGUOUS[4], ERNAST=None, ASSET=None)]
        res = run_rate_sensitivity_phased(
            latest, hist, beta_mode="textbook", scenarios_bps=[-100, 100],
            apply_volume_effects=True)
        base = res["base_growth_rates"]
        self.assertAlmostEqual(base["loans_growth"], 0.10, places=12)
        self.assertIsNone(base["deposits_growth"])
        self.assertIsNone(base["earning_assets_growth"])
        self.assertEqual(res["growth_rates_defaulted"], ["earning_assets_growth"])
        ea0 = res["inputs"]["earning_assets_usd"]
        for s in res["scenarios"]:
            self.assertEqual(s["growth_rates_defaulted"], ["earning_assets_growth"])
            for y in s["years"]:
                self.assertEqual(y["earning_assets_usd"], ea0)


if __name__ == "__main__":
    unittest.main()
