"""
Corporate Profile's Latest News uses THE junk filter (REVIEW-2026-09-24 P2).

ui/bank_detail._render_latest_activity filtered with is_routine_noise only, so
13F-churn / SEO / law-firm headlines that the Home feed and Recent Activity
reject (data/events/wire_base.is_junk_news — CLAUDE.md's one junk filter)
still showed on the company page. Driven through the real renderer with a
recording `st`.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.bank_detail as bd  # noqa: E402

JUNK_13F = "State Street Corp Acquires 394,198 Shares of Banner Corp $BANR"
LEGIT = "Banner Corporation Reports Third Quarter 2026 Results"
NOTE = "Banner Corp Issues Autocallable Contingent Coupon Notes Linked to the S&P 500"


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecSt:
    def __init__(self):
        self.html = []

    def columns(self, n):
        return [_Ctx() for _ in range(n)]

    def markdown(self, body, **k):
        self.html.append(body)


class TestProfileNewsUsesJunkFilter(unittest.TestCase):
    def _render(self, events):
        import data.events.wire_base as wb
        rec = _RecSt()
        # is_junk_news(headline, ticker) runs the counterparty check, which
        # reads the bank-name index; seed it so no universe load (or live
        # universe build on an empty store) happens here.
        with patch.object(wb, "_NAME_INDEX", [("banner", "BANR")]), \
                patch.object(bd, "st", rec), \
                patch("data.events.get_recent_events", lambda t, limit=12: events), \
                patch("ui.states.empty_state", lambda *a, **k: rec.html.append("EMPTY")):
            bd._render_latest_activity("BANR", {"cik": None})
        return "\n".join(rec.html)

    def _ev(self, headline):
        return {"ticker": "BANR", "source": "google_news", "headline": headline,
                "url": "https://www.example.com/story"}

    def test_13f_churn_dropped_legit_kept(self):
        out = self._render([self._ev(JUNK_13F), self._ev(LEGIT)])
        # Pre-fix: is_routine_noise let the 13F-churn headline through.
        self.assertNotIn("Acquires 394,198 Shares", out)
        self.assertIn("Third Quarter 2026 Results", out)

    def test_structured_note_noise_still_dropped(self):
        # is_junk_news includes the old routine-noise rule — no regression.
        out = self._render([self._ev(NOTE), self._ev(LEGIT)])
        self.assertNotIn("Autocallable", out)
        self.assertIn("Third Quarter 2026 Results", out)


if __name__ == "__main__":
    unittest.main()
