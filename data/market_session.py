"""
US equity trading-session helpers (clock-based, US/Eastern).

Used to decide when the Home page shows pre-market moves: the bank pre-market
quote job runs only in the pre-market window, and the Home panes label/show
pre-market data only while that window is open. Holidays aren't modeled — on a
market holiday the pre-market job simply finds no fresh quotes, so the panes
fall back to "—" without showing anything wrong.
"""

from __future__ import annotations

from datetime import datetime, time as _time
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_PREMARKET_OPEN = _time(4, 0)     # 4:00 a.m. ET
_REGULAR_OPEN = _time(9, 30)      # 9:30 a.m. ET


def is_premarket(now: datetime | None = None) -> bool:
    """True during the US pre-market window (4:00–9:30 a.m. ET) on a weekday.
    `now` (any tz-aware datetime) is for tests; defaults to the current ET time."""
    n = (now or datetime.now(_ET)).astimezone(_ET)
    if n.weekday() >= 5:          # Saturday / Sunday
        return False
    return _PREMARKET_OPEN <= n.time() < _REGULAR_OPEN
