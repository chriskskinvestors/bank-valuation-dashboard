"""
CFPB HMDA public data API client — mortgage origination data from the
primary source.

Powers the HMDA Mortgages / Mortgage Analytics sub-tabs (docs/
SNL-BUILD-PLAN.md §11). Aggregations only — never loan-level downloads.

Endpoints (all public, no auth; verified live 2026-06):
  • Aggregations — GET {HMDA_BASE}/data-browser-api/view/aggregations
      ?years=Y&leis=LEI&actions_taken=1[&loan_purposes=..|&loan_types=..|&states=XX]
    Rows group by the supplied *filter* params (keys mirror the param names,
    e.g. "loan_purposes"); geography params (states/counties/msamds) only
    FILTER — they never group, so per-state totals take one call per state
    (~0.15s each, verified). Serves years 2018–2024 (its own error message
    still says 2018-2023 — stale; 2024 serves).
  • Filers — GET {HMDA_BASE}/reporting/filers/{year}
    → {"institutions": [{lei, name, period}]}. Name → LEI search. No RSSD.
  • Institution detail — GET {HMDA_BASE}/public/institutions/{lei}/year/{year}
    → includes "rssd". LEI → RSSD only; the reverse (RSSD in the path) 404s,
    and the admin-api is auth-gated (both verified live). So RSSD → LEI goes
    RSSD → FDIC institutions (FED_RSSD filter) → name → filers match → LEI,
    then round-trips the candidate's rssd through this endpoint — a mismatch
    returns None, never a plausible-wrong LEI.

Values: "sum" is dollars, but approximate by design — HMDA reports each
loan_amount as the midpoint of a $10k bucket and the API rounds sums.
Counts are exact (state and purpose breakdowns re-add to the total
exactly; verified against Banner Bank 2024).

Cache: data.cache (``hmda:*`` keys), 30 days via the shared freshness
check — annual snapshots change once a year. The cache backend's own
global TTL (24h) expires entries first locally; worst case is a daily
refetch, and the 30d stamp stays correct if the backend TTL is raised.

Failures return None with one [hmda] log line — never a guess.
"""

from __future__ import annotations

from datetime import datetime

HMDA_BASE = "https://ffiec.cfpb.gov/v2"
AGGREGATIONS_URL = f"{HMDA_BASE}/data-browser-api/view/aggregations"
FILERS_URL = HMDA_BASE + "/reporting/filers/{year}"
INSTITUTION_URL = HMDA_BASE + "/public/institutions/{lei}/year/{year}"

# Newest year the aggregation API serves (verified live 2026-06). Bump
# annually when the new snapshot publishes (~June).
LATEST_YEAR = 2024

CACHE_TTL_SECONDS = 30 * 86400

ACTION_ORIGINATED = "1"  # actions_taken=1 — loan originated

# HMDA enumerations (Filing Instructions Guide) → our field names.
LOAN_PURPOSES = {
    "1": "purchase",
    "2": "home_improvement",
    "31": "refi",
    "32": "cash_out_refi",
    "4": "other",
    "5": "not_applicable",
}
LOAN_TYPES = {
    "1": "conventional",
    "2": "fha",
    "3": "va",
    "4": "usda_rhs",
}

# Two-letter codes for the per-state loop — reuse the house FIPS table's
# keys (50 states + DC + territories; HMDA covers the territories too).
from data.census_client import STATE_FIPS

STATES = tuple(STATE_FIPS)


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _get_json(url: str, params: dict | None, label: str):
    """One GET through the shared retry policy. Parsed JSON, or None
    (logged) on any failure."""
    try:
        from data.http import get_with_retry
        resp = get_with_retry(url, params=params, timeout=30)
        if resp is None:
            print(f"[hmda] {label}: retries exhausted (429)")
            return None
        return resp.json()
    except Exception as e:
        print(f"[hmda] {label} error: {type(e).__name__}: {e}")
        return None


