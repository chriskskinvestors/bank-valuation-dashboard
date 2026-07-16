"""
Pins the 2026-07-16 SEC8KAdapter budget fix.

The adapter serial-looped the whole universe (~0.7s/bank, ~310s at 445 banks)
against poll_events._PER_ADAPTER_S = 240s. The runner enforces that cap by
RAISING TimeoutError, so poll() never returned and EVERY event it had already
collected was discarded — the per-CIK "can't miss" 8-K backstop contributed
zero on every full poll, and with it the 10-K/10-Q events that drive the
fundamentals cache invalidation. Pinned here: a slow straggler can no longer
sink the batch, and the walk stays inside its own budget.

Offline: _poll_one is stubbed; no SEC calls.
"""
import sys
import time
import types
import unittest
from unittest.mock import patch

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from datetime import datetime, timezone  # noqa: E402
from data.events.base import Event  # noqa: E402
from data.events.sec_8k import SEC8KAdapter  # noqa: E402


def _ev(tk):
    return Event(ticker=tk, source="sec_8k", event_type="earnings",
                 headline="8-K · Earnings / Results",
                 published_at=datetime.now(timezone.utc),
                 url=f"https://sec.gov/{tk}", external_id=f"acc-{tk}")


class TestSec8kBudget(unittest.TestCase):
    def test_slow_straggler_does_not_discard_the_whole_batch(self):
        ad = SEC8KAdapter()
        ad.MAX_POLL_SECONDS = 1.0

        def fake(ticker, cutoff):
            if ticker == "SLOW":
                time.sleep(30)          # hangs past the budget
            return [_ev(ticker)]

        with patch.object(SEC8KAdapter, "_poll_one", side_effect=fake,
                          autospec=False):
            out = ad.poll(["AAA", "BBB", "CCC", "SLOW"])

        got = {e.ticker for e in out}
        self.assertIn("AAA", got)
        self.assertIn("BBB", got)
        self.assertIn("CCC", got)
        self.assertNotIn("SLOW", got)
        self.assertEqual(len(got), 3,
                         "fast banks' events must survive a hung straggler")

    def test_all_events_returned_when_nothing_is_slow(self):
        ad = SEC8KAdapter()
        with patch.object(SEC8KAdapter, "_poll_one",
                          side_effect=lambda t, c: [_ev(t)]):
            out = ad.poll(["AAA", "BBB", "CCC"])
        self.assertEqual({e.ticker for e in out}, {"AAA", "BBB", "CCC"})

    def test_one_banks_error_does_not_lose_the_others(self):
        ad = SEC8KAdapter()

        def fake(ticker, cutoff):
            if ticker == "BOOM":
                raise ValueError("bad payload")
            return [_ev(ticker)]

        with patch.object(SEC8KAdapter, "_poll_one", side_effect=fake):
            out = ad.poll(["AAA", "BOOM", "CCC"])
        self.assertEqual({e.ticker for e in out}, {"AAA", "CCC"})

    def test_budget_is_under_the_runners_per_adapter_cap(self):
        # The whole point: finish and RETURN before the runner abandons us.
        from jobs.poll_events import _PER_ADAPTER_S
        self.assertLess(SEC8KAdapter.MAX_POLL_SECONDS, _PER_ADAPTER_S)

    def test_worker_count_respects_sec_rate_limit(self):
        # SEC allows 10 req/s; ~0.7s per request per worker.
        self.assertLessEqual(SEC8KAdapter.MAX_WORKERS / 0.7, 10.0)


if __name__ == "__main__":
    unittest.main()
