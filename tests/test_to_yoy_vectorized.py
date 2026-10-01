"""
data/macro_indicators.to_yoy is a vectorized as-of join that must return
EXACTLY what the per-row _yoy_at loop returned (2026-10-01: the loop was
O(n²) and dominated a cold Market & Macro render — 17.6 s on prod).

Pins: equality with the reference loop on series that exercise every edge
(irregular gaps, a leap day, a zero base, a NaN value, a date earlier than any
year-ago match, duplicate-free sorted output), plus hand-computed values.
"""
import unittest

import numpy as np
import pandas as pd

from data.macro_indicators import _clean, _yoy_at, to_yoy


def _reference(df):
    """The pre-2026-10-01 implementation, verbatim."""
    d = _clean(df)
    if d.empty:
        return d
    yoy = [_yoy_at(d, i) for i in range(len(d))]
    res = pd.DataFrame({"date": d["date"], "value": yoy}).dropna(subset=["value"])
    return res.reset_index(drop=True)


def _frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": values})


class TestToYoyMatchesLoop(unittest.TestCase):
    def assertSameAsLoop(self, df):
        got, want = to_yoy(df), _reference(df)
        self.assertEqual(list(got["date"]), list(want["date"]))
        np.testing.assert_allclose(got["value"].to_numpy(dtype=float),
                                   want["value"].to_numpy(dtype=float),
                                   rtol=0, atol=1e-12)

    def test_hand_computed_monthly(self):
        df = _frame(["2024-01-31", "2024-06-30", "2025-01-31", "2025-06-30"],
                    [100.0, 110.0, 103.0, 99.0])
        got = to_yoy(df)
        self.assertEqual(list(got["date"]), list(pd.to_datetime(["2025-01-31", "2025-06-30"])))
        # 103/100 − 1 = +3 %; 99/110 − 1 = −10 %
        np.testing.assert_allclose(got["value"], [3.0, -10.0], atol=1e-12)
        self.assertSameAsLoop(df)

    def test_irregular_gaps_take_last_observation_on_or_before(self):
        # Year-ago target 2025-03-15 → the last obs on/before it is 2025-03-01.
        df = _frame(["2025-02-01", "2025-03-01", "2025-04-01", "2026-03-15"],
                    [50.0, 40.0, 70.0, 44.0])
        got = to_yoy(df)
        np.testing.assert_allclose(got["value"], [10.0], atol=1e-12)   # 44/40 − 1
        self.assertSameAsLoop(df)

    def test_leap_day_zero_base_and_nan(self):
        df = _frame(["2023-02-28", "2023-03-01", "2024-02-28", "2024-02-29",
                     "2024-03-01", "2025-02-28", "2025-03-01"],
                    [0.0, 10.0, 12.0, np.nan, 15.0, 18.0, 30.0])
        # 2024-02-28 → base 2023-02-28 = 0 → dropped (no division by zero);
        # the NaN row is removed by _clean before anything else.
        self.assertSameAsLoop(df)
        self.assertNotIn(pd.Timestamp("2024-02-28"), list(to_yoy(df)["date"]))

    def test_daily_ten_years_random_walk(self):
        rng = np.random.default_rng(7)
        dates = pd.bdate_range("2015-01-02", "2025-12-31")
        keep = rng.random(len(dates)) > 0.03            # punch random holes
        vals = 100 + np.cumsum(rng.normal(0, 0.5, len(dates)))
        self.assertSameAsLoop(pd.DataFrame({"date": dates[keep], "value": vals[keep]}))

    def test_unsorted_input_and_empty(self):
        df = _frame(["2025-06-30", "2024-06-30", "2025-01-31", "2024-01-31"],
                    [99.0, 110.0, 103.0, 100.0])
        self.assertSameAsLoop(df)
        self.assertTrue(to_yoy(pd.DataFrame(columns=["date", "value"])).empty)
        self.assertTrue(to_yoy(None).empty)


if __name__ == "__main__":
    unittest.main()
