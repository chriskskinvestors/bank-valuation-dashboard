"""Agent export `valuation_history` == the Corporate Profile P/TBV / P/E card.

The card (ui.bank_detail._valuation_history_chart → valuation_series) and the
export (data/export/tables_valuation) build their frame through ONE function,
analysis.valuation_history.build_valuation_series; they differ only in where
the inputs come from (live seams vs persisted caches). Pins, on a synthetic
bank with hand-computed values:

  1. The export's full daily frame == the chart's frame (assert_frame_equal),
     and every exported (sampled) row equals the chart's row for that date.
  2. The TBV step lands on the 10-Q FILING date (the chart's no-lookahead
     convention), not the period end; the day before still uses the old TBV.
  3. Gaps stay gaps: a quarter whose TBV is n/a exports None P/TBV for the
     days it is in effect (never the previous quarter's value, never 0).
  4. Column dict == frame columns (same order, ticker first), also when empty.
  5. Read-only: inputs come from the cache with max_age_s=None; a bank with no
     cached price history is skipped and counted, never fetched.
"""
import time
import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

import tests  # noqa: F401  (streamlit stub + isolated store + network guard)
from data import cache
from data import sec_client as sc
from data.export import tables_valuation as tv
from ui import bank_detail as bd

T, CIK, CERT = "TSTV", 9_999_001, 99_001
INFO = {"cik": CIK, "fdic_cert": CERT}
SHARES = 10_000_000
GW, CDI = 50_000_000, 5_000_000

# quarter-end → (parent equity, filed, form, intangibles tagged?)
BS = {
    "2024-12-31": (250_000_000, "2025-02-27", "10-K", True),   # TBV 19.50
    "2025-03-31": (255_000_000, "2025-05-06", "10-Q", True),   # 20.00
    "2025-06-30": (260_000_000, "2025-08-05", "10-Q", True),   # 20.50
    "2025-09-30": (265_000_000, "2025-11-04", "10-Q", False),  # n/a (untagged)
    "2025-12-31": (270_000_000, "2026-02-26", "10-K", True),   # 21.50
    "2026-03-31": (275_000_000, "2026-05-05", "10-Q", True),   # 22.00
    "2026-06-30": (280_000_000, "2026-08-04", "10-Q", True),   # 22.50
}
# (start, end, val, filed, form) — diluted EPS; Q4 derives as FY − 9M.
EPS = [
    ("2024-01-01", "2024-03-31", 0.50, "2024-05-06", "10-Q"),
    ("2024-04-01", "2024-06-30", 0.50, "2024-08-05", "10-Q"),
    ("2024-07-01", "2024-09-30", 0.50, "2024-11-04", "10-Q"),
    ("2024-01-01", "2024-12-31", 2.10, "2025-02-27", "10-K"),  # Q4-24 0.60
    ("2025-01-01", "2025-03-31", 0.55, "2025-05-06", "10-Q"),
    ("2025-04-01", "2025-06-30", 0.55, "2025-08-05", "10-Q"),
    ("2025-07-01", "2025-09-30", 0.55, "2025-11-04", "10-Q"),
    ("2025-01-01", "2025-12-31", 2.30, "2026-02-26", "10-K"),  # Q4-25 0.65
    ("2026-01-01", "2026-03-31", 0.60, "2026-05-05", "10-Q"),
    ("2026-04-01", "2026-06-30", 0.60, "2026-08-04", "10-Q"),
]


def _e(end, val, filed, form, start=None):
    e = {"end": end, "val": val, "filed": filed, "form": form,
         "accn": f"0000{CIK}-{filed[2:4]}-{filed[5:7]}{filed[8:10]}"}
    if start:
        e["start"] = start
    return e


def _facts() -> dict:
    ug = {"StockholdersEquity": {"units": {"USD": []}},
          "Goodwill": {"units": {"USD": []}},
          "FiniteLivedIntangibleAssetsNet": {"units": {"USD": []}},
          "PreferredStockValue": {"units": {"USD": []}},
          "CommonStockSharesOutstanding": {"units": {"shares": []}},
          "EarningsPerShareDiluted": {"units": {"USD/shares": []}}}
    for end, (eq, filed, form, tagged) in BS.items():
        ug["StockholdersEquity"]["units"]["USD"].append(_e(end, eq, filed, form))
        ug["PreferredStockValue"]["units"]["USD"].append(_e(end, 0, filed, form))
        ug["CommonStockSharesOutstanding"]["units"]["shares"].append(
            _e(end, SHARES, filed, form))
        if tagged:
            ug["Goodwill"]["units"]["USD"].append(_e(end, GW, filed, form))
            ug["FiniteLivedIntangibleAssetsNet"]["units"]["USD"].append(
                _e(end, CDI, filed, form))
    for start, end, v, filed, form in EPS:
        ug["EarningsPerShareDiluted"]["units"]["USD/shares"].append(
            _e(end, v, filed, form, start))
    return {"cik": CIK, "entityName": T, "facts": {"us-gaap": ug, "dei": {}}}


