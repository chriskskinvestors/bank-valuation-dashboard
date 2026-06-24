"""
Unit tests for the firm tagging added in the consensus-upload redo:
- save_manual_consensus stores a manual entry as ONE firm's view (default
  "My model", overridable), so it aggregates with broker notes.
- parse_bulk_consensus tags every bank's row in a multi-bank sector note with
  the ONE firm that published it.

Both monkeypatch save_consensus to capture the payloads (no GCS/local write),
mirroring the style of test_consensus_compile.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.consensus as c  # noqa: E402


class _CaptureSave(unittest.TestCase):
    """Base: replace save_consensus with a capture so nothing touches storage."""

    def setUp(self):
        self._orig = c.save_consensus
        self.saved: list[dict] = []
        c.save_consensus = lambda data: self.saved.append(dict(data)) or Path("x")

    def tearDown(self):
        c.save_consensus = self._orig


class TestSaveManualFirm(_CaptureSave):

    def test_manual_tags_explicit_firm(self):
        c.save_manual_consensus("SFST", "2026Q1", {"eps": 1.25, "nim": 3.4},
                                firm="KBW")
        self.assertEqual(len(self.saved), 1)
        d = self.saved[0]
        self.assertEqual(d["firm"], "KBW")
        self.assertEqual(d["ticker"], "SFST")
        self.assertEqual(d["source"], "manual")
        keys = {m["key"]: m["value"] for m in d["metrics"]}
        self.assertEqual(keys, {"eps": 1.25, "nim": 3.4})

    def test_manual_defaults_to_my_model(self):
        # A blank firm must still be a real, groupable label — never empty.
        c.save_manual_consensus("SFST", "2026Q1", {"eps": 1.25}, firm="")
        self.assertEqual(self.saved[0]["firm"], "My model")
        # And the default when the arg is omitted entirely.
        self.saved.clear()
        c.save_manual_consensus("SFST", "2026Q1", {"eps": 1.25})
        self.assertEqual(self.saved[0]["firm"], "My model")


class TestBulkFirm(_CaptureSave):

    CSV = b"Ticker,EPS,NIM\nJPM,5.44,2.75\nBAC,0.82,1.95\n"

    def test_bulk_tags_every_bank_with_one_firm(self):
        out = c.parse_bulk_consensus(self.CSV, "2026Q1", "sector.csv", firm="KBW")
        self.assertEqual(out["total_banks"], 2)
        by_tkr = {d["ticker"]: d for d in self.saved}
        self.assertEqual(set(by_tkr), {"JPM", "BAC"})
        for d in self.saved:
            self.assertEqual(d["firm"], "KBW")          # all one firm
            self.assertEqual(d["period"], "2026Q1")
        # Values mapped to canonical keys, in the units they were given.
        jpm = {m["key"]: m["value"] for m in by_tkr["JPM"]["metrics"]}
        self.assertEqual(jpm["eps"], 5.44)
        self.assertEqual(jpm["nim"], 2.75)

    def test_bulk_without_firm_leaves_no_firm_key(self):
        # Back-compat: omitting firm must not inject a firm (save_consensus then
        # falls back to the source label) — never a wrong "KBW"-style tag.
        c.parse_bulk_consensus(self.CSV, "2026Q1", "sector.csv")
        self.assertTrue(self.saved)
        for d in self.saved:
            self.assertNotIn("firm", d)


if __name__ == "__main__":
    unittest.main()
