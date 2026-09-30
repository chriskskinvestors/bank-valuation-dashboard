"""Guard: AppTest suites never write the developer's real store.

Found 2026-09-25: tests/test_nav_renders.py ran app.py against a 2-bank stub
universe with no store isolation, so Home persisted a 2-bank
`watchlist_metrics_snap` into the real local cache.db and the local app then
served 2 of 364 banks. tests/_apptest_store.isolate_store is the fix; these
pins keep it covering (1) every suite that runs app.py and (2) every store
module holding its own data.db engine, and (3) check it routes and restores.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from sqlalchemy import text

from tests._apptest_store import STORE_MODULES, isolate_store

REPO = Path(__file__).resolve().parent.parent


class TestAppTestStoreIsolation(unittest.TestCase):

    def test_every_apptest_suite_isolates_the_store(self):
        suites = [p for p in sorted((REPO / "tests").glob("*.py"))
                  if "AppTest.from_" in p.read_text(encoding="utf-8")]
        self.assertTrue(suites, "no AppTest suites found — glob broken?")
        bare = [p.name for p in suites
                if "isolate_store(self)" not in p.read_text(encoding="utf-8")]
        self.assertEqual(bare, [], "these suites run app.py against the "
                         "developer's REAL cache.db — call isolate_store(self) "
                         "in setUp (see tests/_apptest_store.py)")

    def test_store_list_covers_every_engine_holder(self):
        holders = set()
        for p in (REPO / "data").rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            if (re.search(r"^_engine = None$", src, re.M)
                    and "from data.db import get_engine" in src):
                rel = p.relative_to(REPO).with_suffix("")
                holders.add(".".join(rel.parts))
        self.assertEqual(holders, set(STORE_MODULES),
                         "a store module caching its own data.db engine is "
                         "missing from _apptest_store.STORE_MODULES (it would "
                         "keep writing the real cache.db under AppTest)")

    def test_routes_writes_privately_and_restores(self):
        import data.cache as cache
        import data.db as db
        before = (db._engine, cache._engine)

        case = unittest.TestCase()
        eng = isolate_store(case)
        try:
            cache.put("watchlist_metrics_snap", {"n_tickers": 2})
            self.assertEqual(cache.get("watchlist_metrics_snap"), {"n_tickers": 2})
            self.assertIs(cache._engine, eng)
            with eng.connect() as conn:
                n = conn.execute(text("SELECT COUNT(*) FROM cache WHERE key = "
                                      "'watchlist_metrics_snap'")).scalar()
            self.assertEqual(n, 1)
        finally:
            case.doCleanups()
        self.assertEqual((db._engine, cache._engine), before)


if __name__ == "__main__":
    unittest.main()
