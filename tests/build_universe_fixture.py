"""Regenerate tests/universe_snapshot_fixture.json — the frozen universe that
tests/__init__ seeds into the isolated store (LIVE: ~7 min, SEC + FDIC).

Two parts, both computed exactly as production computes them:
  • "universe": data.bank_universe._build_universe_live() — the snapshot the
    nightly refresh-universe job persists as bank_universe_lastgood;
  • "fdic_active": {cert: ACTIVE} for every ticker get_universe_tickers()
    checks via fdic_client.cert_is_active (those with no CIK) — the
    fdic_active:<cert> rows prod's store holds warm.

Run: python tests/build_universe_fixture.py   (NOT under the unittest runner:
the tests/__init__ network guard would block it).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = Path(__file__).resolve().parent / "universe_snapshot_fixture.json"


def build(universe: dict | None = None) -> dict:
    # The stub by file path: importing it as tests._streamlit_stub would run
    # tests/__init__, which seeds from the very fixture this regenerates.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_streamlit_stub", Path(__file__).resolve().parent / "_streamlit_stub.py")
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    stub.install()
    from datetime import datetime
    from sqlalchemy import create_engine
    import data.db as db
    db._engine = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'c.db'}",
                               connect_args={"check_same_thread": False}, future=True)
    db.USE_POSTGRES = False

    from data import cache
    from data.bank_universe import _build_universe_live, coverage_excluded
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.fdic_client import cert_is_active
    if universe is None:
        universe = _build_universe_live()
    cache.put("bank_universe_lastgood",
              {"cached_at": datetime.now().isoformat(), "universe": universe})
    excluded = coverage_excluded()
    active = {}
    for t in sorted(universe):
        if t in excluded or get_cik(t) is not None:
            continue
        cert = get_fdic_cert(t)
        if cert is not None:
            active[str(cert)] = cert_is_active(cert)
    return {"universe": universe, "fdic_active": active}


if __name__ == "__main__":
    fixture = build()
    OUT.write_text(json.dumps(fixture, indent=0, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT}: {len(fixture['universe'])} banks, "
          f"{len(fixture['fdic_active'])} FDIC-only certs")
