"""UX-P1-02: Home's computed spread rows (3M − 5Y, 10Y − 30Y, FF − 2Y) had no
range bars — the job-built rates bundle now carries each spread's
date-aligned a − b history anchors (data/live_rates.calc_spread_anchors)."""
import sys
import unittest
from datetime import date
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: E402
_streamlit_stub.install()
import pandas as pd  # noqa: E402

import data.live_rates as lr  # noqa: E402


def _frame(dates, vals):
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": vals})


class T(unittest.TestCase):
    def test_diff_is_date_aligned_and_ranges_from_diff(self):
        end = pd.Timestamp(date.today())
        days = pd.date_range(end=end, periods=60, freq="D")        # DFF: 7-day
        bdays = [d for d in days if d.weekday() < 5]                # DGS2: bdays
        a = _frame(days, [3.0 + 0.01 * i for i in range(len(days))])
        b = _frame(bdays, [4.0 + (0.03 if i % 2 else -0.03) for i in range(len(bdays))])
        a.loc[len(a) - 1, "value"] = None                           # a missing last day
        series = {"DFF": a, "DGS2": b}
        with patch("data.fred_client.fetch_series",
                   side_effect=lambda sid, years=1: series[sid].copy()):
            an = lr.calc_spread_anchors("DFF", "DGS2")
        # hand-compute over dates where BOTH legs have a value
        m = a.dropna().merge(b, on="date", suffixes=("_a", "_b"))
        d = (m["value_a"] - m["value_b"]).tolist()
        self.assertTrue(all(x.weekday() < 5 for x in m["date"]))
        self.assertAlmostEqual(an["level"], d[-1])
        self.assertAlmostEqual(an["d1"], d[-2])
        self.assertAlmostEqual(an["w1"], d[-6])
        self.assertAlmostEqual(an["lo"], min(d))
        self.assertAlmostEqual(an["hi"], max(d))
        self.assertAlmostEqual(an["w_lo"], min(d[-6:]))
        self.assertAlmostEqual(an["m_hi"], max(d[-22:]))
        # the per-leg-extremes shortcut would be wrong here
        self.assertNotAlmostEqual(an["lo"], a["value"].min() - b["value"].max())

    def test_none_when_a_leg_is_empty(self):
        with patch("data.fred_client.fetch_series", return_value=pd.DataFrame()):
            self.assertIsNone(lr.calc_spread_anchors("DFF", "DGS2"))

    def test_bundle_carries_calc_keys(self):
        with patch.object(lr, "rate_anchors_live", return_value={"level": 1.0}), \
             patch.object(lr, "calc_spread_anchors", return_value={"level": -0.4}):
            bundle = lr.build_rates_anchor_bundle()
        for k in ("DGS3MO-DGS5", "DGS10-DGS30", "DFF-DGS2"):
            self.assertEqual(bundle[k], {"level": -0.4})
        # the pairs are exactly the Home board's calc rows
        from ui import home
        calc = {(a, b) for _s, rows in home._AF_RATES_SECTIONS
                for _l, kind, a, b in rows if kind == "calc"}
        self.assertEqual(calc, set(lr.RATES_CALC_SPREADS))


if __name__ == "__main__":
    unittest.main()
