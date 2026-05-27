"""
Branch storage layer.

Persists FDIC Summary-of-Deposits branch-level data into the same
Postgres (or SQLite for local dev) backend used by the cache + events
modules. One row per branch per year, keyed by (cert, brnum, year).

Tables:
  branches(
    cert         INTEGER     — FDIC certificate (links to bank)
    brnum        INTEGER     — branch number within the bank
    year         INTEGER     — SOD reporting year
    ticker       VARCHAR(20) — public ticker (denormalized from bank_mapping)
    bank_name    TEXT        — bank's NAMEFULL at the time
    branch_name  TEXT        — branch's NAMEBR
    address      TEXT
    city         TEXT
    state        VARCHAR(2)
    zip          VARCHAR(10)
    county       TEXT
    stcntybr     VARCHAR(10) — 5-digit state+county FIPS
    msa_code     VARCHAR(10) — CBSA / MSA code
    msa_name     TEXT
    deposits     BIGINT      — DEPSUMBR in $thousands
    lat          DOUBLE PRECISION
    lng          DOUBLE PRECISION
    serv_type    VARCHAR(10) — BRSERTYP (11=main office, 12=full-service, etc.)
    ingested_at  TIMESTAMP   — when this row was written
    PRIMARY KEY (cert, brnum, year)
  )

Provides:
  • init_branches_schema()       — idempotent CREATE TABLE
  • upsert_branches(rows)        — bulk insert/update for one bank
  • get_branches_by_state(s)     — query for the new geo UI view
  • get_branches_by_msa(m)       — query
  • get_branch_counts_by_ticker() — quick coverage check
"""

from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Iterable

import pandas as pd

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_USE_POSTGRES = _DATABASE_URL.startswith(
    ("postgres://", "postgresql://", "postgresql+psycopg2://")
)

_engine = None


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
    init_branches_schema()
    return _engine


def init_branches_schema():
    """Create the branches table if it doesn't exist. Idempotent."""
    from sqlalchemy import create_engine, text
    from pathlib import Path

    if _USE_POSTGRES:
        url = _DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        eng = create_engine(url, future=True)
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
        ts_col = "TIMESTAMP WITH TIME ZONE"
    else:
        db_path = Path(__file__).parent.parent / "cache.db"
        eng = create_engine(f"sqlite:///{db_path}", future=True)
        ts_default = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ts_col = "TIMESTAMP"

    with eng.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS branches (
                cert         INTEGER NOT NULL,
                brnum        INTEGER NOT NULL,
                year         INTEGER NOT NULL,
                ticker       VARCHAR(20),
                bank_name    TEXT,
                branch_name  TEXT,
                address      TEXT,
                city         TEXT,
                state        VARCHAR(2),
                zip          VARCHAR(10),
                county       TEXT,
                stcntybr     VARCHAR(10),
                msa_code     VARCHAR(10),
                msa_name     TEXT,
                deposits     BIGINT,
                lat          DOUBLE PRECISION,
                lng          DOUBLE PRECISION,
                serv_type    VARCHAR(10),
                ingested_at  {ts_default},
                PRIMARY KEY (cert, brnum, year)
            )
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_branches_state ON branches(state)",
            "CREATE INDEX IF NOT EXISTS idx_branches_msa ON branches(msa_code)",
            "CREATE INDEX IF NOT EXISTS idx_branches_ticker ON branches(ticker)",
            "CREATE INDEX IF NOT EXISTS idx_branches_year ON branches(year)",
        ]:
            conn.execute(text(idx_sql))