FDIC = [{"REPDTE": d.replace("-", ""), "CERT": CERT} for d in sorted(BS, reverse=True)]


def _px() -> pd.DataFrame:
    days = pd.bdate_range("2025-01-02", "2026-09-30")
    return pd.DataFrame({"date": days,
                         "close": [30.0 + (i % 7) * 0.1 for i in range(len(days))]})


def _seed(window="ALL", price=True):
    """Persist the bank's inputs exactly where the export reads them."""
    if price:
        recs = _px().assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d"))
        cache.put(f"fmp_history:{T}:{window}",
                  {"_ts": time.time(), "_v": recs.to_dict("records")})
    cache.put(f"fdic_hist:{T}", FDIC)
    cache.put(sc.company_facts_cache_key(CIK), _facts())


def _clear():
    for w in tv.PRICE_WINDOWS:
        cache.invalidate(f"fmp_history:{T}:{w}")
    cache.invalidate(f"fdic_hist:{T}")
    cache.invalidate(sc.company_facts_cache_key(CIK))


def _chart_frame(period="ALL"):
    """The card's series through its own (live) seams, stubbed."""
    facts = _facts()
    with patch("ui.history_range.load_hist_df_for_range",
               lambda *a, **k: pd.DataFrame(FDIC)), \
         patch("data.loaders.load_fdic_hist_df", lambda *a, **k: pd.DataFrame(FDIC)), \
         patch("data.fmp_client.get_history", lambda *a, **k: _px()), \
         patch.object(sc, "fetch_company_facts", lambda cik: facts):
        return bd.valuation_series(T, INFO, period)


def _export(tickers=(T,)):
    with patch("data.bank_mapping.get_bank_info", lambda t: INFO if t == T else None), \
         patch("data.fdic_history_store.deep_group_history", lambda *a, **k: []):
        return tv.build_valuation_history(list(tickers))


class _Base(unittest.TestCase):
    def setUp(self):
        _clear()
        self.addCleanup(_clear)
        # The dashboard's read-time overlays (they read per-accession SEC
        # caches): a pass-through here; TestOverlay drives a real marker.
        p = patch.object(sc, "_overlay", lambda cik, blob: blob)
        p.start()
        self.addCleanup(p.stop)


class TestExportEqualsChart(_Base):
    def test_full_daily_frame_is_the_charts(self):
        _seed()
        chart = _chart_frame("ALL")
        self.assertIsNotNone(chart)
        with patch("data.fdic_history_store.deep_group_history", lambda *a, **k: []):
            val, window, why = tv._series_for(T, INFO)
        self.assertEqual((window, why), ("ALL", ""))
        pd.testing.assert_frame_equal(val.reset_index(drop=True),
                                      chart.reset_index(drop=True))

    def test_exported_rows_equal_chart_rows(self):
        _seed()
        chart = _chart_frame("ALL").set_index("date")
        df = _export()
        self.assertGreater(len(df), 20)
        self.assertLess(len(df), len(chart))                  # sampled, not daily
        for r in df.itertuples():
            c = chart.loc[pd.Timestamp(r.date)]
            for col, ccol in (("close", "close"), ("tbv_ps", "tbvps"),
                              ("eps_ttm", "ttm_eps"), ("ptbv", "ptbv"), ("pe", "pe")):
                got, want = getattr(r, col), c[ccol]
                if pd.isna(want):
                    self.assertIsNone(got, f"{r.date} {col}")
                else:
                    self.assertAlmostEqual(got, want, places=12, msg=f"{r.date} {col}")

    def test_hand_computed_latest_row(self):
        _seed()
        last = _export().iloc[-1]
        self.assertEqual(last["date"], date(2026, 9, 30))
        self.assertEqual(last["point_type"], "latest")
        self.assertAlmostEqual(last["tbv_ps"], (280e6 - GW - CDI) / SHARES, places=9)  # 22.50
        self.assertAlmostEqual(last["eps_ttm"], 0.55 + 0.65 + 0.60 + 0.60, places=9)  # 2.40
        self.assertAlmostEqual(last["ptbv"], last["close"] / 22.50, places=9)
        self.assertAlmostEqual(last["pe"], last["close"] / 2.40, places=9)
        self.assertEqual(last["fund_period_end"], date(2026, 6, 30))
        self.assertEqual(last["tbv_balance_sheet_date"], date(2026, 6, 30))
        self.assertEqual(last["fund_effective_date"], date(2026, 8, 4))
        self.assertEqual(last["fund_effective_basis"], "filed")
        self.assertEqual(last["fund_age_days"], (date(2026, 9, 30) - date(2026, 8, 4)).days)
        self.assertEqual(last["fund_filing_form"], "10-Q")
        self.assertEqual(last["tbv_ps_basis"], "reconstructed_xbrl")

    def test_shorter_cached_window_is_that_windows_chart(self):
        _seed(window="1Y")
        chart = _chart_frame("1Y")
        with patch("data.fdic_history_store.deep_group_history", lambda *a, **k: []):
            val, window, _ = tv._series_for(T, INFO)
        self.assertEqual(window, "1Y")
        pd.testing.assert_frame_equal(val.reset_index(drop=True),
                                      chart.reset_index(drop=True))
        self.assertEqual(set(_export()["chart_period"]), {"1Y"})


