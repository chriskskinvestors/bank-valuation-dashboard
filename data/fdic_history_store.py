"""Deep FDIC history store — full call-report depth (1992 →) per charter.

DEEP-HISTORY-PLAN.md foundation (owner-approved 2026-09-08: 1992 depth,
the 212 in-use fields, charts-first). One row per (cert, repdte) holding
the full record `fdic_client.fetch_financials` returns, stored as JSON.

Jobs build, renders read: `jobs/backfill_fdic_history.py` populates and
appends; `deep_group_history()` is the read path — it consolidates
multi-charter groups per REPDTE with the SAME aggregation as
`data/cert_group.fetch_group_history` (the mandatory seam), so WTFC-class
holdcos stay correct at depth without new math.

~620 certs × ~135 quarters × 212 fields ≈ 350-800MB — inside the existing
10GB Cloud SQL provision (cost brief 2026-09-08).
"""
from __future__ import annotations

import json

import math
import re

from data.db import USE_POSTGRES as _USE_POSTGRES


def _norm_repdte(raw) -> str | None:
    """Normalize any REPDTE rendering to 8-digit YYYYMMDD. The live API hands
    records whose REPDTE serializes longer than YYYYMMDD (second live
    backfill failure 2026-09-08: StringDataRightTruncation on varchar(8) —
    pandas renders the value as a full timestamp). Digits-only, first 8,
    sanity-checked century — anything else is unkeyable and dropped."""
    digits = re.sub(r"\D", "", str(raw or ""))[:8]
    if len(digits) == 8 and digits[:2] in ("19", "20"):
        return digits
    return None


def _strict_json(rec: dict) -> str:
    """Serialize a fetch_financials record as STRICT JSON. Pandas hands us
    float('nan') for absent fields and Python's json would emit a bare NaN
    token — invalid JSON that Postgres JSONB rejects (live backfill failure
    2026-09-08: InvalidTextRepresentation on every cert). NaN/±inf become
    null; allow_nan=False makes any future leak a loud error, not a stored
    corruption."""
    clean = {k: (None if isinstance(v, float)
                 and (math.isnan(v) or math.isinf(v)) else v)
             for k, v in rec.items()}
    return json.dumps(clean, default=str, allow_nan=False)

_engine = None


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    from data.db import get_engine
    _engine = get_engine()
    init_history_schema()
    return _engine


def init_history_schema() -> None:
    """Create the fdic_history table. Idempotent."""
    from sqlalchemy import text
    from data.db import get_engine
    eng = get_engine()
    fields_type = "JSONB" if _USE_POSTGRES else "TEXT"
    ts_default = ("TIMESTAMP WITH TIME ZONE DEFAULT NOW()" if _USE_POSTGRES
                  else "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    with eng.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS fdic_history (
                cert       INTEGER NOT NULL,
                repdte     VARCHAR(8) NOT NULL,
                fields     {fields_type} NOT NULL,
                updated_at {ts_default},
                PRIMARY KEY (cert, repdte)
            )
        """))


def upsert_history(cert: int, records: list[dict]) -> int:
    """Upsert fetch_financials rows for one cert. A record without a REPDTE
    is dropped (unkeyable). Rows are written in sorted repdte order
    (deterministic lock order — the price-upsert deadlock lesson).
    Returns rows written."""
    from sqlalchemy import text
    rows = []
    for rec in records or []:
        repdte = _norm_repdte(rec.get("REPDTE"))
        if not repdte:
            continue
        rows.append({"cert": int(cert), "repdte": repdte,
                     "fields": _strict_json(rec)})
    if not rows:
        return 0
    rows.sort(key=lambda r: r["repdte"])
    eng = _get_engine()
    with eng.begin() as conn:
        if _USE_POSTGRES:
            conn.execute(text("""
                INSERT INTO fdic_history (cert, repdte, fields, updated_at)
                VALUES (:cert, :repdte, CAST(:fields AS JSONB), NOW())
                ON CONFLICT (cert, repdte) DO UPDATE SET
                  fields = EXCLUDED.fields, updated_at = NOW()
            """), rows)
        else:
            for r in rows:
                conn.execute(text("""
                    INSERT OR REPLACE INTO fdic_history
                      (cert, repdte, fields, updated_at)
                    VALUES (:cert, :repdte, :fields, CURRENT_TIMESTAMP)
                """), r)
    return len(rows)


def get_cert_history(cert: int, limit: int | None = None) -> list[dict]:
    """Stored records for one cert, newest first."""
    from sqlalchemy import text
    eng = _get_engine()
    sql = ("SELECT fields FROM fdic_history WHERE cert = :cert "
           "ORDER BY repdte DESC")
    params: dict = {"cert": int(cert)}
    if limit:
        sql += " LIMIT :lim"
        params["lim"] = int(limit)
    with eng.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    out = []
    for (f,) in rows:
        rec = f if isinstance(f, dict) else json.loads(f)
        out.append(rec)
    return out


def max_repdte(cert: int) -> str | None:
    """Newest stored quarter for a cert (backfill/append checkpoint)."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT MAX(repdte) FROM fdic_history WHERE cert = :cert"),
            {"cert": int(cert)}).fetchone()
    return row[0] if row and row[0] else None


def min_repdte(cert: int) -> str | None:
    """Oldest stored quarter for a cert (deep-enough checkpoint)."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT MIN(repdte) FROM fdic_history WHERE cert = :cert"),
            {"cert": int(cert)}).fetchone()
    return row[0] if row and row[0] else None


def store_counts() -> tuple[int, int]:
    """(certs stored, total rows) — job summary / coverage checks."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(DISTINCT cert), COUNT(*) FROM fdic_history"
        )).fetchone()
    return (int(row[0] or 0), int(row[1] or 0))


def deep_group_history(ticker: str, limit: int | None = None,
                       cert: int | None = None) -> list[dict]:
    """The deep read path: stored history for the ticker's WHOLE banking
    operation, newest first, one consolidated record per REPDTE — the same
    single-charter passthrough / multi-charter aggregation contract as
    cert_group.fetch_group_history, applied to stored rows instead of live
    fetches. Returns [] when nothing is stored (caller falls back to the
    live 20-quarter path)."""
    from data.cert_group import aggregate_records, get_cert_group
    certs = get_cert_group(ticker, cert=cert)
    if not certs:
        return []
    if len(certs) == 1:
        return get_cert_history(certs[0], limit=limit)

    by_period: dict[str, list[dict]] = {}
    for c in certs:
        for rec in get_cert_history(c):
            period = str(rec.get("REPDTE") or "")
            if period:
                by_period.setdefault(period, []).append(rec)
    out = []
    for period in sorted(by_period, reverse=True):
        recs = sorted(by_period[period],
                      key=lambda r: -(float(r.get("ASSET") or 0)))
        out.append(aggregate_records(recs))
    return out[:limit] if limit else out
