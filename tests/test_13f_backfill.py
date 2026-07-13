"""Unit tests for the EDGAR 13F backfill (plan §13 phase 2).

Run: python -m unittest tests.test_13f_backfill
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.form13f_client import (  # noqa: E402
    _quarter_filing_window,
    backfill_quarter,
)


class TestQuarterFilingWindow(unittest.TestCase):
    def test_hand_computed_windows(self):
        # Q1 ends 03-31 → window 04-01 .. 06-14 (75 days after quarter end)
        self.assertEqual(_quarter_filing_window("2026Q1"),
                         ("2026-04-01", "2026-06-14"))
        # Q4 ends 12-31 → window crosses the year boundary
        self.assertEqual(_quarter_filing_window("2025Q4"),
                         ("2026-01-01", "2026-03-16"))
        # Q2 ends 06-30
        self.assertEqual(_quarter_filing_window("2025Q2"),
                         ("2025-07-01", "2025-09-13"))

    def test_malformed_quarter(self):
        self.assertIsNone(_quarter_filing_window("2026Q5"))
        self.assertIsNone(_quarter_filing_window("garbage"))
        self.assertIsNone(_quarter_filing_window(""))


class TestBackfillQuarter(unittest.TestCase):
    def test_existing_snapshot_skipped_never_clobbered(self):
        with patch("data.form13f_client.load_json",
                   return_value={"holders": [{"filer_cik": "1"}]}), \
             patch("data.form13f_client._search_13f_for_ticker") as search:
            out = backfill_quarter("WAL", "Western Alliance", "2025Q4")
        self.assertIsNone(out)
        search.assert_not_called()  # merge-only: no fetch when stored

    def test_empty_result_not_persisted(self):
        with patch("data.form13f_client.load_json", return_value=None), \
             patch("data.form13f_client._search_13f_for_ticker", return_value=[]), \
             patch("data.form13f_client._save_quarter_snapshots") as save:
            out = backfill_quarter("TINY", "", "2025Q4")
        self.assertEqual(out, 0)
        save.assert_not_called()  # retry stays possible

    def test_found_holders_persisted_via_shared_writer(self):
        holders = [{"filer_cik": "1", "date_filed": "2026-02-10", "shares": 5}]
        with patch("data.form13f_client.load_json", return_value=None), \
             patch("data.form13f_client._search_13f_for_ticker",
                   return_value=[{"cik": "1"}]) as search, \
             patch("data.form13f_client._holders_from_candidates",
                   return_value=holders), \
             patch("data.form13f_client._save_quarter_snapshots") as save:
            out = backfill_quarter("WAL", "Western Alliance", "2025Q4")
        self.assertEqual(out, 1)
        save.assert_called_once_with("WAL", holders)
        # the search must be date-bounded to the quarter's filing season
        _, kwargs = search.call_args
        self.assertEqual(kwargs.get("startdt"), "2026-01-01")
        self.assertEqual(kwargs.get("enddt"), "2026-03-16")

    def test_malformed_quarter_is_none(self):
        self.assertIsNone(backfill_quarter("WAL", "", "26Q1"))


if __name__ == "__main__":
    unittest.main()
