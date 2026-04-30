"""
Caching layer for FDIC and SEC data.

Two backends, selected by env var ``DATABASE_URL``:

  • Postgres (cloud)  — set ``DATABASE_URL=postgresql+psycopg2://...``
                        Used in Cloud Run; survives instance restarts.

  • SQLite (default)  — no env var needed. Falls back to ./cache.db
                        Used for local dev.

The public API (get/put/invalidate/clear_all/get_age and the typed
fdic/sec wrappers) is identical for both backends, so callers don't
need to know which one is active.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from config import FUNDAMENTAL_CACHE_TTL_HOURS

TTL_SECONDS = FUNDAMENTAL_CACHE_TTL_HOURS * 3600


# ──────────────────────────────────────────────────────────────────────────
# Backend selection
# ──────────────────────────────────────────────────────────────────────────
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_USE_POSTGRES = _DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://"))


# ──────────────────────────────────────────────────────────────────────────
# SQLAlchemy engine (shared across calls, lazily initialized)
# ──────────────────────────────────────────────────────────────────────────
_engine = None


def _get_engine():
    """Lazily build an engine; pool size kept small for Cloud Run."""
    global _engine
    if _engine is not None:
        return _engine

    from sqlalchemy import create_engine, text

    if _USE_POSTGRES:
        # Normalize Heroku-style "postgres://" to SQLAlchemy's expected form
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

    # Create the table on first use
    with _engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cache (
                key       VARCHAR(255) PRIMARY KEY,
                value     TEXT NOT NULL,
                timestamp DOUBLE PRECISION NOT NULL
            )
        """))
    return _engine


# ──────────────────────────────────────────────────────────────────────────
# Public API — identical signature whether SQLite or Postgres
# ──────────────────────────────────────────────────────────────────────────

def get(key: str) -> dict | None:
    """Get cached value if it exists and is not expired."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT value, timestamp FROM cache WHERE key = :k"),
            {"k": key},
        ).fetchone()
    if row is None:
        return None
    value, ts = row
    if time.time() - float(ts) > TTL_SECONDS:
        return None  # Expired
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def put(key: str, value: dict):
    """Store a value in the cache (idempotent upsert)."""
    from sqlalchemy import text
    eng = _get_engine()
    payload = json.dumps(value, default=str)
    now = time.time()
    with eng.begin() as conn:
        if _USE_POSTGRES:
            conn.execute(
                text("""
                    INSERT INTO cache (key, value, timestamp)
                    VALUES (:k, :v, :t)
                    ON CONFLICT (key) DO UPDATE
                      SET value = EXCLUDED.value,
                          timestamp = EXCLUDED.timestamp
                """),
                {"k": key, "v": payload, "t": now},
            )
        else:
            # SQLite: use INSERT OR REPLACE for the same effect
            conn.execute(
                text("INSERT OR REPLACE INTO cache (key, value, timestamp) VALUES (:k, :v, :t)"),
                {"k": key, "v": payload, "t": now},
            )


def invalidate(key: str):
    """Remove a specific cache entry."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM cache WHERE key = :k"), {"k": key})


def clear_all():
    """Clear the entire cache."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM cache"))


def get_age(key: str) -> float | None:
    """Return age of cached entry in seconds, or None if not cached."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT timestamp FROM cache WHERE key = :k"),
            {"k": key},
        ).fetchone()
    if row is None:
        return None
    return time.time() - float(row[0])


# ── Convenience wrappers for typed cache access ─────────────────────────

def get_fdic(ticker: str) -> dict | None:
    return get(f"fdic:{ticker}")

def put_fdic(ticker: str, data: dict):
    put(f"fdic:{ticker}", data)

def get_sec(ticker: str) -> dict | None:
    return get(f"sec:{ticker}")

def put_sec(ticker: str, data: dict):
    put(f"sec:{ticker}", data)

def fdic_age(ticker: str) -> float | None:
    return get_age(f"fdic:{ticker}")

def sec_age(ticker: str) -> float | None:
    return get_age(f"sec:{ticker}")


# ──────────────────────────────────────────────────────────────────────────
# Diagnostic
# ──────────────────────────────────────────────────────────────────────────

def backend_info() -> dict:
    """Return backend type for diagnostics / Data Quality tab."""
    return {
        "backend": "postgres" if _USE_POSTGRES else "sqlite",
        "ttl_hours": FUNDAMENTAL_CACHE_TTL_HOURS,
    }
