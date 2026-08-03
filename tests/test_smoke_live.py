"""
Regression test for the post-deploy live smoke's cold-start retry
(tests/smoke_live.py). Deploy run #936 false-failed: a stone-cold revision
after a full Docker layer-cache-miss rebuild served the shell HTML but didn't
mount the React app within the 30s window; a re-run 30 minutes later passed
with zero code changes. One stApp-selector miss must earn exactly one
fresh-page retry with a longer window — and a repeat miss must still fail,
because a revision that can't mount twice IS a broken deploy.
"""
import importlib.util
import sys
import types
import unittest

# Stub playwright if absent (local dev / keyless CI) — smoke_live imports
# sync_playwright at module load, but these tests never touch a browser.
if importlib.util.find_spec("playwright") is None:
    _pw = types.ModuleType("playwright")
    _api = types.ModuleType("playwright.sync_api")
    _api.sync_playwright = None  # referenced by import only, never called here
    _pw.sync_api = _api
    sys.modules.setdefault("playwright", _pw)
    sys.modules.setdefault("playwright.sync_api", _api)

from tests import smoke_live


class TestShellRetryDecision(unittest.TestCase):
    def test_first_miss_is_retried(self):
        self.assertTrue(smoke_live._shell_retry_allowed(0))

    def test_second_miss_is_final(self):
        self.assertFalse(smoke_live._shell_retry_allowed(1))

    def test_exactly_one_retry_with_a_longer_window(self):
        self.assertEqual(len(smoke_live.SHELL_TIMEOUTS_MS), 2)
        first, second = smoke_live.SHELL_TIMEOUTS_MS
        self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main()
