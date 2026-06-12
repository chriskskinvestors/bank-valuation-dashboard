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

  ri_income_detail(
    cert        INTEGER NOT NULL — FDIC certificate (joins bank)
    rssd_id     INTEGER NOT NULL — Fed RSSD ID
    report_date DATE NOT NULL    — quarter-end of the report (RI is YTD)
    detail_json TEXT             — full get_ri_income_detail dict as JSON
    ingested_at TIMESTAMP
    PRIMARY KEY (cert, report_date)
  )

  rcn_detail(
    cert        INTEGER NOT NULL — FDIC certificate (joins bank)
    rssd_id     INTEGER NOT NULL — Fed RSSD ID
    report_date DATE NOT NULL    — quarter-end of the report
    detail_json TEXT             — full get_rcn_detail dict as JSON
    ingested_at TIMESTAMP
    PRIMARY KEY (cert, report_date)
  )

  rcr_capital(
    cert        INTEGER NOT NULL — FDIC certificate (joins bank)
    rssd_id     INTEGER NOT NULL — Fed RSSD ID
    report_date DATE NOT NULL    — quarter-end of the report
    detail_json TEXT             — full get_rcr_capital_detail dict as JSON
    ingested_at TIMESTAMP
    PRIMARY KEY (cert, report_date)
  )

  rie_detail(
    cert        INTEGER NOT NULL — FDIC certificate (joins bank)
    rssd_id     INTEGER NOT NULL — Fed RSSD ID
    report_date DATE NOT NULL    — quarter-end of the report (RI-E is YTD)
    detail_json TEXT             — full get_ri_e_detail dict as JSON
    ingested_at TIMESTAMP
    PRIMARY KEY (cert, report_date)
  )

Public functions:
  • init_call_report_schema()                   — idempotent CREATE TABLE
  • upsert_securities_ladder(cert, rssd, ...)   — write one bank's data
  • get_latest_ladder(cert)                     — read most recent ladder
  • get_all_ladders()                           — bulk read for ranking
  • upsert_ri_income_detail(cert, rssd, detail) — write one bank-quarter's RI detail
  • get_stored_ri_detail(cert, quarters=8)      — read RI detail, newest-first
  • upsert_rcn_detail(cert, rssd, detail)       — write one bank-quarter's RC-N detail
  • get_stored_rcn_detail(cert, quarters=8)     — read RC-N detail, newest-first
  • upsert_rcr_detail(cert, rssd, detail)       — write one bank-quarter's RC-R capital walk
  • get_stored_rcr_detail(cert, quarters=8)     — read RC-R capital walk, newest-first
  • upsert_rie_detail(cert, rssd, detail)       — write one bank-quarter's RI-E itemization
  • get_stored_rie_detail(cert, quarters=8)     — read RI-E itemization, newest-first
"""

from __future__ import annotations
import json
from datetime import datetime

import pandas as pd

from data.db import USE_POSTGRES as _USE_POSTGRES

_engine = None


def _get_engine():
    """Shared engine (data/db) + this store's first-use schema init."""
    global _engine
    if _engine is not None:
        return _engine

    from data.db import get_engine
    _engine = get_engine()
    init_call_report_schema()
    return _engine


