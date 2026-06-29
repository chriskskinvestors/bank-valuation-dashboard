"""
Tests for the Rates · Credit board's anchor math and row resolver.

Pins:
  • rate_anchors_live derives level / d1 / w1 / m1 / ytd / lo / hi from one
    year of a daily FRED series (positional offsets + Jan-1 + min/max).
  • _af_row_anchors: a live tenor overlays intraday level/1D/1W on the FRED
    history anchors; a computed (calc) spread subtracts leg-by-leg and reports
    NO 52-week range (lo/hi None) — never a wrong min-of-a-difference.

Run:  python -m unittest tests.test_rates_board
"""
from __future__ import annotations
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
sys.modules.setdefault("streamlit", _st)


def _ramp_series(n=300, start=3.00, step=0.01):
    import pandas as pd
    dates = pd.date_range(end=pd.Timestamp(date.today()), periods=n, freq="D")
    vals = [start + i * step for i in range(n)]
    return pd.DataFrame({"date": dates, "value": vals})


class TestAnchors(unittest.TestCase):
    def test_anchor_offsets_and_range(self):
        from data import live_rates
        df = _ramp_series()
        vals = df["value"].tolist()
        with patch("data.fred_client.fetch_series", return_value=df):
            a = live_rates.rate_anchors_live("DGS10")
        self.assertIsNotNone(a)
        self.assertAlmostEqual(a["level"], vals[-1])
        self.assertAlmostEqual(a["d1"], vals[-2])     # ~1 business day
        self.assertAlmostEqual(a["w1"], vals[-6])     # ~1 week
        self.assertAlmostEqual(a["m1"], vals[-22])    # ~1 month
        self.assertAlmostEqual(a["lo"], min(vals))
        self.assertAlmostEqual(a["hi"], max(vals))
        # ytd = first observation on/after Jan 1 of the current year
        jan1 = __import__("datetime").datetime(date.today().year, 1, 1)
        ytd_vals = [v for d, v in zip(df["date"].tolist(), vals)
                    if d.to_pydatetime() >= jan1]
        self.assertAlmostEqual(a["ytd"], ytd_vals[0])
        # per-window min/max for the range bars (1W ≈ last 6 obs, 1M ≈ last 22)
        self.assertAlmostEqual(a["w_lo"], min(vals[-6:]))
        self.assertAlmostEqual(a["w_hi"], max(vals[-6:]))
        self.assertAlmostEqual(a["m_lo"], min(vals[-22:]))
        self.assertAlmostEqual(a["m_hi"], max(vals[-22:]))
        self.assertAlmostEqual(a["y_lo"], min(ytd_vals))
        self.assertAlmostEqual(a["y_hi"], max(ytd_vals))

    def test_anchor_none_on_empty(self):
        import pandas as pd
        from data import live_rates
        with patch("data.fred_client.fetch_series", return_value=pd.DataFrame()):
            self.assertIsNone(live_rates.rate_anchors_live("DGS10"))


