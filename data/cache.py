"""
SQLite-based caching layer for FDIC and SEC data.

Avoids repeatedly hitting external APIs for data that only changes quarterly.
"""

import json
import sqlite3
import time
from pathlib import Path

from config import FUNDAMENTAL_CACHE_TTL_HOURS

DB_PATH = Path(__file__).parent.parent / "cache.db"
TTL_SECONDS = FUNDAMENTAL_CACHE_TTL_HOURS * 3600


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
    """)
    return conn


def get(key: str) -> dict | None:
    """Get cached value if it exists and is not expired."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT value, timestamp FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, ts = row
        if time.time() - ts > TTL_SECONDS:
            return None  # Expired
        return json.loads(value)
    finally:
        conn.close()


def put(key: str, value: dict):
    """Store a value in the cache."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, timestamp) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate(key: str):
    """Remove a specific cache entry."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def clear_all():
    """Clear the entire cache."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM cache")
        conn.commit()
    finally:
        conn.close()


def get_age(key: str) -> float | None:
    """Return age of cached entry in seconds, or None if not cached."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT timestamp FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return time.time() - row[0]
    finally:
        conn.close()


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
