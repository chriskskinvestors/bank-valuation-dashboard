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
import time

from config import FUNDAMENTAL_CACHE_TTL_HOURS

TTL_SECONDS = FUNDAMENTAL_CACHE_TTL_HOURS * 3600


# ──────────────────────────────────────────────────────────────────────────
# Backend — the process-wide shared engine lives in data/db.py
# ──────────────────────────────────────────────────────────────────────────
from data.db import USE_POSTGRES as _USE_POSTGRES

_engine = None


def _get_engine():
    """Shared engine (data/db) + this store's first-use schema init."""
    global _engine
    if _engine is not None:
        return _engine

    from sqlalchemy import text
    from data.db import get_engine
    _engine = get_engine()

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


def served_snapshot(key: str, ttl_s: float, build, guard=None):
    """Cross-instance persisted snapshot: serve the stored value when fresh
    (and the guard matches), else build live and persist for every other
    instance. This is THE pattern for read-time aggregates whose per-instance
    @st.cache_data memo dies on each deploy (watchlist metrics, earnings
    calendar, FRED rate bundle) — without it every cold instance pays the
    full rebuild.

    guard: optional invalidation token (e.g. ticker count) stored alongside;
    a mismatch forces a rebuild. JSON round-trip applies: tuples come back
    as lists.
    """
    from data.freshness import is_fresh
    snap = None
    try:
        snap = get(key)
    except Exception:
        snap = None
    if (snap and is_fresh(snap, ttl_s)
            and (guard is None or snap.get("guard") == guard)):
        return snap["value"]
    value = build()
    try:
        from datetime import datetime
        put(key, {"cached_at": datetime.now().isoformat(),
                  "guard": guard, "value": value})
    except Exception as e:
        print(f"[cache] could not persist snapshot {key}: {type(e).__name__}")
    return value


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


def get_multi(keys: list[str]) -> dict[str, dict]:
    """
    Bulk-fetch multiple keys with a single round-trip to the backend.

    Returns {key: value} only for keys that exist AND are not expired.
    Missing/expired keys are absent from the returned dict.

    Use this instead of N calls to get() when you have a known list of
    keys (e.g. loading the watchlist) — turns N network round-trips into 1.
    """
    from sqlalchemy import text
    if not keys:
        return {}
    eng = _get_engine()
    now = time.time()
    with eng.connect() as conn:
        if _USE_POSTGRES:
            rows = conn.execute(
                text("SELECT key, value, timestamp FROM cache WHERE key = ANY(:ks)"),
                {"ks": list(keys)},
            ).fetchall()
        else:
            # SQLite doesn't support ANY/= — use IN with named placeholders
            placeholders = ",".join(f":k{i}" for i in range(len(keys)))
            params = {f"k{i}": k for i, k in enumerate(keys)}
            rows = conn.execute(
                text(f"SELECT key, value, timestamp FROM cache WHERE key IN ({placeholders})"),
                params,
            ).fetchall()

    out: dict[str, dict] = {}
    for k, v, ts in rows:
        if now - float(ts) > TTL_SECONDS:
            continue
        try:
            out[k] = json.loads(v)
        except (TypeError, ValueError):
            continue
    return out


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
