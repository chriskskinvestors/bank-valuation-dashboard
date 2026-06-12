"""
Tests for 13F quarterly history retention (docs/SNL-BUILD-PLAN.md §13,
Ownership History tab) in data/form13f_client.py. All storage and EDGAR
access is mocked; no live network. Pins:

  • a refresh writes BOTH the latest window ({T}.json) and a quarter-keyed
    snapshot ({T}_{YYYYQn}.json) — latest is preserved, history added
  • re-running the same refresh is idempotent (no duplicate holders)
  • quarter merge is by filer CIK: earlier-seen filers in the same quarter
    are never dropped, fresh data wins per filer
  • get_holder_history assembles a holder × quarter matrix across 3
    synthetic quarters, newest-first cap via `quarters`
  • _report_quarter maps filing dates to the covered quarter (hand-checked)
  • existing API shape (fetch_institutional_holdings / summarize_holdings)
    is unchanged for current consumers (ui/ownership.py)
"""
import fnmatch
import json
import sys
import types
import unittest
from unittest.mock import patch

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data import form13f_client as f13  # noqa: E402

PREFIX = f13.FORM13F_CACHE_PREFIX


class _MemStore:
    """In-memory stand-in for data.cloud_storage save/load/list."""

    def __init__(self):
        self.files: dict[tuple[str, str], dict] = {}

    def save_json(self, prefix, filename, data) -> bool:
        # Round-trip through JSON like the real store does
        self.files[(prefix, filename)] = json.loads(json.dumps(data, default=str))
        return True

    def load_json(self, prefix, filename):
        return self.files.get((prefix, filename))

    def list_files(self, prefix, pattern="*.json"):
        return sorted(n for (p, n) in self.files
                      if p == prefix and fnmatch.fnmatch(n, pattern))


def _holder(cik, name, shares, value_usd, date_filed):
    return {
        "filer_cik": cik, "filer_name": name, "date_filed": date_filed,
        "accession": f"0000000000-26-{cik[-6:]}",
        "filing_url": "https://example.invalid",
        "shares": shares, "value_usd": value_usd, "positions": [],
    }


class _StoreBackedTest(unittest.TestCase):
    """Patches form13f_client's storage bindings with an in-memory store."""

    def setUp(self):
        self.store = _MemStore()
        for fn in ("save_json", "load_json", "list_files"):
            p = patch.object(f13, fn, getattr(self.store, fn))
            p.start()
            self.addCleanup(p.stop)


class TestQuarterKeyedWrite(_StoreBackedTest):

    CANDIDATES = [
        {"cik": "0001000001", "accession": "0001000001-26-000001",
         "filer_name": "Alpha Capital", "date_filed": "2026-05-15"},
        {"cik": "0001000002", "accession": "0001000002-26-000001",
         "filer_name": "Beta Advisors", "date_filed": "2026-05-10"},
    ]

    @staticmethod
    def _positions(cik, accession, ticker):
        return [{"issuer": "BANR", "cusip": "06652K103", "class": "COM",
                 "shares": 100_000.0, "value_thousands": 5_000_000.0}]

    def _run_refresh(self):
        with patch.object(f13, "_search_13f_for_ticker",
                          return_value=list(self.CANDIDATES)), \
             patch.object(f13, "_fetch_13f_info_table", self._positions):
            return f13.fetch_institutional_holdings(
                "BANR", max_filers=5, with_changes=False)

    def test_refresh_writes_latest_and_quarter_key(self):
        holders = self._run_refresh()
        self.assertEqual(len(holders), 2)

        # Latest window preserved, exactly as before
        latest = self.store.files[(PREFIX, "BANR.json")]
        self.assertEqual(len(latest["holders"]), 2)
        self.assertIn("cached_at", latest)

        # NEW: quarter-keyed snapshot (filed 2026-05 covers 2026Q1)
        snap = self.store.files[(PREFIX, "BANR_2026Q1.json")]
        self.assertEqual(snap["quarter"], "2026Q1")
        self.assertEqual(snap["ticker"], "BANR")
        self.assertEqual({h["filer_cik"] for h in snap["holders"]},
                         {"0001000001", "0001000002"})

    def test_rerun_is_idempotent(self):
        self._run_refresh()
        first = json.dumps(
            self.store.files[(PREFIX, "BANR_2026Q1.json")]["holders"],
            sort_keys=True)
        # Simulate latest-window expiry, then re-run the identical refresh
        del self.store.files[(PREFIX, "BANR.json")]
        self._run_refresh()
        snap = self.store.files[(PREFIX, "BANR_2026Q1.json")]
        self.assertEqual(len(snap["holders"]), 2)  # no duplicates
        self.assertEqual(
            json.dumps(snap["holders"], sort_keys=True), first)

    def test_quarter_merge_keeps_earlier_filers_and_updates_fresh(self):
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, "2026-05-01"),
            _holder("0002", "Beta", 200.0, 2000.0, "2026-05-01"),
        ])
        f13._save_quarter_snapshots("BANR", [
            _holder("0002", "Beta", 250.0, 2500.0, "2026-05-20"),  # updated
            _holder("0003", "Gamma", 300.0, 3000.0, "2026-05-20"),  # new
        ])
        snap = self.store.files[(PREFIX, "BANR_2026Q1.json")]
        by_cik = {h["filer_cik"]: h for h in snap["holders"]}
        self.assertEqual(set(by_cik), {"0001", "0002", "0003"})  # union
        self.assertEqual(by_cik["0002"]["shares"], 250.0)        # fresh wins
        self.assertEqual(by_cik["0001"]["shares"], 100.0)        # preserved

    def test_straddling_filings_land_in_their_own_quarters(self):
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, "2026-04-20"),  # 2026Q1
            _holder("0002", "Late Filer", 50.0, 500.0, "2026-01-10"),  # 2025Q4
        ])
        self.assertIn((PREFIX, "BANR_2026Q1.json"), self.store.files)
        self.assertIn((PREFIX, "BANR_2025Q4.json"), self.store.files)
        q4 = self.store.files[(PREFIX, "BANR_2025Q4.json")]["holders"]
        self.assertEqual([h["filer_cik"] for h in q4], ["0002"])


