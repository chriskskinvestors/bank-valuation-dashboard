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
    # Always include identifiers and date
    base_fields = {"CERT", "REPNM", "REPDTE", "ASSET", "DEP", "LNLSNET", "NETINC", "EQTOT", "INTANGW"}
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
