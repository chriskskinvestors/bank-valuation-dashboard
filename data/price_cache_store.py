"""
Warm price-cache storage layer.

FMP's plan is rate-capped (~300 req/min, one symbol per call, no batch
endpoint), so fetching all ~355 universe prices live on every screen load
takes ~70s cold. Instead a small Cloud Run job (jobs/refresh_prices.py)
refreshes every bank's price into this table every couple of minutes during
market hours, and the dashboard reads warm prices instantly.

One row per ticker (latest quote only — we don't keep history here; that's
what FMP get_history is for).

Table:
  price_cache(
    ticker        VARCHAR PRIMARY KEY
    price         DOUBLE PRECISION
    prev_close    DOUBLE PRECISION
    change        DOUBLE PRECISION
    change_pct    DOUBLE PRECISION
    volume        DOUBLE PRECISION
    updated_at    TIMESTAMP
  )

Public functions:
  • init_price_cache_schema()        — idempotent CREATE TABLE
  • upsert_prices(quotes)            — bulk write {ticker: quote_dict}
  • get_prices(tickers, max_age_s)   — bulk read, fresh rows only
  • get_all_prices(max_age_s)        — bulk read everything fresh
"""
from __future__ import annotations
import os
from datetime import datetime, timezone

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_USE_POSTGRES = _DATABASE_URL.startswith(
    ("postgres://", "postgresql://", "postgresql+psycopg2://")
)

_engine = None

# Quote dict keys we persist (matches fmp_client / IBKR quote shape).
_FIELDS = ("price", "prev_close", "change", "change_pct", "volume")


def _get_engine():
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
        db_path = Path(__file__).parent.parent / "cache.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
    init_price_cache_schema()
    return _engine


def init_price_cache_schema():
    """Create the price_cache table. Idempotent."""
    from sqlalchemy import create_engine, text
    from pathlib import Path

    if _USE_POSTGRES:
        url = _DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        eng = create_engine(url, future=True)
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
    else:
        db_path = Path(__file__).parent.parent / "cache.db"
        eng = create_engine(f"sqlite:///{db_path}", future=True)
        ts_default = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

    with eng.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS price_cache (
                ticker         VARCHAR(20) PRIMARY KEY,
                price          DOUBLE PRECISION,
                prev_close     DOUBLE PRECISION,
                change         DOUBLE PRECISION,
                change_pct     DOUBLE PRECISION,
                volume         DOUBLE PRECISION,
                dividend_yield DOUBLE PRECISION,
                updated_at     {ts_default}
            )
        """))
        # Add dividend_yield to pre-existing tables (created before this column
        # existed). Postgres supports IF NOT EXISTS on ALTER; SQLite doesn't, so
        # we check PRAGMA first.
        try:
            if _USE_POSTGRES:
                conn.execute(text(
                    "ALTER TABLE price_cache ADD COLUMN IF NOT EXISTS "
                    "dividend_yield DOUBLE PRECISION"))
            else:
                cols = [row[1] for row in conn.execute(
                    text("PRAGMA table_info(price_cache)")).fetchall()]
                if "dividend_yield" not in cols:
                    conn.execute(text(
                        "ALTER TABLE price_cache ADD COLUMN "
                        "dividend_yield DOUBLE PRECISION"))
        except Exception:
            pass


def _coerce(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def upsert_prices(quotes: dict[str, dict]) -> int:
    """
    Bulk-write {ticker: quote_dict}. Only rows with a non-null price are
    written (a missing price would clobber a good prior value). Returns the
    number of rows written.
    """
    from sqlalchemy import text
    if not quotes:
        return 0

    rows = []
    for ticker, q in quotes.items():
        if not q:
            continue
        price = _coerce(q.get("price"))
        if price is None:
            continue
        rows.append({
            "ticker": ticker.upper(),
            "price": price,
            "prev_close": _coerce(q.get("close") if q.get("close") is not None
                                  else q.get("prev_close")),
            "change": _coerce(q.get("change")),
            "change_pct": _coerce(q.get("change_pct")),
            "volume": _coerce(q.get("volume")),
            "dividend_yield": _coerce(q.get("dividend_yield")),
        })
    if not rows:
        return 0

    eng = _get_engine()
    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO price_cache
                  (ticker, price, prev_close, change, change_pct, volume,
                   dividend_yield, updated_at)
                VALUES (:ticker, :price, :prev_close, :change, :change_pct,
                        :volume, :dividend_yield, NOW())
                ON CONFLICT (ticker) DO UPDATE SET
                  price = EXCLUDED.price,
                  prev_close = EXCLUDED.prev_close,
                  change = EXCLUDED.change,
                  change_pct = EXCLUDED.change_pct,
                  volume = EXCLUDED.volume,
                  dividend_yield = COALESCE(EXCLUDED.dividend_yield,
                                            price_cache.dividend_yield),
                  updated_at = NOW()
            """)
            conn.execute(sql, rows)
        else:
            # SQLite: CURRENT_TIMESTAMP for updated_at
            sql = text("""
                INSERT OR REPLACE INTO price_cache
                  (ticker, price, prev_close, change, change_pct, volume,
                   dividend_yield, updated_at)
                VALUES (:ticker, :price, :prev_close, :change, :change_pct,
                        :volume, :dividend_yield, CURRENT_TIMESTAMP)
            """)
            for r in rows:
                conn.execute(sql, r)
    return len(rows)


