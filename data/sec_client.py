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


def _extract_latest_value_with_source(facts: dict, concept: str,
                                         max_age_years: int = 3) -> tuple | None:
    """
    Like _extract_latest_value but returns (value, as_of, filed, form, unit) tuple
    so the caller can track provenance. Returns None if stale/missing.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    concept_data = us_gaap.get(concept, {})
    units = concept_data.get("units", {})

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

    for unit_type in ["USD", "USD/shares", "shares", "pure"]:
        entries = units.get(unit_type, [])
        if not entries:
            continue
        filing_entries = [e for e in entries if e.get("form") in ("10-K", "10-Q")]
        if not filing_entries:
            filing_entries = entries
        filing_entries.sort(
            key=lambda e: (e.get("end", ""), e.get("filed", "")), reverse=True,
        )
        if filing_entries:
            top = filing_entries[0]
            if top.get("end", "") < cutoff:
                return None
            return (
                top.get("val"),
                top.get("end", ""),
                top.get("filed", ""),
                top.get("form", ""),
                unit_type,
            )
    return None


def _extract_latest_value(facts: dict, concept: str, prefer_quarterly: bool = True,
                            max_age_years: int = 3) -> float | None:
    """
    Extract the most recent value for a given XBRL concept.

    max_age_years: if the latest value is older than this, return None so the
    caller can fall back to a different concept. Some companies (e.g. Citi)
    stopped reporting certain concepts years ago — we don't want to return
    15-year-old share counts silently.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    concept_data = us_gaap.get(concept, {})
    units = concept_data.get("units", {})

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

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
            top = filing_entries[0]
            # Staleness guard: if the latest reported value is older than cutoff,
            # the issuer likely stopped using this concept. Return None.
            if top.get("end", "") < cutoff:
                return None
            return top.get("val")

    return None


