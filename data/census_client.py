"""
US Census Bureau ACS client — market demographics from the primary source.

Powers the Market Demographics sub-tab (docs/SNL-BUILD-PLAN.md §11): SNL
ships vendor-licensed demographic estimates; the Census Bureau publishes
the actuals those vendors resell. ACS 5-year estimates are the only ACS
product with full county coverage, which is what branch footprints need.

Auth: CENSUS_API_KEY (env). The API formerly allowed 500 anonymous
requests/day, but as of 2026 every request needs a key (it answers HTTP 200
with an HTML "Missing Key" page otherwise — detected and logged, never
parsed as data). Keys are free: https://api.census.gov/data/key_signup.html

Functions:
  get_state_demographics(state_fips)               — population, income,
  get_county_demographics(state_fips, county_fips)    home value, unemployment
  get_population_change(state_fips, county_fips)   — latest vs prior vintage

Cache:
  Every vintage+geo response is cached in data.cache (``census:{dataset}:{geo}``)
  for 30 days via the shared freshness check — vintages change annually, so
  30d is generous but honest. Note the cache backend's own global TTL (24h)
  expires entries first locally; worst case is a daily refetch, and the 30d
  stamp stays correct if the backend TTL is ever raised.
"""

from __future__ import annotations

import os
from datetime import datetime

CENSUS_BASE = "https://api.census.gov/data"
LATEST_VINTAGE = 2023   # newest published ACS 5-year vintage
PRIOR_VINTAGE = 2018    # 5 years back, for population-change trend
CACHE_TTL_SECONDS = 30 * 86400

# ACS table variables → our field names. B23025 is the employment-status
# table: unemployment rate = unemployed / civilian labor force.
ACS_VARIABLES = {
    "B01003_001E": "population",          # total population
    "B19013_001E": "median_hh_income",    # median household income ($)
    "B25077_001E": "median_home_value",   # median value, owner-occupied ($)
    "B23025_005E": "unemployed",          # civilian labor force: unemployed
    "B23025_003E": "labor_force",         # civilian labor force: total
}

# USPS state/territory code → 2-digit FIPS. Source: Census FIPS 5-2.
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
    "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}


def _api_key() -> str:
    return (os.environ.get("CENSUS_API_KEY") or "").strip()


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _num(raw) -> float | None:
    """Parse one ACS value. Census encodes suppressed/unavailable estimates
    as large negative sentinels (-666666666 etc.) — those become None, never
    a plausible-wrong number."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def _fetch_acs(year: int, geo: str, geo_params: dict) -> dict | None:
    """
    Fetch NAME + ACS_VARIABLES for one geography in one vintage, cached
    under ``census:acs5_{year}:{geo}``. Returns the parsed record (our
    field names, numeric values, cached_at stamp) or None on any failure.
    """
    from data import cache

    key = f"census:acs5_{year}:{geo}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached

    url = f"{CENSUS_BASE}/{year}/acs/acs5"
    params = {"get": "NAME," + ",".join(ACS_VARIABLES), **geo_params}
    if _api_key():
        params["key"] = _api_key()

    resp = None
    try:
        from data.http import get_with_retry
        resp = get_with_retry(url, params=params, timeout=15)
        if resp is None:
            print(f"[census] acs5 {year} {geo}: retries exhausted (429)")
            return None
        rows = resp.json()
    except Exception as e:
        # The API answers HTTP 200 + an HTML "Missing Key" page when no key
        # is supplied — surface the actionable cause, not a JSON parse error.
        if resp is not None and "Missing Key" in (getattr(resp, "text", "") or ""):
            print(f"[census] acs5 {year} {geo}: API key required — "
                  "set CENSUS_API_KEY (free: api.census.gov/data/key_signup.html)")
        else:
            print(f"[census] acs5 {year} {geo} error: {type(e).__name__}: {e}")
        return None

    # Shape: [["NAME","B01003_001E",...,"state"], ["Washington","7812880",...]]
    if not isinstance(rows, list) or len(rows) < 2 or len(rows[1]) != len(rows[0]):
        print(f"[census] acs5 {year} {geo}: unexpected response shape")
        return None
    rec = dict(zip(rows[0], rows[1]))

    out = {field: _num(rec.get(var)) for var, field in ACS_VARIABLES.items()}
    out["name"] = rec.get("NAME")
    out["cached_at"] = datetime.now().isoformat()
    cache.put(key, out)
    return out


def _build_demographics(raw: dict, geo: str) -> dict:
    """Shape one fetched record into the Market Demographics row."""
    unemployment = None
    if raw.get("unemployed") is not None and raw.get("labor_force"):
        unemployment = round(raw["unemployed"] / raw["labor_force"] * 100, 2)
    return {
        "population": raw.get("population"),
        "median_hh_income": raw.get("median_hh_income"),
        "median_home_value": raw.get("median_home_value"),
        "unemployment_rate_pct": unemployment,
        "vintage": f"ACS5 {LATEST_VINTAGE}",
        "name": raw.get("name"),
        "geo": geo,
        "cached_at": raw.get("cached_at"),
    }


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_state_demographics(state_fips: str) -> dict | None:
    """Demographics for one state (2-digit FIPS, e.g. "53" = WA).
    Returns {population, median_hh_income, median_home_value,
    unemployment_rate_pct, vintage, name, geo, cached_at} or None."""
    state_fips = str(state_fips).zfill(2)
    geo = f"state:{state_fips}"
    raw = _fetch_acs(LATEST_VINTAGE, geo, {"for": f"state:{state_fips}"})
    if raw is None:
        return None
    return _build_demographics(raw, geo)


def get_county_demographics(state_fips: str, county_fips: str) -> dict | None:
    """Demographics for one county (state 2-digit + county 3-digit FIPS,
    e.g. "53", "033" = King County WA). Same shape as state, or None."""
    state_fips = str(state_fips).zfill(2)
    county_fips = str(county_fips).zfill(3)
    geo = f"county:{state_fips}{county_fips}"
    raw = _fetch_acs(LATEST_VINTAGE, geo,
                     {"for": f"county:{county_fips}", "in": f"state:{state_fips}"})
    if raw is None:
        return None
    return _build_demographics(raw, geo)


def get_population_change(state_fips: str,
                          county_fips: str | None = None) -> dict | None:
    """
    Population change for a state (or county when county_fips is given),
    comparing the latest ACS5 vintage against the vintage 5 years prior.
    Returns {pop_latest, pop_prior, change_pct, years, name, geo} or None.
    """
    state_fips = str(state_fips).zfill(2)
    if county_fips is not None:
        county_fips = str(county_fips).zfill(3)
        geo = f"county:{state_fips}{county_fips}"
        geo_params = {"for": f"county:{county_fips}", "in": f"state:{state_fips}"}
    else:
        geo = f"state:{state_fips}"
        geo_params = {"for": f"state:{state_fips}"}

    latest = _fetch_acs(LATEST_VINTAGE, geo, geo_params)
    prior = _fetch_acs(PRIOR_VINTAGE, geo, geo_params)
    if latest is None or prior is None:
        return None
    pop_latest = latest.get("population")
    pop_prior = prior.get("population")
    if pop_latest is None or not pop_prior:
        print(f"[census] population change {geo}: population missing in a vintage")
        return None
    return {
        "pop_latest": pop_latest,
        "pop_prior": pop_prior,
        "change_pct": round((pop_latest - pop_prior) / pop_prior * 100, 2),
        "years": f"{PRIOR_VINTAGE}-{LATEST_VINTAGE}",
        "name": latest.get("name"),
        "geo": geo,
    }
