"""
SEC EDGAR API client.

Fetches structured financial data (XBRL) from SEC EDGAR for public companies.
API docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

import requests
import pandas as pd
from config import SEC_USER_AGENT

SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

# Key XBRL concepts for bank valuation
CONCEPTS_OF_INTEREST = {
    "EarningsPerShareDiluted": "eps",
    "EarningsPerShareBasic": "eps_basic",
    "StockholdersEquity": "book_value_total",
    "CommonStockSharesOutstanding": "shares_outstanding",
    "Revenues": "revenue",
    "NetIncomeLoss": "net_income",
    "Assets": "total_assets_sec",
    "Liabilities": "total_liabilities",
    "CommonStockDividendsPerShareDeclared": "dividends_per_share",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "InterestIncome": "interest_income",
    "InterestExpense": "interest_expense",
    "Goodwill": "goodwill",
    "IntangibleAssetsNetExcludingGoodwill": "intangibles",
}


def _pad_cik(cik: int) -> str:
    return str(cik).zfill(10)


def fetch_company_facts(cik: int) -> dict:
    """Fetch all XBRL facts for a company."""
    url = SEC_COMPANY_FACTS_URL.format(cik=_pad_cik(cik))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[SEC] Error fetching CIK {cik}: {e}")
        return {}


def _extract_latest_value(facts: dict, concept: str, prefer_quarterly: bool = True) -> float | None:
    """Extract the most recent value for a given XBRL concept."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    concept_data = us_gaap.get(concept, {})
    units = concept_data.get("units", {})

    # Try USD first, then shares, then pure
    for unit_type in ["USD", "USD/shares", "shares", "pure"]:
        entries = units.get(unit_type, [])
        if not entries:
            continue

        # Filter to 10-K and 10-Q filings
        filing_entries = [
            e for e in entries
            if e.get("form") in ("10-K", "10-Q")
        ]
        if not filing_entries:
            filing_entries = entries

        # Sort by end date descending, then by filed date to break ties
        filing_entries.sort(
            key=lambda e: (e.get("end", ""), e.get("filed", "")),
            reverse=True,
        )
        if filing_entries:
            return filing_entries[0].get("val")

    return None


def _extract_time_series(facts: dict, concept: str) -> pd.DataFrame:
    """Extract a time series of values for a concept."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    concept_data = us_gaap.get(concept, {})
    units = concept_data.get("units", {})

    all_entries = []
    for unit_type in ["USD", "USD/shares", "shares", "pure"]:
        entries = units.get(unit_type, [])
        for e in entries:
            if e.get("form") in ("10-K", "10-Q"):
                all_entries.append({
                    "end": e.get("end"),
                    "val": e.get("val"),
                    "form": e.get("form"),
                    "filed": e.get("filed"),
                })

    if not all_entries:
        return pd.DataFrame()

    df = pd.DataFrame(all_entries)
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.dropna(subset=["end"]).sort_values("end", ascending=False)
    # Deduplicate by end date (keep most recently filed)
    df = df.drop_duplicates(subset=["end"], keep="first")
    return df


def get_latest_fundamentals(cik: int) -> dict:
    """
    Return latest key fundamentals as a dict.
    Keys match the short names in CONCEPTS_OF_INTEREST values.
    """
    facts = fetch_company_facts(cik)
    if not facts:
        return {}

    result = {}
    for xbrl_concept, short_name in CONCEPTS_OF_INTEREST.items():
        val = _extract_latest_value(facts, xbrl_concept)
        result[short_name] = val

    # Compute derived values
    equity = result.get("book_value_total")
    shares = result.get("shares_outstanding")
    goodwill = result.get("goodwill") or 0
    intangibles = result.get("intangibles") or 0

    if equity and shares and shares > 0:
        result["book_value_per_share"] = equity / shares
        result["tangible_book_value_per_share"] = (equity - goodwill - intangibles) / shares
    else:
        result["book_value_per_share"] = None
        result["tangible_book_value_per_share"] = None

    return result


def get_historical_fundamentals(cik: int, concept: str = "EarningsPerShareDiluted") -> pd.DataFrame:
    """Get historical time series for a specific concept."""
    facts = fetch_company_facts(cik)
    if not facts:
        return pd.DataFrame()
    return _extract_time_series(facts, concept)


def get_filing_info(cik: int) -> dict:
    """Get recent filing metadata (dates, types, links)."""
    url = SEC_SUBMISSIONS_URL.format(cik=_pad_cik(cik))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return {}

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])

    filings = []
    for i, form in enumerate(forms):
        if form in ("10-K", "10-Q", "8-K"):
            filings.append({
                "form": form,
                "date": dates[i] if i < len(dates) else "",
                "accession": accessions[i] if i < len(accessions) else "",
                "description": descriptions[i] if i < len(descriptions) else "",
            })
            if len(filings) >= 10:
                break

    return {
        "name": data.get("name", ""),
        "cik": data.get("cik", ""),
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
        "recent_filings": filings,
    }


def fetch_multiple_banks(ciks: dict[str, int]) -> dict[str, dict]:
    """
    Fetch latest fundamentals for multiple banks.
    ciks: {ticker: cik_number}
    Returns: {ticker: fundamentals_dict}
    """
    results = {}
    for ticker, cik in ciks.items():
        if cik is not None:
            results[ticker] = get_latest_fundamentals(cik)
    return results