class TestStepAndGaps(_Base):
    def test_tbv_steps_on_the_filing_date(self):
        _seed()
        df = _export().set_index("date")
        # Q2-26 10-Q filed Tue 2026-08-04: that day is a step row on the new TBV.
        step = df.loc[date(2026, 8, 4)]
        self.assertEqual(step["point_type"], "filing_step")
        self.assertAlmostEqual(step["tbv_ps"], 22.50, places=9)
        self.assertEqual(step["fund_period_end"], date(2026, 6, 30))
        # The chart's day before still divides by Q1-26's 22.00 (no lookahead).
        chart = _chart_frame("ALL").set_index("date")
        self.assertAlmostEqual(chart.loc["2026-08-03", "tbvps"], 22.00, places=9)
        # Period end (06-30) is NOT where the step lands.
        self.assertAlmostEqual(df.loc[date(2026, 6, 30), "tbv_ps"], 22.00, places=9)

    def test_untagged_quarter_is_a_gap_not_a_carry(self):
        _seed()
        df = _export().set_index("date")
        # Q3-25 (filed 2025-11-04) has no intangibles tagged → TBV n/a for the
        # days it is in effect, until the 10-K steps in on 2026-02-26.
        gap = df.loc[date(2025, 11, 4)]
        self.assertEqual(gap["point_type"], "filing_step")
        self.assertIsNone(gap["tbv_ps"])
        self.assertIsNone(gap["ptbv"])
        self.assertAlmostEqual(gap["eps_ttm"], 0.60 + 0.55 * 3, places=9)  # P/E lives on
        self.assertIsNotNone(gap["pe"])
        self.assertIsNone(df.loc[date(2025, 12, 31), "ptbv"])           # month-end in gap
        self.assertAlmostEqual(df.loc[date(2026, 2, 26), "tbv_ps"], 21.50, places=9)
        # Before Q1-25 TTM can't form (needs four quarters) → P/E None, not 0.
        self.assertIsNone(df.loc[date(2025, 5, 6), "pe"])
        self.assertTrue((df["ptbv"].dropna() > 0).all())


class TestOverlay(_Base):
    def test_post_balance_sheet_split_reaches_the_export_like_the_chart(self):
        """SFBS 2:1 after the balance sheet: the chart (overlaid facts) divides
        filed TBV/EPS by the ratio; the export reads the same overlaid blob."""
        _seed()
        split = {"ratio": 2.0, "date": "2026-09-15"}

        def with_split(cik, blob):
            return dict(blob, _stock_split=split)
        facts = with_split(CIK, _facts())
        with patch.object(sc, "_overlay", with_split):
            df = _export()
        with patch("ui.history_range.load_hist_df_for_range",
                   lambda *a, **k: pd.DataFrame(FDIC)),              patch("data.loaders.load_fdic_hist_df", lambda *a, **k: pd.DataFrame(FDIC)),              patch("data.fmp_client.get_history", lambda *a, **k: _px()),              patch.object(sc, "fetch_company_facts", lambda cik: facts):
            chart = bd.valuation_series(T, INFO, "ALL").set_index("date")
        last = df.iloc[-1]
        self.assertAlmostEqual(last["tbv_ps"], 22.50 / 2, places=9)
        self.assertAlmostEqual(last["eps_ttm"], 2.40 / 2, places=9)
        self.assertAlmostEqual(last["ptbv"], chart.loc[pd.Timestamp(last["date"]), "ptbv"],
                               places=12)


