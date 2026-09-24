"""Long SEC companyfacts lag is a WARNING finding, never an error (2026-09-24).

The overlay (data/sec_facts_overlay) keeps the card right while SEC's XBRL
API lags a filed 10-Q, and after it runs `sec_as_of` is current — so the
200-day sec_filings staleness rule no longer sees anything. A lag past 90
days (Citi: ~7 months) is a source-health signal the nightly summary should
carry, without tripping the growth gate (errors only).
"""
import unittest
from datetime import date, timedelta

from data.validation import (SEC_FACTS_LAG_WARN_DAYS, check_sec_facts_lag,
                             validate_bank_metrics)


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class TestSecFactsLagWarning(unittest.TestCase):
    def test_overlay_older_than_threshold_warns(self):
        row = {"sec_facts_overlay": {"form": "10-Q", "filed": _ago(120),
                                     "report_date": "2026-06-30"}}
        f = check_sec_facts_lag(row)
        self.assertIsNotNone(f)
        self.assertEqual((f.severity, f.field, f.source, f.value),
                         ("warning", "sec_facts_lag", "SEC", 120))
        self.assertIn(f"10-Q filed {_ago(120)} (120 days)", f.message)

    def test_lag_diagnostics_without_overlay_warn(self):
        row = {"sec_facts_lag": True, "sec_filed_date": _ago(100),
               "sec_filed_form": "10-K"}
        f = check_sec_facts_lag(row)
        self.assertEqual(f.value, 100)
        self.assertIn("10-K", f.message)

    def test_within_threshold_is_silent(self):
        row = {"sec_facts_overlay": {"form": "10-Q",
                                     "filed": _ago(SEC_FACTS_LAG_WARN_DAYS)}}
        self.assertIsNone(check_sec_facts_lag(row))

    def test_no_lag_and_unknown_are_silent(self):
        self.assertIsNone(check_sec_facts_lag({}))
        self.assertIsNone(check_sec_facts_lag({"sec_facts_lag": False}))
        self.assertIsNone(check_sec_facts_lag({"sec_facts_lag": None,
                                               "sec_filed_date": _ago(400)}))
        self.assertIsNone(check_sec_facts_lag({"sec_facts_lag": True,
                                               "sec_filed_date": "garbage"}))

    def test_rides_validate_bank_metrics_as_warning_only(self):
        row = {"sec_facts_overlay": {"form": "10-Q", "filed": _ago(200)}}
        findings = validate_bank_metrics(row, sec_data={"sec_as_of": _ago(5)},
                                         fdic_data=None)
        lag = [f for f in findings if f.field == "sec_facts_lag"]
        self.assertEqual(len(lag), 1)
        self.assertEqual(lag[0].severity, "warning")
        self.assertFalse([f for f in findings if f.severity == "error"])


if __name__ == "__main__":
    unittest.main()
