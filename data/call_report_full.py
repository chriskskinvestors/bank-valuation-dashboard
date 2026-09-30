"""
Full Call Report store — EVERY line item of every stored bank-quarter.

jobs/refresh_ffiec.py already downloads each universe bank's entire Call
Report (data/ffiec_client.fetch_call_report: one DataFrame row per MDRM
code). This module persists that whole frame in long format so any Call
Report item can be screened, instead of only the handful of schedules
data/call_report_store.py extracts.

Keying: the RAW MDRM code (mnemonic + item, e.g. RCFDB530 vs RCONB530).
RCFD (consolidated, FFIEC 031 filers) and RCON (domestic offices) are
different items and are never merged — unlike ffiec_client._lookup_concept,
which picks the larger of the two for its fixed-schedule parsers. Codes are
stored upper-case.

Values — verified in ffiec-data-connect 3.0.0
(xbrl_processor._process_xbrl_item, the schema fetch_call_report returns):
  • data_type 'int'   — the XBRL unit was USD; the package integer-divides
                        by 1,000, so the value is $THOUSANDS (the FDIC/FFIEC
                        convention — callers scale ×1000 at their boundary).
  • data_type 'float' — unit PURE or NON-MONETARY (ratios, percentages,
                        counts), stored as filed.
  • data_type 'bool' / 'str' — non-numeric (yes/no answers, TEXT labels).
                        Stored with value NULL.
  • A blank (nil) fact carries no value; the package types it 'str' (with
    str_data "None"), so it is stored NULL. Blank means "not reported" —
    never 0. A code absent from the filing has no row at all; values()
    returns None for both.

Table:
  call_report_full(
    cert        INTEGER NOT NULL  — FDIC certificate
    rssd_id     INTEGER NOT NULL  — Fed RSSD ID (FFIEC's key)
    report_date DATE NOT NULL     — quarter-end of the filing
    mdrm        VARCHAR(16) NOT NULL — raw MDRM code, e.g. 'RCONB530'
    value       DOUBLE PRECISION  — numeric value (see above) or NULL
    data_type   VARCHAR(8)        — the package's data_type
    PRIMARY KEY (cert, report_date, mdrm)
  )
Re-upserting a bank-quarter REPLACES all of its rows (delete + insert in one
transaction), so an amended filing that drops an item leaves no stale row.

Titles come from the Federal Reserve's Micro Data Reference Manual
(https://www.federalreserve.gov/apps/mdrm/pdf/MDRM.zip → MDRM_CSV.csv,
utf-8, a 'PUBLIC' banner line then a header row: Mnemonic, Item Code,
Start Date, End Date, Item Name, Confidentiality, ItemType, Reporting Form,
Description, ...). Verified 2026-09-30. The MDRM has NO schedule column, and
its free-text descriptions name schedules of other reports (RCFAP859's says
"Schedule HC-R", the FR Y-9C schedule), so catalog() reports schedule None
rather than guess; an authoritative code→schedule map needs the FFIEC
Call Report XBRL taxonomy (not wired). If the MDRM download is unreachable,
titles are None — never invented.

Public functions (the Stage-1 screener contract):
  • upsert_full_report(cert, rssd_id, report_date, df) -> int   rows written
  • values(codes, report_date, certs) -> {cert: {code: float | None}}
  • available_report_dates() -> ['yyyy-mm-dd', ...] newest first
  • catalog() -> [{"code", "title", "schedule", "unit"}]  numeric codes only
  • mdrm_titles() -> {code: title}   (cached; the job warms it)
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime

import pandas as pd

from data.call_report_store import _parse_period
from data.db import USE_POSTGRES as _USE_POSTGRES

MDRM_URL = "https://www.federalreserve.gov/apps/mdrm/pdf/MDRM.zip"
_MDRM_CACHE_KEY = "mdrm_call_report_titles_v1"
_MDRM_TTL_S = 30 * 24 * 3600
# Call Report forms in the MDRM "Reporting Form" column (031 = banks with
# foreign offices, 041 = domestic-only, 051 = small domestic filers).
_CALL_FORMS = {"FFIEC 031", "FFIEC 041", "FFIEC 051"}
_NUMERIC_TYPES = ("int", "float")
_UNIT = {"int": "usd_thousands", "float": "non_monetary"}
_IN_CHUNK = 500  # bound IN-list sizes (SQLite variable limit)

_engine = None
_table = None


def _get_engine():
    """Shared engine (data/db) + this store's first-use schema init."""
    global _engine
    if _engine is not None:
        return _engine
    from data.db import get_engine
    _engine = get_engine()
    init_full_schema()
    return _engine


