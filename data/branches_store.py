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
    filed_cert      INTEGER  — set only on a branch RE-ATTRIBUTED to its
                               post-survey owner: the charter that filed it
    filed_bank_name TEXT     — the filer's name, where bank_name now carries
                               the current owner's
    owner_since     TEXT     — merger effective date (YYYY-MM-DD) for a
                               re-attributed branch
    PRIMARY KEY (cert, brnum, year)
  )

OWNERSHIP MODEL. `cert` is the charter that owns the branch TODAY. The SOD
survey is as of June 30; when a whole-bank merger closes after it, the
absorbed charter's branches are re-attributed to the surviving charter by
jobs/refresh_sod (brnum := -UNINUMBR, FDIC's nationally unique branch id —
never collides with the owner's own non-negative BRNUMs), with the filer kept
in filed_cert/filed_bank_name. Bank-level views then group by OWNER: a public
company's charters together (its ticker — M&T runs two, WTFC sixteen), a
private bank by its cert. Every such query goes through _OWNER_KEY so no view
can silently count one company as several banks (the Beacon Financial "28
branches" / duplicate-MTB picker reports, 2026-09-16).

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
import math

import pandas as pd

from data.db import USE_POSTGRES as _USE_POSTGRES

_engine = None

_OWNERSHIP_COLUMNS = (("filed_cert", "INTEGER"), ("filed_bank_name", "TEXT"),
                      ("owner_since", "TEXT"))

# The ONE bank-identity expression for grouping: a public company's charters
# share its ticker; a private bank is its own (owning) cert. Valid SQL in both
# Postgres and SQLite. Pass a table alias prefix ("b.") where needed.
def _owner_key(prefix: str = "") -> str:
    return (f"COALESCE(NULLIF({prefix}ticker, ''), "
            f"'c' || CAST({prefix}cert AS TEXT))")


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

    _canon_memo.clear()
    eng = get_engine()
    if _USE_POSTGRES:
        ts_default = "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
    else:
        ts_default = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

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
        # Ownership columns (2026-09-16) on tables created before them.
        # Additive + nullable: existing rows read as "filed by their owner".
        if _USE_POSTGRES:
            for col, typ in _OWNERSHIP_COLUMNS:
                conn.execute(text(
                    f"ALTER TABLE branches ADD COLUMN IF NOT EXISTS {col} {typ}"))
        else:
            have = {r[1] for r in conn.execute(
                text("PRAGMA table_info(branches)")).fetchall()}
            for col, typ in _OWNERSHIP_COLUMNS:
                if col not in have:
                    conn.execute(text(
                        f"ALTER TABLE branches ADD COLUMN {col} {typ}"))


def upsert_branches(ticker: str, cert: int, df: pd.DataFrame) -> int:
    """
    Bulk insert/replace branch rows for one bank.

    df comes from sod_client.fetch_branches(). Returns count written.
    """
    from sqlalchemy import text

    if df is None or df.empty:
        return 0

    eng = _get_engine()
    _canon_memo.clear()

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
                  -- A row renamed to its post-merger owner's name keeps it;
                  -- the refreshed as-filed name lands in filed_bank_name.
                  bank_name = CASE WHEN branches.filed_bank_name IS NULL
                                   THEN EXCLUDED.bank_name
                                   ELSE branches.bank_name END,
                  filed_bank_name = CASE WHEN branches.filed_bank_name IS NULL
                                         THEN NULL
                                         ELSE EXCLUDED.bank_name END,
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

def reattribute_absorbed_branches(absorbed_cert: int, owner_cert: int,
                                  owner_ticker: str | None, owner_name: str,
                                  owner_since: str, df: pd.DataFrame,
                                  year: int) -> dict:
    """Write an absorbed charter's survey-year branches under their CURRENT
    owner, atomically (see OWNERSHIP MODEL in the module docstring):

      • each branch lands as cert=owner_cert, brnum=-UNINUMBR, ticker and
        bank_name of the owner, filed_cert/filed_bank_name/owner_since kept;
      • every row still stored under the absorbed cert for that year is
        deleted (it is not a bank any more — left in place it would render as
        a separate, unlinked institution);
      • the owner's own as-filed rows take the owner's current name (old name
        kept in filed_bank_name) so the company reads as ONE bank.

    `df` is sod_client.fetch_branches(absorbed_cert, year). Idempotent.
    Returns {written, skipped_no_uninumbr, renamed}."""
    from sqlalchemy import text

    if df is None or df.empty or int(absorbed_cert) == int(owner_cert):
        return {"written": 0, "skipped_no_uninumbr": 0, "renamed": 0}

    def _num(v):
        try:
            f = float(v)
            return None if f != f else f
        except (TypeError, ValueError):
            return None

    rows, skipped = [], 0
    for rd in df.to_dict("records"):
        uni = _num(rd.get("UNINUMBR"))
        if uni is None:
            skipped += 1                   # no stable key — never guess one
            continue
        dep = _num(rd.get("DEPSUMBR"))
        rows.append({
            "cert": int(owner_cert), "brnum": -int(uni), "year": int(year),
            "ticker": owner_ticker.upper() if owner_ticker else None,
            "bank_name": owner_name[:500],
            "branch_name": str(rd.get("NAMEBR") or "")[:500],
            "address": str(rd.get("ADDRESBR") or "")[:500],
            "city": str(rd.get("CITYBR") or "")[:200],
            "state": str(rd.get("STALPBR") or "")[:2],
            "zip": str(rd.get("ZIPBR") or "")[:10],
            "county": str(rd.get("CNTYNAMB") or "")[:200],
            "stcntybr": str(rd.get("STCNTYBR") or "")[:10],
            "msa_code": str(rd.get("MSABR") or "")[:10],
            "msa_name": str(rd.get("MSANAMB") or "")[:500],
            "deposits": int(dep) if dep is not None else 0,
            "lat": _num(rd.get("SIMS_LATITUDE")),
            "lng": _num(rd.get("SIMS_LONGITUDE")),
            "serv_type": str(rd.get("BRSERTYP") or "")[:10],
            "filed_cert": int(absorbed_cert),
            "filed_bank_name": str(rd.get("NAMEFULL") or "")[:500],
            "owner_since": owner_since,
        })
    cols = ("cert, brnum, year, ticker, bank_name, branch_name, address, city, "
            "state, zip, county, stcntybr, msa_code, msa_name, deposits, lat, "
            "lng, serv_type, filed_cert, filed_bank_name, owner_since")
    vals = ", ".join(":" + c.strip() for c in cols.split(","))
    if _USE_POSTGRES:
        updates = ", ".join(f"{c.strip()} = EXCLUDED.{c.strip()}"
                            for c in cols.split(",")[3:])
        ins = text(f"INSERT INTO branches ({cols}) VALUES ({vals}) "
                   f"ON CONFLICT (cert, brnum, year) DO UPDATE SET {updates}, "
                   "ingested_at = NOW()")
    else:
        ins = text(f"INSERT OR REPLACE INTO branches ({cols}) VALUES ({vals})")

    eng = _get_engine()
    _canon_memo.clear()
    with eng.begin() as conn:
        if rows:
            conn.execute(ins, rows)
        conn.execute(text("DELETE FROM branches WHERE cert = :c AND year = :y"),
                     {"c": int(absorbed_cert), "y": int(year)})
        renamed = conn.execute(text(
            "UPDATE branches SET filed_bank_name = bank_name, "
            "bank_name = :name WHERE cert = :owner AND year = :y "
            "AND filed_cert IS NULL AND filed_bank_name IS NULL "
            "AND bank_name <> :name"),
            {"name": owner_name[:500], "owner": int(owner_cert),
             "y": int(year)}).rowcount
    return {"written": len(rows), "skipped_no_uninumbr": skipped,
            "renamed": int(renamed or 0)}


def retag_tickers(year: int, cert_to_ticker: dict[int, str]) -> int:
    """Converge stored tickers for a survey year to the current cert→ticker
    map (only where they differ). Tickers are the public-company grouping
    key, so a stale tag (a share-class sibling that won an old mapping race,
    a bank added to coverage) splits or mislabels a company until the monthly
    full sweep — this runs nightly with the ownership pass. Only SETS tickers:
    a transient universe hiccup can never unlink banks. Returns rows changed."""
    from sqlalchemy import text
    if not cert_to_ticker:
        return 0
    eng = _get_engine()
    _canon_memo.clear()
    changed = 0
    with eng.begin() as conn:
        for cert, tk in cert_to_ticker.items():
            if not tk:
                continue
            changed += conn.execute(text(
                "UPDATE branches SET ticker = :t WHERE cert = :c AND year = :y "
                "AND (ticker IS NULL OR ticker <> :t)"),
                {"t": tk.upper(), "c": int(cert), "y": int(year)}).rowcount or 0
    return changed


def get_reattributed_certs(year: int) -> dict[int, int]:
    """{absorbed_cert: owner_cert} already re-attributed for `year` — lets the
    ownership pass skip re-fetching a charter from FDIC every night unless its
    owner has since changed (a chained merger)."""
    df = _q_to_df("SELECT DISTINCT filed_cert, cert FROM branches "
                  "WHERE year = :y AND filed_cert IS NOT NULL", {"y": int(year)})
    return {} if df.empty else {int(f): int(c) for f, c
                                in zip(df["filed_cert"], df["cert"])}


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


_CANON_TTL_S = 600
_canon_memo: dict[tuple[int, int], tuple[float, dict]] = {}


def _canonical_owners(year: int | None) -> dict[str, tuple[int, str]]:
    """{owner_key: (cert, bank_name)} for a survey year — each owner's LEAD
    charter, defined store-wide as its largest by deposits, so every view
    (rankings, picker, market share, merger screen) names and identifies a
    company the same way. Memoized briefly; the store changes nightly."""
    import time as _t
    if year is None:
        year = get_latest_year()
        if year is None:
            return {}
    # Keyed by engine too: a different database (tests, a re-pointed
    # process) must never be served another store's owners.
    memo_key = (id(_get_engine()), int(year))
    hit = _canon_memo.get(memo_key)
    if hit and _t.monotonic() - hit[0] < _CANON_TTL_S:
        return hit[1]
    df = _q_to_df(f"""
        SELECT {_owner_key()} AS owner_key, cert,
               MAX(bank_name) AS bank_name, SUM(deposits) AS dep
        FROM branches WHERE year = :year
        GROUP BY {_owner_key()}, cert
    """, {"year": int(year)})
    out: dict[str, tuple[int, str]] = {}
    if not df.empty:
        df["dep"] = pd.to_numeric(df["dep"], errors="coerce").fillna(0)
        top = (df.sort_values(["dep", "cert"], ascending=[False, True])
                 .drop_duplicates("owner_key"))
        out = {r.owner_key: (int(r.cert), r.bank_name)
               for r in top.itertuples(index=False)}
    _canon_memo[memo_key] = (_t.monotonic(), out)
    return out


def _banks_where(where_sql: str, params: dict, year: int | None,
                 extra_cols: str = "") -> pd.DataFrame:
    """Bank-level aggregate over the rows matching `where_sql`: one row per
    OWNER (see module docstring) with ticker, bank_name, cert (the owner's
    lead charter), n_branches, total_deposits, deposits-descending."""
    if year:
        where_sql += " AND year = :year"
        params = {**params, "year": year}
    df = _q_to_df(f"""
        SELECT {_owner_key()} AS owner_key,
               MAX(ticker) AS ticker,
               MIN(cert) AS cert,
               MAX(bank_name) AS bank_name,
               COUNT(*) AS n_branches,
               SUM(deposits) AS total_deposits{extra_cols}
        FROM branches
        WHERE {where_sql}
        GROUP BY {_owner_key()}
        ORDER BY total_deposits DESC
    """, params)
    if df.empty:
        return df
    canon = _canonical_owners(year)
    df["cert"] = [canon.get(k, (c, n))[0]
                  for k, c, n in zip(df["owner_key"], df["cert"], df["bank_name"])]
    df["bank_name"] = [canon.get(k, (c, n))[1]
                       for k, c, n in zip(df["owner_key"], df["cert"], df["bank_name"])]
    return df


def get_banks_by_state(state: str, year: int | None = None) -> pd.DataFrame:
    """Aggregated: total deposits + branch count per bank (owner) in a state."""
    return _banks_where("state = :state", {"state": state.upper()}, year)


def get_banks_by_msa(msa_code: str, year: int | None = None) -> pd.DataFrame:
    """Aggregated: total deposits + branch count per bank (owner) in an MSA."""
    return _banks_where("msa_code = :msa_code", {"msa_code": str(msa_code)},
                        year, extra_cols=",\n               MAX(msa_name) AS msa_name")


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
    """Aggregated: total deposits + branch count per bank (owner) in a county."""
    return _banks_where("stcntybr = :stcntybr", {"stcntybr": str(stcntybr)},
                        year, extra_cols=",\n               MAX(county) AS county,"
                                         " MAX(state) AS state")


def list_counties() -> pd.DataFrame:
    """List of (stcntybr, county, state) present, sorted by state then county."""
    return _q_to_df("""
        SELECT stcntybr, MAX(county) AS county, MAX(state) AS state
        FROM branches
        WHERE stcntybr != '' AND county != ''
        GROUP BY stcntybr
        ORDER BY MAX(state), MAX(county)
    """, {})


def get_bank_footprint(cert: int) -> tuple[pd.DataFrame, list[dict]]:
    """A bank's CURRENT branch footprint (latest stored survey) plus the
    provenance notes a caption needs to be honest about it.

    Returns (roster, notes). roster = get_owner_branches(cert): every branch
    of the owning company — sibling charters and branches re-attributed from
    charters absorbed after the survey date. notes lists what the roster
    includes beyond the cert's own filing:
      {kind: "merged",  name, cert, date, n_branches} — an absorbed charter
      {kind: "charter", name, cert, n_branches}       — a sibling charter
    Read-only: re-attribution is built by jobs/refresh_sod, never on render."""
    roster = get_owner_branches(int(cert))
    if roster.empty:
        return roster, []
    notes: list[dict] = []
    if "filed_cert" in roster.columns:
        merged = roster[roster["filed_cert"].notna()]
        for (fc, fname, since), grp in merged.groupby(
                ["filed_cert", "filed_bank_name", "owner_since"], dropna=False):
            notes.append({"kind": "merged", "name": fname or f"cert {int(fc)}",
                          "cert": int(fc), "date": since,
                          "n_branches": len(grp)})
    for c, grp in roster.groupby("cert"):
        if int(c) == int(cert):
            continue
        own = grp[grp["filed_cert"].isna()] if "filed_cert" in grp.columns else grp
        if own.empty:
            continue
        notes.append({"kind": "charter", "name": own["bank_name"].iloc[0],
                      "cert": int(c), "n_branches": len(own)})
    return roster, notes


def get_branches_by_cert(cert: int, year: int | None = None) -> pd.DataFrame:
    """One bank's full branch roster (latest survey year for that cert unless
    `year` given): branch name/address/geo/deposits/lat/lng, deposits desc.
    Powers the Market Analysis branch tabs (list / map / proximity)."""
    params: dict = {"cert": int(cert)}
    if year is None:
        year = _cert_year(int(cert))
        if year is None:
            return pd.DataFrame()
    params["year"] = year
    sql = """
        SELECT brnum, branch_name, address, city, state, zip, county,
               stcntybr, msa_code, msa_name, deposits, lat, lng, year
        FROM branches
        WHERE cert = :cert AND year = :year
        ORDER BY deposits DESC NULLS LAST
    """
    if not _USE_POSTGRES:                      # sqlite: no NULLS LAST syntax
        sql = sql.replace("ORDER BY deposits DESC NULLS LAST",
                          "ORDER BY deposits IS NULL, deposits DESC")
    return _q_to_df(sql, params)


# A survey year serves only once it holds at least this share of the
# largest year's rows. Year-over-year branch counts move a few percent
# (closures, consolidation), so a genuinely ingested survey clears it; the
# handful of rows the nightly ownership pass writes into a brand-new survey
# year never does.
_SERVING_YEAR_MIN_SHARE = 0.9


def _cert_year(cert: int) -> int | None:
    """The survey year to show for one institution: the serving year when the
    cert has rows in it, else that cert's own latest year (never a partial
    newer year for a bank the serving survey covers)."""
    serving = get_latest_year()
    if serving is not None:
        hit = _q_to_df("SELECT COUNT(*) AS n FROM branches "
                       "WHERE cert = :cert AND year = :year",
                       {"cert": int(cert), "year": int(serving)})
        if not hit.empty and int(hit["n"].iloc[0]) > 0:
            return int(serving)
    ydf = _q_to_df("SELECT MAX(year) AS y FROM branches WHERE cert = :cert",
                   {"cert": int(cert)})
    if ydf.empty or pd.isna(ydf.iloc[0]["y"]):
        return None
    return int(ydf.iloc[0]["y"])


def get_latest_year() -> int | None:
    """The SOD survey year every view serves: the most recent year that is
    COMPLETE in the store, not merely present.

    FDIC began publishing the 2026 survey ~2026-09-18; the nightly ownership
    pass then wrote 440 re-attributed branches into 2026 while the full sweep
    (monthly) had not ingested it, and "MAX(year)" switched every page to a
    nearly empty year — Geographic showed 0 branches for California for
    ~2.5 days (found 2026-09-21). A year serves once it holds
    _SERVING_YEAR_MIN_SHARE of the largest year's rows."""
    df = _q_to_df("SELECT year, COUNT(*) AS n FROM branches GROUP BY year", {})
    if df.empty:
        return None
    df = df.dropna()
    if df.empty:
        return None
    df["n"] = df["n"].astype(int)
    full = df[df["n"] >= _SERVING_YEAR_MIN_SHARE * df["n"].max()]
    return int(full["year"].max())


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
        WHERE year = :year
        GROUP BY ticker
        ORDER BY total_deposits DESC
    """
    return _q_to_df(sql, {"year": get_latest_year()})


def get_branch_counts_by_bank() -> pd.DataFrame:
    """One row per BANK (owner) for the latest SOD year: cert (the owner's
    lead charter), ticker, bank_name, n_branches, total_deposits, n_charters —
    deposits-descending.

    A private bank is keyed by its cert, so the ~4,200 private banks are
    first-class rows instead of collapsing into a single null-ticker
    aggregate. A public company's charters collapse into ONE row: the picker
    listed M&T Bank and Wilmington Trust as two "MTB" entries, each showing
    part of the footprint (owner report 2026-09-16)."""
    year = get_latest_year()
    if year is None:
        return pd.DataFrame()
    return _banks_where("1 = 1", {}, year,
                        extra_cols=",\n               COUNT(DISTINCT cert) AS n_charters")


def get_market_participants(cert: int, kind: str = "county",
                            year: int | None = None) -> pd.DataFrame:
    """All banks' aggregates in every market where `cert` operates —
    the input frame for the Deposit Market Share table (one row per
    market × bank). kind: 'county' (stcntybr) or 'msa' (msa_code).
    Deposits are SOD $thousands. Defaults to the latest stored year."""
    key = "stcntybr" if kind == "county" else "msa_code"
    label = ("MAX(b.county) || ', ' || MAX(b.state)" if kind == "county"
             else "MAX(b.msa_name)")
    y = int(year) if year else get_latest_year()
    if y is None:
        return pd.DataFrame()
    okey = _owner_key_of(int(cert), y)
    sql = f"""
        SELECT b.{key} AS market_key,
               {label} AS market_label,
               {_owner_key('b.')} AS owner_key,
               MIN(b.cert) AS cert,
               MAX(b.bank_name) AS bank_name,
               MAX(b.ticker) AS ticker,
               COUNT(*) AS n_branches,
               SUM(b.deposits) AS deposits
        FROM branches b
        WHERE b.year = :year
          AND b.{key} IS NOT NULL AND b.{key} NOT IN ('', '0')
          AND b.{key} IN (
              SELECT DISTINCT s.{key} FROM branches s
              WHERE {_owner_key('s.')} = :okey AND s.year = :year
          )
        GROUP BY b.{key}, {_owner_key('b.')}
        ORDER BY b.{key}, SUM(b.deposits) DESC
    """
    df = _q_to_df(sql, {"year": y, "okey": okey})
    if df.empty:
        return df
    # One row per market x OWNER (a company's charters are one participant —
    # HHI and rank are holding-company concepts). `cert` identifies the
    # participant: the caller's own cert for the subject (callers compare
    # against it), the owner's lead charter for everyone else.
    canon = _canonical_owners(y)
    certs, names = [], []
    for k, c, n in zip(df["owner_key"], df["cert"], df["bank_name"]):
        lead_c, lead_n = canon.get(k, (int(c), n))
        certs.append(int(cert) if k == okey else lead_c)
        names.append(lead_n)
    df["cert"], df["bank_name"] = certs, names
    return df


def _owner_key_of(cert: int, year: int) -> str:
    """The owner key for a cert's rows in a survey year (ticker when public,
    else 'c<cert>'). A cert with no rows keys as itself."""
    df = _q_to_df(f"SELECT MAX({_owner_key()}) AS k FROM branches "
                  "WHERE cert = :cert AND year = :year",
                  {"cert": int(cert), "year": int(year)})
    k = None if df.empty else df["k"].iloc[0]
    return k if isinstance(k, str) and k else f"c{int(cert)}"


def get_owner_branches(cert: int, year: int | None = None) -> pd.DataFrame:
    """Every branch the bank identified by `cert` owns: all of its company's
    charters (by ticker) including branches re-attributed from charters
    absorbed after the survey — the roster behind every Company-page branch
    view. Latest survey year that has rows for the cert unless `year` given.
    All columns, deposits-descending."""
    if year is None:
        year = _cert_year(int(cert))
        if year is None:
            return pd.DataFrame()
    okey = _owner_key_of(int(cert), int(year))
    sql = f"""
        SELECT * FROM branches
        WHERE {_owner_key()} = :okey AND year = :year
        ORDER BY deposits DESC NULLS LAST
    """
    if not _USE_POSTGRES:                      # sqlite: no NULLS LAST syntax
        sql = sql.replace("ORDER BY deposits DESC NULLS LAST",
                          "ORDER BY deposits IS NULL, deposits DESC")
    return _q_to_df(sql, {"okey": okey, "year": int(year)})


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
        f"WHERE year = :year AND {_owner_key()} <> :okey "
        "  AND (lat IS NULL OR lng IS NULL)",
        {"year": int(year), "okey": _owner_key_of(int(cert), int(year))},
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
    # "Other bank" = a different OWNER: a company's sibling charters and
    # re-attributed branches are never its own competitors.
    cand = _q_to_df(
        f"""
        SELECT * FROM branches
        WHERE year = :year AND {_owner_key()} <> :okey
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND lat BETWEEN :lat_min AND :lat_max
          AND lng BETWEEN :lng_min AND :lng_max
        """,
        {"year": int(year), "okey": _owner_key_of(int(cert), int(year)),
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
    "cert", "brnum", "ticker", "bank_name", "branch_name", "address",
    "city", "state", "zip", "deposits", "lat", "lng", "serv_type",
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
    okey = _owner_key_of(int(cert), int(year))
    subj = _q_to_df(
        f"SELECT * FROM branches WHERE {_owner_key()} = :okey AND year = :year",
        {"okey": okey, "year": int(year)},
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
        f"""
        SELECT * FROM branches
        WHERE year = :year AND {_owner_key()} <> :okey
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND lat BETWEEN :lat_min AND :lat_max
          AND lng BETWEEN :lng_min AND :lng_max
        """,
        {"year": int(year), "okey": okey,
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
                    "brnum": int(c.brnum),
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
