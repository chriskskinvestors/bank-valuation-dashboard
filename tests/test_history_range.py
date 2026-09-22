"""Deep-history UI phases (DEEP-HISTORY-PLAN.md, owner scope 2026-09-22):
the shared range helpers behind the statement tables and dynamics charts.

Hermetic: the store tests run on an isolated in-memory SQLite (the
test_deep_history fixture), FRED and the cert group are patched, and every
expectation is hand-computed. The load-bearing pins:

  * the default range is EXACTLY today's warm read (no new arguments);
  * a deep request returns the bank's whole stored history when that is
    shorter than the range (32 stored quarters, 10Y asked -> 32, never a
    20-quarter fallback);
  * absent early fields annotate "from <label>" and structure breaks are
    flagged from hand-computed asset jumps -- never fabricated or hidden;
  * the Fed-funds lookup memoizes the whole series in one read and the
    NIM backtest no longer skips every quarter when REPDTE is a string
    (the shape every warm-cache / deep-store record has).
"""
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

from tests import _streamlit_stub  # noqa: F401

from ui import history_range as hr


class TestRangeMath(unittest.TestCase):
    def test_years_and_quarters(self):
        self.assertEqual(hr.range_years("5Y"), 5)
        self.assertEqual(hr.range_years("10Y"), 10)
        self.assertIsNone(hr.range_years("MAX"))
        self.assertEqual(hr.quarters_needed("10Y", "Quarterly"), 40)
        # Annual needs N December rows: 4N+4 (a Q3 latest puts the Nth
        # December 4N-1 quarters back).
        self.assertEqual(hr.quarters_needed("10Y", "Annual"), 44)
        self.assertEqual(hr.quarters_needed("20Y", "Annual"), 84)
        self.assertEqual(hr.quarters_needed("MAX", "Annual"), hr.MAX_QUARTERS)

    def test_unknown_label_falls_back_to_default(self):
        self.assertEqual(hr.range_years("bogus"), 5)


class TestLoaderPath(unittest.TestCase):
    def test_default_range_is_todays_warm_call(self):
        calls = []

        def fake(ticker, min_quarters=8, limit=20):
            calls.append((ticker, min_quarters, limit))
            return [{"REPDTE": "2026-06-30"}]

        with patch("data.loaders.load_fdic_hist", fake):
            hr.load_hist_for_range("TCBK", "5Y")
        # Positional ticker only -> the untouched load_fdic_hist(ticker) path.
        self.assertEqual(calls, [("TCBK", 8, 20)])

    def test_deep_range_asks_store_with_low_floor(self):
        calls = []

        def fake(ticker, min_quarters=8, limit=20):
            calls.append((ticker, min_quarters, limit))
            return []

        with patch("data.loaders.load_fdic_hist", fake):
            hr.load_hist_for_range("TCBK", "10Y")
            hr.load_hist_for_range("TCBK", "20Y")
            hr.load_hist_for_range("TCBK", "MAX")
        self.assertEqual(calls, [("TCBK", 8, 40), ("TCBK", 8, 80),
                                 ("TCBK", 8, hr.MAX_QUARTERS)])

    def test_table_seam_is_load_fdic_hist_df(self):
        """The statement engines keep reading data.loaders.load_fdic_hist_df
        (the seam every render fixture stubs); the default range is today's
        exact 44 / 36-quarter call."""
        calls = []

        def fake_df(ticker, quarters=44):
            calls.append((ticker, quarters))
            return pd.DataFrame()

        with patch("data.loaders.load_fdic_hist_df", fake_df):
            hr.load_hist_df_for_range("TCBK", "5Y", "Annual", floor=44)
            hr.load_hist_df_for_range("TCBK", "2Y", "Quarterly", floor=36)
            hr.load_hist_df_for_range("TCBK", "10Y", "Annual", floor=36)
            hr.load_hist_df_for_range("TCBK", "20Y", "Annual", floor=44)
            hr.load_hist_df_for_range("TCBK", "MAX", "Quarterly", floor=44)
        self.assertEqual(calls, [("TCBK", 44), ("TCBK", 36), ("TCBK", 44),
                                 ("TCBK", 84), ("TCBK", hr.MAX_QUARTERS)])