def init_full_schema():
    """Create call_report_full + its screen index. Idempotent."""
    from sqlalchemy import text
    from data.db import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS call_report_full (
                cert        INTEGER NOT NULL,
                rssd_id     INTEGER NOT NULL,
                report_date DATE NOT NULL,
                mdrm        VARCHAR(16) NOT NULL,
                value       DOUBLE PRECISION,
                data_type   VARCHAR(8),
                PRIMARY KEY (cert, report_date, mdrm)
            )
        """))
        # Screen reads are "these codes, this quarter, all banks".
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_crfull_date_mdrm "
            "ON call_report_full(report_date, mdrm)"
        ))


def _tbl():
    """Core Table for bulk inserts — insert() batches into multi-row VALUES
    on Postgres (insertmanyvalues); text() executemany would go row-by-row."""
    global _table
    if _table is None:
        from sqlalchemy import (Column, Date, Float, Integer, MetaData,
                                String, Table)
        _table = Table(
            "call_report_full", MetaData(),
            Column("cert", Integer), Column("rssd_id", Integer),
            Column("report_date", Date), Column("mdrm", String(16)),
            Column("value", Float), Column("data_type", String(8)),
        )
    return _table


def _numeric(row) -> float | None:
    """The numeric value of one frame row, or None (blank / non-numeric).
    Same typed-column rule as ffiec_client._lookup_concept."""
    dt = str(row.get("data_type") or "").lower()
    if dt == "int":
        v = row.get("int_data")
    elif dt == "float":
        v = row.get("float_data")
    else:
        return None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _frame_quarters(df) -> set[str]:
    """ISO dates in the frame's own 'quarter' column (the XBRL context date,
    'M/D/YYYY' from the package)."""
    if "quarter" not in df.columns:
        return set()
    out = set()
    for q in df["quarter"].dropna().unique():
        ts = pd.to_datetime(str(q), errors="coerce")
        out.add("?" if pd.isna(ts) else ts.strftime("%Y-%m-%d"))
    return out


def upsert_full_report(cert: int, rssd_id: int, report_date: str, df) -> int:
    """Replace one bank-quarter's full Call Report. Returns rows written
    (0 for an empty/None frame — the existing rows are left untouched).

    Raises ValueError when the frame's own quarter differs from report_date:
    fetch_call_report silently falls back one quarter for late filers, and
    storing that frame under the requested date would mislabel every item.
    """
    if df is None or df.empty or "mdrm" not in df.columns:
        return 0
    iso = _parse_period(report_date)
    if not iso:
        raise ValueError(f"unparseable report_date {report_date!r}")
    quarters = _frame_quarters(df)
    if quarters and quarters != {iso}:
        raise ValueError(f"period mismatch: frame is {sorted(quarters)}, "
                         f"requested {iso}")

    rd = date.fromisoformat(iso)
    rows, seen = [], set()
    for rec in df.to_dict("records"):
        code = str(rec.get("mdrm") or "").strip().upper()
        if not code or code in seen:
            continue  # first occurrence wins, as in _lookup_concept
        seen.add(code)
        rows.append({
            "cert": int(cert), "rssd_id": int(rssd_id), "report_date": rd,
            "mdrm": code, "value": _numeric(rec),
            "data_type": (str(rec.get("data_type") or "")[:8] or None),
        })
    if not rows:
        return 0

    from sqlalchemy import text
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM call_report_full "
                          "WHERE cert = :c AND report_date = :d"),
                     {"c": int(cert), "d": iso})
        conn.execute(_tbl().insert(), rows)
    return len(rows)


def _iso(d) -> str:
    """DATE column → 'yyyy-mm-dd' (Postgres returns date, SQLite a string)."""
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def values(codes: list[str], report_date: str,
           certs: list[int]) -> dict[int, dict[str, float | None]]:
    """Batch read: {cert: {code: value}} for every requested cert × code.
    Anything not stored (bank, quarter, or code) or stored blank → None."""
    codes_u = list(dict.fromkeys(str(c).strip().upper() for c in codes if c))
    certs_i = list(dict.fromkeys(int(c) for c in certs))
    out = {c: {k: None for k in codes_u} for c in certs_i}
    iso = _parse_period(report_date)
    if not codes_u or not certs_i or not iso:
        return out

    from sqlalchemy import bindparam, text
    sql = text("""
        SELECT cert, mdrm, value FROM call_report_full
        WHERE report_date = :d AND mdrm IN :codes AND cert IN :certs
    """).bindparams(bindparam("codes", expanding=True),
                    bindparam("certs", expanding=True))
    eng = _get_engine()
    with eng.connect() as conn:
        for i in range(0, len(certs_i), _IN_CHUNK):
            for j in range(0, len(codes_u), _IN_CHUNK):
                for cert, code, v in conn.execute(sql, {
                        "d": iso, "codes": codes_u[j:j + _IN_CHUNK],
                        "certs": certs_i[i:i + _IN_CHUNK]}):
                    out[int(cert)][code] = None if v is None else float(v)
    return out


def available_report_dates() -> list[str]:
    """Quarter-ends with any stored full report, newest first (ISO)."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT report_date FROM call_report_full "
            "ORDER BY report_date DESC")).fetchall()
    return [_iso(r[0]) for r in rows]


