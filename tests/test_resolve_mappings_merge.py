"""
Regression tests for AUDIT-2026-07-02 P1 #18: tools/resolve_all_mappings.py
unconditionally overwrote the curated data/bank_map_resolved.json, silently
reverting hand-curated wrong-entity fixes and §12(i) cik=None overrides on a
naive rerun. The default write is now a merge that preserves every existing
entry; a full re-resolution requires the explicit --overwrite flag.
"""
import unittest

from tools.resolve_all_mappings import _merge_resolved


class TestResolvedMappingMerge(unittest.TestCase):
    def setUp(self):
        # A curated on-disk mapping: TOWN is a §12(i) FDIC-only filer pinned to
        # cik=None on purpose; ACME carries a hand-fixed wrong-entity cert.
        self.existing = {
            "TOWN": {"cik": None, "fdic_cert": 12345, "name": "TowneBank",
                     "fdic_score": 100},
            "ACME": {"cik": 111, "fdic_cert": 222, "name": "Acme Bancorp",
                     "fdic_score": 95},
        }
        # A fresh heuristic re-resolution that would CORRUPT both curated
        # entries (invents a CIK for TOWN, joins ACME to the wrong cert) and
        # legitimately discovers one new ticker.
        self.fresh = {
            "TOWN": {"cik": 999999, "fdic_cert": 12345, "name": "Towne Bank",
                     "fdic_score": 80},
            "ACME": {"cik": 111, "fdic_cert": 888, "name": "Acme Corp",
                     "fdic_score": 60},
            "NEWB": {"cik": 333, "fdic_cert": 444, "name": "New Bancshares",
                     "fdic_score": 90},
        }

    def test_default_merge_preserves_curated_entries_verbatim(self):
        merged = _merge_resolved(self.existing, self.fresh, overwrite=False)
        # Curated §12(i) cik=None must NOT be overwritten by the heuristic CIK.
        self.assertIsNone(merged["TOWN"]["cik"])
        self.assertEqual(merged["TOWN"], self.existing["TOWN"])
        # Hand-fixed wrong-entity cert must survive too.
        self.assertEqual(merged["ACME"]["fdic_cert"], 222)
        self.assertEqual(merged["ACME"], self.existing["ACME"])

    def test_default_merge_adds_only_new_tickers(self):
        merged = _merge_resolved(self.existing, self.fresh, overwrite=False)
        self.assertIn("NEWB", merged)
        self.assertEqual(merged["NEWB"], self.fresh["NEWB"])
        # No entries dropped; exactly the one new ticker added.
        self.assertEqual(set(merged), {"TOWN", "ACME", "NEWB"})
        self.assertEqual(len(merged) - len(self.existing), 1)

    def test_overwrite_replaces_with_fresh(self):
        merged = _merge_resolved(self.existing, self.fresh, overwrite=True)
        self.assertEqual(merged, self.fresh)
        # Explicit opt-in: curated cik=None is gone, replaced by the heuristic.
        self.assertEqual(merged["TOWN"]["cik"], 999999)

    def test_merge_does_not_mutate_inputs(self):
        before = dict(self.existing["TOWN"])
        _merge_resolved(self.existing, self.fresh, overwrite=False)
        self.assertEqual(self.existing["TOWN"], before)

    def test_empty_existing_is_full_fresh(self):
        merged = _merge_resolved({}, self.fresh, overwrite=False)
        self.assertEqual(merged, self.fresh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