def upsert_branches(ticker: str, cert: int, df: pd.DataFrame) -> int:
    """
    Bulk insert/replace branch rows for one bank.

    df comes from sod_client.fetch_branches(). Returns count written.
    """
    from sqlalchemy import text

    if df is None or df.empty:
        return 0

    eng = _get_engine()

    def _s(v, n: int = 500) -> str:
        """Coerce any value to a string of max length n. Handles int/float/None."""
        if v is None:
            return ""
        return str(v)[:n]

    def _i(v) -> int:
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    def _f(v):
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    rows = []
    for r in df.itertuples(index=False):
        rd = r._asdict()
        rows.append({
            "cert": cert,
            "brnum": _i(rd.get("BRNUM")),
            "year": _i(rd.get("YEAR")),
            "ticker": ticker.upper() if ticker else None,
            "bank_name": _s(rd.get("NAMEFULL"), 500),
            "branch_name": _s(rd.get("NAMEBR"), 500),
            "address": _s(rd.get("ADDRESBR"), 500),
            "city": _s(rd.get("CITYBR"), 200),
            "state": _s(rd.get("STALPBR"), 2),
            "zip": _s(rd.get("ZIPBR"), 10),
            "county": _s(rd.get("CNTYNAMB"), 200),
            "stcntybr": _s(rd.get("STCNTYBR"), 10),
            "msa_code": _s(rd.get("MSABR"), 10),
            "msa_name": _s(rd.get("MSANAMB"), 500),
            "deposits": _i(rd.get("DEPSUMBR")),
            "lat": _f(rd.get("SIMS_LATITUDE")),
            "lng": _f(rd.get("SIMS_LONGITUDE")),
            "serv_type": _s(rd.get("BRSERTYP"), 10),
        })

    if not rows:
        return 0

    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO branches
                  (cert, brnum, year, ticker, bank_name, branch_name,
                   address, city, state, zip, county, stcntybr, msa_code,
                   msa_name, deposits, lat, lng, serv_type)
                VALUES
                  (:cert, :brnum, :year, :ticker, :bank_name, :branch_name,
                   :address, :city, :state, :zip, :county, :stcntybr,
                   :msa_code, :msa_name, :deposits, :lat, :lng, :serv_type)
                ON CONFLICT (cert, brnum, year) DO UPDATE SET
                  ticker = EXCLUDED.ticker,
                  bank_name = EXCLUDED.bank_name,
                  branch_name = EXCLUDED.branch_name,
                  address = EXCLUDED.address,
                  city = EXCLUDED.city,
                  state = EXCLUDED.state,
                  zip = EXCLUDED.zip,
                  county = EXCLUDED.county,
                  stcntybr = EXCLUDED.stcntybr,
                  msa_code = EXCLUDED.msa_code,
                  msa_name = EXCLUDED.msa_name,
                  deposits = EXCLUDED.deposits,
                  lat = EXCLUDED.lat,
                  lng = EXCLUDED.lng,
                  serv_type = EXCLUDED.serv_type,
                  ingested_at = NOW()
            """)
        else:
            sql = text("""
                INSERT OR REPLACE INTO branches
                  (cert, brnum, year, ticker, bank_name, branch_name,
                   address, city, state, zip, county, stcntybr, msa_code,
                   msa_name, deposits, lat, lng, serv_type)
                VALUES
                  (:cert, :brnum, :year, :ticker, :bank_name, :branch_name,
                   :address, :city, :state, :zip, :county, :stcntybr,
                   :msa_code, :msa_name, :deposits, :lat, :lng, :serv_type)
            """)
        for r in rows:
            conn.execute(sql, r)
    return len(rows)


# ──────────────────────────────────────────────────────────────────────────
# Query API for the UI
# ──────────────────────────────────────────────────────────────────────────

def _q_to_df(sql: str, params: dict) -> pd.DataFrame:
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def get_branches_by_state(state: str, tickers: list[str] | None = None,
                           year: int | None = None) -> pd.DataFrame:
    """All branches in a state, optionally filtered to a ticker subset."""
    params = {"state": state.upper()}
    sql = """
        SELECT * FROM branches
        WHERE state = :state
    """
    if year:
        sql += " AND year = :year"
        params["year"] = year
    if tickers:
        if _USE_POSTGRES:
            sql += " AND ticker = ANY(:tickers)"
            params["tickers"] = [t.upper() for t in tickers]
        else:
            placeholders = ",".join(f":t{i}" for i in range(len(tickers)))
            sql += f" AND ticker IN ({placeholders})"
            for i, t in enumerate(tickers):
                params[f"t{i}"] = t.upper()
    sql += " ORDER BY deposits DESC"
    return _q_to_df(sql, params)


def get_branches_by_msa(msa_code: str, tickers: list[str] | None = None,
                         year: int | None = None) -> pd.DataFrame:
    """All branches in an MSA (CBSA code), optionally filtered to a ticker subset."""
    params = {"msa_code": str(msa_code)}
    sql = "SELECT * FROM branches WHERE msa_code = :msa_code"
    if year:
        sql += " AND year = :year"
        params["year"] = year
    if tickers:
        if _USE_POSTGRES:
            sql += " AND ticker = ANY(:tickers)"
            params["tickers"] = [t.upper() for t in tickers]
        else:
            placeholders = ",".join(f":t{i}" for i in range(len(tickers)))
            sql += f" AND ticker IN ({placeholders})"
            for i, t in enumerate(tickers):
                params[f"t{i}"] = t.upper()
    sql += " ORDER BY deposits DESC"
    return _q_to_df(sql, params)


def get_banks_by_state(state: str, year: int | None = None) -> pd.DataFrame:
    """Aggregated: total deposits + branch count per bank in a state."""
    params = {"state": state.upper()}
    extra = " AND year = :year" if year else ""
    if year:
        params["year"] = year
    sql = f"""
        SELECT ticker, bank_name,
               COUNT(*) AS n_branches,
               SUM(deposits) AS total_deposits
        FROM branches
        WHERE state = :state {extra}
        GROUP BY ticker, bank_name
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, params)


def get_banks_by_msa(msa_code: str, year: int | None = None) -> pd.DataFrame:
    """Aggregated: total deposits + branch count per bank in an MSA."""
    params = {"msa_code": str(msa_code)}
    extra = " AND year = :year" if year else ""
    if year:
        params["year"] = year
    sql = f"""
        SELECT ticker, bank_name,
               COUNT(*) AS n_branches,
               SUM(deposits) AS total_deposits,
               MAX(msa_name) AS msa_name
        FROM branches
        WHERE msa_code = :msa_code {extra}
        GROUP BY ticker, bank_name
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, params)


def list_states() -> list[str]:
    """List of distinct states present in the table."""
    df = _q_to_df(
        "SELECT DISTINCT state FROM branches WHERE state != '' ORDER BY state",
        {},
    )
    return df["state"].tolist() if not df.empty else []


def list_msas() -> pd.DataFrame:
    """List of (msa_code, msa_name) pairs present, sorted by name."""
    return _q_to_df("""
        SELECT msa_code, MAX(msa_name) AS msa_name
        FROM branches
        WHERE msa_code != '' AND msa_name != ''
        GROUP BY msa_code
        ORDER BY MAX(msa_name)
    """, {})


def get_latest_year() -> int | None:
    """Most recent SOD year present in the table."""
    df = _q_to_df("SELECT MAX(year) AS y FROM branches", {})
    if df.empty:
        return None
    return int(df["y"].iloc[0]) if df["y"].iloc[0] else None


def get_branch_counts_by_ticker() -> pd.DataFrame:
    """Coverage check: how many branches per ticker (latest year only)."""
    sql = """
        SELECT ticker,
               COUNT(*) AS n_branches,
               SUM(deposits) AS total_deposits
        FROM branches
        WHERE year = (SELECT MAX(year) FROM branches)
        GROUP BY ticker
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, {})
