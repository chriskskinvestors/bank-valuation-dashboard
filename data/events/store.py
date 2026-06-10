"""
Postgres/SQLite-backed event store.

Shares the same SQLAlchemy engine as data/cache.py (via DATABASE_URL env
var). The events table is the unified ingestion target — every adapter
writes here and the UI reads here.

Schema:
  events(
    id            SERIAL PK
    ticker        VARCHAR(20)   NOT NULL
    source        VARCHAR(40)   NOT NULL
    event_type    VARCHAR(40)   NOT NULL
    headline      TEXT          NOT NULL
    summary       TEXT
    url           TEXT
    external_id   VARCHAR(255)
    published_at  TIMESTAMP     NOT NULL
    ingested_at   TIMESTAMP     DEFAULT NOW()
    raw_json      TEXT          -- JSON-serialized adapter payload
    UNIQUE (source, external_id)
  )
"""

from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Iterable

from data.events.base import Event


_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_USE_POSTGRES = _DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://"))

_engine = None


def _get_engine():
    """Reuse the engine pattern from data/cache.py."""
    global _engine
    if _engine is not None:
        return _engine

    from sqlalchemy import create_engine
    from pathlib import Path

    if _USE_POSTGRES:
        url = _DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        _engine = create_engine(
            url,
            pool_size=2, max_overflow=3,
            pool_pre_ping=True, pool_recycle=300,
            future=True,
        )
    else:
        db_path = Path(__file__).parent.parent.parent / "cache.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
    init_schema()
    return _engine


def init_schema():
    """Create the events table if it doesn't exist. Idempotent."""
    from sqlalchemy import create_engine, text
    from pathlib import Path

    # Bootstrap our own engine to avoid recursion
    if _USE_POSTGRES:
        url = _DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        eng = create_engine(url, future=True)
        autoinc = "BIGSERIAL"
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
        ts_col = "TIMESTAMP WITH TIME ZONE"
    else:
        db_path = Path(__file__).parent.parent.parent / "cache.db"
        eng = create_engine(f"sqlite:///{db_path}", future=True)
        autoinc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        ts_default = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ts_col = "TIMESTAMP"

    pk = "id BIGSERIAL PRIMARY KEY" if _USE_POSTGRES else "id INTEGER PRIMARY KEY AUTOINCREMENT"

    with eng.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS events (
                {pk},
                ticker        VARCHAR(20) NOT NULL,
                source        VARCHAR(40) NOT NULL,
                event_type    VARCHAR(40) NOT NULL,
                headline      TEXT NOT NULL,
                summary       TEXT,
                url           TEXT,
                external_id   VARCHAR(255),
                published_at  {ts_col} NOT NULL,
                ingested_at   {ts_default},
                raw_json      TEXT,
                UNIQUE (source, external_id)
            )
        """))
        # Indexes for the two common access patterns
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_events_ticker_published "
            "ON events(ticker, published_at DESC)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_events_published "
            "ON events(published_at DESC)"
        ))


def insert_events_returning_new(events: Iterable[Event]) -> list[Event]:
    """
    Insert events idempotently. Returns the list of events that were ACTUALLY
    written (duplicates by (source, external_id) are skipped). Callers that
    only need the count can use insert_events().
    """
    from sqlalchemy import text

    events = [e for e in events if e]
    if not events:
        return []

    eng = _get_engine()
    new: list[Event] = []
    with eng.begin() as conn:
        for e in events:
            if not e.external_id:
                # Without an external_id we can't dedupe, so don't insert.
                # Adapters should always provide one.
                continue
            params = {
                "ticker": e.ticker.upper(),
                "source": e.source,
                "event_type": e.event_type,
                "headline": e.headline[:5000] if e.headline else "",
                "summary": e.summary[:20000] if e.summary else None,
                "url": e.url or None,
                "external_id": e.external_id[:255],
                "published_at": e.published_at,
                "raw_json": json.dumps(e.raw, default=str) if e.raw else None,
            }
            if _USE_POSTGRES:
                stmt = text("""
                    INSERT INTO events
                      (ticker, source, event_type, headline, summary, url,
                       external_id, published_at, raw_json)
                    VALUES
                      (:ticker, :source, :event_type, :headline, :summary, :url,
                       :external_id, :published_at, :raw_json)
                    ON CONFLICT (source, external_id) DO NOTHING
                """)
            else:
                stmt = text("""
                    INSERT OR IGNORE INTO events
                      (ticker, source, event_type, headline, summary, url,
                       external_id, published_at, raw_json)
                    VALUES
                      (:ticker, :source, :event_type, :headline, :summary, :url,
                       :external_id, :published_at, :raw_json)
                """)
            result = conn.execute(stmt, params)
            if (result.rowcount or 0) > 0:
                new.append(e)
    return new


def insert_events(events: Iterable[Event]) -> int:
    """Insert events idempotently. Returns count of NEW rows written."""
    return len(insert_events_returning_new(events))


def get_recent_events(ticker: str, limit: int = 20) -> list[dict]:
    """Most recent events for a single ticker, newest first."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ticker, source, event_type, headline, summary, url,
                       published_at, external_id
                FROM events
                WHERE ticker = :t
                ORDER BY published_at DESC
                LIMIT :n
            """),
            {"t": ticker.upper(), "n": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_universe_recent(limit: int = 50, sources: list[str] | None = None) -> list[dict]:
    """Most recent events across all tickers."""
    from sqlalchemy import text
    eng = _get_engine()
    if sources:
        sql = """
            SELECT ticker, source, event_type, headline, summary, url,
                   published_at, external_id
            FROM events
            WHERE source = ANY(:srcs)
            ORDER BY published_at DESC
            LIMIT :n
        """ if _USE_POSTGRES else """
            SELECT ticker, source, event_type, headline, summary, url,
                   published_at, external_id
            FROM events
            WHERE source IN ({placeholders})
            ORDER BY published_at DESC
            LIMIT :n
        """.format(placeholders=",".join(f":s{i}" for i in range(len(sources))))
        params = {"n": limit}
        if _USE_POSTGRES:
            params["srcs"] = sources
        else:
            for i, s in enumerate(sources):
                params[f"s{i}"] = s
    else:
        sql = """
            SELECT ticker, source, event_type, headline, summary, url,
                   published_at, external_id
            FROM events
            ORDER BY published_at DESC
            LIMIT :n
        """
        params = {"n": limit}

    with _get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def last_seen_published(source: str, ticker: str | None = None) -> datetime | None:
    """
    Return the most recent published_at for a source (optionally narrowed
    by ticker). Used by adapters to skip events they've already ingested.
    """
    from sqlalchemy import text
    eng = _get_engine()
    if ticker:
        sql = """
            SELECT MAX(published_at) AS last_seen
            FROM events WHERE source = :s AND ticker = :t
        """
        params = {"s": source, "t": ticker.upper()}
    else:
        sql = "SELECT MAX(published_at) AS last_seen FROM events WHERE source = :s"
        params = {"s": source}
    with eng.connect() as conn:
        row = conn.execute(text(sql), params).fetchone()
    return row[0] if row and row[0] else None
