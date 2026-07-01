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
    "businesswire", "prnewswire", "globenewswire", "google_news",
    "yfinance_news", "fmp_news",
}
_CONTENT_DEDUP_WINDOW_DAYS = 5

# When the SAME release is syndicated across an AGGREGATOR (fmp_news /
# google_news / yfinance_news) and a first-party WIRE (businesswire /
# prnewswire / globenewswire), we keep ONE row — and it should be the wire copy:
# cleaner headline, first-party URL, and (critically) the Home feed surfaces the
# wires but hides the aggregators. Polls are unordered, so without an explicit
# preference an aggregator that happens to poll first would "capture" the
# release under a source Home never shows (the bug that hid the JPMorgan
# co-presidents release: stored under fmp_news, deduped away the businesswire
# copy, invisible on Home). Lower rank = preferred. When a higher-priority copy
# arrives for a key already stored under a lower-priority source, we UPGRADE the
# stored row in place rather than inserting a duplicate.
_SOURCE_RANK = {
    "businesswire": 0, "prnewswire": 0, "globenewswire": 0,
    "fmp_news": 1, "google_news": 1, "yfinance_news": 1,
}


def _source_rank(source: str | None) -> int:
    """Dedup preference for a source; lower = preferred. Unknown sources rank as
    aggregators (1), never beating a first-party wire."""
    return _SOURCE_RANK.get(source or "", 1)


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


def _existing_content_rows(eng, events: list[Event]) -> dict[str, tuple[str, str]]:
    """Content keys already in the store for this batch's tickers (dedup-eligible
    sources, within the recent window) -> the BEST (highest-priority) stored row
    for each, as ``(source, external_id)``. Used to collapse the same release
    syndicated across sources, and to decide whether an incoming higher-priority
    wire copy should upgrade a stored aggregator copy in place (see _SOURCE_RANK)."""
    from sqlalchemy import text

    dedup_events = [e for e in events
                    if e.source in _CONTENT_DEDUP_SOURCES and e.ticker]
    if not dedup_events:
        return {}

    tickers = sorted({e.ticker.upper() for e in dedup_events})
    src_list = sorted(_CONTENT_DEDUP_SOURCES)
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CONTENT_DEDUP_WINDOW_DAYS)

    if _USE_POSTGRES:
        sql = text("""
            SELECT ticker, headline, source, external_id FROM events
            WHERE ticker = ANY(:tickers) AND source = ANY(:srcs)
              AND published_at >= :cutoff
        """)
        params = {"tickers": tickers, "srcs": src_list, "cutoff": cutoff}
    else:
        t_ph = ",".join(f":t{i}" for i in range(len(tickers)))
        s_ph = ",".join(f":s{i}" for i in range(len(src_list)))
        sql = text(f"""
            SELECT ticker, headline, source, external_id FROM events
            WHERE ticker IN ({t_ph}) AND source IN ({s_ph})
              AND published_at >= :cutoff
        """)
        params = {"cutoff": cutoff}
        params.update({f"t{i}": t for i, t in enumerate(tickers)})
        params.update({f"s{i}": s for i, s in enumerate(src_list)})

    best: dict[str, tuple[str, str]] = {}
    with eng.connect() as conn:
        for r in conn.execute(sql, params).mappings().all():
            ck = _content_key(r["ticker"], r["headline"])
            prev = best.get(ck)
            if prev is None or _source_rank(r["source"]) < _source_rank(prev[0]):
                best[ck] = (r["source"], r["external_id"])
    return best


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

    # Cross-source dedup: load the best stored copy per content key for the
    # tickers in this batch (eligible sources only, recent window), so a release
    # already ingested from one wire/aggregator isn't duplicated from another.
    # When a higher-priority wire copy arrives for a key currently stored under a
    # lower-priority aggregator, we UPGRADE that row in place instead of skipping
    # (so the single retained copy is first-party and Home-visible). Rows seen /
    # written earlier in THIS batch are tracked as we go.
    existing_rows = _existing_content_rows(eng, events)
    best_rank: dict[str, int] = {
        ck: _source_rank(src) for ck, (src, _eid) in existing_rows.items()
    }
    upgrade_stmt = text("""
        UPDATE events SET
          source = :source, event_type = :event_type, headline = :headline,
          summary = :summary, url = :url, external_id = :external_id,
          published_at = :published_at, raw_json = :raw_json
        WHERE source = :old_source AND external_id = :old_external_id
    """)

    new: list[Event] = []
    with eng.begin() as conn:
        for e in events:
            if not e.external_id:
                # Without an external_id we can't dedupe, so don't insert.
                # Adapters should always provide one.
                continue
            ck = None
            if e.source in _CONTENT_DEDUP_SOURCES:
                ck = _content_key(e.ticker, e.headline)
                incoming_rank = _source_rank(e.source)
                cur_rank = best_rank.get(ck)
                if cur_rank is not None:
                    if incoming_rank >= cur_rank:
                        continue  # an equal-or-better copy is already present
                    # Strictly higher priority (a wire beating a stored aggregator
                    # copy): swap the stored row's content for the wire's so the
                    # one retained copy is first-party — and surfaced on Home.
                    old = existing_rows.get(ck)
                    if old is not None:
                        conn.execute(upgrade_stmt, {
                            "source": e.source,
                            "event_type": e.event_type,
                            "headline": e.headline[:5000] if e.headline else "",
                            "summary": e.summary[:20000] if e.summary else None,
                            "url": e.url or None,
                            "external_id": e.external_id[:255],
                            "published_at": e.published_at,
                            "raw_json": json.dumps(e.raw, default=str) if e.raw else None,
                            "old_source": old[0],
                            "old_external_id": old[1],
                        })
                        existing_rows[ck] = (e.source, e.external_id[:255])
                        best_rank[ck] = incoming_rank
                        new.append(e)
                    continue
                best_rank[ck] = incoming_rank
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
                if ck is not None:
                    # Track this just-written row so a higher-priority copy later
                    # in the SAME batch can upgrade it in place.
                    existing_rows[ck] = (e.source, e.external_id[:255])
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


