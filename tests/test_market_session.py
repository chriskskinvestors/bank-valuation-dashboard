"""Unit tests for data.market_session.is_premarket — the 4:00-9:30 a.m. ET
weekday window that gates the Home page's pre-market display."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.market_session import is_premarket  # noqa: E402

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")
# 2026-06-22 is a Monday; 2026-06-20 is a Saturday.


class TestIsPremarket(unittest.TestCase):

    def test_weekday_premarket_window(self):
        self.assertTrue(is_premarket(datetime(2026, 6, 22, 4, 0, tzinfo=_ET)))   # open
        self.assertTrue(is_premarket(datetime(2026, 6, 22, 7, 30, tzinfo=_ET)))
        self.assertTrue(is_premarket(datetime(2026, 6, 22, 9, 29, tzinfo=_ET)))

    def test_outside_window(self):
        self.assertFalse(is_premarket(datetime(2026, 6, 22, 3, 59, tzinfo=_ET)))  # too early
        self.assertFalse(is_premarket(datetime(2026, 6, 22, 9, 30, tzinfo=_ET)))  # regular open
        self.assertFalse(is_premarket(datetime(2026, 6, 22, 12, 0, tzinfo=_ET)))  # midday
        self.assertFalse(is_premarket(datetime(2026, 6, 22, 17, 0, tzinfo=_ET)))  # after-hours

    def test_weekend_is_never_premarket(self):
        self.assertFalse(is_premarket(datetime(2026, 6, 20, 7, 0, tzinfo=_ET)))   # Saturday

    def test_converts_from_other_tz(self):
        # 12:00 UTC == 8:00 a.m. EDT on a June weekday → in the window.
        self.assertTrue(is_premarket(datetime(2026, 6, 22, 12, 0, tzinfo=_UTC)))


if __name__ == "__main__":
    unittest.main()
