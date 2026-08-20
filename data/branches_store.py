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
  • haversine_miles(...)         — pure great-circle distance
  • get_nearest_branches(...)    — other-bank branches nearest a point
  • get_branch_competitors(...)  — competitor branches within a radius of
                                   each subject-bank branch
"""

from __future__ import annotations
import json
import math
from datetime import datetime
from typing import Iterable

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
    init_branches_schema()
    return _engine


def init_branches_schema():
    """Create the branches table if it doesn't exist. Idempotent."""
    from sqlalchemy import text
    from data.db import get_engine

    eng = get_engine()
    if _USE_POSTGRES:
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
        ts_col = "TIMESTAMP WITH TIME ZONE"
    else:
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


def get_branches_by_county(stcntybr: str, tickers: list[str] | None = None,
                            year: int | None = None) -> pd.DataFrame:
    """All branches in a county (5-digit state+county FIPS, STCNTYBR), optionally
    filtered to a ticker subset."""
    params = {"stcntybr": str(stcntybr)}
    sql = "SELECT * FROM branches WHERE stcntybr = :stcntybr"
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


def get_banks_by_county(stcntybr: str, year: int | None = None) -> pd.DataFrame:
    """Aggregated: total deposits + branch count per bank in a county."""
    params = {"stcntybr": str(stcntybr)}
    extra = " AND year = :year" if year else ""
    if year:
        params["year"] = year
    sql = f"""
        SELECT ticker, bank_name,
               COUNT(*) AS n_branches,
               SUM(deposits) AS total_deposits,
               MAX(county) AS county, MAX(state) AS state
        FROM branches
        WHERE stcntybr = :stcntybr {extra}
        GROUP BY ticker, bank_name
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, params)


def list_counties() -> pd.DataFrame:
    """List of (stcntybr, county, state) present, sorted by state then county."""
    return _q_to_df("""
        SELECT stcntybr, MAX(county) AS county, MAX(state) AS state
        FROM branches
        WHERE stcntybr != '' AND county != ''
        GROUP BY stcntybr
        ORDER BY MAX(state), MAX(county)
    """, {})


def get_latest_year() -> int | None:
    """Most recent SOD year present in the table."""
    df = _q_to_df("SELECT MAX(year) AS y FROM branches", {})
    if df.empty:
        return None
    return int(df["y"].iloc[0]) if df["y"].iloc[0] else None


def get_branch_counts_by_ticker() -> pd.DataFrame:
    """Coverage check: how many branches per ticker (latest year only).

    NOTE: every branch with no ticker collapses into ONE null-ticker row whose
    deposits are the SUM across all ~4,200 private banks — fine as a coverage
    diagnostic, misleading as a bank list. Use get_branch_counts_by_bank() for
    anything that presents banks to a user."""
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


def get_branch_counts_by_bank() -> pd.DataFrame:
    """One row per INSTITUTION for the latest SOD year: cert, ticker, bank_name,
    n_branches, total_deposits — deposits-descending.

    Keyed on cert, not ticker, so the ~4,200 private banks are first-class rows
    instead of collapsing into a single null-ticker aggregate. refresh_sod
    already ingests SOD for every active FDIC institution (ticker=None for the
    private ones), so this is purely a grouping change — no new data.

    MAX(bank_name) picks one name per cert: a bank that renamed mid-survey can
    carry two spellings across its branches, and GROUPing by name too would
    split one institution into two rows."""
    sql = """
        SELECT cert,
               MAX(ticker)    AS ticker,
               MAX(bank_name) AS bank_name,
               COUNT(*)       AS n_branches,
               SUM(deposits)  AS total_deposits
        FROM branches
        WHERE year = (SELECT MAX(year) FROM branches)
        GROUP BY cert
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, {})


def get_market_participants(cert: int, kind: str = "county",
                            year: int | None = None) -> pd.DataFrame:
    """All banks' aggregates in every market where `cert` operates —
    the input frame for the Deposit Market Share table (one row per
    market × bank). kind: 'county' (stcntybr) or 'msa' (msa_code).
    Deposits are SOD $thousands. Defaults to the latest stored year."""
    key = "stcntybr" if kind == "county" else "msa_code"
    label = ("MAX(b.county) || ', ' || MAX(b.state)" if kind == "county"
             else "MAX(b.msa_name)")
    params: dict = {"cert": int(cert)}
    if year:
        year_expr = ":year"
        params["year"] = int(year)
    else:
        year_expr = "(SELECT MAX(year) FROM branches)"
    sql = f"""
        SELECT b.{key} AS market_key,
               {label} AS market_label,
               b.cert AS cert,
               MAX(b.bank_name) AS bank_name,
               MAX(b.ticker) AS ticker,
               COUNT(*) AS n_branches,
               SUM(b.deposits) AS deposits
        FROM branches b
        WHERE b.year = {year_expr}
          AND b.{key} IS NOT NULL AND b.{key} NOT IN ('', '0')
          AND b.{key} IN (
              SELECT DISTINCT s.{key} FROM branches s
              WHERE s.cert = :cert AND s.year = {year_expr}
          )
        GROUP BY b.{key}, b.cert
        ORDER BY b.{key}, SUM(b.deposits) DESC
    """
    return _q_to_df(sql, params)