# ── Event-level M&A dedup (display) ──────────────────────────────────────
# The same deal surfaces per ticker as its OWN 8-K summary AND a wire headline
# (different wording), and the wire is tagged to BOTH parties — so PB and STEL
# each show the merger twice. Text-match dedup can't collapse differently-worded
# rows; this collapses a ticker's M&A rows that name the SAME companies.
_MA_RE = re.compile(
    r"\b(?:merger|acquisition|acquir\w+|merged\s+(?:in|into|with)|"
    r"completes?\s+(?:its\s+)?(?:merger|acquisition|combination)|"
    r"definitive\s+(?:merger|agreement)|business\s+combination|to\s+acquire)\b",
    re.IGNORECASE)
_MA_DEDUP_DAYS = 3
# Stopwords so the tokens that remain are DISTINCTIVE brand cores (prosperity,
# stellar) — bank-name suffixes, the generic adjectives shared by many banks
# (first/community/citizens/…), M&A verbs, generic words and months are dropped.
_MA_STOPWORDS = frozenset({
    "bank", "banks", "bancorp", "bancshares", "banc", "financial", "holdings",
    "holding", "group", "corporation", "corp", "company", "incorporated",
    "trust", "savings", "national", "first", "community", "citizens", "united",
    "american", "merger", "mergers", "merged", "merges", "acquisition",
    "acquisitions", "acquire", "acquires", "acquiring", "acquired", "completes",
    "completed", "complete", "completion", "definitive", "agreement",
    "combination", "transaction", "announces", "announced", "announcement",
    "effective", "approximately", "billion", "million", "assets",
    "stockholders", "shareholders", "common", "stock", "shares", "under",
    "with", "into", "from", "that", "which", "been", "have", "will", "today",
    "expanding", "expand", "january", "february", "march", "april", "june",
    "july", "august", "september", "october", "november", "december",
})


def _significant_tokens(text: str) -> set:
    """Distinctive brand tokens for M&A dedup — 4+ char words minus bank-name
    suffixes, generic bank adjectives, M&A verbs and months, so what's left is
    the company brand cores (e.g. {'prosperity', 'stellar'})."""
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in _MA_STOPWORDS}


