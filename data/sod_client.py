"""
FDIC Summary of Deposits (SOD) API client.

Provides branch-level deposit data, geographic coordinates, and
market share analysis by county/MSA.

API docs: https://banks.data.fdic.gov/api/
"""

import requests
import pandas as pd

SOD_URL = "https://banks.data.fdic.gov/api/sod"

BRANCH_FIELDS = [
    "CERT", "YEAR", "BRNUM", "NAMEBR", "NAMEFULL",
    "ADDRESBR", "CITYBR", "STALPBR", "ZIPBR",
    "CNTYNAMB", "STCNTYBR", "MSANAMB", "MSABR",
    "DEPSUMBR", "DEPSUM", "ASSET",
    "SIMS_LATITUDE", "SIMS_LONGITUDE",
    "BRSERTYP", "SIMS_ESTABLISHED_DATE",
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
        resp = requests.get(SOD_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("data"):
            return int(data["data"][0]["data"]["YEAR"])
    except Exception:
        pass
    return 2024  # fallback


def fetch_branches(cert: int, year: int | None = None) -> pd.DataFrame:
    """
    Fetch all branches for a bank by FDIC cert number.

    Returns DataFrame with one row per branch including lat/lon and deposits.
    """
    if year is None:
        year = get_latest_sod_year()

    params = {
        "filters": f"CERT:{cert} AND YEAR:{year}",
        "fields": ",".join(BRANCH_FIELDS),
        "limit": 500,
    }
    try:
        resp = requests.get(SOD_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[SOD] Error fetching cert {cert}: {e}")
        return pd.DataFrame()

    rows = [r["data"] for r in data.get("data", [])]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Convert numeric columns
    for col in ["DEPSUMBR", "DEPSUM", "ASSET", "SIMS_LATITUDE", "SIMS_LONGITUDE"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_county_market_share(stcntybr: str, year: int | None = None) -> pd.DataFrame:
    """
    Fetch all branches in a county (FIPS code) and compute market share by bank.

    Returns DataFrame with columns: CERT, NAMEFULL, branches, deposits, market_share.
    Sorted by deposits descending.
    """
    if year is None:
        year = get_latest_sod_year()

    params = {
        "filters": f"STCNTYBR:{stcntybr} AND YEAR:{year}",
        "fields": "CERT,NAMEFULL,DEPSUMBR,BRNUM",
        "limit": 10000,
    }
    try:
        resp = requests.get(SOD_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[SOD] Error fetching county {stcntybr}: {e}")
        return pd.DataFrame()

    rows = [r["data"] for r in data.get("data", [])]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["DEPSUMBR"] = pd.to_numeric(df["DEPSUMBR"], errors="coerce").fillna(0)

    # Aggregate by bank
    agg = df.groupby(["CERT", "NAMEFULL"]).agg(
        branches=("BRNUM", "count"),
        deposits=("DEPSUMBR", "sum"),
    ).reset_index()

    total = agg["deposits"].sum()
    agg["market_share"] = (agg["deposits"] / total * 100) if total > 0 else 0
    agg = agg.sort_values("deposits", ascending=False).reset_index(drop=True)
    agg["rank"] = range(1, len(agg) + 1)
    return agg


def fetch_msa_market_share(msabr: int, year: int | None = None) -> pd.DataFrame:
    """
    Fetch all branches in an MSA and compute market share by bank.

    Returns DataFrame with columns: CERT, NAMEFULL, branches, deposits, market_share.
    """
    if year is None:
        year = get_latest_sod_year()

    params = {
        "filters": f"MSABR:{msabr} AND YEAR:{year}",
        "fields": "CERT,NAMEFULL,DEPSUMBR,BRNUM",
        "limit": 10000,
    }
    try:
        resp = requests.get(SOD_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[SOD] Error fetching MSA {msabr}: {e}")
        return pd.DataFrame()

    rows = [r["data"] for r in data.get("data", [])]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["DEPSUMBR"] = pd.to_numeric(df["DEPSUMBR"], errors="coerce").fillna(0)

    agg = df.groupby(["CERT", "NAMEFULL"]).agg(
        branches=("BRNUM", "count"),
        deposits=("DEPSUMBR", "sum"),
    ).reset_index()

    total = agg["deposits"].sum()
    agg["market_share"] = (agg["deposits"] / total * 100) if total > 0 else 0
    agg = agg.sort_values("deposits", ascending=False).reset_index(drop=True)
    agg["rank"] = range(1, len(agg) + 1)
    return agg


def search_bank_by_name(name: str) -> list[dict]:
    """
    Search for a bank by name in the SOD data.
    Returns list of {cert, name} dicts for matching banks.
    """
    params = {
        "filters": f'NAMEFULL:"{name}*" AND YEAR:2025',
        "fields": "CERT,NAMEFULL",
        "limit": 200,
    }
    try:
        resp = requests.get(SOD_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # Fallback: try institutions endpoint
        try:
            resp = requests.get(
                "https://banks.data.fdic.gov/api/financials",
                params={"filters": f'REPNM:"{name}*"', "fields": "CERT,REPNM", "limit": 50, "sort_by": "REPDTE", "sort_order": "DESC"},
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