def _parse_ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        s = str(v).replace("T", " ")[:19]
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_prices(tickers: list[str], max_age_s: int | None = None) -> dict[str, dict]:
    """
    Bulk-read warm prices for the given tickers. Returns {ticker: quote_dict}
    only for rows present (and fresh, if max_age_s is set). quote_dict mirrors
    the FMP/IBKR shape plus 'updated_at' and 'age_seconds'.
    """
    from sqlalchemy import text
    if not tickers:
        return {}
    eng = _get_engine()
    up = [t.upper() for t in tickers]
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        # Chunk the IN list to stay well under driver param limits.
        for i in range(0, len(up), 500):
            chunk = up[i:i + 500]
            params = {f"t{j}": t for j, t in enumerate(chunk)}
            placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
            rows = conn.execute(text(
                f"SELECT ticker, price, prev_close, change, change_pct, volume, "
                f"dividend_yield, updated_at FROM price_cache "
                f"WHERE ticker IN ({placeholders})"
            ), params).fetchall()
            for r in rows:
                ts = _parse_ts(r.updated_at)
                age = (now - ts).total_seconds() if ts else None
                if max_age_s is not None and (age is None or age > max_age_s):
                    continue
                out[r.ticker] = {
                    "price": r.price,
                    "close": r.prev_close,
                    "prev_close": r.prev_close,
                    "change": r.change,
                    "change_pct": r.change_pct,
                    "volume": r.volume,
                    "dividend_yield": r.dividend_yield,
                    "updated_at": ts.isoformat() if ts else None,
                    "age_seconds": age,
                }
    return out


def get_all_prices(max_age_s: int | None = None) -> dict[str, dict]:
    """Read every cached price (optionally only fresh rows)."""
    from sqlalchemy import text
    eng = _get_engine()
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT ticker, price, prev_close, change, change_pct, volume, "
            "dividend_yield, updated_at FROM price_cache"
        )).fetchall()
    for r in rows:
        ts = _parse_ts(r.updated_at)
        age = (now - ts).total_seconds() if ts else None
        if max_age_s is not None and (age is None or age > max_age_s):
            continue
        out[r.ticker] = {
            "price": r.price, "close": r.prev_close, "prev_close": r.prev_close,
            "change": r.change, "change_pct": r.change_pct, "volume": r.volume,
            "dividend_yield": r.dividend_yield,
            "updated_at": ts.isoformat() if ts else None, "age_seconds": age,
        }
    return out
