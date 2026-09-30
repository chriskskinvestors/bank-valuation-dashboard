"""Package init: install the shared streamlit stub BEFORE any test module
imports pipeline code, and route every data.db-backed store at a private
per-process SQLite file.

unittest discovery imports every test module before running any test, so a
single module that reaches `import streamlit` through the pipeline (e.g. via
data/sec_client) binds the REAL package for the whole process — and real
st.cache_data memoizes functions like get_latest_fundamentals by cik ACROSS
test modules, so later modules patching fetch_company_facts get another
module's cached results (2026-08-19: 24 tests failed only in composition;
test_share_equity_coherence's FSUN fixture was served another bank's share
count). A 2026-08-19 scan found 18 offender modules, so per-module
install-before-import discipline (PR #45's approach) cannot be the only
guard. Installing here is idempotent and additive (see _streamlit_stub) and
runs before any sibling module import, killing the class by construction.

Store isolation (2026-09-30): a discovery run wrote the developer's REAL
./cache.db — `announcement_call_snap` overwritten with {}, fake-domain IR
probes, sec_facts / filing_overlay rows. Every store takes its engine lazily
from data.db.get_engine(), which returns data.db._engine once set, so setting
it here — before any store module is imported — sends every write to a temp
FILE (not sqlite://: in-memory is per connection, invisible to other threads).
USE_POSTGRES is forced off before the stores bind it, so a shell with
DATABASE_URL set can't aim a test run at Postgres. Suites that swap in their
own engine save/restore around this one. Pinned by
tests/test_apptest_store_isolation.TestDiscoveryStoreIsolation.

Script-style suites (python tests/test_render_smoke.py) do not import this
package and keep their own rich fakes (AppTest suites: tests/_apptest_store).
"""
import atexit
import shutil
import tempfile
from pathlib import Path

from tests._streamlit_stub import install as _install

_install()


def _isolate_store():
    from sqlalchemy import create_engine
    import data.db as db

    tmp = tempfile.mkdtemp(prefix="bvd-tests-store-")
    eng = create_engine(f"sqlite:///{Path(tmp) / 'cache.db'}",
                        connect_args={"check_same_thread": False}, future=True)
    db._engine = eng
    db.USE_POSTGRES = False

    def _cleanup():
        eng.dispose()                     # release the file (Windows locks)
        shutil.rmtree(tmp, ignore_errors=True)
    atexit.register(_cleanup)
    return eng


_STORE_ENGINE = _isolate_store()
