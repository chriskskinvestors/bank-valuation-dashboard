"""
Pin (2026-09-30, owner report): the Earnings page showed "Reporting This Week
0 (14 in 14d)" in its summary header while the Calendar tab's ledger said
"This Week 2". The header counted the raw yfinance snapshot; the calendar
counted the merged agenda (yfinance + FMP + the IR/PR pipeline). One concept
must show one number: both renderers read ONE cached agenda build
(ui.earnings._upcoming_agenda) and count it with ONE function
(data.earnings_call.agenda_counts).

Source-level pin (the renderers need a live Streamlit session to run), same
pattern as tests/test_announcement_call_snapshot.

Run: python -m unittest tests.test_earnings_counts_consistency
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub


class TestEarningsCountsConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _streamlit_stub.install()
        import ui.earnings as earnings
        cls.kpi = inspect.getsource(earnings._render_earnings_kpi_bar)
        cls.cal = inspect.getsource(earnings._render_earnings_calendar)
        cls.helper = inspect.getsource(earnings._upcoming_agenda)

    def test_both_renderers_read_the_shared_agenda(self):
        for src in (self.kpi, self.cal):
            self.assertIn("_upcoming_agenda(", src)
            self.assertIn("agenda_counts(", src)
            # Neither counts the raw yfinance snapshot on its own any more.
            self.assertNotIn("fetch_earnings_calendar(", src)

    def test_shared_helper_builds_the_merged_agenda(self):
        self.assertIn("build_calls_agenda(", self.helper)
        self.assertIn("merged_call_info(", self.helper)
        # An all-sources-down outage must raise (never cached as an empty
        # agenda → "0 reporting" would be a confident wrong number).
        self.assertIn("earnings_agenda_sources_down(", self.helper)
        self.assertIn("raise RuntimeError", self.helper)


if __name__ == "__main__":
    unittest.main()
