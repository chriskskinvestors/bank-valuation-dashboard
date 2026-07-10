"""data/estimates._fetch_from_yfinance — next-earnings-date fallback direction.

Pins the P3 fix: yfinance get_earnings_dates returns rows newest-first, so
when several FUTURE (unreported) quarters are listed, the old "take
history[0]" fallback picked the FARTHEST future date — showing an earnings
date (and its EPS estimate) two-plus quarters out instead of the nearest
upcoming report. The fix selects the MINIMUM date among unreported rows dated
today-or-later, and takes eps_estimate from that same row.

No network: yfinance is replaced with a fake module in sys.modules (the
function imports it lazily), so the REAL parsing/selection logic runs against
synthetic earnings-date tables.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.estimates as estimates  # noqa: E402

TODAY = datetime.now()


def _day(offset_days: int) -> datetime:
    return TODAY + timedelta(days=offset_days)


class _FakeEarningsDates:
    """Duck-typed stand-in for the DataFrame get_earnings_dates returns:
    only .empty and .iterrows() are consumed."""

    def __init__(self, rows):
        self._rows = rows  # list of (index_datetime, row_dict)

    @property
    def empty(self):
        return not self._rows

    def iterrows(self):
        return iter(self._rows)


def _row(when: datetime, estimate, actual, surprise=None):
    return (when, {"EPS Estimate": estimate, "Reported EPS": actual,
                   "Surprise(%)": surprise})


class _FakeTicker:
    def __init__(self, earnings_rows, info=None, calendar=None):
        self._earnings_rows = earnings_rows
        self.info = info or {}
        self.calendar = calendar

    def get_earnings_dates(self, limit=12):
        return _FakeEarningsDates(self._earnings_rows)


def _fetch(rows, info=None, calendar=None):
    """Run the real _fetch_from_yfinance against a fake yfinance module."""
    ticker_obj = _FakeTicker(rows, info=info, calendar=calendar)
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda symbol: ticker_obj
    with mock.patch.dict(sys.modules, {"yfinance": fake_yf}):
        return estimates._fetch_from_yfinance("TEST")


class TestNextEarningsDateFallback(unittest.TestCase):
    def test_picks_nearest_upcoming_not_farthest(self):
        # Newest-first table with TWO future quarters: history[0] is the
        # farthest one (+95d). The fallback must pick +5d — and take the EPS
        # estimate from that same nearest row, never the farthest row's.
        rows = [
            _row(_day(95), 9.99, None),          # farthest future — the old pick
            _row(_day(5), 4.12, None),           # nearest upcoming — correct
            _row(_day(-85), 3.90, 3.95, 1.28),   # reported past quarters
            _row(_day(-175), 3.80, 3.88, 2.11),
        ]
        result = _fetch(rows)
        self.assertIsNone(result.get("error"))
        self.assertEqual(result["next_earnings_date"],
                         str(_day(5).date()))
        self.assertEqual(result["eps_estimate"], 4.12)

    def test_single_future_candidate_behaves_as_before(self):
        # Only one unreported future row — behavior identical to the old code.
        rows = [
            _row(_day(7), 2.50, None),
            _row(_day(-83), 2.40, 2.45, 2.08),
        ]
        result = _fetch(rows)
        self.assertEqual(result["next_earnings_date"], str(_day(7).date()))
        self.assertEqual(result["eps_estimate"], 2.50)

    def test_no_future_rows_yields_none_not_a_stale_date(self):
        # A past-dated unreported row (reported yesterday, EPS not yet on
        # yfinance) is NOT a "next" earnings date; with no future rows both
        # fields stay None rather than a plausible-wrong guess.
        rows = [
            _row(_day(-1), 3.10, None),          # past + unreported — not a candidate
            _row(_day(-91), 3.00, 3.05, 1.67),
        ]
        result = _fetch(rows)
        self.assertIsNone(result["next_earnings_date"])
        self.assertIsNone(result["eps_estimate"])

    def test_today_counts_as_upcoming(self):
        # date >= today: a report dated today (not yet reported) is the next one.
        rows = [
            _row(_day(90), 5.50, None),
            _row(_day(0), 5.00, None),
        ]
        result = _fetch(rows)
        self.assertEqual(result["next_earnings_date"], str(_day(0).date()))
        self.assertEqual(result["eps_estimate"], 5.00)

    def test_calendar_date_is_not_overwritten(self):
        # t.calendar already supplied next_earnings_date — the history fallback
        # must keep it, while eps_estimate still comes from the nearest
        # upcoming history row.
        rows = [
            _row(_day(95), 9.99, None),
            _row(_day(5), 4.12, None),
        ]
        cal_date = _day(4)  # calendar's own (authoritative) date
        result = _fetch(rows, calendar={"Earnings Date": [cal_date]})
        self.assertEqual(result["next_earnings_date"], str(cal_date.date()))
        self.assertEqual(result["eps_estimate"], 4.12)

    def test_history_is_fully_preserved(self):
        # The selection change must not alter the earnings_history payload.
        rows = [
            _row(_day(95), 9.99, None),
            _row(_day(5), 4.12, None),
            _row(_day(-85), 3.90, 3.95, 1.28),
        ]
        result = _fetch(rows)
        self.assertEqual(len(result["earnings_history"]), 3)
        self.assertEqual(result["earnings_history"][0]["date"],
                         str(_day(95).date()))
        self.assertEqual(result["earnings_history"][2]["eps_actual"], 3.95)


if __name__ == "__main__":
    unittest.main()