class TestHonestDepth(unittest.TestCase):
    """A young bank's whole stored history is served, not a 20-row fallback."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.fdic_history_store as store
        self._db, self._store = db, store
        self._saved = (db._engine, store._engine, db.USE_POSTGRES,
                       store._USE_POSTGRES)
        db._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True)
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_history_schema()

    def tearDown(self):
        (self._db._engine, self._store._engine, self._db.USE_POSTGRES,
         self._store._USE_POSTGRES) = self._saved

    def test_32_stored_quarters_10y_returns_all_32(self):
        recs = []
        for i in range(32):
            y, q = divmod(i, 4)
            recs.append({"REPDTE": f"{2018 + y}{(q + 1) * 3:02d}30", "ASSET": 1000 + i})
        self._store.upsert_history(5, recs)
        with patch("data.cert_group.get_cert_group", return_value=[5]), \
             patch("data.cache.get", return_value=None):
            out = hr.load_hist_for_range("YOUNG", "10Y")
            df = hr.load_hist_df_for_range("YOUNG", "10Y", "Annual", floor=44)
        self.assertEqual(len(out), 32)
        self.assertEqual(out[0]["REPDTE"], "20251230")   # newest first
        self.assertEqual(out[-1]["REPDTE"], "20180330")
        # The table seam too: 44 asked, 32 stored -> all 32 (was 20).
        self.assertEqual(len(df), 32)


class TestAnnotations(unittest.TestCase):
    def test_first_live_index(self):
        self.assertEqual(hr.first_live_index([False, False, True, True]), 2)
        self.assertEqual(hr.first_live_index([True, False]), 0)
        self.assertEqual(hr.first_live_index([False, False]), -1)
        self.assertEqual(hr.first_live_index([]), -1)

    def test_qlabel(self):
        self.assertEqual(hr.qlabel("2016-09-30"), "Q3 '16")
        self.assertEqual(hr.qlabel(pd.Timestamp("2026-06-30")), "Q2 '26")

    def test_structure_breaks_hand_computed(self):
        recs = [  # any order in; ASSET 1000 -> 1100 (+10%) -> 1700 (+54.5%) -> 1750
            {"REPDTE": "2017-06-30", "ASSET": 1100},
            {"REPDTE": "2017-03-31", "ASSET": 1000},
            {"REPDTE": "2017-12-31", "ASSET": 1750},
            {"REPDTE": "2017-09-30", "ASSET": 1700},
        ]
        out = hr.structure_breaks(recs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["repdte"], pd.Timestamp("2017-09-30"))
        self.assertAlmostEqual(out[0]["pct"], 0.6 / 1.1, places=9)
        # `since` scopes to the displayed window.
        self.assertEqual(hr.structure_breaks(recs, since="2017-10-01"), [])

    def test_structure_breaks_skip_absent_and_gaps(self):
        recs = [
            {"REPDTE": "2017-03-31", "ASSET": 1000},
            {"REPDTE": "2017-06-30", "ASSET": None},      # absent -> no ratio
            {"REPDTE": "2017-09-30", "ASSET": 5000},      # vs None: skipped
            {"REPDTE": "2019-09-30", "ASSET": 20000},     # two-year gap: not q/q
        ]
        self.assertEqual(hr.structure_breaks(recs), [])
        # A halving is a break too (charter spun off).
        self.assertEqual(len(hr.structure_breaks([
            {"REPDTE": "2017-03-31", "ASSET": 1000},
            {"REPDTE": "2017-06-30", "ASSET": 400}])), 1)

    def test_series_notes(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2016-03-31", "2016-06-30", "2016-09-30", "2016-12-31"]),
            "a": [1, 2, 3, 4],                 # full -> no note
            "b": [None, None, 3.0, 4.0],       # begins Q3 '16
            "c": [None, None, None, None],     # never -> skipped (chart omits it)
        })
        self.assertEqual(hr.series_notes(df, [("a", "A"), ("b", "B"), ("c", "C"), ("zz", "Z")]),
                         ["B from Q3 '16"])

    def test_describe_window_composes_banner(self):
        s = hr.describe_window("40 quarters · Q3 '16 – Q2 '26",
                               breaks=[{"repdte": pd.Timestamp("2017-09-30"), "pct": 0.545}],
                               notes=["B from Q3 '16"], entity="bank-subsidiary call reports")
        self.assertIn("40 quarters · Q3 '16 – Q2 '26 · bank-subsidiary call reports", s)
        self.assertIn("Series begin later: B from Q3 '16", s)
        self.assertIn("Structure break: total assets +55% q/q at Q3 '17", s)

    def test_entity_note_labels_multi_charter_pro_forma(self):
        with patch("data.cert_group.get_cert_group", return_value=[1, 2, 3]):
            self.assertIn("3 charters combined", hr.entity_note("X"))
            self.assertIn("pro forma", hr.entity_note("X"))
        with patch("data.cert_group.get_cert_group", return_value=[1]):
            self.assertEqual(hr.entity_note("X"), "bank-subsidiary call reports")


class TestChartTimeline(unittest.TestCase):
    def test_default_returns_base_untouched(self):
        base = pd.DataFrame({"date": [1]})
        tl, cap = hr.chart_timeline("X", "5Y", base, lambda recs: None)
        self.assertIs(tl, base)
        self.assertIsNone(cap)

    def test_deep_rebuilds_and_captions(self):
        base = pd.DataFrame({"date": pd.to_datetime(["2026-06-30"]), "v": [1.0]})
        recs = [{"REPDTE": f"20{y}-{m:02d}-30", "ASSET": 100}
                for y in (24, 25, 26) for m in (3, 6, 9, 12)]

        def build(rs):
            d = pd.to_datetime([r["REPDTE"] for r in rs])
            return pd.DataFrame({"date": d, "v": [None] * 4 + [1.0] * 8}).sort_values("date")

        with patch.object(hr, "load_hist_for_range", return_value=recs), \
             patch("data.cert_group.get_cert_group", return_value=[9]):
            tl, cap = hr.chart_timeline("X", "10Y", base, build, [("v", "V")])
        self.assertEqual(len(tl), 12)
        self.assertIn("12 quarters · Q1 '24 – Q4 '26", cap)
        self.assertIn("V from Q1 '25", cap)

    def test_deep_with_empty_store_keeps_base(self):
        base = pd.DataFrame({"date": [1]})
        with patch.object(hr, "load_hist_for_range", return_value=[]):
            tl, cap = hr.chart_timeline("X", "MAX", base, lambda recs: None)
        self.assertIs(tl, base)
        self.assertIsNone(cap)


class TestTableComponentWide(unittest.TestCase):
    def test_wide_adds_scroll_wrap_and_sticky_labels(self):
        from ui.financial_highlights import _build_component
        narrow = _build_component("<th>h</th>", "<tr><td>x</td></tr>", {}, "E", "f", "s")
        wide = _build_component("<th>h</th>", "<tr><td>x</td></tr>", {}, "E", "f", "s", wide=True)
        self.assertNotIn('class="wrap"', narrow)
        self.assertNotIn("position:sticky", narrow)
        self.assertIn('<div class="wrap"><table>', wide)
        self.assertIn("position:sticky", wide)
        # The "from" tag style is always present (harmless on narrow).
        self.assertIn(".from {", narrow)


class TestFedFundsDeepMap(unittest.TestCase):
    def _swap_fred(self, fn):
        fred = types.ModuleType("data.fred_client")
        fred.fetch_series = fn
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        return old

    def _restore(self, old):
        if old is not None:
            sys.modules["data.fred_client"] = old
        else:
            sys.modules.pop("data.fred_client", None)

    def test_one_read_memoizes_every_quarter(self):
        import analysis.deposit_dynamics as dd
        dd._FED_FUNDS_LIVE.clear()
        calls = []

        def fetch(sid, years=3):
            calls.append(years)
            return pd.DataFrame({
                "date": pd.to_datetime(["2010-01-01", "2010-02-01", "2010-03-01",
                                        "2010-04-01", "2010-05-01", "2010-06-01"]),
                "value": [0.11, 0.13, 0.16, 0.20, 0.20, 0.18],
            })
        old = self._swap_fred(fetch)
        try:
            # Hand-computed quarter means: Q1 = 0.1333 -> 0.13, Q2 = 0.1933 -> 0.19
            self.assertAlmostEqual(dd._get_fed_funds("2010-03-31"), 0.13, places=2)
            self.assertAlmostEqual(dd._get_fed_funds("2010-06-30"), 0.19, places=2)
            self.assertIsNone(dd._get_fed_funds("2010-09-30"))   # not in the series
        finally:
            self._restore(old)
        # Two lookups hit, one miss: the map was filled by the first read;
        # only the true miss re-reads. Depth asked is the full history.
        self.assertEqual(calls, [40, 40])


class TestBacktestStringDates(unittest.TestCase):
    """Warm-cache / deep-store records carry REPDTE as a string; the
    backtest used to return None for every such quarter."""

    def test_backtest_scores_string_repdte_history(self):
        from analysis.rate_sensitivity import backtest_bank
        hist = []
        dates = [f"{y}-{m:02d}-{'31' if m in (3, 12) else '30'} 00:00:00"
                 for y in (2023, 2024, 2025) for m in (3, 6, 9, 12)]
        for i, d in enumerate(dates):
            hist.append({
                "REPDTE": d, "ASSET": 1_000_000, "LNLSNET": 650_000, "SC": 200_000,
                "CHBAL": 50_000, "DEP": 800_000, "DEPIDOM": 550_000,
                "DEPNIDOM": 250_000, "BRO": 20_000, "ERNAST": 900_000,
                "INTINCY": 5.0 + 0.05 * i, "INTEXPY": 2.0 + 0.05 * i,
                "NIMY": 3.2 + 0.01 * i,
            })
        hist.reverse()   # newest first, like the loader
        months = pd.date_range("2022-01-01", "2026-06-01", freq="MS")
        fred = types.ModuleType("data.fred_client")
        fred.fetch_series = lambda sid, years=10: pd.DataFrame({
            "date": months, "value": [4.0 + 0.02 * i for i in range(len(months))]})
        old = sys.modules.get("data.fred_client")
        sys.modules["data.fred_client"] = fred
        try:
            bt = backtest_bank(hist)
        finally:
            if old is not None:
                sys.modules["data.fred_client"] = old
            else:
                sys.modules.pop("data.fred_client", None)
        self.assertIsNotNone(bt, "string REPDTE must not blank the backtest")
        self.assertGreaterEqual(bt["n_quarters"], 4)



class TestCapitalTimelineNoneRatios(unittest.TestCase):
    """A multi-charter group's consolidated records carry None for every
    average-based ratio (CET1 included). The timeline must build with n/a
    QoQ columns, not crash the tab (MTB, 2026-09-22)."""

    def test_all_none_cet1_builds_with_na_qoq(self):
        from analysis.capital_dynamics import build_capital_timeline
        recs = [{"REPDTE": f"2025-{m:02d}-30", "EQTOT": 1000 + m, "NETINC": 10 * m,
                 "LNLSNET": 5000, "IDT1CER": None, "RBCRWAJ": None, "RBCT1JR": None}
                for m in (3, 6, 9, 12)]
        tl = build_capital_timeline(recs)
        self.assertEqual(len(tl), 4)
        self.assertTrue(tl["cet1_qoq_pp"].isna().all())
        # Levels still diff: equity 1003 -> 1006 -> 1009 -> 1012 = +3 each quarter.
        self.assertEqual(tl["equity_qoq_k"].dropna().tolist(), [3.0, 3.0, 3.0])


if __name__ == "__main__":
    unittest.main()
