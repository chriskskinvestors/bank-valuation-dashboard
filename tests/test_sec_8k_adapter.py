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
import data.events.wire_base as wire_base  # noqa: E402
from data.events.sec_8k import SEC8KAdapter, SEC8KRecentAdapter  # noqa: E402
from data.events.wire_base import RSSItem  # noqa: E402
from jobs.poll_events import _is_high_signal_8k  # noqa: E402

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)


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


def _entry(cik10, items_line, acc, title="Test Bank Corp", pub=NOW):
    return RSSItem(
        title=f"8-K - {title} ({cik10}) (Filer)",
        summary=f"Filed: 2026-06-18 AccNo: {acc} Size: 100 KB {items_line}",
        link=f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/x-index.htm",
        published=pub,
        guid=f"urn:tag:sec.gov,2008:accession-number={acc}",
    )


class TestRecentFeedAdapter(unittest.TestCase):
    """SEC8KRecentAdapter — all-banks 8-K from EDGAR's recent-filings feed."""

    def _poll(self, entries, tickers=("TBNK",), cik=123456):
        with patch.object(sec_8k, "get_cik",
                          side_effect=lambda t: cik if t.upper() == "TBNK" else None), \
             patch.object(wire_base, "fetch_rss", return_value=entries):
            return SEC8KRecentAdapter().poll(list(tickers), since=PAST)

    def test_matches_only_universe_banks(self):
        evs = self._poll([
            _entry("0000123456", "Item 2.02: Results of Operations", "0000123456-26-000001"),
            # different CIK — not a tracked bank — must be ignored
            _entry("0000999999", "Item 8.01: Other Events", "0000999999-26-000002", title="Random Co"),
        ])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "TBNK")
        self.assertEqual(evs[0].external_id, "0000123456-26-000001")
        self.assertIn("Earnings", evs[0].headline)
        self.assertEqual(evs[0].raw["items"], ["2.02"])

    def test_pure_9_01_skipped(self):
        evs = self._poll([
            _entry("0000123456", "Item 9.01: Financial Statements and Exhibits", "0000123456-26-000003"),
        ])
        self.assertEqual(evs, [], "exhibits-only feed entry must be dropped")

    def test_dedup_on_accession(self):
        evs = self._poll([
            _entry("0000123456", "Item 5.02: Departure of Officers", "0000123456-26-000004"),
            _entry("0000123456", "Item 5.02: Departure of Officers", "0000123456-26-000004"),
        ])
        self.assertEqual(len(evs), 1)

    def test_same_source_and_id_as_per_cik_adapter(self):
        # Both adapters use source 'sec_8k' + accession id, so they dedup in store.
        evs = self._poll([_entry("0000123456", "Item 1.01: Material Agreement",
                                 "0000123456-26-000005")])
        self.assertEqual(evs[0].source, "sec_8k")


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
