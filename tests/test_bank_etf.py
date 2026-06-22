"""
Tests for data/bank_etf.py — the bank-sector ETF deep-dive reducers behind
Market & Macro's "Bank Sector" section (docs/HOME-MACRO-PLAN.md §2).

Pure-math pins on synthetic OHLCV (no network): period return (incl. the 1D
gap-inclusive baseline), period high/low + the high's date, drawdown-from-high,
average volume, the latest-session split, and the window→fetch warm contract.
get_etf_history's branching is pinned by mocking get_history (no network). n/a
paths (empty / missing-column / cold-cache frames) are checked to return None /
empty, never raise — the live render is verified in production with the FMP key.
"""
import unittest
from unittest import mock

import pandas as pd

import data.bank_etf as be
from data.bank_etf import (
    FETCH_PERIODS, PERIODS, _fetch_period, compute_stats, get_etf_history,
    latest_session, parse_market_data, window_cutoff,
)


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


class TestComputeStatsBaseline(unittest.TestCase):
    def test_baseline_includes_gap(self):
        # Session opens 100, ends 110; prior close 95 → the 1D return measures
        # from the prior close (the day's full move incl. the opening gap).
        s = compute_stats(_ohlcv([100, 105, 110]), baseline=95.0)
        self.assertAlmostEqual(s["period_return_pct"], (110 / 95 - 1) * 100, places=6)
        # High / drawdown are unaffected by the baseline (still over the bars).
        self.assertAlmostEqual(s["period_high"], 110.0)
        self.assertAlmostEqual(s["drawdown_from_high_pct"], 0.0, places=9)

    def test_baseline_none_falls_back_to_first_bar(self):
        s = compute_stats(_ohlcv([100, 110]), baseline=None)
        self.assertAlmostEqual(s["period_return_pct"], 10.0, places=6)

    def test_baseline_zero_falls_back_to_first_bar(self):
        # A 0/None baseline must not divide-by-zero — fall back to the first bar.
        s = compute_stats(_ohlcv([100, 110]), baseline=0)
        self.assertAlmostEqual(s["period_return_pct"], 10.0, places=6)


class TestLatestSession(unittest.TestCase):
    def test_picks_only_the_latest_calendar_day(self):
        dates = [pd.Timestamp("2026-06-18 15:45"), pd.Timestamp("2026-06-19 09:30"),
                 pd.Timestamp("2026-06-19 10:00")]
        out = latest_session(pd.DataFrame({"date": dates, "close": [10, 20, 21]}))
        self.assertEqual(out["close"].tolist(), [20, 21])

    def test_empty_in_empty_out(self):
        self.assertTrue(latest_session(pd.DataFrame()).empty)
        self.assertTrue(latest_session(None).empty)


class TestFetchContract(unittest.TestCase):
    def test_fetch_period_mapping(self):
        self.assertEqual(_fetch_period("1D"), "1W")
        self.assertEqual(_fetch_period("1W"), "1W")
        self.assertEqual(_fetch_period("3Y"), "5Y")
        self.assertEqual(_fetch_period("5Y"), "5Y")
        for p in ("1M", "3M", "6M", "YTD", "1Y"):
            self.assertEqual(_fetch_period(p), "1Y")

    def test_warm_contract_covers_every_window(self):
        # The warm contract (what the job pre-fetches) must equal the set of
        # underlying fetches behind EVERY selectable window — guards the
        # "add a window, forget to warm it" drift.
        self.assertEqual(set(FETCH_PERIODS), {_fetch_period(p) for p in PERIODS})
        self.assertEqual(FETCH_PERIODS, sorted(set(FETCH_PERIODS)))


class TestGetEtfHistory(unittest.TestCase):
    # IMPORTANT: fixtures use STRING dates — that's what a cache_only read
    # returns (the cache JSON-serializes Timestamps with default=str). Real
    # datetime fixtures would pass against code that forgot to coerce and mask
    # the exact "No history" regression (overlay-render-cache-only-warm memory).
    # get_etf_history must coerce (via _clean_history) before any datetime op.
    def _intraday(self):
        # Two trading days of intraday bars (3 on the 18th, 4 on the 19th).
        dates = list(pd.date_range("2026-06-18 09:30", periods=3, freq="15min")) + \
                list(pd.date_range("2026-06-19 09:30", periods=4, freq="15min"))
        return pd.DataFrame({"date": [str(d) for d in dates],
                             "close": [10, 11, 12, 20, 21, 22, 23],
                             "volume": [1] * 7})

    def test_1d_returns_latest_session_only_and_passes_cache_only(self):
        with mock.patch.object(be, "get_history", return_value=self._intraday()) as gh:
            out = get_etf_history("KRE", period="1D", cache_only=True)
        gh.assert_called_once_with("KRE", period="1W", cache_only=True)
        self.assertEqual(len(out), 4)
        self.assertTrue((out["date"].dt.normalize() == pd.Timestamp("2026-06-19")).all())

    def test_1w_returns_full_week_from_1w_fetch(self):
        with mock.patch.object(be, "get_history", return_value=self._intraday()) as gh:
            out = get_etf_history("KRE", period="1W")
        gh.assert_called_once_with("KRE", period="1W", cache_only=False)
        self.assertEqual(len(out), 7)

    def test_eod_window_uses_1y_fetch_and_slices_by_cutoff(self):
        dates = [str(d) for d in pd.date_range("2025-01-01", periods=500, freq="D")]
        df = pd.DataFrame({"date": dates, "close": list(range(500)), "volume": [1] * 500})
        with mock.patch.object(be, "get_history", return_value=df) as gh:
            out = get_etf_history("KRE", period="1M")
        gh.assert_called_once_with("KRE", period="1Y", cache_only=False)
        cutoff = window_cutoff("1M", out["date"].iloc[-1])
        self.assertTrue((out["date"] >= cutoff).all())
        self.assertLess(len(out), len(df))

    def test_multi_year_window_uses_5y_fetch(self):
        df = pd.DataFrame({"date": [str(d) for d in pd.date_range("2021-01-01", periods=10, freq="D")],
                           "close": list(range(10))})
        with mock.patch.object(be, "get_history", return_value=df) as gh:
            get_etf_history("KRE", period="3Y")
        gh.assert_called_once_with("KRE", period="5Y", cache_only=False)

    def test_empty_when_cache_cold(self):
        with mock.patch.object(be, "get_history", return_value=pd.DataFrame()):
            self.assertTrue(get_etf_history("KRE", period="1D", cache_only=True).empty)


if __name__ == "__main__":
    unittest.main()
