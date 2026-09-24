"""Long-run context (DEEP-HISTORY-PLAN.md Phase 3): percentile of today
vs the bank's own history. Every expectation is hand-computed; the pins are
the rank definition, the minimum-observation guard (n/a, never a percentile
on a handful of points), None handling (absent ≠ zero), and the TCE/TA
formula matching the Financial Highlights table.
"""
import unittest

import pandas as pd

from tests import _streamlit_stub  # noqa: F401

from analysis import long_run as lr


class TestPercentileRank(unittest.TestCase):
    def test_hand_computed(self):
        # 4 of 5 observations at or below 4 → 80th percentile.
        self.assertEqual(lr.percentile_rank([1, 2, 3, 4, 5], 4), 80.0)
        # Ties count as "at or below": current equal to the max → 100.
        self.assertEqual(lr.percentile_rank([1, 2, 3, 4, 5], 5), 100.0)
        # Below every observation → 0.
        self.assertEqual(lr.percentile_rank([1, 2, 3], 0.5), 0.0)

    def test_absent_and_garbage(self):
        self.assertIsNone(lr.percentile_rank([1, 2, 3], None))
        self.assertIsNone(lr.percentile_rank([], 1))
        # None / NaN / strings in history are dropped, not treated as 0.
        self.assertEqual(lr.percentile_rank([None, float("nan"), "x", 1, 3], 2), 50.0)


class TestContext(unittest.TestCase):
    def test_order_statistics_hand_computed(self):
        c = lr.context(list(range(1, 11)), 7, min_obs=8, start="a", end="b")
        self.assertEqual(c["n"], 10)
        self.assertEqual(c["median"], 5.5)
        self.assertEqual(c["low"], 1.0)
        self.assertEqual(c["high"], 10.0)
        # pandas linear quantile: p10 of 1..10 = 1.9, p90 = 9.1
        self.assertAlmostEqual(c["p10"], 1.9)
        self.assertAlmostEqual(c["p90"], 9.1)
        self.assertEqual(c["pct_rank"], 70.0)
        self.assertEqual((c["start"], c["end"]), ("a", "b"))

    def test_too_few_observations_is_none(self):
        self.assertIsNone(lr.context([1, 2, 3, 4, 5, 6, 7], 3, min_obs=8))
        self.assertIsNotNone(lr.context([1, 2, 3, 4, 5, 6, 7, 8], 3, min_obs=8))

    def test_absent_current_is_none(self):
        self.assertIsNone(lr.context(list(range(20)), None, min_obs=8))


class TestMultipleContext(unittest.TestCase):
    def test_daily_series_uses_latest_day_as_current(self):
        dates = pd.date_range("2020-01-01", periods=300, freq="D")
        vals = [1.0] * 299 + [2.0]          # 299 days at 1.0x, today 2.0x
        val = pd.DataFrame({"date": dates, "ptbv": vals})
        c = lr.multiple_context(val, "ptbv")
        self.assertEqual(c["current"], 2.0)
        self.assertEqual(c["median"], 1.0)
        self.assertEqual(c["pct_rank"], 100.0)
        self.assertEqual(c["n"], 300)
        self.assertEqual(c["start"], dates[0])
        self.assertEqual(c["end"], dates[-1])

    def test_short_daily_history_is_none(self):
        val = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=100),
                            "ptbv": [1.2] * 100})
        self.assertIsNone(lr.multiple_context(val, "ptbv"))
        self.assertIsNone(lr.multiple_context(val, "pe"))     # column absent
        self.assertIsNone(lr.multiple_context(None, "ptbv"))


