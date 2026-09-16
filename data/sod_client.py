"""
FDIC Summary of Deposits (SOD) API client.

Provides branch-level deposit data and geographic coordinates. (Market
share is computed from the owner-resolved store, data/branches_store.)

Rate-limit hardening: FDIC's public API throttles aggressive callers with
429s. All fetches go through the shared data.http.get_with_retry (previously
this module kept a verbatim copy AND four raw requests.get call sites that
bypassed it).

API docs: https://api.fdic.gov/banks/
"""

import pandas as pd

from data.http import get_with_retry as _get_with_retry

SOD_URL = "https://api.fdic.gov/banks/sod"

# FDIC API hard-caps limit at 10,000 rows per request. No US bank has that
# many branches today (JPM tops out near 5,000), but fetch_branches still
# paginates by offset: the previous single-request limit of 500 silently
# truncated every bank above it (WFC/JPM/BAC/USB), holing the branches store.
_PAGE_LIMIT = 10000

BRANCH_FIELDS = [
    "CERT", "YEAR", "BRNUM", "NAMEBR", "NAMEFULL",
    "ADDRESBR", "CITYBR", "STALPBR", "ZIPBR",
    "CNTYNAMB", "STCNTYBR", "MSANAMB", "MSABR",
    "DEPSUMBR", "DEPSUM", "ASSET",
    "SIMS_LATITUDE", "SIMS_LONGITUDE",
    "BRSERTYP", "SIMS_ESTABLISHED_DATE",
    # FDIC's nationally unique branch id — the stable key for a branch
    # re-attributed to its post-merger owner (data/branches_store), where the
    # per-charter BRNUM would collide with the owner's own numbering.
    "UNINUMBR",
]


def get_latest_sod_year() -> int:
    """Find the most recent SOD year available."""
    params = {
        "fields": "YEAR",
        "sort_by": "YEAR",
        "sort_order": "DESC",
        "limit": 1,
    }
    try:
        resp = _get_with_retry(SOD_URL, params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("data"):
            return int(data["data"][0]["data"]["YEAR"])
    except Exception as e:
        print(f"[SOD] latest-year lookup failed ({type(e).__name__}: {e}); "
              "falling back to prior calendar year")
    # The June-30 SOD survey publishes each October, so the prior calendar
    # year is always available. Derived, not hardcoded — the previous frozen
    # literal (2024) was already a year stale and would have rotted silently.
    from datetime import date
    return date.today().year - 1


def fetch_branches(cert: int, year: int | None = None) -> pd.DataFrame:
    """
    Fetch all branches for a bank by FDIC cert number.

    Returns DataFrame with one row per branch including lat/lon and deposits.
    """
    if year is None:
        year = get_latest_sod_year()

    # Paginate to completion; on any failed page return empty rather than a
    # partial frame (partial = the same truncation bug in a new shape — the
    # refresh job then counts the bank as failed and keeps prior store rows).
    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "filters": f"CERT:{cert} AND YEAR:{year}",
            "fields": ",".join(BRANCH_FIELDS),
            "limit": _PAGE_LIMIT,
            "offset": offset,
        }
        try:
            resp = _get_with_retry(SOD_URL, params)
            if resp is None:
                return pd.DataFrame()
            data = resp.json()
        except Exception as e:
            print(f"[SOD] Error fetching cert {cert}: {e}")
            return pd.DataFrame()
        page = [r["data"] for r in data.get("data", [])]
        rows.extend(page)
        if len(page) < _PAGE_LIMIT:
            break
        offset += len(page)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Convert numeric columns
    for col in ["DEPSUMBR", "DEPSUM", "ASSET", "SIMS_LATITUDE", "SIMS_LONGITUDE"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def search_bank_by_name(name: str) -> list[dict]:
    """
    Search for a bank by name in the SOD data.
    Returns list of {cert, name} dicts for matching banks.
    """
    params = {
        # Latest survey year derived, not hardcoded (same rot class as the
        # frozen 2024 fallback this module used to have).
        "filters": f'NAMEFULL:"{name}*" AND YEAR:{get_latest_sod_year()}',
        "fields": "CERT,NAMEFULL",
        "limit": 200,
    }
    try:
        resp = _get_with_retry(SOD_URL, params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # Fallback: try institutions endpoint
        try:
            resp = _get_with_retry(
                "https://api.fdic.gov/banks/financials",
                {"filters": f'REPNM:"{name}*"', "fields": "CERT,REPNM", "limit": 50, "sort_by": "REPDTE", "sort_order": "DESC"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            seen = set()
            results = []
            for r in data.get("data", []):
                d = r["data"]
                c = d.get("CERT")
                if c and c not in seen:
                    seen.add(c)
                    results.append({"cert": int(c), "name": d.get("REPNM", "")})
            return results
        except Exception:
            return []

    # Deduplicate by CERT
    seen = set()
    results = []
    for r in data.get("data", []):
        d = r["data"]
        c = d.get("CERT")
        if c and c not in seen:
            seen.add(c)
            results.append({"cert": int(c), "name": d.get("NAMEFULL", "")})
    return results