class TestContract(_Base):
    def test_columns_match_dict_in_order(self):
        _seed()
        df = _export()
        self.assertEqual(list(df.columns), list(tv.COLUMNS_VALUATION_HISTORY))
        self.assertEqual(df.columns[0], "ticker")
        empty = _export(("NOPE",))
        self.assertEqual(list(empty.columns), list(tv.COLUMNS_VALUATION_HISTORY))
        self.assertTrue(all(isinstance(d, date) for d in df["date"]))

    def test_uncached_bank_is_skipped_and_counted(self):
        _seed(price=False)
        df = _export((T, "NOPE"))
        self.assertTrue(df.empty)
        self.assertEqual(df.attrs["misses"], 2)
        self.assertEqual(df.attrs["miss_reasons"],
                         {T: "no_cached_price_history", "NOPE": "no_cik"})

    def test_refresh_prices_fetches_through_the_charts_fetcher(self):
        """refresh_prices=True: each bank's ALL history is refreshed via
        fmp_client.get_history (which caches) before the series is read; a
        bank with no cached history then exports. Default reads cache only."""
        _seed(price=False)
        calls = []

        def fake_get_history(t, period="1Y", **k):
            calls.append((t, period))
            recs = _px().assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d"))
            cache.put(f"fmp_history:{t}:{period}",
                      {"_ts": time.time(), "_v": recs.to_dict("records")})
            return _px()
        with patch("data.fmp_client.get_history", fake_get_history), \
             patch.object(tv, "_PRICE_MIN_INTERVAL_S", 0), \
             patch("data.bank_mapping.get_bank_info", lambda t: INFO), \
             patch("data.fdic_history_store.deep_group_history", lambda *a, **k: []):
            self.assertTrue(tv.build_valuation_history([T]).empty)     # default: no fetch
            self.assertEqual(calls, [])
            df = tv.build_valuation_history([T], refresh_prices=True)
        self.assertEqual(calls, [(T, "ALL")])
        self.assertFalse(df.empty)
        self.assertEqual(df.attrs["price_refresh_errors"], 0)

    def test_price_refresh_failure_falls_back_to_cache_and_is_counted(self):
        _seed()

        def boom(*a, **k):
            raise TimeoutError("FMP slow")
        with patch("data.fmp_client.get_history", boom), \
             patch.object(tv, "_PRICE_MIN_INTERVAL_S", 0), \
             patch("data.bank_mapping.get_bank_info", lambda t: INFO), \
             patch("data.fdic_history_store.deep_group_history", lambda *a, **k: []):
            df = tv.build_valuation_history([T], refresh_prices=True)
        self.assertFalse(df.empty)                       # cached history served
        self.assertEqual(df.attrs["price_refresh_errors"], 1)

    def test_stale_cache_entries_are_read(self):
        """max_age_s=None: a days-old entry still serves (job reads, never fetches)."""
        _seed()
        real_get = cache.get
        seen = []

        def spy(key, max_age_s=cache.TTL_SECONDS):
            seen.append(max_age_s)
            return real_get(key, max_age_s=max_age_s)
        with patch.object(cache, "get", spy):
            self.assertFalse(_export().empty)
        self.assertTrue(seen)
        self.assertTrue(all(m is None for m in seen))

    def test_quarter_ends_prefer_deep_store_then_warm_cache(self):
        cache.put(f"fdic_hist:{T}", FDIC)
        deep = [{"REPDTE": f"{y}{md}"} for y in range(2016, 2022)
                for md in ("1231", "0930", "0630", "0331")]
        with patch("data.fdic_history_store.deep_group_history", lambda *a, **k: deep):
            self.assertEqual(len(tv._cached_quarter_ends(T, 200)), 24)
        with patch("data.fdic_history_store.deep_group_history", lambda *a, **k: deep[:5]):
            ends = tv._cached_quarter_ends(T, 200)                # < 20 → warm entry
        self.assertEqual(ends[-1], datetime(2026, 6, 30))
        self.assertEqual(len(ends), len(BS))


if __name__ == "__main__":
    unittest.main()
