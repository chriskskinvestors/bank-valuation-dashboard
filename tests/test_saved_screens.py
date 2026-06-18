"""Unit tests for data.saved_screens — versioned save/load + history.

Storage (data.cloud_storage) is monkeypatched with an in-memory store.
"""
import unittest

import data.saved_screens as ss


class _MemStore:
    def __init__(self):
        self.files = {}

    def save_json(self, prefix, filename, data):
        # store a copy so later mutation of `data` can't corrupt history
        import copy
        self.files[(prefix, filename)] = copy.deepcopy(data)
        return True

    def load_json(self, prefix, filename):
        import copy
        v = self.files.get((prefix, filename))
        return copy.deepcopy(v) if v is not None else None

    def list_files(self, prefix, pattern="*.json"):
        return sorted(fn for (p, fn) in self.files if p == prefix)

    def delete_json(self, prefix, filename):
        self.files.pop((prefix, filename), None)
        return True


class TestSavedScreens(unittest.TestCase):
    def setUp(self):
        self.store = _MemStore()
        self._orig = (ss.save_json, ss.load_json, ss.list_files, ss.delete_json)
        ss.save_json = self.store.save_json
        ss.load_json = self.store.load_json
        ss.list_files = self.store.list_files
        ss.delete_json = self.store.delete_json

    def tearDown(self):
        ss.save_json, ss.load_json, ss.list_files, ss.delete_json = self._orig

    def test_first_save_is_version_1(self):
        self.assertTrue(ss.save_screen("S", {"tab_key": "valuation", "filters": [1]}))
        vs = ss.screen_versions("S")
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0]["version"], 1)
        self.assertTrue(vs[0]["current"])

    def test_resave_bumps_version_and_archives(self):
        ss.save_screen("S", {"v": 1})
        ss.save_screen("S", {"v": 2})
        ss.save_screen("S", {"v": 3})
        # current config is v3
        self.assertEqual(ss.load_screen("S"), {"v": 3})
        # versions newest-first
        vers = [v["version"] for v in ss.screen_versions("S")]
        self.assertEqual(vers, [3, 2, 1])
        # prior versions still loadable
        self.assertEqual(ss.load_screen("S", version=1), {"v": 1})
        self.assertEqual(ss.load_screen("S", version=2), {"v": 2})

    def test_list_screens_reports_version(self):
        ss.save_screen("S", {"tab_key": "capital", "filters": [1, 2]})
        ss.save_screen("S", {"tab_key": "capital", "filters": [1, 2, 3]})
        row = next(r for r in ss.list_screens() if r["name"] == "S")
        self.assertEqual(row["version"], 2)
        self.assertEqual(row["filter_count"], 3)

    def test_empty_name_rejected(self):
        self.assertFalse(ss.save_screen("  ", {"x": 1}))

    def test_history_capped(self):
        for i in range(ss._MAX_HISTORY + 5):
            ss.save_screen("S", {"v": i})
        vs = ss.screen_versions("S")
        # current + at most _MAX_HISTORY archived
        self.assertLessEqual(len(vs), ss._MAX_HISTORY + 1)
        self.assertTrue(vs[0]["current"])

    def test_unknown_version_returns_none(self):
        ss.save_screen("S", {"v": 1})
        self.assertIsNone(ss.load_screen("S", version=99))
        self.assertEqual(ss.screen_versions("missing"), [])
        self.assertIsNone(ss.load_screen("missing"))


if __name__ == "__main__":
    unittest.main()
