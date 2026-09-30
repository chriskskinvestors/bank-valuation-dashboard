"""A curated `cik: None` must not shadow a real SEC registrant (review P1-8).

PBAM registered with the SEC in 2026 (CIK 1705284, Nasdaq, 10-Q for
2026-06-30), but BANK_MAP / bank_map_resolved.json still said `cik: None`,
and curated entries win over discovery — so the site showed no filings, no
Company Reported statements and no EPS. Offline: SEC's company_tickers.json
is a checked-in subset (tests/sec_company_tickers_fixture.json, 2026-09-30:
every row whose ticker we map to None, plus PBAM). The live re-check is
tools/check_none_cik_vs_sec.py.
"""
import json
import unittest
from pathlib import Path

from tools.check_none_cik_vs_sec import (
    REVIEWED_NON_FILERS, mapped_none_cik_tickers, shadowed_registrants)

_FIXTURE = Path(__file__).parent / "sec_company_tickers_fixture.json"


def _sec_rows():
    return list(json.loads(_FIXTURE.read_text(encoding="utf-8")).values())


class TestNoneCikGuard(unittest.TestCase):

    def test_no_unreviewed_none_cik_ticker_is_an_sec_registrant(self):
        hits = shadowed_registrants(mapped_none_cik_tickers(), _sec_rows())
        unreviewed = [h for h in hits if h[0] not in REVIEWED_NON_FILERS]
        self.assertEqual(unreviewed, [],
                         "curated cik=None shadows an SEC registrant — set the "
                         "CIK or record why not in REVIEWED_NON_FILERS")

    def test_reviewed_list_has_no_stale_entries(self):
        # An entry whose ticker is no longer mapped None (or no longer listed
        # by SEC) is dead weight that could hide a future regression.
        hits = {h[0] for h in shadowed_registrants(mapped_none_cik_tickers(),
                                                   _sec_rows())}
        self.assertEqual(sorted(set(REVIEWED_NON_FILERS) - hits), [])

    def test_pbam_maps_to_its_sec_cik(self):
        from data.bank_mapping import BANK_MAP, _RESOLVED_FROM_JSON, get_cik
        self.assertEqual(get_cik("PBAM"), 1705284)
        self.assertEqual(BANK_MAP["PBAM"]["cik"], 1705284)
        self.assertEqual(_RESOLVED_FROM_JSON["PBAM"]["cik"], 1705284)
        self.assertNotIn("PBAM", REVIEWED_NON_FILERS)

    def test_detector_flags_a_none_mapped_registrant(self):
        rows = [{"cik_str": 1705284, "ticker": "PBAM",
                 "title": "Private Bancorp of America, Inc."},
                {"cik_str": 19617, "ticker": "JPM", "title": "JPMORGAN CHASE"}]
        self.assertEqual(shadowed_registrants({"pbam", "TOWN"}, rows),
                         [("PBAM", 1705284, "Private Bancorp of America, Inc.")])


if __name__ == "__main__":
    unittest.main()