def _agg_rows(lei: str, year: int, extra: dict, label: str) -> list[dict] | None:
    """Origination aggregation rows for one lender-year, grouped by the
    filter params in ``extra``. None (logged) on failure or bad shape."""
    params = {"years": year, "leis": lei,
              "actions_taken": ACTION_ORIGINATED, **extra}
    data = _get_json(AGGREGATIONS_URL, params, label)
    if data is None:
        return None
    rows = data.get("aggregations") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        print(f"[hmda] {label}: unexpected response shape")
        return None
    return rows


def _bucket(rows: list[dict], row_key: str, labels: dict) -> dict | None:
    """Shape grouped rows into {label: {count, volume_usd}}, every label
    present (absent enum = 0). None if a row is malformed."""
    out = {name: {"count": 0, "volume_usd": 0.0} for name in labels.values()}
    for row in rows:
        name = labels.get(str(row.get(row_key)))
        if name is None:  # unknown enum value — refuse to mislabel
            return None
        try:
            out[name] = {"count": int(row["count"]),
                         "volume_usd": float(row["sum"])}
        except (KeyError, TypeError, ValueError):
            return None
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_lender_originations(lei: str, year: int = LATEST_YEAR) -> dict | None:
    """
    Originated-mortgage totals for one lender-year, split by loan purpose
    and loan type. Returns
      {lei, year, total_count, total_volume_usd,
       by_purpose: {purchase|refi|cash_out_refi|home_improvement|other|
                    not_applicable: {count, volume_usd}},
       by_type:    {conventional|fha|va|usda_rhs: {count, volume_usd}},
       cached_at}
    or None on any failure.
    """
    from data import cache

    key = f"hmda:orig:{lei}:{year}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached

    total = _agg_rows(lei, year, {}, f"originations {lei} {year}")
    if total is None:
        return None
    purposes = _agg_rows(lei, year, {"loan_purposes": ",".join(LOAN_PURPOSES)},
                         f"by-purpose {lei} {year}")
    if purposes is None:
        return None
    types = _agg_rows(lei, year, {"loan_types": ",".join(LOAN_TYPES)},
                      f"by-type {lei} {year}")
    if types is None:
        return None

    by_purpose = _bucket(purposes, "loan_purposes", LOAN_PURPOSES)
    by_type = _bucket(types, "loan_types", LOAN_TYPES)
    if len(total) != 1 or by_purpose is None or by_type is None:
        print(f"[hmda] originations {lei} {year}: unexpected aggregation rows")
        return None
    try:
        total_count = int(total[0]["count"])
        total_volume = float(total[0]["sum"])
    except (KeyError, TypeError, ValueError):
        print(f"[hmda] originations {lei} {year}: malformed total row")
        return None

    out = {
        "lei": lei,
        "year": year,
        "total_count": total_count,
        "total_volume_usd": total_volume,
        "by_purpose": by_purpose,
        "by_type": by_type,
        "cached_at": datetime.now().isoformat(),
    }
    cache.put(key, out)
    return out


def get_lender_by_state(lei: str, year: int = LATEST_YEAR) -> list[dict] | None:
    """
    Originated-mortgage totals by state for one lender-year:
    [{state, count, volume_usd}], volume-descending, zero-volume states
    omitted. One aggregation call per state (the API filters by geography
    but never groups by it); all 50+DC+territories are queried so a state
    served only outside MSAs is never missed. None if ANY state call fails
    — a partial list would be wrong by omission.
    """
    from data import cache

    key = f"hmda:bystate:{lei}:{year}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached["rows"]

    rows: list[dict] = []
    for state in STATES:
        agg = _agg_rows(lei, year, {"states": state},
                        f"by-state {lei} {year} {state}")
        if agg is None or len(agg) != 1:
            return None  # _agg_rows already logged
        try:
            count = int(agg[0]["count"])
            volume = float(agg[0]["sum"])
        except (KeyError, TypeError, ValueError):
            print(f"[hmda] by-state {lei} {year} {state}: malformed row")
            return None
        if count > 0:
            rows.append({"state": state, "count": count, "volume_usd": volume})

    rows.sort(key=lambda r: r["volume_usd"], reverse=True)
    cache.put(key, {"rows": rows, "cached_at": datetime.now().isoformat()})
    return rows


