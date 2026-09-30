"""Private store for AppTest suites that run the real app.py.

Found 2026-09-25: the AppTest suites run app.py against a 2-bank stub
universe, and Home's load_all_data_fast persisted that 2-bank
`watchlist_metrics_snap` / `watchlist_metrics_last` into the developer's REAL
cache.db — the local app then served a 2-of-364 snapshot. isolate_store()
routes every data.db-backed store at a throwaway SQLite file for the test's
duration and restores the originals on cleanup.

A temp FILE, not `sqlite://`: AppTest runs the script on its own thread (and
the app fans out to pools), and an in-memory SQLite database is per
connection — other threads would see an empty database with no tables.

_USE_POSTGRES is forced off too, so a shell with DATABASE_URL set (local
tooling against prod) can never point a test run at Postgres.

Imported as a top-level module (the suites put tests/ on sys.path): going
through the `tests` package would install the streamlit stub over the real
runtime AppTest needs.
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from unittest import mock

# Every module holding its own `_engine` taken from data.db.get_engine().
# tests/test_apptest_store_isolation.py pins this list against the source.
STORE_MODULES = (
    "data.cache",
    "data.branches_store",
    "data.call_report_store",
    "data.events.store",
    "data.fdic_history_store",
    "data.nim_assumptions_store",
    "data.price_cache_store",
)


def isolate_store(test):
    """Route all stores at a private temp SQLite DB until `test` cleans up.
    Returns the private engine."""
    from sqlalchemy import create_engine
    import data.db as db

    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    eng = create_engine(f"sqlite:///{Path(tmp.name) / 'cache.db'}",
                        connect_args={"check_same_thread": False}, future=True)
    test.addCleanup(eng.dispose)          # release the file before cleanup

    patches = [mock.patch.object(db, "_engine", eng),
               mock.patch.object(db, "USE_POSTGRES", False)]
    for name in STORE_MODULES:
        mod = importlib.import_module(name)
        # None, not eng: each store's _get_engine() then runs its own
        # first-use schema init against the private DB.
        patches.append(mock.patch.object(mod, "_engine", None))
        patches.append(mock.patch.object(mod, "_USE_POSTGRES", False))
    for p in patches:
        p.start()
        test.addCleanup(p.stop)
    return eng
