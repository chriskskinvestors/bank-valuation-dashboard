"""
Tests for data/econ_calendar.py — the FMP economics-calendar parser behind the
Economic Data "latest releases & surprises" panel and upcoming-release calendar.

Pins the pure parse: surprise = actual − consensus (and % vs |consensus|),
the `released` flag, impact normalization, and the n/a paths. No network.
Field names match the live FMP shape captured 2026-06-16 (e.g. Building
Permits, Housing Starts MoM).
"""
import unittest

from data.econ_calendar import parse_event, _impact_ok, is_marquee, merge_us_events


class TestParseEvent(unittest.TestCase):
    def test_surprise_and_fields(self):
        e = {"date": "2026-06-16 12:30:00", "country": "US",
             "event": "Housing Starts MoM (May)", "previous": -8.5,
             "estimate": -2.0, "actual": -15.4, "impact": "Medium", "unit": "%"}
        p = parse_event(e)
        self.assertEqual(p["date"], "2026-06-16")
        self.assertAlmostEqual(p["actual"], -15.4)
        self.assertAlmostEqual(p["estimate"], -2.0)
        self.assertAlmostEqual(p["previous"], -8.5)
        self.assertAlmostEqual(p["surprise"], -15.4 - (-2.0), places=6)   # -13.4
        self.assertAlmostEqual(p["surprise_pct"], (-13.4 / 2.0) * 100, places=6)
        self.assertTrue(p["released"])
        self.assertEqual(p["impact"], "Medium")

    def test_upcoming_has_no_actual(self):
        e = {"date": "2026-06-17 18:00:00", "country": "US",
             "event": "Fed Interest Rate Decision", "previous": 3.75,
             "estimate": 3.75, "actual": None, "impact": "High", "unit": "%"}
        p = parse_event(e)
        self.assertFalse(p["released"])
        self.assertIsNone(p["surprise"])      # no actual
        self.assertIsNone(p["surprise_pct"])

    def test_missing_estimate_no_surprise(self):
        e = {"date": "2026-06-16 17:00:00", "country": "US",
             "event": "20-Year Bond Auction", "previous": 5.122,
             "estimate": None, "actual": 4.927, "impact": "Low", "unit": "%"}
        p = parse_event(e)
        self.assertTrue(p["released"])
        self.assertIsNone(p["surprise"])
        self.assertAlmostEqual(p["actual"], 4.927)

    def test_zero_estimate_no_pct(self):
        e = {"date": "2026-06-10 12:30:00", "country": "US", "event": "X",
             "estimate": 0, "actual": 0.3, "previous": 0.1, "impact": "Low"}
        p = parse_event(e)
        self.assertAlmostEqual(p["surprise"], 0.3, places=6)
        self.assertIsNone(p["surprise_pct"])   # guard against /0

    def test_bad_impact_defaults_low_and_garbage_none(self):
        self.assertEqual(parse_event({"date": "d", "event": "E", "impact": "???"})["impact"], "Low")
        self.assertIsNone(parse_event({"event": "", "date": "d"}))   # no name
        self.assertIsNone(parse_event({"event": "E"}))               # no date
        self.assertIsNone(parse_event("nope"))


class TestImpactFilter(unittest.TestCase):
    def test_min_impact(self):
        hi = {"impact": "High"}; med = {"impact": "Medium"}; lo = {"impact": "Low"}
        self.assertTrue(_impact_ok(hi, "Medium"))
        self.assertTrue(_impact_ok(med, "Medium"))
        self.assertFalse(_impact_ok(lo, "Medium"))
        self.assertTrue(_impact_ok(lo, "Low"))
        self.assertFalse(_impact_ok(med, "High"))


class TestIsMarquee(unittest.TestCase):
    def test_includes_core_releases(self):
        for name in [
            "Core CPI YoY (May)", "PCE Price Index MoM", "Nonfarm Payrolls (May)",
            "Unemployment Rate (May)", "Initial Jobless Claims (Jun/06)", "GDP Growth Rate QoQ",
            "Retail Sales MoM (May)", "ISM Manufacturing PMI", "S&P Global Services PMI",
            "Housing Starts (May)", "Building Permits (May)", "NAHB Housing Market Index (Jun)",
            "Industrial Production MoM (May)", "NY Empire State Manufacturing Index (Jun)",
            "Michigan Consumer Sentiment Prel (Jun)", "Fed Interest Rate Decision",
        ]:
            self.assertTrue(is_marquee(name), name)

    def test_excludes_noise(self):
        for name in [
            "API Crude Oil Stock Change (Jun/12)", "Atlanta Fed GDPNow (Q2)",
            "20-Year Bond Auction", "CFTC S&P 500 speculative net positions",
            "Redbook YoY (Jun/13)", "Import Prices YoY (May)", "Export Prices MoM (May)",
            "EIA Crude Oil Stocks Change", "MBA Mortgage Applications", "Baker Hughes Oil Rig Count",
        ]:
            self.assertFalse(is_marquee(name), name)

    def test_empty(self):
        self.assertFalse(is_marquee(""))
        self.assertFalse(is_marquee(None))


class TestMergeUsEvents(unittest.TestCase):
    """Pins the 2026-06-25 bug: an 8:30am print sat in Upcoming because the
    endpoint queried first carried a null actual. A released print on EITHER
    endpoint must win over a not-yet-released duplicate of the same release."""

    def _ev(self, actual):
        return {"date": "2026-06-25 12:30:00", "country": "US",
                "event": "Core PCE Price Index YoY (May)", "previous": 3.3,
                "estimate": 3.4, "actual": actual, "impact": "Low", "unit": "%"}

    def test_released_supersedes_null_regardless_of_order(self):
        null_first = merge_us_events([[self._ev(None)], [self._ev(3.4)]])
        actual_first = merge_us_events([[self._ev(3.4)], [self._ev(None)]])
        for merged in (null_first, actual_first):
            self.assertEqual(len(merged), 1)            # one row per release
            self.assertTrue(merged[0]["released"])
            self.assertAlmostEqual(merged[0]["actual"], 3.4)

    def test_dedups_same_release_across_endpoints(self):
        merged = merge_us_events([[self._ev(3.4)], [self._ev(3.4)]])
        self.assertEqual(len(merged), 1)

    def test_drops_non_us_and_ignores_non_list(self):
        foreign = {"date": "2026-06-25 12:30:00", "country": "JP",
                   "event": "CPI YoY (Jun)", "actual": 1.6, "impact": "Low"}
        merged = merge_us_events([None, [foreign, self._ev(3.4)], "oops"])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["event"], "Core PCE Price Index YoY (May)")


if __name__ == "__main__":
    unittest.main()
