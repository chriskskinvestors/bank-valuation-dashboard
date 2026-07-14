"""Pin the Results-board row builder (data/earnings_results.py, 2026-07-06).

Pure-function tests: surprise math (zero/None estimates must yield None, never
a fabricated number — cardinal rule), the reaction-session convention (after-
close reports react the NEXT session), price reaction against real close
series (missing session / no prior close / stale series → None), results-PR
window matching, and the row builder's filter-and-merge behavior.

Run: python -m unittest tests.test_earnings_results
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.earnings_results import (
    build_results_rows,
    pick_release_pr,
    price_reaction,
    reaction_session,
    release_matches_report,
    surprise_pct,
)


class TestPlatformHistoryMapping(unittest.TestCase):
    def test_q_label_conversion(self):
        from data.earnings_results import q_label
        self.assertEqual(q_label("2026-03-31"), "Q1 2026")
        self.assertEqual(q_label("2025-12-31"), "Q4 2025")
        self.assertIsNone(q_label(None))
        self.assertIsNone(q_label("junk"))

    def test_capital_never_platform_mapped(self):
        """Holdco capital (release) vs bank-sub capital (FDIC) must never mix
        — the map must not carry any capital-ratio key."""
        from data.earnings_results import PLATFORM_HIST_MAP
        for key in ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio",
                    "tce_ratio"):
            self.assertNotIn(key, PLATFORM_HIST_MAP)

    def test_mapped_series_exist_in_trend_registry(self):
        """Every FDIC-mapped series key must exist in TREND_KEYS — a renamed
        trend metric must fail here, not silently blank the exhibit."""
        from data.as_of_metrics import TREND_KEYS
        from data.earnings_results import PLATFORM_HIST_MAP
        for key, (src, skey) in PLATFORM_HIST_MAP.items():
            if src == "fdic":
                self.assertIn(skey, TREND_KEYS, f"{key} -> {skey}")


class TestReleaseMatchesReport(unittest.TestCase):
    def test_same_day_through_five_days_match(self):
        self.assertTrue(release_matches_report("2026-07-14", "2026-07-14"))
        self.assertTrue(release_matches_report("2026-07-19", "2026-07-14"))

    def test_prior_quarter_release_never_attaches(self):
        # An 8-K filed BEFORE the report date is last quarter's release.
        self.assertFalse(release_matches_report("2026-04-15", "2026-07-14"))
        self.assertFalse(release_matches_report("2026-07-13", "2026-07-14"))

    def test_too_late_or_unparseable_never_attaches(self):
        self.assertFalse(release_matches_report("2026-07-20", "2026-07-14"))
        self.assertFalse(release_matches_report(None, "2026-07-14"))
        self.assertFalse(release_matches_report("2026-07-14", None))


class TestSurprisePct(unittest.TestCase):
    def test_beat_and_miss(self):
        self.assertAlmostEqual(surprise_pct(1.10, 1.00), 10.0)
        self.assertAlmostEqual(surprise_pct(0.90, 1.00), -10.0)

    def test_negative_estimate_uses_magnitude(self):
        # Loss narrower than expected is a POSITIVE surprise.
        self.assertAlmostEqual(surprise_pct(-0.50, -1.00), 50.0)

    def test_missing_or_zero_estimate_is_none(self):
        self.assertIsNone(surprise_pct(1.10, None))
        self.assertIsNone(surprise_pct(None, 1.00))
        self.assertIsNone(surprise_pct(1.10, 0))
        self.assertIsNone(surprise_pct("n/a", 1.00))


class TestReactionSession(unittest.TestCase):
    def test_after_close_reacts_next_session(self):
        self.assertEqual(reaction_session(date(2026, 7, 14), "After close"),
                         date(2026, 7, 15))

    def test_before_open_and_unknown_react_same_day(self):
        self.assertEqual(reaction_session(date(2026, 7, 14), "Before open"),
                         date(2026, 7, 14))
        self.assertEqual(reaction_session(date(2026, 7, 14), None),
                         date(2026, 7, 14))


class TestPriceReaction(unittest.TestCase):
    CLOSES = [(date(2026, 7, 10), 100.0), (date(2026, 7, 13), 102.0),
              (date(2026, 7, 14), 107.1)]

    def test_session_move_over_prior_close(self):
        self.assertAlmostEqual(price_reaction(self.CLOSES, date(2026, 7, 14)), 5.0)

    def test_weekend_session_resolves_to_next_trading_day(self):
        # Sat Jul 11 → first trading day Mon Jul 13, vs Fri's close.
        self.assertAlmostEqual(price_reaction(self.CLOSES, date(2026, 7, 11)), 2.0)

    def test_session_not_posted_yet_is_none(self):
        self.assertIsNone(price_reaction(self.CLOSES, date(2026, 7, 15)))

    def test_no_prior_close_is_none(self):
        self.assertIsNone(price_reaction(self.CLOSES, date(2026, 7, 9)))

    def test_stale_series_gap_is_none(self):
        closes = [(date(2026, 7, 1), 100.0), (date(2026, 7, 20), 110.0)]
        self.assertIsNone(price_reaction(closes, date(2026, 7, 2)))

    def test_empty_series_is_none(self):
        self.assertIsNone(price_reaction([], date(2026, 7, 14)))


class TestPickReleasePr(unittest.TestCase):
    def test_picks_pr_on_or_after_report_never_before(self):
        events = [  # newest-first, as the store returns them
            {"headline": "Q2 results", "published_at": "2026-07-14T08:00:00",
             "url": "https://x/results"},
            {"headline": "Announces date", "published_at": "2026-07-01T08:00:00",
             "url": "https://x/announce"},
        ]
        pr = pick_release_pr(events, date(2026, 7, 14))
        self.assertEqual(pr["url"], "https://x/results")
        # Only the weeks-old announcement → nothing in the window.
        self.assertIsNone(pick_release_pr(events[1:], date(2026, 7, 14)))

    def test_window_closes_after_three_days(self):
        events = [{"headline": "h", "published_at": "2026-07-18", "url": "u"}]
        self.assertIsNone(pick_release_pr(events, date(2026, 7, 14)))


class TestBuildResultsRows(unittest.TestCase):
    TODAY = date(2026, 7, 15)

    def test_only_reported_universe_rows_within_window(self):
        fmp = [
            {"symbol": "JPM", "date": "2026-07-14", "time": "bmo",
             "epsActual": 5.80, "epsEstimated": 5.61,
             "revenueActual": 5.0e10, "revenueEstimated": 4.98e10,
             "periodEnding": "2026-06-30"},
            {"symbol": "GS", "date": "2026-07-14", "time": "bmo",
             "epsActual": None, "epsEstimated": 14.01,
             "revenueActual": None, "revenueEstimated": 1.6e10},  # not reported
            {"symbol": "AAPL", "date": "2026-07-14", "epsActual": 2.0,
             "epsEstimated": 1.9},                                 # not universe
            {"symbol": "WFC", "date": "2026-05-01", "epsActual": 1.5,
             "epsEstimated": 1.4},                                 # outside window
        ]
        rows = build_results_rows(fmp, {"JPM", "GS", "WFC"}, {}, self.TODAY)
        self.assertEqual([r["ticker"] for r in rows], ["JPM"])
        r = rows[0]
        self.assertAlmostEqual(r["eps_surprise"], (5.80 - 5.61) / 5.61 * 100)
        self.assertEqual(r["when"], "Before open")
        self.assertEqual(r["reaction_session"], "2026-07-14")

    def test_after_close_reaction_session_is_next_day(self):
        fmp = [{"symbol": "EQBK", "date": "2026-07-14", "time": "amc",
                "epsActual": 1.30, "epsEstimated": 1.23}]
        rows = build_results_rows(fmp, {"EQBK"}, {}, self.TODAY)
        self.assertEqual(rows[0]["reaction_session"], "2026-07-15")

    def test_newest_report_kept_and_sorted_newest_first(self):
        fmp = [
            {"symbol": "A1", "date": "2026-07-10", "epsActual": 1.0},
            {"symbol": "A1", "date": "2026-06-20", "epsActual": 0.9},  # older dup
            {"symbol": "B2", "date": "2026-07-14", "epsActual": 2.0},
        ]
        rows = build_results_rows(fmp, {"A1", "B2"}, {}, self.TODAY)
        self.assertEqual([(r["ticker"], r["date"]) for r in rows],
                         [("B2", "2026-07-14"), ("A1", "2026-07-10")])

    def test_pr_signaled_report_included_pending_before_fmp_actuals(self):
        """BKSC-class catch (2026-07-09): FMP actuals lag/never fill for
        micro-caps and deregistered banks have no 8-K — the bank's own results
        PR on/after the scheduled date must surface the row as pending."""
        fmp = [{"symbol": "BKSC", "date": "2026-07-09", "epsActual": None,
                "revenueActual": None, "epsEstimated": None}]
        events = {"BKSC": [{"headline": "Bank of South Carolina Reports Q2 "
                            "2026 Results", "url": "https://x/pr",
                            "published_at": "2026-07-09T09:00:00"}]}
        rows = build_results_rows(fmp, {"BKSC"}, events, self.TODAY)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["pending"])
        self.assertEqual(rows[0]["pr_url"], "https://x/pr")
        self.assertIsNone(rows[0]["eps_act"])

    def test_no_pr_and_no_actuals_still_excluded(self):
        fmp = [{"symbol": "GS", "date": "2026-07-14", "epsActual": None,
                "revenueActual": None}]
        self.assertEqual(build_results_rows(fmp, {"GS"}, {}, self.TODAY), [])

    def test_upcoming_announcement_pr_never_marks_reported(self):
        # A date-announcement PR published near the projected date is NOT a
        # results release.
        fmp = [{"symbol": "ABCB", "date": "2026-07-14", "epsActual": None,
                "revenueActual": None}]
        events = {"ABCB": [{"headline": "Ameris Bancorp Will Report Second "
                            "Quarter 2026 Results on July 23",
                            "url": "https://x/announce",
                            "published_at": "2026-07-14T09:00:00"}]}
        self.assertEqual(build_results_rows(fmp, {"ABCB"}, events, self.TODAY), [])

    def test_negative_revenue_is_fmp_junk(self):
        """JPM/AMJB showed Rev Act $-47.81B (2026-07-14) — bank revenue is
        never negative; the junk must not render OR feed the surprise."""
        fmp = [{"symbol": "JPM", "date": "2026-07-14", "epsActual": 5.8,
                "revenueActual": -47.81e9, "revenueEstimated": 51.09e9}]
        rows = build_results_rows(fmp, {"JPM"}, {}, self.TODAY)
        self.assertIsNone(rows[0]["rev_act"])
        self.assertIsNone(rows[0]["rev_surprise"])

    def test_actuals_row_is_not_pending(self):
        fmp = [{"symbol": "JPM", "date": "2026-07-14", "epsActual": 5.8,
                "periodEnding": "2026-06-30"}]
        rows = build_results_rows(fmp, {"JPM"}, {}, self.TODAY)
        self.assertFalse(rows[0]["pending"])

    def test_implausible_fmp_period_is_dropped(self):
        """FMP periodEnding junk (2026-07-07 prod catch): CARV showed a period
        ending AFTER its report date, CPBI one ~a year old. A real report
        lands 7-150 days after the period closes; outside that → None."""
        fmp = [
            {"symbol": "JPM", "date": "2026-07-14", "epsActual": 5.8,
             "periodEnding": "2026-06-30"},                  # 14d — plausible
            {"symbol": "CARV", "date": "2026-06-29", "epsActual": -0.29,
             "periodEnding": "2026-06-30"},                  # ends AFTER report
            {"symbol": "CPBI", "date": "2026-06-18", "epsActual": 0.25,
             "periodEnding": "2025-06-30"},                  # ~a year old
        ]
        rows = {r["ticker"]: r for r in build_results_rows(
            fmp, {"JPM", "CARV", "CPBI"}, {}, self.TODAY)}
        self.assertEqual(rows["JPM"]["period_ending"], "2026-06-30")
        self.assertIsNone(rows["CARV"]["period_ending"])
        self.assertIsNone(rows["CPBI"]["period_ending"])

    def test_pr_link_attached_from_events(self):
        fmp = [{"symbol": "JPM", "date": "2026-07-14", "epsActual": 5.8}]
        events = {"JPM": [{"headline": "JPM Reports Q2", "url": "https://x/pr",
                           "published_at": "2026-07-14T07:00:00"}]}
        rows = build_results_rows(fmp, {"JPM"}, events, self.TODAY)
        self.assertEqual(rows[0]["pr_url"], "https://x/pr")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReleaseActualsFill(unittest.TestCase):
    """Actuals fill from the bank's own release when FMP lags (owner,
    2026-07-13 — FBK reported after close, board stuck on 'pending')."""

    @staticmethod
    def _run_fill(row, metrics, prior=None):
        from unittest.mock import patch
        from data.earnings_results import _fill_release_metrics
        rm = {"metrics": metrics, "prior_metrics": prior or {},
              "prior_qend": "2026-03-31", "capital": {},
              "url": "u", "filed_date": row["date"], "accession": "a"}
        with patch("data.release_metrics.release_metrics", return_value=rm), \
             patch("data.bank_mapping.get_cik", return_value=1):
            _fill_release_metrics([row], max_workers=1)
        return row

    def test_adjusted_eps_preferred_and_pending_resolves(self):
        row = {"ticker": "FBK", "date": "2026-07-13", "eps_act": None,
               "eps_est": 1.14, "rev_act": None, "rev_est": 178e6,
               "pending": True, "eps_surprise": None, "rev_surprise": None}
        self._run_fill(row, {"eps_adj": 1.14, "eps_diluted": 1.13,
                             "total_revenue": 174_752_000.0})
        self.assertEqual(row["eps_act"], 1.14)          # adjusted, not GAAP
        self.assertEqual(row["eps_act_src"], "release, adj.")
        self.assertFalse(row["pending"])
        self.assertAlmostEqual(row["eps_surprise"], 0.0)
        self.assertEqual(row["rev_act"], 174_752_000.0)
        self.assertEqual(row["rev_act_src"], "release")
        # (174.752 - 178) / 178 = -1.82%
        self.assertAlmostEqual(row["rev_surprise"], -1.8247, places=3)

    def test_gaap_fallback_when_no_adjusted(self):
        row = {"ticker": "T", "date": "2026-07-13", "eps_act": None,
               "eps_est": 1.10, "pending": True, "eps_surprise": None}
        self._run_fill(row, {"eps_diluted": 1.13})
        self.assertEqual(row["eps_act"], 1.13)
        self.assertEqual(row["eps_act_src"], "release, GAAP")

    def test_fmp_actual_never_overwritten(self):
        row = {"ticker": "T", "date": "2026-07-13", "eps_act": 1.20,
               "eps_est": 1.10, "eps_surprise": 9.09, "pending": False}
        self._run_fill(row, {"eps_adj": 1.14})
        self.assertEqual(row["eps_act"], 1.20)          # FMP actual kept
        self.assertNotIn("eps_act_src", row)

    def test_stale_release_never_fills(self):
        # A release filed BEFORE the report date is last quarter's.
        from unittest.mock import patch
        from data.earnings_results import _fill_release_metrics
        row = {"ticker": "T", "date": "2026-07-13", "eps_act": None,
               "eps_est": 1.10, "pending": True}
        rm = {"metrics": {"eps_adj": 1.14}, "prior_metrics": {},
              "prior_qend": None, "capital": {}, "url": "u",
              "filed_date": "2026-04-15", "accession": "a"}
        with patch("data.release_metrics.release_metrics", return_value=rm), \
             patch("data.bank_mapping.get_cik", return_value=1):
            _fill_release_metrics([row], max_workers=1)
        self.assertIsNone(row["rel"])
        self.assertIsNone(row["eps_act"])
        self.assertTrue(row["pending"])
