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


def _build_filing_url(cik: int, accession: str, primary_doc: str) -> str:
    """Build direct URL to a filing document on SEC.gov."""
    acc_no_hyphens = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/{primary_doc}"
    )


def _build_index_url(cik: int, accession: str) -> str:
    """Build URL to the filing index page on SEC.gov."""
    acc_no_hyphens = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_hyphens}/"
    )


# Form types to include in the filings page
FILING_FORM_TYPES = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "8-K", "8-K/A",
    "DEF 14A", "DEFA14A",
    "S-1", "S-1/A", "S-3", "S-3/A",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
    "4", "3",
}


def get_filing_info(cik: int, max_filings: int = 50) -> dict:
    """
    Get filing metadata with direct EDGAR links and earnings flagging.

    Returns a dict with company info and a list of filings, each containing:
      form, date, report_date, description, items, accession,
      url (direct link), index_url, is_earnings, size
    """
    padded = _pad_cik(cik)
    url = SEC_SUBMISSIONS_URL.format(cik=padded)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}

    raw_cik = int(data.get("cik", cik))

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return {}

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])
    primary_docs = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])
    sizes = recent.get("size", [])

    def _safe(lst, i, default=""):
        return lst[i] if i < len(lst) else default

    filings = []
    for i, form in enumerate(forms):
        if form not in FILING_FORM_TYPES:
            continue

        accession = _safe(accessions, i)
        primary_doc = _safe(primary_docs, i)
        items = _safe(items_list, i)

        # Flag earnings releases: 8-K with Item 2.02
        is_earnings = (
            form in ("8-K", "8-K/A")
            and isinstance(items, str)
            and "2.02" in items
        )

        filing_url = ""
        index_url = ""
        if accession and primary_doc:
            filing_url = _build_filing_url(raw_cik, accession, primary_doc)
            index_url = _build_index_url(raw_cik, accession)

        filings.append({
            "form": form,
            "date": _safe(dates, i),
            "report_date": _safe(report_dates, i),
            "description": _safe(descriptions, i),
            "items": items if isinstance(items, str) else "",
            "accession": accession,
            "url": filing_url,
            "index_url": index_url,
            "is_earnings": is_earnings,
            "size": _safe(sizes, i, 0),
        })
        if len(filings) >= max_filings:
            break

    return {
        "name": data.get("name", ""),
        "cik": raw_cik,
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
        "fiscal_year_end": data.get("fiscalYearEnd", ""),
        "website": (data.get("website") or ""),
        "tickers": data.get("tickers", []),
        "exchanges": data.get("exchanges", []),
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
