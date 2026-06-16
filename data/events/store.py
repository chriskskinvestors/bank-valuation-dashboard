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
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from data.events.base import Event


# ──────────────────────────────────────────────────────────────────────────
# Cross-source dedup
# ──────────────────────────────────────────────────────────────────────────
# The (source, external_id) UNIQUE constraint only collapses duplicates WITHIN a
# source. But the SAME press release is syndicated across wires + Google News +
# Yahoo, so "Ameris Bancorp Reports Q2 Results" lands once per source — three
# near-identical rows in the feed. We additionally collapse these by a
# normalized-headline content key, scoped to the ticker + a recent window, so a
# release already ingested from one wire isn't re-added from another.
#
# Excluded: sec_8k (generic headlines like "8-K · Earnings / Results" would
# falsely collapse two distinct filings; it already dedups on accession),
# ir_site (raw IR titles, lower volume), and the topic feed (not bank events).
_CONTENT_DEDUP_SOURCES = {
    "businesswire", "prnewswire", "globenewswire", "google_news", "yfinance_news",
}
_CONTENT_DEDUP_WINDOW_DAYS = 5


def _content_key(ticker: str, headline: str) -> str:
    """Normalized (ticker, headline) key for cross-source dedup: lower-cased,
    punctuation→space, whitespace collapsed. The same release worded identically
    by two outlets maps to one key."""
    h = re.sub(r"[^a-z0-9]+", " ", (headline or "").lower()).strip()
    h = re.sub(r"\s+", " ", h)
    return f"{(ticker or '').upper()}|{h}"


# ──────────────────────────────────────────────────────────────────────────
# Topic-feed rows (Home page categorized overnight news — Macro /
# Geopolitical / Domestic / Markets). They share the events table but are
# NOT bank events: source = TOPIC_SOURCE and ticker is the sentinel
# 'TOPIC:<CATEGORY>' (fits the existing VARCHAR(20) column — no schema
# change, existing rows untouched). The category is also stamped into
# raw_json. Read back with get_topic_news(); excluded from the default
# get_universe_recent() so bank-activity panels never see them.
# ──────────────────────────────────────────────────────────────────────────

TOPIC_SOURCE = "google_news_topic"
_TOPIC_TICKER_PREFIX = "TOPIC:"


def topic_ticker(category: str) -> str:
    """Sentinel ticker for a topic category, e.g. 'macro' -> 'TOPIC:MACRO'."""
    return f"{_TOPIC_TICKER_PREFIX}{category.strip().upper()}"


from data.db import USE_POSTGRES as _USE_POSTGRES

_engine = None


def _get_engine():
    """Shared engine (data/db) + this store's first-use schema init.

    Note: the old local copy pointed SQLite one directory ABOVE the repo
    (parent.parent.parent) — local-dev events went to a different cache.db
    than every other store. The shared engine fixes that divergence.
    """
    global _engine
    if _engine is not None:
        return _engine

    from data.db import get_engine
    _engine = get_engine()
    init_schema()
    return _engine


def init_schema():
    """Create the events table if it doesn't exist. Idempotent."""
    from sqlalchemy import text
    from data.db import get_engine

    eng = get_engine()
    if _USE_POSTGRES:
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
        ts_col = "TIMESTAMP WITH TIME ZONE"
    else:
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


def _existing_content_keys(eng, events: list[Event]) -> set[str]:
    """Content keys already in the store for this batch's tickers (dedup-eligible
    sources, within the recent window). Used to collapse the same release
    syndicated across multiple sources."""
    from sqlalchemy import text

    dedup_events = [e for e in events
                    if e.source in _CONTENT_DEDUP_SOURCES and e.ticker]
    if not dedup_events:
        return set()

    tickers = sorted({e.ticker.upper() for e in dedup_events})
    src_list = sorted(_CONTENT_DEDUP_SOURCES)
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CONTENT_DEDUP_WINDOW_DAYS)

    if _USE_POSTGRES:
        sql = text("""
            SELECT ticker, headline FROM events
            WHERE ticker = ANY(:tickers) AND source = ANY(:srcs)
              AND published_at >= :cutoff
        """)
        params = {"tickers": tickers, "srcs": src_list, "cutoff": cutoff}
    else:
        t_ph = ",".join(f":t{i}" for i in range(len(tickers)))
        s_ph = ",".join(f":s{i}" for i in range(len(src_list)))
        sql = text(f"""
            SELECT ticker, headline FROM events
            WHERE ticker IN ({t_ph}) AND source IN ({s_ph})
              AND published_at >= :cutoff
        """)
        params = {"cutoff": cutoff}
        params.update({f"t{i}": t for i, t in enumerate(tickers)})
        params.update({f"s{i}": s for i, s in enumerate(src_list)})

    keys: set[str] = set()
    with eng.connect() as conn:
        for r in conn.execute(sql, params).mappings().all():
            keys.add(_content_key(r["ticker"], r["headline"]))
    return keys


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

    # Cross-source dedup: load content keys already present for the tickers in
    # this batch (eligible sources only, recent window), so a release already
    # ingested from one wire/aggregator isn't duplicated from another. Keys seen
    # earlier in THIS batch are added as we go.
    seen_content_keys = _existing_content_keys(eng, events)

    new: list[Event] = []
    with eng.begin() as conn:
        for e in events:
            if not e.external_id:
                # Without an external_id we can't dedupe, so don't insert.
                # Adapters should always provide one.
                continue
            if e.source in _CONTENT_DEDUP_SOURCES:
                ck = _content_key(e.ticker, e.headline)
                if ck in seen_content_keys:
                    continue  # same release already present from another source
                seen_content_keys.add(ck)
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
        # Topic-feed rows aren't bank events — keep them out of the default
        # "recent across the universe" view (per-bank activity panels).
        sql = """
            SELECT ticker, source, event_type, headline, summary, url,
                   published_at, external_id
            FROM events
            WHERE source <> :topic_src
            ORDER BY published_at DESC
            LIMIT :n
        """
        params = {"n": limit, "topic_src": TOPIC_SOURCE}

    with _get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_topic_news(category: str, hours: int = 24, limit: int = 50) -> list[dict]:
    """
    Recent topic-feed headlines for one Home-page category ('macro',
    'geopolitical', 'domestic', 'markets'), newest first, within the last
    ``hours``. Each dict carries the store's usual event fields plus
    ``category``. Unknown categories simply return [].
    """
    from sqlalchemy import text
    eng = _get_engine()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with eng.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ticker, source, event_type, headline, summary, url,
                       published_at, external_id, raw_json
                FROM events
                WHERE source = :src AND ticker = :t AND published_at >= :cutoff
                ORDER BY published_at DESC
                LIMIT :n
            """),
            {"src": TOPIC_SOURCE, "t": topic_ticker(category),
             "cutoff": cutoff, "n": limit},
        ).mappings().all()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["category"] = category.strip().lower()
        # Surface the publisher (adapter stored it in raw.via) so the UI can
        # show "Reuters", not the adapter name, and curation can whitelist.
        raw = d.pop("raw_json", None)
        if raw:
            try:
                d["source_name"] = (json.loads(raw).get("via") or "").strip()
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


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
