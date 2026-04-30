"""
FDIC BankFind API client.

Fetches Call Report financial data for banks by FDIC certificate number.
API docs: https://banks.data.fdic.gov/api/
"""

import requests
import pandas as pd
from config import get_fdic_fields

FDIC_FINANCIALS_URL = "https://banks.data.fdic.gov/api/financials"


def fetch_financials(cert: int, limit: int = 20) -> pd.DataFrame:
    """
    Fetch recent quarterly financials for a bank by FDIC cert number.

    Returns a DataFrame with one row per quarter, columns matching the
    FDIC field names defined in the metric registry.
    """
    fields_needed = get_fdic_fields()
    # Always include identifiers and date. ERNAST is fetched for rate sensitivity
    # calculations (true earning assets base), INTEXPY for cost of funds.
    base_fields = {
        "CERT", "REPNM", "REPDTE", "ASSET", "DEP", "LNLSNET", "NETINC",
        "EQTOT", "INTANGW", "ERNAST", "INTEXPY", "INTINCY", "NIMY",
    }
    all_fields = sorted(base_fields | fields_needed)

    params = {
        "filters": f"CERT:{cert}",
        "fields": ",".join(all_fields),
        "sort_by": "REPDTE",
        "sort_order": "DESC",
        "limit": limit,
    }

    try:
        resp = requests.get(FDIC_FINANCIALS_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[FDIC] Error fetching cert {cert}: {e}")
        return pd.DataFrame()

    rows = [r["data"] for r in data.get("data", [])]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Convert numeric columns
    for col in df.columns:
        if col not in ("REPNM", "REPDTE"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["REPDTE"] = pd.to_datetime(df["REPDTE"], format="%Y%m%d", errors="coerce")
    return df.sort_values("REPDTE", ascending=False).reset_index(drop=True)


def get_latest_financials(cert: int) -> dict:
    """
    Return the most recent quarter's financial data as a flat dict.
    Keys are FDIC field names (e.g. ROA, ROE, NIMY, ASSET, etc.).
    """
    df = fetch_financials(cert, limit=1)
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    # Convert NaT/NaN to None for JSON safety
    return {k: (None if pd.isna(v) else v) for k, v in row.items()}


def build_fdic_provenance(cert: int, field: str, repdte) -> dict:
    """Return a Source dict describing a FDIC Call Report field."""
    from data.provenance import Source

    if hasattr(repdte, "strftime"):
        as_of = repdte.strftime("%Y-%m-%d")
    else:
        s = str(repdte) if repdte else ""
        if "-" not in s and len(s) >= 8:
            s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        as_of = s

    return Source(
        origin="FDIC",
        identifier=str(cert),
        concept=field,
        as_of=as_of,
        form="Call Report",
        unit="$thousands" if field in (
            "ASSET", "DEP", "LNLSNET", "LNLSGR", "EQTOT", "NETINC", "INTANGW",
            "INTINC", "EINTEXP", "NONII", "NONIX", "ELNATR",
        ) else "%" if field in (
            "ROA", "ROE", "NIMY", "EEFFR", "NCLNLSR", "IDT1CER",
            "INTINCY", "INTEXPY", "RBCT1JR", "RBCRWAJ",
        ) else "",
    )


def get_historical_financials(cert: int, quarters: int = 20) -> pd.DataFrame:
    """Fetch historical quarterly data for trend charts."""
    return fetch_financials(cert, limit=quarters)


def fetch_multiple_banks(certs: dict[str, int]) -> dict[str, dict]:
    """
    Fetch latest financials for multiple banks.
    certs: {ticker: fdic_cert_number}
    Returns: {ticker: latest_financials_dict}
    """
    results = {}
    for ticker, cert in certs.items():
        if cert is not None:
            results[ticker] = get_latest_financials(cert)
    return results


def fetch_multiple_banks_parallel(
    certs: dict[str, int], limit: int = 4, max_workers: int = 10
) -> dict[str, pd.DataFrame]:
    """
    Fetch FDIC financials for multiple banks in parallel.

    Args:
        certs: {ticker: fdic_cert_number}
        limit: number of recent quarters to fetch
        max_workers: concurrent HTTP connections

    Returns: {ticker: DataFrame of quarterly financials}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    valid_certs = {t: c for t, c in certs.items() if c is not None}

    if not valid_certs:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_financials, cert, limit): ticker
            for ticker, cert in valid_certs.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                print(f"[FDIC] Parallel fetch error for {ticker}: {e}")
                results[ticker] = pd.DataFrame()

    return results