class TestHolderHistory(_StoreBackedTest):

    def _seed_three_quarters(self):
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 100.0, 1000.0, "2025-11-10"),  # 2025Q3
            _holder("0002", "Beta", 900.0, 9000.0, "2025-11-10"),
        ])
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 120.0, 1300.0, "2026-02-10"),  # 2025Q4
        ])
        f13._save_quarter_snapshots("BANR", [
            _holder("0001", "Alpha", 150.0, 1800.0, "2026-05-10"),  # 2026Q1
            _holder("0003", "Gamma", 75.0, 800.0, "2026-05-10"),
        ])

    def test_history_matrix_across_three_quarters(self):
        self._seed_three_quarters()
        hist = f13.get_holder_history("BANR")

        self.assertEqual(set(hist), {"Alpha", "Beta", "Gamma"})
        # Alpha present in all three quarters with per-quarter values
        self.assertEqual(set(hist["Alpha"]), {"2025Q3", "2025Q4", "2026Q1"})
        self.assertEqual(hist["Alpha"]["2025Q4"],
                         {"shares": 120.0, "value_usd": 1300.0})
        # Beta only in 2025Q3, Gamma only in 2026Q1 (sparse matrix)
        self.assertEqual(set(hist["Beta"]), {"2025Q3"})
        self.assertEqual(set(hist["Gamma"]), {"2026Q1"})

    def test_quarters_cap_keeps_most_recent(self):
        self._seed_three_quarters()
        hist = f13.get_holder_history("BANR", quarters=2)
        seen = {q for per_q in hist.values() for q in per_q}
        self.assertEqual(seen, {"2025Q4", "2026Q1"})  # 2025Q3 dropped
        self.assertNotIn("Beta", hist)

    def test_empty_store_and_bad_args(self):
        self.assertEqual(f13.get_holder_history("BANR"), {})
        self.assertEqual(f13.get_holder_history(""), {})
        self.assertEqual(f13.get_holder_history("BANR", quarters=0), {})

    def test_latest_window_file_is_not_mistaken_for_a_quarter(self):
        # A latest file plus an unrelated ticker's quarter file must not leak in
        self.store.save_json(PREFIX, "BANR.json", {"holders": []})
        self.store.save_json(PREFIX, "OTHER_2026Q1.json", {"holders": [
            _holder("0009", "Other Holder", 1.0, 1.0, "2026-05-01")]})
        self.assertEqual(f13.get_holder_history("BANR"), {})


class TestReportQuarter(unittest.TestCase):
    """Hand-checked filing-date → covered-quarter mappings."""

    def test_mappings(self):
        cases = {
            "2026-05-15": "2026Q1",  # filed in 45-day window after 3/31
            "2026-08-14": "2026Q2",
            "2026-11-12": "2026Q3",
            "2026-02-10": "2025Q4",  # January/February filings cover prior Q4
            "2026-01-02": "2025Q4",
            "2026-04-01": "2026Q1",
        }
        for filed, expected in cases.items():
            self.assertEqual(f13._report_quarter(filed), expected, filed)

    def test_garbage_returns_none(self):
        self.assertIsNone(f13._report_quarter(""))
        self.assertIsNone(f13._report_quarter(None))
        self.assertIsNone(f13._report_quarter("not-a-date"))


class TestExistingApiShapeUnchanged(_StoreBackedTest):
    """ui/ownership.py consumes fetch_institutional_holdings +
    summarize_holdings — pin the exact keys it relies on."""

    def test_holder_dict_keys_and_summary_shape(self):
        t = TestQuarterKeyedWrite
        with patch.object(f13, "_search_13f_for_ticker",
                          return_value=list(t.CANDIDATES)), \
             patch.object(f13, "_fetch_13f_info_table", t._positions):
            holders = f13.fetch_institutional_holdings(
                "BANR", max_filers=5, with_changes=False)

        self.assertIsInstance(holders, list)
        for key in ("filer_cik", "filer_name", "date_filed", "accession",
                    "filing_url", "shares", "value_usd", "positions"):
            self.assertIn(key, holders[0])

        summary = f13.summarize_holdings(holders)
        self.assertEqual(
            set(summary),
            {"total_filers", "total_shares", "total_value_usd",
             "top_holder", "top_5_concentration"})
        self.assertEqual(summary["total_filers"], 2)
        self.assertEqual(summary["total_shares"], 200_000.0)


if __name__ == "__main__":
    unittest.main()
