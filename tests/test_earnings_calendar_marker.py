"""
Pin the Calendar's date-status marker (ui.earnings._cal_tr, 2026-09-30):
  ✓  the date is a FACT (company announcement / FMP confirming that date),
  ◐  the company has published a call date that fits the estimate — the
     date itself stays "(proj.)",
  —  estimate only.
The ◐ replaced the old rule that let a nearby call date CONFIRM an estimate
(PR #195); the fact and the softer signal never render the same glyph.

Run: python -m unittest tests.test_earnings_calendar_marker
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub


def _row(**over):
    base = {"ticker": "AAA", "date": "2026-10-16", "days_until": 16,
            "when": "Before open", "confirmed": False, "call_consistent": False,
            "eps_est": 1.0, "rev_est": 1e8, "call_date": None,
            "call_time": None, "webcast_url": None, "dial_in": None}
    base.update(over)
    return base


class TestCalendarMarker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _streamlit_stub.install()
        import ui.earnings as earnings
        cls.E = earnings

    def _tr(self, **over):
        from unittest import mock
        with mock.patch.object(self.E, "get_name", lambda t: "AAA Bancorp"):
            return self.E._cal_tr(_row(**over), soon=False)

    def test_confirmed_renders_check_without_proj(self):
        tr = self._tr(confirmed=True)
        self.assertIn("✓", tr)
        self.assertNotIn("◐", tr)
        self.assertNotIn("(proj.)", tr)

    def test_call_consistent_renders_half_marker_and_stays_projected(self):
        tr = self._tr(call_consistent=True, call_date="2026-10-16",
                      call_time="10:00a ET")
        self.assertIn("◐", tr)
        self.assertNotIn("✓", tr)
        self.assertIn("(proj.)", tr)

    def test_estimate_only_renders_dash_and_projected(self):
        tr = self._tr()
        self.assertNotIn("✓", tr)
        self.assertNotIn("◐", tr)
        self.assertIn("(proj.)", tr)

    def test_confirmed_wins_over_call_consistent(self):
        tr = self._tr(confirmed=True, call_consistent=True)
        self.assertIn("✓", tr)
        self.assertNotIn("◐", tr)


if __name__ == "__main__":
    unittest.main()
