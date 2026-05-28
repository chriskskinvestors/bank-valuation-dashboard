"""
Call Report storage layer.

Persists the per-bank securities maturity ladder (Schedule RC-B Memo 2)
and loan repricing data fetched from FFIEC into Postgres so the dashboard
and rate-sensitivity model can read it without re-hitting FFIEC.

One row per (cert, report_date). The buckets are stored as a JSON blob
so we can extend the schema later without migrations.

Tables:
  call_report_securities(
    cert                INTEGER NOT NULL — FDIC certificate (joins bank)
    rssd_id             INTEGER NOT NULL — Fed RSSD ID (FFIEC's primary key)
    report_date         DATE NOT NULL    — quarter-end of the report
    total_securities    BIGINT           — total debt securities (sum of buckets)
    buckets_json        TEXT             — {bucket_key: fraction} JSON
    amounts_json        TEXT             — {bucket_key: usd_amount} JSON
    weighted_dur_yrs    DOUBLE PRECISION — midpoint-weighted duration
    floating_loan_share DOUBLE PRECISION — if available from RC-K
    source              VARCHAR(20)      — 'ffiec' or 'estimated'
    ingested_at         TIMESTAMP
    PRIMARY KEY (cert, report_date)
  )

Public functions:
  • init_call_report_schema()                   — idempotent CREATE TABLE
  • upsert_securities_ladder(cert, rssd, ...)   — write one bank's data
  • get_latest_ladder(cert)                     — read most recent ladder
  • get_all_ladders()                           — bulk read for ranking
"""

from __future__ import annotations
import json
import os
from datetime import datetime

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
    init_call_report_schema()
    return _engine


def init_call_report_schema():
    """Create the call_report_securities table. Idempotent."""
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
            CREATE TABLE IF NOT EXISTS call_report_securities (
                cert                INTEGER NOT NULL,
                rssd_id             INTEGER NOT NULL,
                report_date         DATE NOT NULL,
                total_securities    BIGINT,
                buckets_json        TEXT,
                amounts_json        TEXT,
                weighted_dur_yrs    DOUBLE PRECISION,
                floating_loan_share DOUBLE PRECISION,
                source              VARCHAR(20),
                ingested_at         {ts_default},
                PRIMARY KEY (cert, report_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_callrep_rssd "
            "ON call_report_securities(rssd_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_callrep_date "
            "ON call_report_securities(report_date DESC)"
        ))


