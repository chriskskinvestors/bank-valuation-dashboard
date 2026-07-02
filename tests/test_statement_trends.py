"""Grouped statement-trend charts (2026-06-14).

Two guarantees:
  1. Every metric key in a tab's trend set exists in METRICS_BY_KEY AND has an
     FDIC field — a typo'd key would otherwise silently render a blank chart.
  2. grouped_trend_chart scales $ fields to $B, leaves % as-is, and adds a
     secondary y-axis only when a group mixes dollar levels with a ratio.
"""
import sys
import types
import unittest

# ui.financials_statements imports streamlit + streamlit.components.v1 at load.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
# The statement pages decorate at module load with @st.fragment (bare and
# @st.fragment(run_every=...)); the identity-decorator lambda covers both.
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)
_c = types.ModuleType("streamlit.components")
_c1 = types.ModuleType("streamlit.components.v1")
_c.v1 = _c1
_st.components = _c
sys.modules.setdefault("streamlit.components", _c)
sys.modules.setdefault("streamlit.components.v1", _c1)

import pandas as pd

from config import METRICS_BY_KEY
from ui.financials_statements import _DEFAULT_TRENDS, _BS_TRENDS, _INCOME_TRENDS
from ui.charts import grouped_trend_chart


class TestTrendKeysResolve(unittest.TestCase):
    def _check(self, trends, label):
        for title, keys in trends:
            self.assertTrue(keys, f"{label}: '{title}' has no keys")
            for k in keys:
                m = METRICS_BY_KEY.get(k)
                self.assertIsNotNone(
                    m, f"{label}: '{title}' key '{k}' not in METRICS_BY_KEY")
                self.assertIsNotNone(
                    m.get("fdic_field"),
                    f"{label}: '{title}' key '{k}' has no fdic_field — chart "
                    f"would render blank")

    def test_default_trends_resolve(self):
        self._check(_DEFAULT_TRENDS, "_DEFAULT_TRENDS")

    def test_bs_trends_resolve(self):
        self._check(_BS_TRENDS, "_BS_TRENDS")

    def test_income_trends_resolve(self):
        # Income Statement has its own dollar-P&L trend set (≠ Performance's
        # ratio set), so the two tabs no longer render identical charts.
        self._check(_INCOME_TRENDS, "_INCOME_TRENDS")

    def test_bs_has_full_chart_set(self):
        # Same-scale regroup (2026-06-26): 8 charts, each grouping like-magnitude
        # series so the trends read. Pin the count so a future edit can't quietly
        # drop the set back to a stub.
        self.assertEqual(len(_BS_TRENDS), 8)


class TestGroupedTrendChart(unittest.TestCase):
    def _df(self):
        return pd.DataFrame({
            "REPDTE": pd.to_datetime(["2023-12-31", "2024-12-31", "2025-12-31"]),
            "ASSET":    [16_000_000, 16_200_000, 16_338_071],   # $thousands
            "LNLSNET":  [11_000_000, 11_300_000, 11_600_000],
            "DEP":      [13_000_000, 13_100_000, 13_200_000],
            "LNLSDEPR": [85.0, 86.0, 84.3],                     # % ratio
            "ROA":      [1.1, 1.2, 1.05],
            "NIMY":     [3.5, 3.7, 3.9],
        })

    def test_dollar_only_group_single_axis(self):
        fig = grouped_trend_chart(self._df(), ["total_assets", "total_deposits"], "Size")
        self.assertEqual(len(fig.data), 2)
        # No secondary axis is even created for a single-unit group.
        self.assertNotIn("yaxis2", fig.layout.to_plotly_json())

    def test_mixed_group_gets_secondary_axis(self):
        fig = grouped_trend_chart(
            self._df(), ["total_loans", "total_deposits", "loans_to_deposits"], "L/D")
        self.assertEqual(len(fig.data), 3)
        layout = fig.layout.to_plotly_json()
        self.assertEqual(layout["yaxis"]["title"]["text"], "$B")    # primary = dollars
        self.assertEqual(layout["yaxis2"]["title"]["text"], "%")    # secondary = ratio

    def test_dollars_scaled_to_billions(self):
        fig = grouped_trend_chart(self._df(), ["total_assets"], "Assets")
        # ASSET 16,338,071 ($000) -> 16.338071 ($B)
        self.assertAlmostEqual(fig.data[0].y[-1], 16.338071, places=4)

    def test_pct_not_scaled(self):
        fig = grouped_trend_chart(self._df(), ["roaa", "nim"], "Returns")
        self.assertEqual(fig.data[0].y[-1], 1.05)              # ROA untouched

    def test_unknown_field_skipped_not_crashed(self):
        # ln_credit_card (LNCRCD) isn't in this df -> skipped, no error.
        fig = grouped_trend_chart(self._df(), ["total_assets", "ln_credit_card"], "X")
        self.assertEqual(len(fig.data), 1)

    def test_empty_df_no_raise(self):
        fig = grouped_trend_chart(pd.DataFrame(), ["total_assets"], "X")
        self.assertEqual(len(fig.data), 0)


if __name__ == "__main__":
    unittest.main()
