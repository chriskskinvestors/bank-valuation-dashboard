"""
One SQLAlchemy engine for every store.

Previously five modules (cache, events/store, branches_store,
call_report_store, price_cache_store) each carried a verbatim copy of the
DATABASE_URL detection + engine construction — five separate connection
pools to the same database (up to ~25 connections on a small Cloud Run
instance), and one copy (events/store) pointed local SQLite at a different
path than the rest.

Backends:
  • Postgres (cloud) — assembled from the Cloud SQL parts: DB_USER, DB_NAME,
    INSTANCE_CONNECTION_NAME (plain env) + DB_PASSWORD (mounted from Secret
    Manager `db-password`). Connects over the Cloud SQL unix socket.
  • Postgres via ``DATABASE_URL=postgresql+psycopg2://...`` — explicit
    override (local tooling against prod, one-off scripts).
  • SQLite (default) — ./cache.db at the repo root, for local dev.

Why parts, not one URL: the deploy used to set DATABASE_URL — password
embedded — as a PLAIN environment variable on the service and every job,
readable by anyone with Cloud Run viewer access and printed in revision
YAML (pre-assessment security sweep, 2026-09-16). The password now only ever
exists as a secret reference.
"""
import os
from pathlib import Path
from urllib.parse import quote_plus


def database_url(env=None) -> str:
    """The Postgres URL for this process, or "" for SQLite. An explicit
    DATABASE_URL wins; otherwise all four Cloud SQL parts must be present —
    a partial set is treated as absent (never a half-built URL)."""
    env = os.environ if env is None else env
    explicit = (env.get("DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    parts = {k: (env.get(k) or "").strip() for k in
             ("DB_USER", "DB_PASSWORD", "DB_NAME", "INSTANCE_CONNECTION_NAME")}
    if not all(parts.values()):
        return ""
    return (f"postgresql+psycopg2://{quote_plus(parts['DB_USER'])}:"
            f"{quote_plus(parts['DB_PASSWORD'])}@/{parts['DB_NAME']}"
            f"?host=/cloudsql/{parts['INSTANCE_CONNECTION_NAME']}")


_DATABASE_URL = database_url()
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