def _extract_ttm_value(facts: dict, concept: str, max_age_years: int = 2) -> float | None:
    """
    Trailing-twelve-months value for a flow concept (net income, revenue, etc.).

    XBRL reports income-statement items at multiple period granularities:
      • quarterly  (start..end ≈ 90 days)
      • year-to-date  (Q1, H1, 9M, FY)
      • annual  (start..end ≈ 365 days)

    For ROATCE / ROE / margin calculations we want **TTM**, not whichever
    period happens to be most recently filed. This helper finds:
      1. The 4 most recent quarterly entries and sums them, OR
      2. If insufficient quarterlies exist, falls back to the latest
         annual (~365-day) entry.

    Returns None if neither path yields a value.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    units = us_gaap.get(concept, {}).get("units", {})

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

    quarterly: list[dict] = []
    annual: list[dict] = []

    for unit_type in ("USD", "USD/shares", "pure"):
        for e in units.get(unit_type, []):
            if e.get("form") not in ("10-K", "10-Q"):
                continue
            start, end = e.get("start"), e.get("end")
            if not start or not end or end < cutoff:
                continue
            try:
                d_start = datetime.fromisoformat(start)
                d_end = datetime.fromisoformat(end)
            except ValueError:
                continue
            span = (d_end - d_start).days
            if 80 <= span <= 100:
                quarterly.append({"end": end, "val": e.get("val"), "filed": e.get("filed", "")})
            elif 350 <= span <= 380:
                annual.append({"end": end, "val": e.get("val"), "filed": e.get("filed", "")})

    # Path 1: sum 4 most recent quarterly periods (de-duplicated by end-date)
    if quarterly:
        quarterly.sort(key=lambda x: (x["end"], x["filed"]), reverse=True)
        seen_ends, latest_q = set(), []
        for q in quarterly:
            if q["end"] not in seen_ends:
                seen_ends.add(q["end"])
                latest_q.append(q)
            if len(latest_q) == 4:
                break
        if len(latest_q) == 4 and all(q["val"] is not None for q in latest_q):
            return float(sum(q["val"] for q in latest_q))

    # Path 2: latest annual report
    if annual:
        annual.sort(key=lambda x: (x["end"], x["filed"]), reverse=True)
        if annual[0]["val"] is not None:
            return float(annual[0]["val"])

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

    # StockholdersEquity fallback — some issuers report the broader version with
    # noncontrolling interest. Use that if primary is stale/missing.
    if not result.get("book_value_total"):
        result["book_value_total"] = _extract_latest_value(
            facts, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            max_age_years=1,
        )

    # Net income — must be TTM, not the single quarter the issuer last filed.
    # When a bank files Q1 2026 10-Q, NetIncomeLoss returns the latest entry
    # by end-date which is Q1-only. ROATCE/ROE expect TTM, so sum 4 quarters
    # (or fall back to annual). Try concept fallback chain in order:
    #   1. NetIncomeLoss (the standard)
    #   2. NetIncomeLossAvailableToCommonStockholdersBasic (common shareholders' portion)
    #   3. ProfitLoss (total — includes minority interest, will slightly overstate)
    ni_ttm = _extract_ttm_value(facts, "NetIncomeLoss")
    if ni_ttm is None:
        ni_ttm = _extract_ttm_value(facts, "NetIncomeLossAvailableToCommonStockholdersBasic")
    if ni_ttm is None:
        ni_ttm = _extract_ttm_value(facts, "ProfitLoss")
    if ni_ttm is not None:
        # Overwrite the single-period value with TTM. Keep the legacy field
        # for callers that need the latest reported quarterly NI.
        result["net_income_latest_period"] = result.get("net_income")
        result["net_income"] = ni_ttm
        result["net_income_ttm"] = ni_ttm
    else:
        # No TTM derivation possible — keep whatever fallback chain returned
        if not result.get("net_income"):
            result["net_income"] = _extract_latest_value(
                facts, "NetIncomeLossAvailableToCommonStockholdersBasic", max_age_years=1,
            )
        if not result.get("net_income"):
            result["net_income"] = _extract_latest_value(facts, "ProfitLoss", max_age_years=1)

    # Share-count fallback chain — some issuers (e.g., Citi) stopped
    # reporting CommonStockSharesOutstanding years ago. Fall back in order:
    # 1. CommonStockSharesOutstanding (primary)
    # 2. EntityCommonStockSharesOutstanding (DEI namespace — usually fresh)
    # 3. WeightedAverageNumberOfSharesOutstandingBasic (period average)
    # 4. CommonStockSharesIssued − TreasuryStockCommonShares (derived)
    if not result.get("shares_outstanding"):
        # 2: try dei:EntityCommonStockSharesOutstanding (point-in-time)
        dei = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
        for unit, entries in dei.get("units", {}).items():
            entries = sorted(entries, key=lambda x: (x.get("end", ""), x.get("filed", "")), reverse=True)
            if entries:
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
                if entries[0].get("end", "") >= cutoff:
                    result["shares_outstanding"] = entries[0].get("val")
                    break
    if not result.get("shares_outstanding"):
        # 3: weighted-average basic shares (from NI statement)
        result["shares_outstanding"] = _extract_latest_value(
            facts, "WeightedAverageNumberOfSharesOutstandingBasic", max_age_years=1,
        )
    if not result.get("shares_outstanding"):
        # 4: issued − treasury (rarely needed)
        issued = _extract_latest_value(facts, "CommonStockSharesIssued", max_age_years=1)
        treasury = _extract_latest_value(facts, "TreasuryStockCommonShares", max_age_years=1) or 0
        if issued and issued > 0:
            result["shares_outstanding"] = issued - treasury

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


def get_fundamentals_with_provenance(cik: int) -> dict:
    """
    Fetch fundamentals + track WHERE each value came from.

    Returns {short_name: {"value": ..., "source": Source}} instead of raw scalars.
    Used by the UI when users want to trace a number back to its primary filing.
    """
    from data.provenance import Source

    facts = fetch_company_facts(cik)
    if not facts:
        return {}

    def _wrap(short_name: str, tup, resolved_concept: str):
        """Turn (value, end, filed, form, unit) tuple into provenance dict."""
        if tup is None:
            return {"value": None, "source": Source(
                origin="SEC", identifier=str(cik), concept=resolved_concept,
                notes="No recent filing data found",
            )}
        val, as_of, filed, form, unit = tup
        return {
            "value": val,
            "source": Source(
                origin="SEC", identifier=str(cik), concept=resolved_concept,
                as_of=as_of, filed=filed, form=form, unit=unit,
            ),
        }

    result = {}
    # Standard concepts — try each, record which one succeeded
    for concept, short in CONCEPTS_OF_INTEREST.items():
        tup = _extract_latest_value_with_source(facts, concept)
        result[short] = _wrap(short, tup, concept)

    # ── Fallbacks with provenance ────────────────────────────────────
    # Share count fallback chain (same as get_latest_fundamentals but provenance-aware)
    if result["shares_outstanding"]["value"] is None:
        dei = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
        for unit, entries in dei.get("units", {}).items():
            if entries:
                srt = sorted(entries, key=lambda x: (x.get("end", ""), x.get("filed", "")), reverse=True)
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
                if srt[0].get("end", "") >= cutoff:
                    e = srt[0]
                    result["shares_outstanding"] = {
                        "value": e.get("val"),
                        "source": Source(
                            origin="SEC", identifier=str(cik),
                            concept="dei:EntityCommonStockSharesOutstanding",
                            as_of=e.get("end", ""), filed=e.get("filed", ""),
                            form=e.get("form", ""), unit=unit,
                            notes="Fallback — primary CommonStockSharesOutstanding stale",
                        ),
                    }
                    break
    if result["shares_outstanding"]["value"] is None:
        tup = _extract_latest_value_with_source(
            facts, "WeightedAverageNumberOfSharesOutstandingBasic", max_age_years=1
        )
        if tup:
            result["shares_outstanding"] = _wrap(
                "shares_outstanding", tup, "WeightedAverageNumberOfSharesOutstandingBasic"
            )
            result["shares_outstanding"]["source"] = Source(
                **{**result["shares_outstanding"]["source"].__dict__,
                   "notes": "Fallback — using weighted-average shares"}
            )

    # Net income fallback chain
    if result["net_income"]["value"] is None:
        for fallback in ["NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]:
            tup = _extract_latest_value_with_source(facts, fallback, max_age_years=1)
            if tup:
                result["net_income"] = _wrap("net_income", tup, fallback)
                result["net_income"]["source"] = Source(
                    **{**result["net_income"]["source"].__dict__,
                       "notes": f"Fallback — primary NetIncomeLoss stale/missing; using {fallback}"}
                )
                break

    # StockholdersEquity fallback
    if result["book_value_total"]["value"] is None:
        tup = _extract_latest_value_with_source(
            facts, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            max_age_years=1,
        )
        if tup:
            result["book_value_total"] = _wrap(
                "book_value_total", tup,
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
            )

    # Derived values
    equity = result["book_value_total"]["value"]
    shares = result["shares_outstanding"]["value"]
    goodwill = result["goodwill"]["value"] or 0
    intangibles = result["intangibles"]["value"] or 0

    if equity and shares and shares > 0:
        tbvps = (equity - goodwill - intangibles) / shares
        bvps = equity / shares
        # Provenance of a computed value: combine the parents
        parents = (
            result["book_value_total"]["source"],
            result["shares_outstanding"]["source"],
            result["goodwill"]["source"],
            result["intangibles"]["source"],
        )
        result["book_value_per_share"] = {
            "value": bvps,
            "source": Source(
                origin="COMPUTED", concept="book_value_per_share",
                derived_from=tuple(p for p in parents[:2]),
                notes="= StockholdersEquity / SharesOutstanding",
            ),
        }
        result["tangible_book_value_per_share"] = {
            "value": tbvps,
            "source": Source(
                origin="COMPUTED", concept="tangible_book_value_per_share",
                derived_from=parents,
                notes="= (StockholdersEquity − Goodwill − Intangibles) / SharesOutstanding",
            ),
        }
    else:
        result["book_value_per_share"] = {"value": None, "source": Source(origin="COMPUTED", concept="book_value_per_share", notes="Insufficient inputs")}
        result["tangible_book_value_per_share"] = {"value": None, "source": Source(origin="COMPUTED", concept="tangible_book_value_per_share", notes="Insufficient inputs")}

    return result


def get_historical_fundamentals(cik: int, concept: str = "EarningsPerShareDiluted") -> pd.DataFrame:
    """Get historical time series for a specific concept."""
    facts = fetch_company_facts(cik)
    if not facts:
        return pd.DataFrame()
    return _extract_time_series(facts, concept)


def fetch_multiple_banks_parallel(
    ciks: dict[str, int], max_workers: int = 5
) -> dict[str, dict]:
    """
    Fetch SEC fundamentals for multiple banks in parallel.

    SEC EDGAR rate limit is 10 req/sec. Uses 5 workers with small jitter
    to stay safely under the limit.

    Args:
        ciks: {ticker: cik_number}
        max_workers: concurrent HTTP connections (keep <= 5 for SEC rate limit)

    Returns: {ticker: fundamentals_dict}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    import threading

    results = {}
    valid_ciks = {t: c for t, c in ciks.items() if c is not None}

    if not valid_ciks:
        return results

    # Thread-safe rate limiter (10 req/sec = 100ms per request)
    _lock = threading.Lock()
    _last_call = [0.0]

    def _fetch_with_rate_limit(cik: int) -> dict:
        with _lock:
            elapsed = time.time() - _last_call[0]
            if elapsed < 0.11:  # 110ms to be safe
                time.sleep(0.11 - elapsed)
            _last_call[0] = time.time()
        return get_latest_fundamentals(cik)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_with_rate_limit, cik): ticker
            for ticker, cik in valid_ciks.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                print(f"[SEC] Parallel fetch error for {ticker}: {e}")
                results[ticker] = {}

    return results


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