def init_call_report_schema():
    """Create the call_report_securities table. Idempotent."""
    from sqlalchemy import text
    from data.db import get_engine

    eng = get_engine()
    ts_default = ("TIMESTAMP WITH TIME ZONE DEFAULT NOW()" if _USE_POSTGRES
                  else "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

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
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS ri_income_detail (
                cert        INTEGER NOT NULL,
                rssd_id     INTEGER NOT NULL,
                report_date DATE NOT NULL,
                detail_json TEXT,
                ingested_at {ts_default},
                PRIMARY KEY (cert, report_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_ri_detail_rssd "
            "ON ri_income_detail(rssd_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_ri_detail_date "
            "ON ri_income_detail(report_date DESC)"
        ))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS rcn_detail (
                cert        INTEGER NOT NULL,
                rssd_id     INTEGER NOT NULL,
                report_date DATE NOT NULL,
                detail_json TEXT,
                ingested_at {ts_default},
                PRIMARY KEY (cert, report_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rcn_detail_rssd "
            "ON rcn_detail(rssd_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rcn_detail_date "
            "ON rcn_detail(report_date DESC)"
        ))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS rcr_capital (
                cert        INTEGER NOT NULL,
                rssd_id     INTEGER NOT NULL,
                report_date DATE NOT NULL,
                detail_json TEXT,
                ingested_at {ts_default},
                PRIMARY KEY (cert, report_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rcr_capital_rssd "
            "ON rcr_capital(rssd_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rcr_capital_date "
            "ON rcr_capital(report_date DESC)"
        ))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS rie_detail (
                cert        INTEGER NOT NULL,
                rssd_id     INTEGER NOT NULL,
                report_date DATE NOT NULL,
                detail_json TEXT,
                ingested_at {ts_default},
                PRIMARY KEY (cert, report_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rie_detail_rssd "
            "ON rie_detail(rssd_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rie_detail_date "
            "ON rie_detail(report_date DESC)"
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
    except Exception as e:
        # Corrupted stored JSON would otherwise pose as a valid empty ladder
        # (repricing pace silently zero).
        print(f"[call_report_store] corrupted ladder JSON for cert {cert}: "
              f"{type(e).__name__}: {e}")
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


def upsert_ri_income_detail(cert: int, rssd_id: int, detail: dict) -> int:
    """
    Write one bank's Schedule RI income detail for one reporting period.
    Returns 1 on success, 0 otherwise.

    detail is the dict returned by ffiec_client.get_ri_income_detail
    (stored whole as JSON so new RI codes need no migration).
    """
    from sqlalchemy import text

    if not detail:
        return 0

    report_date = _parse_period(detail.get("reporting_period", ""))
    if not report_date:
        return 0

    eng = _get_engine()
    row = {
        "cert": int(cert),
        "rssd_id": int(rssd_id),
        "report_date": report_date,
        "detail_json": json.dumps(detail),
    }

    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO ri_income_detail
                  (cert, rssd_id, report_date, detail_json)
                VALUES
                  (:cert, :rssd_id, :report_date, :detail_json)
                ON CONFLICT (cert, report_date) DO UPDATE SET
                  rssd_id = EXCLUDED.rssd_id,
                  detail_json = EXCLUDED.detail_json,
                  ingested_at = NOW()
            """)
        else:
            sql = text("""
                INSERT OR REPLACE INTO ri_income_detail
                  (cert, rssd_id, report_date, detail_json)
                VALUES
                  (:cert, :rssd_id, :report_date, :detail_json)
            """)
        conn.execute(sql, row)
    return 1


def get_stored_ri_detail(cert: int, quarters: int = 8) -> list[dict]:
    """
    Return up to `quarters` stored RI income-detail dicts for a bank,
    newest-first, each in the shape ffiec_client.get_ri_income_detail
    returns (reporting_period MM/DD/YYYY, rssd_id, per-code values + _usd).
    Empty list when nothing is stored.
    """
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text("""
            SELECT report_date, rssd_id, detail_json
            FROM ri_income_detail
            WHERE cert = :cert
            ORDER BY report_date DESC
            LIMIT :quarters
        """), {"cert": int(cert), "quarters": int(quarters)}).fetchall()

    out: list[dict] = []
    for row in rows:
        try:
            detail = json.loads(row.detail_json) if row.detail_json else None
        except Exception as e:
            # A corrupted row must not pose as a valid (empty) quarter.
            print(f"[call_report_store] corrupted RI detail JSON for cert "
                  f"{cert}: {type(e).__name__}: {e}")
            continue
        if not detail:
            continue

        # Normalize reporting_period from the date column (the JSON copy may
        # be ISO or MM/DD/YYYY depending on what the client was handed).
        report_date = row.report_date
        if hasattr(report_date, "strftime"):
            detail["reporting_period"] = report_date.strftime("%m/%d/%Y")
        else:
            s = str(report_date)
            try:
                y, m, d = s[:10].split("-")
                detail["reporting_period"] = f"{m}/{d}/{y}"
            except Exception:
                detail["reporting_period"] = s
        detail["rssd_id"] = int(row.rssd_id or 0)
        out.append(detail)
    return out


# Generic JSON-detail upsert/read used by the newer schedule tables
# (rcr_capital, rie_detail). Third duplication of the ri/rcn pattern —
# parametrized instead of copied; ri/rcn keep their tested originals.
def _upsert_detail(table: str, cert: int, rssd_id: int, detail: dict) -> int:
    from sqlalchemy import text
    if not detail:
        return 0
    report_date = _parse_period(detail.get("reporting_period", ""))
    if not report_date:
        return 0
    eng = _get_engine()
    row = {"cert": int(cert), "rssd_id": int(rssd_id),
           "report_date": report_date, "detail_json": json.dumps(detail)}
    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text(f"""
                INSERT INTO {table}
                  (cert, rssd_id, report_date, detail_json)
                VALUES (:cert, :rssd_id, :report_date, :detail_json)
                ON CONFLICT (cert, report_date) DO UPDATE SET
                  rssd_id = EXCLUDED.rssd_id,
                  detail_json = EXCLUDED.detail_json,
                  ingested_at = NOW()
            """)
        else:
            sql = text(f"""
                INSERT OR REPLACE INTO {table}
                  (cert, rssd_id, report_date, detail_json)
                VALUES (:cert, :rssd_id, :report_date, :detail_json)
            """)
        conn.execute(sql, row)
    return 1


def _get_stored_detail(table: str, cert: int, quarters: int) -> list[dict]:
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT report_date, rssd_id, detail_json
            FROM {table}
            WHERE cert = :cert
            ORDER BY report_date DESC
            LIMIT :quarters
        """), {"cert": int(cert), "quarters": int(quarters)}).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            detail = json.loads(row.detail_json) if row.detail_json else None
        except Exception as e:
            print(f"[call_report_store] corrupted {table} JSON for cert "
                  f"{cert}: {type(e).__name__}: {e}")
            continue
        if not detail:
            continue
        report_date = row.report_date
        if hasattr(report_date, "strftime"):
            detail["reporting_period"] = report_date.strftime("%m/%d/%Y")
        else:
            s = str(report_date)
            try:
                y, m, d = s[:10].split("-")
                detail["reporting_period"] = f"{m}/{d}/{y}"
            except Exception:
                detail["reporting_period"] = s
        detail["rssd_id"] = int(row.rssd_id or 0)
        out.append(detail)
    return out


def upsert_rcr_detail(cert: int, rssd_id: int, detail: dict) -> int:
    """Write one bank-quarter's RC-R capital walk
    (ffiec_client.get_rcr_capital_detail dict). 1 on success, 0 otherwise."""
    return _upsert_detail("rcr_capital", cert, rssd_id, detail)


def get_stored_rcr_detail(cert: int, quarters: int = 8) -> list[dict]:
    """Stored RC-R capital-walk dicts for a bank, newest-first."""
    return _get_stored_detail("rcr_capital", cert, quarters)


def upsert_rie_detail(cert: int, rssd_id: int, detail: dict) -> int:
    """Write one bank-quarter's RI-E itemization
    (ffiec_client.get_ri_e_detail dict). 1 on success, 0 otherwise."""
    return _upsert_detail("rie_detail", cert, rssd_id, detail)


def get_stored_rie_detail(cert: int, quarters: int = 8) -> list[dict]:
    """Stored RI-E itemization dicts for a bank, newest-first."""
    return _get_stored_detail("rie_detail", cert, quarters)


def upsert_rcn_detail(cert: int, rssd_id: int, detail: dict) -> int:
    """
    Write one bank's Schedule RC-N past-due/nonaccrual detail for one
    reporting period. Returns 1 on success, 0 otherwise.

    detail is the dict returned by ffiec_client.get_rcn_detail (stored whole
    as JSON so new RC-N categories need no migration).
    """
    from sqlalchemy import text

    if not detail:
        return 0

    report_date = _parse_period(detail.get("reporting_period", ""))
    if not report_date:
        return 0

    eng = _get_engine()
    row = {
        "cert": int(cert),
        "rssd_id": int(rssd_id),
        "report_date": report_date,
        "detail_json": json.dumps(detail),
    }

    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO rcn_detail
                  (cert, rssd_id, report_date, detail_json)
                VALUES
                  (:cert, :rssd_id, :report_date, :detail_json)
                ON CONFLICT (cert, report_date) DO UPDATE SET
                  rssd_id = EXCLUDED.rssd_id,
                  detail_json = EXCLUDED.detail_json,
                  ingested_at = NOW()
            """)
        else:
            sql = text("""
                INSERT OR REPLACE INTO rcn_detail
                  (cert, rssd_id, report_date, detail_json)
                VALUES
                  (:cert, :rssd_id, :report_date, :detail_json)
            """)
        conn.execute(sql, row)
    return 1


def get_stored_rcn_detail(cert: int, quarters: int = 8) -> list[dict]:
    """
    Return up to `quarters` stored RC-N detail dicts for a bank,
    newest-first, each in the shape ffiec_client.get_rcn_detail returns
    (reporting_period MM/DD/YYYY, rssd_id, categories matrix + totals).
    Empty list when nothing is stored.
    """
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text("""
            SELECT report_date, rssd_id, detail_json
            FROM rcn_detail
            WHERE cert = :cert
            ORDER BY report_date DESC
            LIMIT :quarters
        """), {"cert": int(cert), "quarters": int(quarters)}).fetchall()

    out: list[dict] = []
    for row in rows:
        try:
            detail = json.loads(row.detail_json) if row.detail_json else None
        except Exception as e:
            # A corrupted row must not pose as a valid (empty) quarter.
            print(f"[call_report_store] corrupted RC-N detail JSON for cert "
                  f"{cert}: {type(e).__name__}: {e}")
            continue
        if not detail:
            continue

        # Normalize reporting_period from the date column (the JSON copy may
        # be ISO or MM/DD/YYYY depending on what the client was handed).
        report_date = row.report_date
        if hasattr(report_date, "strftime"):
            detail["reporting_period"] = report_date.strftime("%m/%d/%Y")
        else:
            s = str(report_date)
            try:
                y, m, d = s[:10].split("-")
                detail["reporting_period"] = f"{m}/{d}/{y}"
            except Exception:
                detail["reporting_period"] = s
        detail["rssd_id"] = int(row.rssd_id or 0)
        out.append(detail)
    return out


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