def find_lei(name_or_rssd: str | int, year: int = LATEST_YEAR) -> str | None:
    """
    Resolve a bank to its HMDA LEI.

    • Name → case-insensitive match against the year's HMDA filers list
      (exact first, then unique substring; ambiguity returns None — never
      a guess).
    • RSSD (int, or all-digits string) → the HMDA public API cannot search
      by RSSD (verified: admin-api auth-gated, RSSD-in-path 404s), so the
      chain is RSSD → FDIC institutions name → filers match, then the
      candidate LEI's own institution record must report the same RSSD or
      this returns None.
    """
    s = str(name_or_rssd).strip()
    if not s:
        return None

    if not s.isdigit():
        return _match_filer_name(s, year)

    # RSSD path
    name = _fdic_name_for_rssd(s)
    if name is None:
        return None
    lei = _match_filer_name(name, year)
    if lei is None:
        return None
    inst = _get_json(INSTITUTION_URL.format(lei=lei, year=year), None,
                     f"institution {lei} {year}")
    if not isinstance(inst, dict) or str(inst.get("rssd")) != s:
        print(f"[hmda] find_lei {s}: candidate {lei} ({name!r}) reports "
              f"rssd {inst.get('rssd') if isinstance(inst, dict) else '?'} — mismatch")
        return None
    return lei


# ── find_lei internals ────────────────────────────────────────────────────

def _filers(year: int) -> list[dict] | None:
    """The year's HMDA filers list [{lei, name, period}], cached 30d."""
    from data import cache

    key = f"hmda:filers:{year}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached["institutions"]

    data = _get_json(FILERS_URL.format(year=year), None, f"filers {year}")
    if data is None:
        return None
    institutions = data.get("institutions") if isinstance(data, dict) else None
    if not isinstance(institutions, list):
        print(f"[hmda] filers {year}: unexpected response shape")
        return None
    cache.put(key, {"institutions": institutions,
                    "cached_at": datetime.now().isoformat()})
    return institutions


def _match_filer_name(name: str, year: int) -> str | None:
    """Case-insensitive filer-name match: exact, else unique substring."""
    filers = _filers(year)
    if filers is None:
        return None
    needle = name.strip().lower()

    exact = [f for f in filers if (f.get("name") or "").strip().lower() == needle]
    if len(exact) == 1:
        return exact[0].get("lei")
    if len(exact) > 1:  # same name filed under multiple LEIs — don't guess
        print(f"[hmda] find_lei {name!r}: {len(exact)} exact name matches")
        return None

    partial = [f for f in filers if needle in (f.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0].get("lei")
    print(f"[hmda] find_lei {name!r}: {len(partial)} matches in {year} filers")
    return None


def _fdic_name_for_rssd(rssd: str) -> str | None:
    """RSSD → institution name via the FDIC institutions API (the HMDA
    side has no public RSSD search)."""
    from data.fdic_client import FDIC_INSTITUTIONS_URL

    data = _get_json(FDIC_INSTITUTIONS_URL,
                     {"filters": f"FED_RSSD:{rssd}", "fields": "NAME", "limit": 2},
                     f"fdic rssd {rssd}")
    if data is None:
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
        print(f"[hmda] fdic rssd {rssd}: expected exactly 1 institution, "
              f"got {len(rows) if isinstance(rows, list) else 'bad shape'}")
        return None
    name = (rows[0].get("data") or {}).get("NAME")
    return name or None
