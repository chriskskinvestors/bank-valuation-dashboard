"""
Tests for the Home sector valuation snapshot (docs/HOME-MACRO-PLAN.md):
per-size-tier medians of P/TBV / P/E / dividend yield + the honest
"vs 1y ago" column fed by the self-populating sector_val_hist record that
jobs/refresh_home_snapshot appends daily.

Pins (pure helpers in ui.home; hand-computed fixtures):
  • median math: odd and even counts, None/NaN excluded from value AND n
  • the n<5 refusal: a 4-bank tier renders n/a, never a thin "median"
  • tier bucketing via analysis.peer_groups.asset_size_tier; "all" spans
    the universe
  • Δ1Y lookup: nearest-to-365d record selection; n/a (never approximated)
    while the oldest record is younger than ~350d, with the
    collecting-since label; malformed record dates skipped
  • history append: same-date replace (idempotent overlapping job runs),
    420-day prune, date-sorted
  • strip HTML: values with (n=...), n/a for empty tiers, the
    collecting-since status while history is young, a hand-computed Δ
    when a 1y-ago record exists
"""
import datetime as dt
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.home as home  # noqa: E402


def _bank(assets, ptbv=None, pe=None, dy=None):
    return {"ticker": "X", "total_assets": assets, "ptbv_ratio": ptbv,
            "pe_ratio": pe, "dividend_yield": dy}


# Five community banks (<$10B): hand-computed medians
#   P/TBV [1.0 1.1 1.2 1.3 1.4] -> 1.2   P/E [8 9 10 11 12] -> 10
#   yield [2.0 2.5 3.0 3.5 4.0] -> 3.0
_COMM5 = [_bank(5e9, 1.0 + i * 0.1, 8.0 + i, 2.0 + i * 0.5) for i in range(5)]


class TestSectorValMedians(unittest.TestCase):

    def test_hand_computed_odd_count(self):
        out = home.sector_val_medians(_COMM5)
        c = out["comm"]
        self.assertAlmostEqual(c["ptbv_median"], 1.2)
        self.assertEqual(c["ptbv_n"], 5)
        self.assertAlmostEqual(c["pe_median"], 10.0)
        self.assertAlmostEqual(c["divyield_median"], 3.0)
        # "all" == the same five banks here
        self.assertAlmostEqual(out["all"]["ptbv_median"], 1.2)
        self.assertEqual(out["all"]["ptbv_n"], 5)

    def test_even_count_averages_middle_pair(self):
        # Sixth bank (regional, $50B) ptbv 1.5 -> all-universe
        # [1.0 1.1 1.2 1.3 1.4 1.5] -> (1.2+1.3)/2 = 1.25, n=6
        rows = _COMM5 + [_bank(50e9, ptbv=1.5)]
        out = home.sector_val_medians(rows)
        self.assertAlmostEqual(out["all"]["ptbv_median"], 1.25)
        self.assertEqual(out["all"]["ptbv_n"], 6)

    def test_none_and_nan_excluded_from_median_and_n(self):
        rows = _COMM5 + [_bank(5e9, ptbv=None), _bank(5e9, ptbv=float("nan"))]
        out = home.sector_val_medians(rows)
        self.assertEqual(out["comm"]["ptbv_n"], 5)       # 7 banks, 5 real values
        self.assertAlmostEqual(out["comm"]["ptbv_median"], 1.2)

    def test_fewer_than_five_values_refuses_median(self):
        rows = _COMM5[:4]                                # n=4 -> n/a, never a guess
        out = home.sector_val_medians(rows)
        self.assertIsNone(out["comm"]["ptbv_median"])
        self.assertEqual(out["comm"]["ptbv_n"], 4)

    def test_tier_bucketing(self):
        rows = [_bank(5e9, 1.0), _bank(50e9, 2.0), _bank(5e11, 3.0),
                _bank(2e12, 4.0), _bank(None, 9.9)]      # sizeless -> "all" only
        out = home.sector_val_medians(rows)
        self.assertEqual(out["comm"]["ptbv_n"], 1)
        self.assertEqual(out["reg"]["ptbv_n"], 1)
        self.assertEqual(out["lg"]["ptbv_n"], 1)
        self.assertEqual(out["mc"]["ptbv_n"], 1)
        self.assertEqual(out["all"]["ptbv_n"], 5)
        self.assertIsNone(out["mc"]["ptbv_median"])      # n=1 < 5


def _hist(day_offsets_and_tiers):
    """[(days_ago, tiers_dict), ...] -> the sector_val_hist value shape."""
    today = dt.date.today()
    return {"records": [
        {"date": (today - dt.timedelta(days=d)).isoformat(), "tiers": t}
        for d, t in day_offsets_and_tiers]}