def _parse_ts(p):
    if p is None:
        return None
    try:
        return p if hasattr(p, "year") else datetime.fromisoformat(
            str(p).replace("Z", "+00:00"))
    except Exception:
        return None


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
    out = [dict(r) for r in rows]

    # Canonicalize + scope the display ticker. Two read-time corrections, both
    # fail-open (a universe-build hiccup never breaks the feed):
    #   1. Remap a non-common sibling (preferred/ETN sharing its registrant's
    #      CIK) onto the common — a legacy JPMorgan 8-K frozen as ">VYLD" shows
    #      ">JPM". The poller no longer creates these, but a row is frozen under
    #      (source, accession) so a re-poll can't re-tag it.
    #   2. Drop rows for out-of-scope tickers — skip-listed broker-dealers / card
    #      issuers / foreign ADRs / non-banks, plus any non-common that couldn't
    #      be resolved to a common. Their events were ingested before the
    #      exclusion (or are frozen), so filter them here too. The FULL _SKIP_
    #      TICKERS set is used (not coverage_excluded's universe∩skip) so a skip
    #      ticker still filters AFTER the nightly rebuild drops it from the
    #      universe — else its stored events (e.g. SF/RJF/JXN) leak back in.
    try:
        from data.bank_universe import (universe_is_cached,
                                         get_noncommon_primary_map,
                                         coverage_excluded, _SKIP_TICKERS)
        # Skip-listed tickers are a STATIC set — always drop them, even before
        # the universe is built. Only the noncommon remap/exclusion needs the
        # built universe, so it's gated (never a cold build on this read path).
        remap = get_noncommon_primary_map() if universe_is_cached() else {}
        excluded = set(_SKIP_TICKERS)
        if universe_is_cached():
            excluded |= coverage_excluded()
        if remap or excluded:
            kept = []
            for r in out:
                tk = remap.get((r.get("ticker") or "").upper(),
                               (r.get("ticker") or "").upper())
                if tk in excluded:
                    continue
                r["ticker"] = tk
                kept.append(r)
            out = kept
    except Exception:
        pass

    # Collapse cross-source duplicates of the SAME release at display time — e.g.
    # a bank's IR-site copy AND its Business Wire / FMP copy of one announcement.
    # ir_site (and sec_8k) are exempt from the ingest-time content dedup, so once
    # the IR adapter runs reliably both copies are stored; collapse them here by
    # (ticker, normalized headline), keeping the first (newest, since ordered
    # DESC). sec_8k is EXEMPT — its headlines are generic ("8-K · Other Material
    # Event") and would falsely collapse two distinct filings.
    seen_ck: set[str] = set()
    ma_kept: dict[str, list] = {}   # ticker -> [(brand_tokens, ts)] of kept M&A rows
    deduped: list[dict] = []
    for r in out:
        tk = (r.get("ticker") or "").upper()
        head = r.get("headline") or ""
        summ = r.get("summary") or ""
        # (1) exact cross-source duplicate (sec_8k exempt — generic headlines).
        if (r.get("source") or "") != "sec_8k":
            ck = _content_key(tk, head)
            if ck in seen_ck:
                continue
            seen_ck.add(ck)
        # (2) event-level M&A dedup: one deal shows up per ticker as its 8-K
        # summary AND a wire headline (different wording), and the wire is tagged
        # to BOTH parties. Collapse a ticker's M&A rows that name the SAME
        # companies — ≥2 shared brand tokens (so BOTH acquirer and target match;
        # two different deals sharing only the acquirer are NOT collapsed) within
        # a few days — keeping the newest. Non-M&A rows are never touched.
        if r.get("event_type") == "m_and_a" or _MA_RE.search(head) or _MA_RE.search(summ):
            toks = _significant_tokens(summ or head)
            ts = _parse_ts(r.get("published_at"))
            if any(len(toks & pt) >= 2
                   and not (ts and pts and abs((ts - pts).days) > _MA_DEDUP_DAYS)
                   for (pt, pts) in ma_kept.get(tk, [])):
                continue
            ma_kept.setdefault(tk, []).append((toks, ts))
        deduped.append(r)
    return deduped


def get_events_by_type(event_type: str, limit: int = 600) -> list[dict]:
    """Recent events of one event_type across the universe, newest-first.

    Used by the calendar's conference-call parsing: a bank's call-details PR is
    often issued weeks before the report date, so a recency window over ALL
    events would push it off the edge. Querying the (sparse) earnings-typed rows
    directly reaches back far enough. Topic-feed rows are excluded."""
    from sqlalchemy import text
    sql = """
        SELECT ticker, source, event_type, headline, summary, url,
               published_at, external_id
        FROM events
        WHERE event_type = :et AND source <> :topic_src
        ORDER BY published_at DESC
        LIMIT :n
    """
    params = {"et": event_type, "topic_src": TOPIC_SOURCE, "n": limit}
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
