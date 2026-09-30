"""PBAM class (REVIEW-2026-09-24 numbers P1-8): a curated `cik: None` beats
discovery, so a bank that became an SEC registrant stayed "no SEC filer".

PBAM (Private Bancorp of America) registered on Form 10-12B in 2026-07 and
filed its first 10-Q for 2026-06-30 (acc 0001705284-26-000008): R2 "Total
Shareholders' Equity" $285,516K, R3 5,725,696 shares outstanding. The nightly
guard must flag exactly that shape, and stay quiet on the ~20 curated Nones
that are still in SEC's ticker file with years-stale or no periodic filings.
Offline: the SEC ticker file and submissions reads are patched. Dates are
relative to today so the test cannot turn into a date bomb.
"""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from data import bank_mapping, bank_universe


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# company_tickers_exchange.json rows: [cik, name, ticker, exchange]
SEC_ROWS = [
    [1705284, "Private Bancorp of America, Inc.", "PBAM", "Nasdaq"],
    [804563, "BENCHMARK BANKSHARES INC", "BMBN", "OTC"],
    [2105965, "West Coast Community Bancorp", "WCCB", "OTC"],
    [1090009, "Southern First Bancshares", "SFST", "Nasdaq"],
]
PERIODIC = {
    1705284: {"form": "10-Q", "date": _ago(26), "report_date": _ago(92)},  # fresh
    804563: {"form": "10-Q", "date": "2004-11-12", "report_date": "2004-09-30"},
    2105965: None,                                   # registered, no 10-Q yet
}
CURATED = {
    "PBAM": {"name": "P", "fdic_cert": 58291, "cik": None},
    "BMBN": {"name": "B", "fdic_cert": 1, "cik": None},
    "WCCB": {"name": "W", "fdic_cert": 2, "cik": None},
    "SFST": {"name": "S", "fdic_cert": 35295, "cik": 1090009},
}


class TestCuratedCikGuard(unittest.TestCase):

    def _run(self, bank_map, resolved=None):
        with patch.object(bank_mapping, "BANK_MAP", bank_map), \
             patch.object(bank_mapping, "_RESOLVED_FROM_JSON", resolved or {}), \
             patch.object(bank_universe, "_fetch_sec_companies", return_value=SEC_ROWS), \
             patch("data.sec_earnings_8k.latest_periodic_filing",
                   side_effect=lambda cik: PERIODIC.get(cik)):
            return bank_universe.run_curated_cik_guard()

    def test_flags_only_a_fresh_periodic_filer(self):
        # PBAM: fresh 10-Q → flag. BMBN: last 10-Q 2004 (deliberate None).
        # WCCB: listed, no periodic filing yet. SFST: already has its CIK.
        self.assertEqual(self._run(CURATED), ["PBAM"])

    def test_bank_map_cik_overrides_a_json_none(self):
        # Lookup order: BANK_MAP wins over the resolved JSON.
        self.assertEqual(self._run({"PBAM": {"cik": 1705284}},
                                   resolved={"PBAM": {"cik": None}}), [])

    def test_json_none_alone_is_checked(self):
        self.assertEqual(self._run({}, resolved={"PBAM": {"cik": None}}), ["PBAM"])

    def test_stale_filer_past_window_not_flagged(self):
        old = {1705284: {"form": "10-K", "report_date": _ago(400)}}
        with patch.dict(PERIODIC, old):
            self.assertEqual(self._run(CURATED), [])

    def test_never_raises(self):
        with patch.object(bank_universe, "_fetch_sec_companies",
                          side_effect=RuntimeError("SEC down")):
            self.assertEqual(bank_universe.run_curated_cik_guard(), [])


class TestPbamMapping(unittest.TestCase):

    def test_pbam_has_its_sec_cik_in_both_maps(self):
        self.assertEqual(bank_mapping.BANK_MAP["PBAM"]["cik"], 1705284)
        self.assertEqual(bank_mapping._RESOLVED_FROM_JSON["PBAM"]["cik"], 1705284)
        self.assertEqual(bank_mapping.get_cik("PBAM"), 1705284)


if __name__ == "__main__":
    unittest.main(verbosity=2)