class TestRowResolver(unittest.TestCase):
    def _home(self):
        import importlib
        return importlib.import_module("ui.home")

    def test_tenor_live_overlay_keeps_fred_history(self):
        home = self._home()
        bundle = {"DGS10": {"level": 4.40, "d1": 4.38, "w1": 4.30,
                            "m1": 4.10, "ytd": 4.00, "lo": 3.50, "hi": 4.60}}
        ly = {"10Y": [4.51, 4.46, 4.42]}   # live intraday level/prior/wk
        an, is_live = home._af_row_anchors("tenor", "10Y", None, bundle, ly)
        self.assertTrue(is_live)
        self.assertAlmostEqual(an["level"], 4.51)   # live overrides
        self.assertAlmostEqual(an["d1"], 4.46)
        self.assertAlmostEqual(an["w1"], 4.42)
        self.assertAlmostEqual(an["m1"], 4.10)      # FRED history preserved
        self.assertAlmostEqual(an["ytd"], 4.00)
        self.assertAlmostEqual(an["lo"], 3.50)

    def test_tenor_falls_back_to_fred_when_no_live(self):
        home = self._home()
        bundle = {"DGS10": {"level": 4.40, "d1": 4.38, "w1": 4.30,
                            "m1": 4.10, "ytd": 4.00, "lo": 3.50, "hi": 4.60}}
        an, is_live = home._af_row_anchors("tenor", "10Y", None, bundle, {})
        self.assertFalse(is_live)
        self.assertAlmostEqual(an["level"], 4.40)   # FRED daily

    def test_calc_spread_subtracts_and_has_no_range(self):
        home = self._home()
        bundle = {
            "DGS30": {"level": 4.95, "d1": 4.90, "w1": 4.85, "m1": 4.80,
                      "ytd": 4.50, "lo": 4.20, "hi": 5.00},
            "DGS10": {"level": 4.50, "d1": 4.48, "w1": 4.40, "m1": 4.30,
                      "ytd": 4.00, "lo": 3.80, "hi": 4.60},
        }
        an, is_live = home._af_row_anchors("calc", "DGS30", "DGS10", bundle, {})
        self.assertFalse(is_live)
        self.assertAlmostEqual(an["level"], 0.45)   # 4.95 − 4.50
        self.assertAlmostEqual(an["ytd"], 0.50)     # 4.50 − 4.00
        # every range (52wk + each window) is n/a for a difference
        for k in ("lo", "hi", "w_lo", "w_hi", "m_lo", "m_hi", "y_lo", "y_hi"):
            self.assertIsNone(an[k], f"{k} must be None for a calc spread")

    def test_neg_flips_sign_and_swaps_range(self):
        home = self._home()
        out = home._neg({"level": 0.30, "d1": 0.28, "w1": 0.25, "m1": 0.20,
                         "ytd": 0.10, "lo": 0.05, "hi": 0.50,
                         "w_lo": 0.10, "w_hi": 0.40, "m_lo": 0.08, "m_hi": 0.45,
                         "y_lo": 0.05, "y_hi": 0.48})
        self.assertAlmostEqual(out["level"], -0.30)
        self.assertAlmostEqual(out["ytd"], -0.10)
        self.assertAlmostEqual(out["lo"], -0.50)    # -hi
        self.assertAlmostEqual(out["hi"], -0.05)    # -lo
        # every window range flips [lo,hi] -> [-hi,-lo] too
        self.assertAlmostEqual(out["w_lo"], -0.40)
        self.assertAlmostEqual(out["w_hi"], -0.10)
        self.assertAlmostEqual(out["m_lo"], -0.45)
        self.assertAlmostEqual(out["m_hi"], -0.08)
        self.assertAlmostEqual(out["y_lo"], -0.48)
        self.assertAlmostEqual(out["y_hi"], -0.05)

    def test_spread_is_short_minus_long_with_live_and_range(self):
        # kind "spread" now quotes 2Y − 10Y (short − long): negated T10Y2Y
        # anchors keep a real 52-week range, live overlay flips too.
        home = self._home()
        bundle = {"T10Y2Y": {"level": 0.34, "d1": 0.33, "w1": 0.30, "m1": 0.20,
                             "ytd": 0.10, "lo": 0.05, "hi": 0.50}}
        ly = {"10Y": [4.52, 4.46, 4.42], "2Y": [4.25, 4.23, 4.18]}
        an, is_live = home._af_row_anchors("spread", "T10Y2Y", None, bundle, ly)
        self.assertTrue(is_live)
        self.assertAlmostEqual(an["level"], 4.25 - 4.52)   # 2Y − 10Y (negative)
        self.assertAlmostEqual(an["m1"], -0.20)            # negated FRED history
        self.assertAlmostEqual(an["lo"], -0.50)            # range preserved, flipped
        self.assertAlmostEqual(an["hi"], -0.05)

    def test_fredn_negates_series_keeps_range(self):
        home = self._home()
        bundle = {"T10Y3M": {"level": 0.23, "d1": 0.22, "w1": 0.20, "m1": 0.15,
                             "ytd": 0.05, "lo": -0.10, "hi": 0.60}}
        an, is_live = home._af_row_anchors("fredn", "T10Y3M", None, bundle, {})
        self.assertFalse(is_live)
        self.assertAlmostEqual(an["level"], -0.23)   # 3M − 10Y
        self.assertAlmostEqual(an["lo"], -0.60)      # -hi
        self.assertAlmostEqual(an["hi"], 0.10)       # -lo


if __name__ == "__main__":
    unittest.main()
