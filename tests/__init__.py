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

Universe seed (2026-09-30): the isolated store starts empty, so the first test
to touch get_universe() ran the LIVE ~6.5-min SEC x FDIC build (~250
fdic_active probes) and every name-index test (feed mis-tags) silently rode on
it. The store is seeded with a frozen snapshot (universe_snapshot_fixture.json;
regenerate with tests/build_universe_fixture.py) under the exact keys prod
serves from — the snapshot plus the fdic_active:<cert> rows
get_universe_tickers reads — so discovery never builds and matching is
deterministic.

Network guard (2026-09-30): under the unittest runner, resolving any
non-localhost host raises LiveNetworkBlocked. It subclasses BaseException so a
pipeline `except Exception` fallback can't swallow it into a quiet pass — a new
unstubbed seam errors its test by name. Only the unittest runner arms it:
`python -m tests.golden_dataset` imports this package too and must go live.
Pinned by tests/test_network_guard.
"""
import atexit
import json
import shutil
import sys
import tempfile
from datetime import datetime
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

UNIVERSE_FIXTURE = Path(__file__).parent / "universe_snapshot_fixture.json"


def _seed_universe():
    import time
    from data import cache
    fixture = json.loads(UNIVERSE_FIXTURE.read_text(encoding="utf-8"))
    cache.put("bank_universe_lastgood", {"cached_at": datetime.now().isoformat(),
                                         "universe": fixture["universe"]})
    # get_universe_tickers' ACTIVE check for the CIK-less (FDIC-only) tickers.
    for cert, active in fixture["fdic_active"].items():
        cache.put(f"fdic_active:{cert}", {"_ts": time.time(), "_v": active})


_seed_universe()


class LiveNetworkBlocked(BaseException):
    """A test resolved a real host — stub the seam (see tests/__init__)."""


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _deny_network():
    import socket
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is None or name in _LOCAL_HOSTS or str(name).endswith(".localhost"):
            return real(host, *args, **kwargs)
        raise LiveNetworkBlocked(
            f"live network call to {name!r} during tests — stub the seam "
            "(guard: tests/__init__._deny_network)")

    socket.getaddrinfo = guarded


def _under_unittest() -> bool:
    main = sys.modules.get("__main__")
    return getattr(getattr(main, "__spec__", None), "name", None) == "unittest.__main__"


if _under_unittest():
    _deny_network()
