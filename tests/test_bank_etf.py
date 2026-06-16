"""
Tests for data/bank_etf.py — the bank-sector ETF deep-dive reducers behind
Market & Macro's "Bank Sector" section (docs/HOME-MACRO-PLAN.md §2).

Pure-math pins on synthetic OHLCV (no network): period return, period
high/low + the high's date, drawdown-from-high, average volume, and the
underwater drawdown series. n/a paths (empty / missing-column frames) are
checked to return None / empty, never raise — the live render is verified in
production where the FMP key exists.
"""
import unittest

import pandas as pd

from data.bank_etf import compute_stats, window_cutoff, parse_market_data


def _ohlcv(closes, volumes=None):
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    data = {"date": dates, "close": closes}
    if volumes is not None:
        data["volume"] = volumes
    return pd.DataFrame(data)


class TestComputeStats(unittest.TestCase):
    def test_basic_stats(self):
        # closes peak at 120 (day 3), end at 90 → -25% from high; +12.5% return.
        closes = [80, 100, 120, 110, 90]
        vols = [1000, 2000, 3000, 1000, 1000]
        s = compute_stats(_ohlcv(closes, vols))
        self.assertAlmostEqual(s["last"], 90.0)
        self.assertAlmostEqual(s["period_return_pct"], (90 / 80 - 1) * 100, places=6)
        self.assertAlmostEqual(s["period_high"], 120.0)
        self.assertEqual(s["period_high_date"], pd.Timestamp("2026-01-03"))
        self.assertAlmostEqual(s["period_low"], 80.0)
        self.assertAlmostEqual(s["drawdown_from_high_pct"], (90 / 120 - 1) * 100, places=6)
        self.assertAlmostEqual(s["avg_volume"], 1600.0)

    def test_at_new_high_drawdown_zero(self):
        s = compute_stats(_ohlcv([80, 100, 120]))
        self.assertAlmostEqual(s["drawdown_from_high_pct"], 0.0, places=9)
        self.assertIsNone(s["avg_volume"])  # no volume column

    def test_empty_and_missing_column(self):
        self.assertIsNone(compute_stats(pd.DataFrame())["last"])
        self.assertIsNone(compute_stats(pd.DataFrame({"date": [1], "x": [2]}))["last"])
        self.assertIsNone(compute_stats(None)["last"])


class TestWindowCutoff(unittest.TestCase):
    LAST = pd.Timestamp("2026-06-16")

    def test_calendar_offsets(self):
        self.assertEqual(window_cutoff("1M", self.LAST), pd.Timestamp("2026-05-16"))
        self.assertEqual(window_cutoff("3M", self.LAST), pd.Timestamp("2026-03-16"))
        self.assertEqual(window_cutoff("6M", self.LAST), pd.Timestamp("2025-12-16"))
        self.assertEqual(window_cutoff("1Y", self.LAST), pd.Timestamp("2025-06-16"))
        self.assertEqual(window_cutoff("3Y", self.LAST), pd.Timestamp("2023-06-16"))
        self.assertEqual(window_cutoff("5Y", self.LAST), pd.Timestamp("2021-06-16"))

    def test_ytd_is_jan_first_of_latest_year(self):
        self.assertEqual(window_cutoff("YTD", self.LAST), pd.Timestamp("2026-01-01"))

    def test_unknown_falls_back_to_1y(self):
        self.assertEqual(window_cutoff("ZZ", self.LAST), pd.Timestamp("2025-06-16"))


class TestParseMarketData(unittest.TestCase):
    # Real FMP Premium response shapes captured live for KRE (2026-06-16).
    QUOTE = {"symbol": "KRE", "price": 72.345, "changePercentage": 0.15921,
             "change": 0.115, "volume": 6515280, "dayLow": 72.26, "dayHigh": 73.285,
             "yearHigh": 74.27, "yearLow": 55.55, "marketCap": 3743573703,
             "open": 72.78, "previousClose": 72.23}
    INFO = {"symbol": "KRE", "expenseRatio": 0.35, "assetsUnderManagement": 4295660000,
            "avgVolume": 15540381.8, "nav": 73.43, "holdingsCount": 147}

    def test_maps_quote_and_info_fields(self):
        m = parse_market_data(self.QUOTE, self.INFO)
        self.assertAlmostEqual(m["price"], 72.345)
        self.assertAlmostEqual(m["change"], 0.115)
        self.assertAlmostEqual(m["change_pct"], 0.15921)  # already a percent
        self.assertAlmostEqual(m["prev_close"], 72.23)
        self.assertAlmostEqual(m["open"], 72.78)
        self.assertAlmostEqual(m["day_low"], 72.26)
        self.assertAlmostEqual(m["day_high"], 73.285)
        self.assertAlmostEqual(m["year_low"], 55.55)
        self.assertAlmostEqual(m["year_high"], 74.27)
        self.assertEqual(m["volume"], 6515280)
        self.assertEqual(m["market_cap"], 3743573703)
        self.assertEqual(m["aum"], 4295660000)
        self.assertAlmostEqual(m["nav"], 73.43)
        self.assertAlmostEqual(m["expense_ratio"], 0.35)
        self.assertAlmostEqual(m["avg_volume"], 15540381.8)

    def test_missing_info_leaves_fund_fields_none(self):
        m = parse_market_data(self.QUOTE, None)
        self.assertAlmostEqual(m["price"], 72.345)
        self.assertIsNone(m["aum"])
        self.assertIsNone(m["nav"])
        self.assertIsNone(m["expense_ratio"])

    def test_both_none(self):
        m = parse_market_data(None, None)
        self.assertTrue(all(v is None for v in m.values()))


if __name__ == "__main__":
    unittest.main()
