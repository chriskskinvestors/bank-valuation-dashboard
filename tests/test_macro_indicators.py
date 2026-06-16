"""
Tests for data/macro_indicators.py — the FRED-based macro print board behind
Market & Macro's "Economy & Calendar" section (docs/HOME-MACRO-PLAN.md §5).

The pure reducers (compute_row, to_yoy, to_mom_change) are pinned with
hand-computed values on synthetic frames — no network. Every basis is
exercised, plus the n/a paths (empty / too-short series). The YoY reducer is
checked to align by DATE (not positional offset) so a gap in the series can't
silently shift the comparison year.
"""
import unittest

import pandas as pd

from data.macro_indicators import (
    compute_row, to_yoy, to_mom_change, credit_regime, curve_regime, fed_path,
)


def _mseries(start: str, values: list[float]) -> pd.DataFrame:
    """Monthly (date, value) frame starting at `start`, month-stepped."""
    dates = pd.date_range(start=start, periods=len(values), freq="MS")
    return pd.DataFrame({"date": dates, "value": values})


class TestYoYPct(unittest.TestCase):
    def test_yoy_acceleration(self):
        # 25 months 2024-01 .. 2026-01, rising +1/mo (100..123) then a jump to
        # 126 in the final month. YoY aligns by date (12-mo-prior partner):
        #   latest (2026-01)=126 vs (2025-01)=112 → +12.5000%
        #   prior  (2025-12)=123 vs (2024-12)=111 → +10.8108%  (accelerating)
        vals = [100 + i for i in range(24)] + [126]
        r = compute_row(_mseries("2024-01-01", vals), "yoy_pct")
        self.assertAlmostEqual(r["latest"], (126 / 112 - 1) * 100, places=4)
        self.assertAlmostEqual(r["prior"], (123 / 111 - 1) * 100, places=4)
        self.assertAlmostEqual(r["delta"], r["latest"] - r["prior"], places=6)
        self.assertEqual(r["as_of"], pd.Timestamp("2026-01-01"))

    def test_yoy_aligns_by_date_not_position(self):
        # A 24-month clean series; drop one middle month so positional -13
        # would point at the wrong year. Date alignment must still pick the
        # observation exactly 12 months before the latest.
        vals = list(range(100, 124))  # 24 months, 100..123
        df = _mseries("2024-01-01", vals)
        df = df.drop(index=5).reset_index(drop=True)  # remove month #6
        r = compute_row(df, "yoy_pct")
        latest = df.iloc[-1]            # 2025-12, value 123
        target = latest["date"] - pd.DateOffset(years=1)  # 2024-12
        base = df[df["date"] <= target]["value"].iloc[-1]
        self.assertAlmostEqual(r["latest"], (123 / base - 1) * 100, places=6)


class TestOtherBases(unittest.TestCase):
    def test_mom_pct(self):
        r = compute_row(_mseries("2026-01-01", [200, 210, 218.4]), "mom_pct")
        self.assertAlmostEqual(r["latest"], (218.4 / 210 - 1) * 100, places=6)
        self.assertAlmostEqual(r["prior"], (210 / 200 - 1) * 100, places=6)

    def test_mom_chg_k(self):
        # Payrolls level in thousands; MoM change = jobs added (thousands).
        r = compute_row(_mseries("2026-01-01", [158000, 158200, 158372]), "mom_chg_k")
        self.assertAlmostEqual(r["latest"], 172.0, places=6)
        self.assertAlmostEqual(r["prior"], 200.0, places=6)
        self.assertAlmostEqual(r["delta"], -28.0, places=6)

    def test_level_pct(self):
        r = compute_row(_mseries("2026-01-01", [4.1, 4.3]), "level_pct")
        self.assertAlmostEqual(r["latest"], 4.3, places=6)
        self.assertAlmostEqual(r["prior"], 4.1, places=6)
        self.assertAlmostEqual(r["delta"], 0.2, places=6)

    def test_level_k_converts_persons_to_thousands(self):
        r = compute_row(_mseries("2026-01-01", [212000, 229000]), "level_k")
        self.assertAlmostEqual(r["latest"], 229.0, places=6)
        self.assertAlmostEqual(r["prior"], 212.0, places=6)
        self.assertAlmostEqual(r["delta"], 17.0, places=6)


