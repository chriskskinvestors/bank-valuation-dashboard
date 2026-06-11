"""
One SQLAlchemy engine for every store.

Previously five modules (cache, events/store, branches_store,
call_report_store, price_cache_store) each carried a verbatim copy of the
DATABASE_URL detection + engine construction — five separate connection
pools to the same database (up to ~25 connections on a small Cloud Run
instance), and one copy (events/store) pointed local SQLite at a different
path than the rest.

Backends, selected by env var ``DATABASE_URL``:
  • Postgres (cloud)  — ``DATABASE_URL=postgresql+psycopg2://...``
  • SQLite (default)  — ./cache.db at the repo root, for local dev.
"""
import os
from pathlib import Path

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = _DATABASE_URL.startswith(
    ("postgres://", "postgresql://", "postgresql+psycopg2://")
)

_engine = None


def get_engine():
    """The process-wide shared engine (lazily created)."""
    global _engine
    if _engine is not None:
        return _engine
    from sqlalchemy import create_engine
    if USE_POSTGRES:
        url = _DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        _engine = create_engine(
            url,
            pool_size=2,            # Cloud Run instances are small
            max_overflow=3,
            pool_pre_ping=True,     # Drop stale connections silently
            pool_recycle=300,       # Recycle every 5 min
            future=True,
        )
    else:
        db_path = Path(__file__).parent.parent / "cache.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
    return _engine
