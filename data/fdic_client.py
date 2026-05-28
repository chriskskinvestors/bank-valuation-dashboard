"""
FDIC BankFind API client.

Fetches Call Report financial data for banks by FDIC certificate number.
API docs: https://banks.data.fdic.gov/api/

Rate-limiting note: FDIC's public API returns 429 Too Many Requests when
hit too fast. We retry up to 3 times with exponential backoff + jitter,
and cap parallel workers at 4 to stay well under their limit.
"""

import random
import time
import requests
import pandas as pd
from config import get_fdic_fields

FDIC_FINANCIALS_URL = "https://banks.data.fdic.gov/api/financials"
FDIC_INSTITUTIONS_URL = "https://banks.data.fdic.gov/api/institutions"


def list_all_active_institutions() -> list[dict]:
    """
    Enumerate every active FDIC-insured institution (used by refresh_sod
    to ingest branches for the full ~4,500-bank universe, not just our
    public-ticker subset).

    Paginates the institutions endpoint in 1,000-row chunks. Returns a
    list of {cert, rssd_id, name, namehcr, stalp, asset} dicts.

    FED_RSSD is included because the FFIEC Call Report API keys on RSSD
    (not FDIC cert), so refresh_ffiec needs both.
    """
    out: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        params = {
            "filters": "ACTIVE:1",
            "fields": "CERT,NAME,NAMEHCR,STALP,ASSET,FED_RSSD",
            "limit": page_size,
            "offset": offset,
            "sort_by": "ASSET",
            "sort_order": "DESC",
        }
        try:
            resp = _get_with_retry(FDIC_INSTITUTIONS_URL, params, timeout=30)
            if resp is None:
                break
            data = resp.json().get("data", [])
        except Exception as e:
            print(f"[FDIC] list_all_active error at offset {offset}: {e}")
            break
        if not data:
            break
        for entry in data:
            d = entry.get("data", {})
            out.append({
                "cert": d.get("CERT"),
                "rssd_id": d.get("FED_RSSD"),
                "name": d.get("NAME", ""),
                "namehcr": d.get("NAMEHCR", ""),
                "state": d.get("STALP", ""),
                "asset": d.get("ASSET", 0),
            })
        if len(data) < page_size:
            break
        offset += page_size
    return out


def cert_is_active(cert: int, ttl_seconds: int = 7 * 86400) -> bool:
    """
    Is this FDIC certificate's institution currently active?

    Banks that were acquired or failed are marked ACTIVE=0 in FDIC's
    institutions endpoint — we want to drop these from the universe so
    they don't appear in screens with stale data. Cached for a week in
    Postgres so this check costs ~one HTTP call per bank per week.
    """
    if not cert:
        return False
    from data import cache as _cache
    key = f"fdic_active:{cert}"
    cached = _cache.get(key)
    if cached is not None:
        ts = (cached or {}).get("_ts", 0)
        if time.time() - float(ts) < ttl_seconds:
            return bool(cached.get("_v", False))

    try:
        resp = _get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"CERT:{cert}",
            "fields": "CERT,ACTIVE",
        })
        active = False
        if resp is not None:
            data = resp.json().get("data", [])
            if data:
                active = int(data[0].get("data", {}).get("ACTIVE", 0)) == 1
        _cache.put(key, {"_ts": time.time(), "_v": active})
        return active
    except Exception:
        # On API failure assume active (don't drop banks on a transient
        # FDIC outage). The next refresh will re-check.
        return True


def _get_with_retry(url: str, params: dict, timeout: int = 15,
                    max_attempts: int = 3) -> requests.Response | None:
    """GET with exponential backoff + jitter on 429s and connection errors."""
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                # Honor Retry-After if provided, else exponential backoff
                wait = float(resp.headers.get("Retry-After", 0)) or (
                    (2 ** attempt) + random.uniform(0, 1)
                )
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError:
            raise  # non-429 HTTP errors aren't retried
        except (requests.ConnectionError, requests.Timeout):
            if attempt == max_attempts - 1:
                raise
            time.sleep((2 ** attempt) + random.uniform(0, 1))
    return None


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
        resp = _get_with_retry(FDIC_FINANCIALS_URL, params)
        if resp is None:
            return pd.DataFrame()
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
    certs: dict[str, int], limit: int = 4, max_workers: int = 4
) -> dict[str, pd.DataFrame]:
    """
    Fetch FDIC financials for multiple banks in parallel.

    max_workers is intentionally low (4) — FDIC's public API rate-limits
    at low concurrency. The per-call _get_with_retry handles transient 429s,
    but going higher than 4 parallel produces sustained 429 storms that
    even retries can't clear in reasonable time.

    Args:
        certs: {ticker: fdic_cert_number}
        limit: number of recent quarters to fetch
        max_workers: concurrent HTTP connections (default 4)

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