class TestNaPaths(unittest.TestCase):
    def test_empty_frame(self):
        r = compute_row(pd.DataFrame(columns=["date", "value"]), "yoy_pct")
        self.assertIsNone(r["latest"])
        self.assertIsNone(r["delta"])
        self.assertIsNone(r["as_of"])

    def test_too_short_for_yoy(self):
        # Only 3 months → no 12-month-prior observation → latest None.
        r = compute_row(_mseries("2026-01-01", [100, 101, 102]), "yoy_pct")
        self.assertIsNone(r["latest"])
        self.assertEqual(r["as_of"], pd.Timestamp("2026-03-01"))

    def test_none_input(self):
        r = compute_row(None, "level_pct")
        self.assertIsNone(r["latest"])


class TestChartHelpers(unittest.TestCase):
    def test_to_yoy_drops_first_year(self):
        vals = list(range(100, 113))  # 13 months
        out = to_yoy(_mseries("2025-01-01", vals))
        # Only the 13th month has a 12-month-prior partner.
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out["value"].iloc[0], (112 / 100 - 1) * 100, places=6)

    def test_to_mom_change(self):
        out = to_mom_change(_mseries("2026-01-01", [100, 105, 103]))
        self.assertEqual(list(out["value"]), [5.0, -2.0])


class TestCreditRegime(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(credit_regime(2.71)["label"], "Tight")     # 271 bps
        self.assertEqual(credit_regime(4.2)["label"], "Normal")     # 420 bps
        self.assertEqual(credit_regime(6.5)["label"], "Elevated")   # 650 bps
        self.assertEqual(credit_regime(9.5)["label"], "Stressed")   # 950 bps

    def test_levels_for_status_dot(self):
        self.assertEqual(credit_regime(2.71)["level"], "ok")
        self.assertEqual(credit_regime(6.5)["level"], "warn")
        self.assertEqual(credit_regime(9.5)["level"], "bad")

    def test_boundaries_are_exclusive_upper(self):
        # exactly 3.5 (350 bps) is no longer "Tight" — it's "Normal".
        self.assertEqual(credit_regime(3.5)["label"], "Normal")
        self.assertEqual(credit_regime(5.0)["label"], "Elevated")
        self.assertEqual(credit_regime(8.0)["label"], "Stressed")

    def test_na(self):
        self.assertEqual(credit_regime(None)["level"], "na")
        self.assertEqual(credit_regime(float("nan"))["level"], "na")


class TestCurveRegime(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(curve_regime(-0.3, -0.2)["shape"], "Inverted")
        self.assertEqual(curve_regime(-0.1, 0.2)["shape"], "Partially inverted")
        self.assertEqual(curve_regime(0.8, 0.9)["shape"], "Steep")
        self.assertEqual(curve_regime(0.2, 0.3)["shape"], "Flat-to-normal")

    def test_levels(self):
        self.assertEqual(curve_regime(-0.3, -0.2)["level"], "bad")
        self.assertEqual(curve_regime(-0.1, 0.2)["level"], "warn")
        self.assertEqual(curve_regime(0.8, 0.9)["level"], "ok")

    def test_direction(self):
        self.assertEqual(curve_regime(0.5, 0.6, spread_2y_prior=0.3)["direction"], "steepening")
        self.assertEqual(curve_regime(0.3, 0.4, spread_2y_prior=0.5)["direction"], "flattening")
        self.assertEqual(curve_regime(0.5, 0.6, spread_2y_prior=0.5)["direction"], "stable")
        self.assertEqual(curve_regime(0.5, 0.6)["direction"], "")  # no prior

    def test_na(self):
        self.assertEqual(curve_regime(None, 0.2)["level"], "na")


class TestFedPath(unittest.TestCase):
    def test_directions(self):
        self.assertEqual(fed_path(3.6, 4.5)["direction"], "Easing")
        self.assertEqual(fed_path(5.0, 4.0)["direction"], "Tightening")
        self.assertEqual(fed_path(4.0, 4.05)["direction"], "On hold")

    def test_change_value_and_levels(self):
        r = fed_path(3.6, 4.5)
        self.assertAlmostEqual(r["change"], -0.9, places=6)
        self.assertEqual(r["level"], "ok")
        self.assertEqual(fed_path(5.0, 4.0)["level"], "warn")

    def test_na_and_no_prior(self):
        self.assertEqual(fed_path(None, 4.0)["direction"], "n/a")
        self.assertEqual(fed_path(4.0, None)["direction"], "—")


if __name__ == "__main__":
    unittest.main()