# ── MDRM dictionary ─────────────────────────────────────────────────────

def _download_mdrm() -> bytes:
    """The MDRM zip bytes (raises on failure)."""
    from data.http import get_with_retry
    resp = get_with_retry(MDRM_URL, timeout=120)
    if resp is None:
        raise RuntimeError("MDRM download rate-limited")
    return resp.content


def _mdrm_date(s: str) -> tuple[int, int, int]:
    """'12/31/9999 12:00:00 AM' → (9999, 12, 31); unparseable sorts first."""
    try:
        m, d, y = str(s).split()[0].split("/")
        return int(y), int(m), int(d)
    except (ValueError, IndexError, AttributeError):
        return (0, 0, 0)


def _parse_mdrm_zip(blob: bytes) -> dict[str, str]:
    """{code: Item Name} for Call Report (FFIEC 031/041/051) items.

    A code has one MDRM row per (form, date range); the title is taken from
    its latest-ending, then latest-starting Call Report row. Item names are
    returned verbatim (the CSV writes commas as ';', e.g. '$250;000').
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text_ = z.read(name).decode("utf-8", errors="replace")
    lines = io.StringIO(text_)
    reader = csv.reader(lines)
    header = None
    for row in reader:  # skip the 'PUBLIC' banner line(s)
        if row and row[0].strip() == "Mnemonic":
            header = row
            break
    if header is None:
        raise ValueError("MDRM CSV header row not found")
    ix = {h.strip(): i for i, h in enumerate(header)}
    need = ("Mnemonic", "Item Code", "Start Date", "End Date",
            "Item Name", "Reporting Form")
    missing = [h for h in need if h not in ix]
    if missing:
        raise ValueError(f"MDRM CSV missing columns {missing}")

    best: dict[str, tuple] = {}
    for row in reader:
        if len(row) <= max(ix[h] for h in need):
            continue
        if row[ix["Reporting Form"]].strip() not in _CALL_FORMS:
            continue
        code = (row[ix["Mnemonic"]].strip()
                + row[ix["Item Code"]].strip()).upper()
        title = row[ix["Item Name"]].strip()
        if not code or not title:
            continue
        rank = (_mdrm_date(row[ix["End Date"]]),
                _mdrm_date(row[ix["Start Date"]]))
        if code not in best or rank > best[code][0]:
            best[code] = (rank, title)
    return {c: t for c, (_, t) in best.items()}


def mdrm_titles() -> dict[str, str]:
    """{code: MDRM item name} for Call Report items. Served from data/cache
    (30-day TTL); refreshed from the Fed when stale; a stale copy beats
    nothing when the download fails; {} when neither exists."""
    from data import cache
    hit = cache.get(_MDRM_CACHE_KEY, max_age_s=_MDRM_TTL_S)
    if hit:
        return hit
    try:
        titles = _parse_mdrm_zip(_download_mdrm())
        if titles:
            cache.put(_MDRM_CACHE_KEY, titles)
            return titles
    except Exception as e:
        print(f"[call_report_full] MDRM dictionary unavailable: "
              f"{type(e).__name__}: {str(e)[:120]}", flush=True)
    return cache.get(_MDRM_CACHE_KEY, max_age_s=None) or {}


def catalog() -> list[dict]:
    """Screenable codes: every code with a numeric value in at least one
    stored filing, sorted. title from the MDRM (None if unknown); schedule
    None (see module docstring); unit 'usd_thousands' (USD facts, $000) or
    'non_monetary' (ratios / counts as filed)."""
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT mdrm, MIN(data_type) FROM call_report_full
            WHERE data_type IN ('int', 'float')
            GROUP BY mdrm ORDER BY mdrm
        """)).fetchall()
    titles = mdrm_titles() if rows else {}
    return [{"code": code, "title": titles.get(code), "schedule": None,
             "unit": _UNIT.get(dt)} for code, dt in rows]
