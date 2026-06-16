"""
Pins two SEC-leg quality tweaks (2026-06-16):

  1. pure-boilerplate skip — an 8-K whose ONLY item is 9.01 (Financial
     Statements / Exhibits) is an exhibit attachment with no substantive event
     and must NOT be emitted; filings carrying a real item alongside 9.01 stay.
  2. summarizer prioritization — _is_high_signal_8k flags the material-but-opaque
     item types (M&A, officer, restatement, regulatory) that jump the summarizer
     queue when budget is tight; routine earnings (2.02) do not.

SEC HTTP + CIK lookup are mocked; no network.
"""
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.events.sec_8k as sec_8k  # noqa: E402
from data.events.sec_8k import SEC8KAdapter  # noqa: E402
from jobs.poll_events import _is_high_signal_8k  # noqa: E402

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _submissions(items_per_filing):
    n = len(items_per_filing)
    return {"filings": {"recent": {
        "form": ["8-K"] * n,
        "filingDate": [f"2026-06-{15 - i:02d}" for i in range(n)],
        "accessionNumber": [f"0001-26-00000{i+1}" for i in range(n)],
        "primaryDocument": [f"doc{i}.htm" for i in range(n)],
        "items": items_per_filing,
    }}}


class TestBoilerplateSkip(unittest.TestCase):
    def _poll(self, items_per_filing):
        payload = _submissions(items_per_filing)
        with patch.object(sec_8k, "get_cik", return_value=320193), \
             patch.object(sec_8k.requests, "get", return_value=_FakeResp(payload)):
            return SEC8KAdapter()._poll_one("TEST", PAST)

    def test_pure_9_01_filing_skipped(self):
        evs = self._poll(["9.01"])
        self.assertEqual(evs, [], "an exhibits-only 8-K must not be emitted")

    def test_substantive_filings_kept(self):
        evs = self._poll(["9.01", "2.02,9.01", "8.01", "9.01"])
        # Two pure-9.01 dropped; earnings (2.02) and other-material (8.01) kept.
        self.assertEqual(len(evs), 2)
        leads = {e.headline for e in evs}
        self.assertTrue(any("Earnings" in h for h in leads))
        self.assertTrue(any("Other Material Event" in h for h in leads))
        self.assertFalse(any("Financial Statements / Exhibits" in h for h in leads))

    def test_real_item_with_9_01_not_dropped(self):
        # 9.01 is almost always attached to a real item — that filing stays.
        evs = self._poll(["5.02,9.01"])
        self.assertEqual(len(evs), 1)
        self.assertIn("Officer / Director Change", evs[0].headline)


class TestSummarizerPriority(unittest.TestCase):
    def test_high_signal_items_flagged(self):
        for item in ("1.01", "2.01", "8.01", "5.02", "4.02", "2.06", "5.01"):
            with self.subTest(item=item):
                self.assertTrue(_is_high_signal_8k(json.dumps({"items": [item, "9.01"]})))

    def test_routine_items_not_flagged(self):
        self.assertFalse(_is_high_signal_8k(json.dumps({"items": ["2.02", "9.01"]})))
        self.assertFalse(_is_high_signal_8k(json.dumps({"items": ["7.01"]})))

    def test_garbled_raw_json_is_safe(self):
        self.assertFalse(_is_high_signal_8k(None))
        self.assertFalse(_is_high_signal_8k("not json"))
        self.assertFalse(_is_high_signal_8k(json.dumps({})))


if __name__ == "__main__":
    unittest.main()