class TestSectorValYoy(unittest.TestCase):

    def test_no_history(self):
        self.assertEqual(home.sector_val_yoy(None), (None, None))
        self.assertEqual(home.sector_val_yoy({"records": []}), (None, None))

    def test_young_history_is_na_with_collecting_since(self):
        h = _hist([(100, {"all": {"ptbv_median": 1.1}}), (1, {})])
        tiers, since = home.sector_val_yoy(h)
        self.assertIsNone(tiers)                         # never approximated
        oldest = dt.date.today() - dt.timedelta(days=100)
        self.assertEqual(since, oldest.strftime("%Y-%m"))

    def test_nearest_to_365d_selected(self):
        h = _hist([(400, {"all": {"ptbv_median": 1.0}}),
                   (366, {"all": {"ptbv_median": 1.1}}),
                   (300, {"all": {"ptbv_median": 1.2}}),
                   (10,  {"all": {"ptbv_median": 1.3}})])
        tiers, since = home.sector_val_yoy(h)
        self.assertIsNone(since)
        self.assertAlmostEqual(tiers["all"]["ptbv_median"], 1.1)  # |366-365|=1

    def test_exactly_350d_oldest_qualifies(self):
        h = _hist([(350, {"all": {"ptbv_median": 1.05}})])
        tiers, since = home.sector_val_yoy(h)
        self.assertIsNone(since)
        self.assertAlmostEqual(tiers["all"]["ptbv_median"], 1.05)

    def test_malformed_dates_skipped(self):
        h = {"records": [{"date": "not-a-date", "tiers": {"all": {}}},
                         {"date": None, "tiers": {}}]}
        self.assertEqual(home.sector_val_yoy(h), (None, None))


class TestSectorHistAppend(unittest.TestCase):

    def test_append_to_empty(self):
        rec = {"date": dt.date.today().isoformat(), "tiers": {}}
        out = home.sector_hist_append(None, rec)
        self.assertEqual(out["records"], [rec])

    def test_same_date_replaces_not_duplicates(self):
        d = dt.date.today().isoformat()
        h = {"records": [{"date": d, "tiers": {"all": {"ptbv_median": 1.0}}}]}
        rec = {"date": d, "tiers": {"all": {"ptbv_median": 2.0}}}
        out = home.sector_hist_append(h, rec)
        self.assertEqual(len(out["records"]), 1)
        self.assertAlmostEqual(out["records"][0]["tiers"]["all"]["ptbv_median"], 2.0)

    def test_prunes_beyond_420_days_and_sorts(self):
        today = dt.date.today()
        old = (today - dt.timedelta(days=421)).isoformat()
        kept = (today - dt.timedelta(days=419)).isoformat()
        h = {"records": [{"date": kept, "tiers": {}}, {"date": old, "tiers": {}}]}
        rec = {"date": today.isoformat(), "tiers": {}}
        out = home.sector_hist_append(h, rec)
        dates = [r["date"] for r in out["records"]]
        self.assertEqual(dates, [kept, today.isoformat()])  # pruned + sorted


class TestSectorValStripHtml(unittest.TestCase):

    def test_values_ns_and_collecting_status(self):
        h = home._sector_val_strip_html(_COMM5, None)
        self.assertIn("1.20x", h)                        # community P/TBV median
        self.assertIn("(n=5)", h)
        self.assertIn("10.0x", h)                        # P/E, dp=1
        self.assertIn("3.00%", h)                        # div yield
        self.assertIn("Community", h)
        self.assertIn("n/a", h)                          # empty tiers refuse
        self.assertIn("(n=0)", h)
        self.assertIn("collecting since", h)             # no 1y history yet
        self.assertIn("Sector Valuation", h)

    def test_delta_vs_1y_record(self):
        prior = {"comm": {"ptbv_median": 1.10, "pe_median": 9.0,
                          "divyield_median": 2.50}}
        h = _hist([(400, prior), (365, prior)])
        html = home._sector_val_strip_html(_COMM5, h)
        self.assertIn("+0.10x", html)                    # 1.20 - 1.10
        self.assertIn("+1.0x", html)                     # 10.0 - 9.0 (dp=1)
        self.assertIn("+0.50pp", html)                   # 3.00 - 2.50
        self.assertNotIn("collecting since", html)

    def test_no_metrics_collapses(self):
        self.assertEqual(home._sector_val_strip_html([], None), "")


if __name__ == "__main__":
    unittest.main()