def has_branches(cert: int, year: int | None = None) -> bool:
    """True when the store holds at least one branch row for this cert
    (optionally restricted to a survey year)."""
    params: dict = {"cert": int(cert)}
    sql = "SELECT COUNT(*) AS n FROM branches WHERE cert = :cert"
    if year:
        sql += " AND year = :year"
        params["year"] = int(year)
    df = _q_to_df(sql, params)
    return bool(int(df["n"].iloc[0])) if not df.empty else False


# ──────────────────────────────────────────────────────────────────────────
# Geo helpers (Branch Proximity / Competitors)
# ──────────────────────────────────────────────────────────────────────────

_EARTH_RADIUS_MILES = 3958.7613          # mean Earth radius (6371.0088 km)
_MILES_PER_DEG_LAT = _EARTH_RADIUS_MILES * math.pi / 180.0   # ≈ 69.0934


def haversine_miles(lat1: float, lng1: float,
                    lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two (lat, lng) points, in
    degrees. Pure spherical haversine on the mean Earth radius."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2)
    return 2.0 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(min(1.0, a)))


def _bbox(lat: float, lng: float, radius_miles: float
          ) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lng_min, lng_max) box CONTAINING the radius circle
    around (lat, lng). Longitude width scales by 1/cos(lat) (clamped away
    from the poles) and the whole box is inflated 0.5% so the SQL prefilter
    only ever over-covers — it must never drop a point inside the radius;
    the exact haversine filter in Python does the final cut."""
    r = radius_miles * 1.005
    dlat = r / _MILES_PER_DEG_LAT
    coslat = max(math.cos(math.radians(lat)), 0.01)
    dlng = r / (_MILES_PER_DEG_LAT * coslat)
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def _count_missing_coords(cert: int, year: int) -> int:
    """Other-bank rows in the survey year with no usable lat/lng anywhere in
    the store — a coverage figure: these rows cannot be evaluated for
    distance and are EXCLUDED from geo results, never treated as far away."""
    df = _q_to_df(
        "SELECT COUNT(*) AS n FROM branches "
        "WHERE year = :year AND cert != :cert "
        "  AND (lat IS NULL OR lng IS NULL)",
        {"year": int(year), "cert": int(cert)},
    )
    return int(df["n"].iloc[0]) if not df.empty else 0


def get_nearest_branches(cert: int, lat: float, lng: float,
                         limit: int = 25, max_miles: float = 25.0,
                         year: int | None = None) -> dict:
    """Nearest OTHER-bank branches to a point, latest survey year by default.

    Returns a dict:
      branches         — DataFrame of branch rows (all `branches` columns)
                         + `distance_miles`, nearest first, at most `limit`
                         rows within `max_miles`. Empty (no columns
                         guaranteed) when nothing matches.
      n_missing_coords — store-wide count of other-bank rows in the year
                         lacking lat/lng (excluded from the search — honest
                         coverage, see _count_missing_coords).
      year             — survey year used; None (with empty result) when
                         the store is empty.

    SQL does a bounding-box prefilter (never a national scan); exact
    haversine + radius cut happen in Python.
    """
    if year is None:
        year = get_latest_year()
    if year is None:
        return {"branches": pd.DataFrame(), "n_missing_coords": 0,
                "year": None}
    lat_min, lat_max, lng_min, lng_max = _bbox(lat, lng, max_miles)
    cand = _q_to_df(
        """
        SELECT * FROM branches
        WHERE year = :year AND cert != :cert
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND lat BETWEEN :lat_min AND :lat_max
          AND lng BETWEEN :lng_min AND :lng_max
        """,
        {"year": int(year), "cert": int(cert),
         "lat_min": lat_min, "lat_max": lat_max,
         "lng_min": lng_min, "lng_max": lng_max},
    )
    n_missing = _count_missing_coords(cert, year)
    if cand.empty:
        return {"branches": cand, "n_missing_coords": n_missing,
                "year": int(year)}
    cand = cand.assign(distance_miles=[
        haversine_miles(lat, lng, float(r.lat), float(r.lng))
        for r in cand.itertuples(index=False)
    ])
    out = (cand[cand["distance_miles"] <= max_miles]
           .sort_values("distance_miles")
           .head(int(limit))
           .reset_index(drop=True))
    return {"branches": out, "n_missing_coords": n_missing,
            "year": int(year)}


_COMPETITOR_PAIR_COLS = [
    "subj_brnum", "subj_branch_name", "subj_address", "subj_city",
    "subj_state", "subj_lat", "subj_lng", "subj_deposits",
    "cert", "ticker", "bank_name", "branch_name", "address", "city",
    "state", "zip", "deposits", "lat", "lng", "serv_type",
    "distance_miles",
]


def get_branch_competitors(cert: int, radius_miles: float = 5.0,
                           year: int | None = None) -> dict:
    """Competitor branches within `radius_miles` of EACH subject-bank branch.

    Returns a dict:
      pairs            — flat DataFrame, one row per (subject branch,
                         competitor branch) pair within the radius; columns
                         _COMPETITOR_PAIR_COLS: subject branch keyed by the
                         subj_* prefix, competitor branch columns unprefixed,
                         plus distance_miles. Sorted by (subj_brnum,
                         distance_miles) — the UI groups on subj_brnum.
      n_subject_branches       — subject branch rows in the year
      n_subject_missing_coords — subject branches lacking lat/lng (excluded
                                 as search centers, counted honestly)
      n_competitor_missing_coords — other-bank rows in the year lacking
                                 lat/lng (excluded, counted)
      year             — survey year used
      reason           — why pairs is empty when it is, else None

    One SQL fetch prefiltered to the union bounding box of the subject's
    branch circles (small for a regional bank; approaches the footprint for
    a national one — never an unconditional national scan), then per-branch
    bounding-box + exact haversine refinement in Python.
    """
    empty = pd.DataFrame(columns=_COMPETITOR_PAIR_COLS)
    if year is None:
        year = get_latest_year()
    if year is None:
        return {"pairs": empty, "n_subject_branches": 0,
                "n_subject_missing_coords": 0,
                "n_competitor_missing_coords": 0,
                "year": None, "reason": "branches store is empty"}
    subj = _q_to_df(
        "SELECT * FROM branches WHERE cert = :cert AND year = :year",
        {"cert": int(cert), "year": int(year)},
    )
    n_missing_comp = _count_missing_coords(cert, year)
    if subj.empty:
        return {"pairs": empty, "n_subject_branches": 0,
                "n_subject_missing_coords": 0,
                "n_competitor_missing_coords": n_missing_comp,
                "year": int(year),
                "reason": f"no SOD branches for cert {int(cert)} "
                          f"in {int(year)}"}
    with_coords = subj[subj["lat"].notna() & subj["lng"].notna()]
    n_subj_missing = len(subj) - len(with_coords)
    if with_coords.empty:
        return {"pairs": empty, "n_subject_branches": len(subj),
                "n_subject_missing_coords": n_subj_missing,
                "n_competitor_missing_coords": n_missing_comp,
                "year": int(year),
                "reason": "no subject branches with coordinates"}
    boxes = [
        (float(r.brnum), _bbox(float(r.lat), float(r.lng), radius_miles))
        for r in with_coords.itertuples(index=False)
    ]
    lat_min = min(b[1][0] for b in boxes)
    lat_max = max(b[1][1] for b in boxes)
    lng_min = min(b[1][2] for b in boxes)
    lng_max = max(b[1][3] for b in boxes)
    cand = _q_to_df(
        """
        SELECT * FROM branches
        WHERE year = :year AND cert != :cert
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND lat BETWEEN :lat_min AND :lat_max
          AND lng BETWEEN :lng_min AND :lng_max
        """,
        {"year": int(year), "cert": int(cert),
         "lat_min": lat_min, "lat_max": lat_max,
         "lng_min": lng_min, "lng_max": lng_max},
    )
    rows: list[dict] = []
    if not cand.empty:
        for s in with_coords.itertuples(index=False):
            s_lat, s_lng = float(s.lat), float(s.lng)
            b_lat_min, b_lat_max, b_lng_min, b_lng_max = _bbox(
                s_lat, s_lng, radius_miles)
            near = cand[cand["lat"].between(b_lat_min, b_lat_max)
                        & cand["lng"].between(b_lng_min, b_lng_max)]
            for c in near.itertuples(index=False):
                d = haversine_miles(s_lat, s_lng, float(c.lat), float(c.lng))
                if d > radius_miles:
                    continue
                rows.append({
                    "subj_brnum": int(s.brnum),
                    "subj_branch_name": s.branch_name,
                    "subj_address": s.address,
                    "subj_city": s.city,
                    "subj_state": s.state,
                    "subj_lat": s_lat,
                    "subj_lng": s_lng,
                    "subj_deposits": s.deposits,
                    "cert": int(c.cert),
                    "ticker": c.ticker,
                    "bank_name": c.bank_name,
                    "branch_name": c.branch_name,
                    "address": c.address,
                    "city": c.city,
                    "state": c.state,
                    "zip": c.zip,
                    "deposits": c.deposits,
                    "lat": float(c.lat),
                    "lng": float(c.lng),
                    "serv_type": c.serv_type,
                    "distance_miles": d,
                })
    pairs = (pd.DataFrame(rows, columns=_COMPETITOR_PAIR_COLS)
             .sort_values(["subj_brnum", "distance_miles"])
             .reset_index(drop=True))
    return {"pairs": pairs, "n_subject_branches": len(subj),
            "n_subject_missing_coords": n_subj_missing,
            "n_competitor_missing_coords": n_missing_comp,
            "year": int(year),
            "reason": None if rows else
            f"no competitor branches within {radius_miles} miles"}
