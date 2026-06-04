"""
User NIM-assumptions storage layer.

Persists the analyst's per-bank overrides for the rate-sensitivity model:
deposit betas by subcategory + asset durations. These let the user inject
their own view of each bank's deposit stickiness and asset repricing speed
instead of relying on the noisy trailing-historical beta estimator (which the
backtest showed loses to a flat-line for small banks).

One row per cert. Overrides win over auto-computed defaults in the UI.

Table:
  user_nim_assumptions(
    cert                     INTEGER PRIMARY KEY — FDIC certificate
    beta_nib                 DOUBLE PRECISION    — non-interest-bearing deposit beta
    beta_ib_core             DOUBLE PRECISION    — IB core (savings/MMDA/NOW) beta
    beta_brokered            DOUBLE PRECISION    — brokered/wholesale beta
    sec_duration_yrs         DOUBLE PRECISION    — securities duration override
    floating_loan_share      DOUBLE PRECISION    — floating-rate loan share
    fixed_loan_duration_yrs  DOUBLE PRECISION    — fixed-rate loan duration
    note                     TEXT                — free-text analyst rationale
    updated_by               VARCHAR(120)        — who saved it
    updated_at               TIMESTAMP
  )

Public functions:
  • init_nim_assumptions_schema()        — idempotent CREATE TABLE
  • upsert_assumptions(cert, fields, by) — write one bank's overrides
  • get_assumptions(cert)                — read overrides or None
  • get_all_assumptions()                — bulk read {cert: fields}
  • delete_assumptions(cert)             — clear overrides (revert to auto)
"""

from __future__ import annotations
import os
from datetime import datetime

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_USE_POSTGRES = _DATABASE_URL.startswith(
    ("postgres://", "postgresql://", "postgresql+psycopg2://")
)

_engine = None

# Columns the user can override. Kept here so UI + store agree on the shape.
ASSUMPTION_FIELDS = (
    "beta_nib",
    "beta_ib_core",
    "beta_brokered",
    "sec_duration_yrs",
    "floating_loan_share",
    "fixed_loan_duration_yrs",
)


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
    init_nim_assumptions_schema()
    return _engine


def init_nim_assumptions_schema():
    """Create the user_nim_assumptions table. Idempotent."""
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
            CREATE TABLE IF NOT EXISTS user_nim_assumptions (
                cert                     INTEGER PRIMARY KEY,
                beta_nib                 DOUBLE PRECISION,
                beta_ib_core             DOUBLE PRECISION,
                beta_brokered            DOUBLE PRECISION,
                sec_duration_yrs         DOUBLE PRECISION,
                floating_loan_share      DOUBLE PRECISION,
                fixed_loan_duration_yrs  DOUBLE PRECISION,
                note                     TEXT,
                updated_by               VARCHAR(120),
                updated_at               {ts_default}
            )
        """))


def _coerce(v):
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


def upsert_assumptions(
    cert: int,
    fields: dict,
    updated_by: str | None = None,
) -> int:
    """
    Write one bank's override assumptions. Returns 1 on success, 0 otherwise.

    fields may contain any subset of ASSUMPTION_FIELDS plus optional 'note'.
    Missing numeric fields are stored as NULL (UI falls back to auto-default).
    """
    from sqlalchemy import text

    if not cert:
        return 0

    row = {f: _coerce(fields.get(f)) for f in ASSUMPTION_FIELDS}
    row["cert"] = int(cert)
    row["note"] = (fields.get("note") or None)
    row["updated_by"] = (updated_by or "dashboard")[:120]

    eng = _get_engine()
    with eng.begin() as conn:
        if _USE_POSTGRES:
            sql = text("""
                INSERT INTO user_nim_assumptions
                  (cert, beta_nib, beta_ib_core, beta_brokered,
                   sec_duration_yrs, floating_loan_share, fixed_loan_duration_yrs,
                   note, updated_by)
                VALUES
                  (:cert, :beta_nib, :beta_ib_core, :beta_brokered,
                   :sec_duration_yrs, :floating_loan_share, :fixed_loan_duration_yrs,
                   :note, :updated_by)
                ON CONFLICT (cert) DO UPDATE SET
                  beta_nib = EXCLUDED.beta_nib,
                  beta_ib_core = EXCLUDED.beta_ib_core,
                  beta_brokered = EXCLUDED.beta_brokered,
                  sec_duration_yrs = EXCLUDED.sec_duration_yrs,
                  floating_loan_share = EXCLUDED.floating_loan_share,
                  fixed_loan_duration_yrs = EXCLUDED.fixed_loan_duration_yrs,
                  note = EXCLUDED.note,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
            """)
        else:
            sql = text("""
                INSERT OR REPLACE INTO user_nim_assumptions
                  (cert, beta_nib, beta_ib_core, beta_brokered,
                   sec_duration_yrs, floating_loan_share, fixed_loan_duration_yrs,
                   note, updated_by)
                VALUES
                  (:cert, :beta_nib, :beta_ib_core, :beta_brokered,
                   :sec_duration_yrs, :floating_loan_share, :fixed_loan_duration_yrs,
                   :note, :updated_by)
            """)
        conn.execute(sql, row)
    return 1


def get_assumptions(cert: int) -> dict | None:
    """Return saved overrides for a bank, or None if none saved."""
    from sqlalchemy import text
    if not cert:
        return None
    eng = _get_engine()
    with eng.begin() as conn:
        r = conn.execute(text("""
            SELECT beta_nib, beta_ib_core, beta_brokered, sec_duration_yrs,
                   floating_loan_share, fixed_loan_duration_yrs, note,
                   updated_by, updated_at
            FROM user_nim_assumptions WHERE cert = :cert
        """), {"cert": int(cert)}).fetchone()
    if r is None:
        return None
    return {
        "beta_nib": r[0],
        "beta_ib_core": r[1],
        "beta_brokered": r[2],
        "sec_duration_yrs": r[3],
        "floating_loan_share": r[4],
        "fixed_loan_duration_yrs": r[5],
        "note": r[6],
        "updated_by": r[7],
        "updated_at": str(r[8]) if r[8] is not None else None,
    }


def get_all_assumptions() -> dict[int, dict]:
    """Bulk read: {cert: fields} for every bank with saved overrides."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text("""
            SELECT cert, beta_nib, beta_ib_core, beta_brokered, sec_duration_yrs,
                   floating_loan_share, fixed_loan_duration_yrs
            FROM user_nim_assumptions
        """)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        out[int(r[0])] = {
            "beta_nib": r[1],
            "beta_ib_core": r[2],
            "beta_brokered": r[3],
            "sec_duration_yrs": r[4],
            "floating_loan_share": r[5],
            "fixed_loan_duration_yrs": r[6],
        }
    return out


def delete_assumptions(cert: int) -> int:
    """Remove a bank's overrides so it reverts to auto-defaults."""
    from sqlalchemy import text
    if not cert:
        return 0
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM user_nim_assumptions WHERE cert = :cert"
        ), {"cert": int(cert)})
    return 1
