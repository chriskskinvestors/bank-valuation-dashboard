"""Unit tests for data.bank_groups — group CRUD + ticker normalization.

The storage layer (data/cloud_storage) is monkeypatched with an in-memory store so
the tests never touch GCS or the real repo bank_groups/ directory.
"""
import unittest

import data.bank_groups as bg


class _MemStore:
    def __init__(self):
        self.files: dict[tuple, dict] = {}

    def save_json(self, prefix, filename, data):
        self.files[(prefix, filename)] = data
        return True

    def load_json(self, prefix, filename):
        return self.files.get((prefix, filename))

    def list_files(self, prefix, pattern="*.json"):
        return sorted(fn for (p, fn) in self.files if p == prefix)

    def delete_json(self, prefix, filename):
        self.files.pop((prefix, filename), None)
        return True


class TestBankGroups(unittest.TestCase):
    def setUp(self):
        self.store = _MemStore()
        self._orig = (bg.save_json, bg.load_json, bg.list_files, bg.delete_json)
        bg.save_json = self.store.save_json
        bg.load_json = self.store.load_json
        bg.list_files = self.store.list_files
        bg.delete_json = self.store.delete_json

    def tearDown(self):
        bg.save_json, bg.load_json, bg.list_files, bg.delete_json = self._orig

    def test_normalize_dedupes_uppercases_sorts(self):
        self.assertEqual(
            bg._normalize_tickers([" bac ", "JPM", "bac", "", None, "wfc"]),
            ["BAC", "JPM", "WFC"],
        )

    def test_save_and_load_roundtrip(self):
        self.assertTrue(bg.save_group("My Banks", ["wfc", "JPM"]))
        self.assertEqual(bg.get_group_tickers("My Banks"), ["JPM", "WFC"])

    def test_empty_name_rejected(self):
        self.assertFalse(bg.save_group("  ", ["JPM"]))
        self.assertFalse(bg.save_group("", ["JPM"]))

    def test_list_groups_counts(self):
        bg.save_group("A", ["JPM", "BAC"])
        bg.save_group("B", ["WFC"])
        names = {g["name"]: g["count"] for g in bg.list_groups()}
        self.assertEqual(names, {"A": 2, "B": 1})

    def test_add_remove_tickers(self):
        bg.save_group("G", ["JPM"])
        bg.add_tickers("G", ["bac", "JPM"])  # JPM is a dup, must collapse
        self.assertEqual(bg.get_group_tickers("G"), ["BAC", "JPM"])
        bg.remove_tickers("G", ["jpm"])      # case-insensitive removal
        self.assertEqual(bg.get_group_tickers("G"), ["BAC"])

    def test_add_remove_on_missing_group_is_false(self):
        self.assertFalse(bg.add_tickers("nope", ["JPM"]))
        self.assertFalse(bg.remove_tickers("nope", ["JPM"]))

    def test_delete(self):
        bg.save_group("G", ["JPM"])
        bg.delete_group("G")
        self.assertIsNone(bg.load_group("G"))
        self.assertEqual(bg.get_group_tickers("G"), [])

    def test_rename(self):
        bg.save_group("Old", ["JPM"], "desc")
        self.assertTrue(bg.rename_group("Old", "New"))
        self.assertIsNone(bg.load_group("Old"))
        self.assertEqual(bg.get_group_tickers("New"), ["JPM"])
        self.assertEqual(bg.load_group("New")["description"], "desc")

    def test_rename_empty_target_rejected(self):
        bg.save_group("Old", ["JPM"])
        self.assertFalse(bg.rename_group("Old", "  "))
        self.assertEqual(bg.get_group_tickers("Old"), ["JPM"])

    def test_portfolio_seed_idempotent(self):
        # Reads the real committed portfolio.json (a non-empty list of tickers).
        bg.ensure_portfolio_seed()
        first = bg.get_group_tickers(bg.PORTFOLIO_GROUP)
        self.assertTrue(len(first) > 0)
        bg.ensure_portfolio_seed()  # second call must be a no-op
        self.assertEqual(bg.get_group_tickers(bg.PORTFOLIO_GROUP), first)


if __name__ == "__main__":
    unittest.main()
