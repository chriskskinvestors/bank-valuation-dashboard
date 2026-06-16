"""
Tests for data/econ_calendar.py — the FMP economics-calendar parser behind the
Economic Data "latest releases & surprises" panel and upcoming-release calendar.

Pins the pure parse: surprise = actual − consensus (and % vs |consensus|),
the `released` flag, impact normalization, and the n/a paths. No network.
Field names match the live FMP shape captured 2026-06-16 (e.g. Building
Permits, Housing Starts MoM).
"""
import unittest

from data.econ_calendar import parse_event, _impact_ok


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


if __name__ == "__main__":
    unittest.main()