class TestFundamentalsContext(unittest.TestCase):
    @staticmethod
    def _recs(n, **fields):
        # newest first, like the loader; quarterly from 2026-06-30 backwards
        out = []
        for i in range(n):
            y, q = divmod(33 - i, 4)             # 33 = 2026Q2 in quarters since 2018Q1
            rec = {"REPDTE": f"{2018 + y}-{(q + 1) * 3:02d}-30"}
            for k, fn in fields.items():
                rec[k] = fn(i)
            out.append(rec)
        return out

    def test_tce_ta_matches_highlights_formula(self):
        # (1000 − 100) ÷ (10000 − 100) = 9.0909…%
        self.assertAlmostEqual(lr._tce_ta({"EQTOT": 1000, "INTAN": 100, "ASSET": 10000}),
                               900 / 9900 * 100)
        # Absent intangibles = none reported → treated as 0, like the table.
        self.assertAlmostEqual(lr._tce_ta({"EQTOT": 1000, "ASSET": 10000}), 10.0)
        # Absent equity or assets → n/a, never 0%.
        self.assertIsNone(lr._tce_ta({"INTAN": 5, "ASSET": 10000}))
        self.assertIsNone(lr._tce_ta({"EQTOT": 1000, "INTAN": 5}))

    def test_ratios_rank_newest_against_full_history(self):
        # ROA: 1.0 for 11 older quarters, newest 1.5 → 100th percentile;
        # NIMY: newest 3.0 among 11 quarters of 3.0 → 100 (ties), median 3.0.
        recs = self._recs(12, ROA=lambda i: 1.5 if i == 0 else 1.0,
                          NIMY=lambda i: 3.0, EEFFR=lambda i: 60 - i,
                          NTLNLSR=lambda i: None, EQTOT=lambda i: 1000,
                          INTAN=lambda i: 0, ASSET=lambda i: 10000)
        ctx = lr.fundamentals_context(recs)
        self.assertEqual(ctx["roaa"]["pct_rank"], 100.0)
        self.assertEqual(ctx["roaa"]["median"], 1.0)
        self.assertEqual(ctx["roaa"]["n"], 12)
        self.assertEqual(ctx["nim"]["pct_rank"], 100.0)
        # Efficiency newest 60 is the worst (highest) of 49..60 → 100th pct.
        self.assertEqual(ctx["efficiency"]["pct_rank"], 100.0)
        # NCO never reported → n/a, not a 0% series.
        self.assertIsNone(ctx["nco"])
        self.assertEqual(ctx["tce_ta"]["current"], 10.0)
        self.assertEqual(ctx["tce_ta"]["start"], pd.Timestamp("2023-09-30"))   # 12 quarters back
        self.assertEqual(ctx["tce_ta"]["end"], pd.Timestamp("2026-06-30"))

    def test_series_that_begins_late_ranks_over_its_own_span(self):
        # NTLNLSR only reported in the newest 9 quarters (of 30): rank over 9,
        # span starts where the series starts, never padded with zeros.
        recs = self._recs(30, NTLNLSR=lambda i: (0.1 * (9 - i)) if i < 9 else None)
        c = lr.fundamentals_context(recs)["nco"]
        self.assertEqual(c["n"], 9)
        self.assertEqual(c["pct_rank"], 100.0)            # newest 0.9 is the max
        self.assertEqual(c["start"], pd.Timestamp("2024-06-30"))

    def test_newest_quarter_missing_value_is_none_even_with_history(self):
        recs = self._recs(12, ROA=lambda i: None if i == 0 else 1.0)
        self.assertIsNone(lr.fundamentals_context(recs)["roaa"])

    def test_too_few_quarters_is_none(self):
        recs = self._recs(7, ROA=lambda i: 1.0)
        self.assertIsNone(lr.fundamentals_context(recs)["roaa"])


class TestValuationSeriesDepth(unittest.TestCase):
    """The ALL window anchors its per-share lookups on the deep store (MAX),
    not the 44-quarter card window — the multiple's history then runs as far
    back as SEC per-share data exists. Other windows keep today's 44."""

    def test_all_window_reads_deep_store_others_keep_44(self):
        from unittest.mock import patch
        import pandas as pd
        from ui import bank_detail as bd
        calls = []
        empty = pd.DataFrame()

        def deep(ticker, label, period="Quarterly", floor=20):
            calls.append(("deep", label, floor)); return empty

        def card(ticker, quarters):
            calls.append(("card", quarters)); return empty

        with patch("ui.history_range.load_hist_df_for_range", deep), \
             patch("data.loaders.load_fdic_hist_df", card):
            info = {"fdic_cert": 1, "cik": 2}
            self.assertIsNone(bd.valuation_series("X", info, "ALL"))
            self.assertIsNone(bd.valuation_series("X", info, "1Y"))
        self.assertEqual(calls, [("deep", "MAX", 44), ("card", 44)])


if __name__ == "__main__":
    unittest.main()