def _parse_period(period_str: str) -> str:
    """
    Convert FFIEC's MM/DD/YYYY reporting_period to ISO YYYY-MM-DD for SQL.
    Accepts already-ISO strings too.
    """
    if not period_str:
        return ""
    s = str(period_str).strip()
    if "-" in s:
        return s[:10]
    # MM/DD/YYYY
    parts = s.split("/")
    if len(parts) == 3:
        m, d, y = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def upsert_securities_ladder(
    cert: int,
    rssd_id: int,
    ladder: dict,
    floating_loan_share: float | None = None,
) -> int:
    """
    Write one bank's ladder for one reporting period. Returns 1 on success, 0 otherwise.

    ladder is the dict returned by ffiec_client.get_securities_maturity_ladder.
    """
    from sqlalchemy import text

    if not ladder or "buckets" not in ladder:
        return 0

    report_date = _parse_period(ladder.get("reporting_period", ""))
    if not report_date:
        return 0

    eng = _get_engine()
    row = {
        "cert": int(cert),
        "rssd_id": int(rssd_id),
        "report_date": report_date,
        "total_securities": int(ladder.get("total_usd") or 0),
        "buckets_json": json.dumps(ladder.get("buckets", {})),
        "amounts_json": json.dumps(ladder.get("amounts_usd", {})),
        "weighted_dur_yrs": float(ladder.get("weighted_avg_duration_years") or 0.0),
        "floating_loan_share": (
            float(floating_loan_share) if floating_loan_share is not None else None
        ),
        "source": "ffiec",
    }

    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO call_report_securities
                  (cert, rssd_id, report_date, total_securities, buckets_json,
                   amounts_json, weighted_dur_yrs, floating_loan_share, source)
                VALUES
                  (:cert, :rssd_id, :report_date, :total_securities,
                   :buckets_json, :amounts_json, :weighted_dur_yrs,
                   :floating_loan_share, :source)
                ON CONFLICT (cert, report_date) DO UPDATE SET
                  rssd_id = EXCLUDED.rssd_id,
                  total_securities = EXCLUDED.total_securities,
                  buckets_json = EXCLUDED.buckets_json,
                  amounts_json = EXCLUDED.amounts_json,
                  weighted_dur_yrs = EXCLUDED.weighted_dur_yrs,
                  floating_loan_share = EXCLUDED.floating_loan_share,
                  source = EXCLUDED.source,
                  ingested_at = NOW()
            """)
        else:
            sql = text("""
                INSERT OR REPLACE INTO call_report_securities
                  (cert, rssd_id, report_date, total_securities, buckets_json,
                   amounts_json, weighted_dur_yrs, floating_loan_share, source)
                VALUES
                  (:cert, :rssd_id, :report_date, :total_securities,
                   :buckets_json, :amounts_json, :weighted_dur_yrs,
                   :floating_loan_share, :source)
            """)
        conn.execute(sql, row)
    return 1


def get_latest_ladder(cert: int) -> dict | None:
    """
    Return the most-recent stored ladder for a bank in the same shape
    that ffiec_client.get_securities_maturity_ladder returns:
      {reporting_period, buckets, amounts_usd, total_usd, weighted_avg_duration_years,
       floating_loan_share, source}
    or None if no row exists.
    """
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text("""
            SELECT report_date, total_securities, buckets_json, amounts_json,
                   weighted_dur_yrs, floating_loan_share, source, rssd_id
            FROM call_report_securities
            WHERE cert = :cert
            ORDER BY report_date DESC
            LIMIT 1
        """), {"cert": int(cert)}).fetchone()

    if row is None:
        return None

    try:
        buckets = json.loads(row.buckets_json) if row.buckets_json else {}
        amounts = json.loads(row.amounts_json) if row.amounts_json else {}
    except Exception:
        buckets, amounts = {}, {}

    report_date = row.report_date
    if hasattr(report_date, "strftime"):
        report_date_str = report_date.strftime("%m/%d/%Y")
    else:
        # SQLite returns a string
        s = str(report_date)
        try:
            y, m, d = s[:10].split("-")
            report_date_str = f"{m}/{d}/{y}"
        except Exception:
            report_date_str = s

    return {
        "reporting_period": report_date_str,
        "buckets": buckets,
        "amounts_usd": amounts,
        "total_usd": int(row.total_securities or 0),
        "weighted_avg_duration_years": float(row.weighted_dur_yrs or 0.0),
        "floating_loan_share": (
            float(row.floating_loan_share) if row.floating_loan_share is not None else None
        ),
        "source": row.source or "ffiec",
        "rssd_id": int(row.rssd_id or 0),
    }


def get_all_latest_ladders() -> pd.DataFrame:
    """
    Return the latest ladder per bank as a DataFrame. Powers the
    cross-bank ranking view if we build it later.
    """
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                SELECT DISTINCT ON (cert)
                  cert, rssd_id, report_date, total_securities,
                  buckets_json, weighted_dur_yrs, floating_loan_share, source
                FROM call_report_securities
                ORDER BY cert, report_date DESC
            """)
        else:
            # SQLite — emulate DISTINCT ON via subquery
            sql = text("""
                SELECT c.cert, c.rssd_id, c.report_date, c.total_securities,
                       c.buckets_json, c.weighted_dur_yrs,
                       c.floating_loan_share, c.source
                FROM call_report_securities c
                INNER JOIN (
                  SELECT cert, MAX(report_date) AS max_dt
                  FROM call_report_securities GROUP BY cert
                ) m ON m.cert = c.cert AND m.max_dt = c.report_date
            """)
        rows = list(conn.execute(sql))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=[
        "cert", "rssd_id", "report_date", "total_securities",
        "buckets_json", "weighted_dur_yrs", "floating_loan_share", "source",
    ])


def coverage_summary() -> dict:
    """Quick stat for the Data Quality tab."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        result = conn.execute(text("""
            SELECT COUNT(DISTINCT cert) AS n_banks,
                   MAX(report_date) AS latest_date,
                   MIN(report_date) AS earliest_date,
                   COUNT(*) AS n_rows
            FROM call_report_securities
        """)).fetchone()
    return {
        "n_banks": result.n_banks if result else 0,
        "n_rows": result.n_rows if result else 0,
        "latest_date": str(result.latest_date) if result and result.latest_date else None,
        "earliest_date": str(result.earliest_date) if result and result.earliest_date else None,
    }
