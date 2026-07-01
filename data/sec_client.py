"""
SEC EDGAR API client.

Fetches structured financial data (XBRL) from SEC EDGAR for public companies.
API docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

import pandas as pd
import streamlit as st
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


# XBRL concepts the dashboard actually reads. The raw companyfacts blob is
# 6-8 MB (650-900 us-gaap concepts) but we use ~two dozen, so we cache only a
# projection over these — ~14× smaller (~450 KB), and json.loads is near-instant
# on the slim copy (vs seconds on the full blob). tools/verify_slim_facts.py
# asserts the projection yields byte-identical fundamentals to the full blob
# across many banks; ADD a concept here before referencing it anywhere.
SLIM_USGAAP_CONCEPTS = {
    "Assets", "Liabilities",
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "CommonStockSharesOutstanding", "CommonStockSharesIssued",
    "TreasuryStockCommonShares", "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "EarningsPerShareDiluted", "EarningsPerShareBasic",
    "AmortizationOfIntangibleAssets",
    "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss",
    "Revenues", "CommonStockDividendsPerShareDeclared",
    "CashAndCashEquivalentsAtCarryingValue",
    "InterestIncome", "InterestExpense",
    "Goodwill", "IntangibleAssetsNetExcludingGoodwill",
    "IntangibleAssetsNetIncludingGoodwill", "FiniteLivedIntangibleAssetsNet",
    # Preferred stock — subtracted from total equity so book/tangible-book per
    # share are COMMON-based (else every preferred issuer overstates them).
    # Big banks (BAC/USB/JPM) tag carrying value as par+APIC, not PreferredStockValue.
    "PreferredStockValue", "PreferredStockValueOutstanding",
    "PreferredStockIncludingAdditionalPaidInCapital",
    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
    "PreferredStockLiquidationPreferenceValue",
    "PreferredStockSharesOutstanding", "PreferredStockSharesIssued",
    # Period-matched actuals for the consensus comparison (data/sec_period.py):
    # bank income-statement flows, net interest income, provision, and the
    # balance-sheet stocks brokers estimate.
    "InterestIncomeExpenseNet", "InterestAndDividendIncomeOperating",
    "NoninterestIncome", "NoninterestIncomeLoss", "NoninterestExpense",
    "ProvisionForCreditLosses", "ProvisionForLoanAndLeaseLosses",
    "ProvisionForLoanLeaseAndOtherLosses", "ProvisionForCreditLossExpenseReversal",
    "Deposits", "DepositsDomestic",
    "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
    "LoansAndLeasesReceivableNetReportedAmount",
    "LoansAndLeasesReceivableNetOfDeferredIncome",
    "LoansReceivableHeldForInvestmentNet", "NotesReceivableNet",
}

# Cache the slim projection under a key that embeds a hash of the kept-concept
# set. When a concept is ADDED above, the hash changes, so blobs cached before
# the addition are bypassed (re-fetched with the new concept) instead of
# silently returning n/a for it — the 2026-06-14 avg-diluted-shares bug.
import hashlib as _hashlib
_SLIM_VER = _hashlib.md5(
    ",".join(sorted(SLIM_USGAAP_CONCEPTS)).encode()).hexdigest()[:8]


def _slim_facts(facts: dict) -> dict:
    """Project a full companyfacts blob down to the concepts we use. Keeps the
    exact JSON shape (so all extractors work unchanged) plus the whole `dei`
    namespace (small, and used for cover-page shares)."""
    if not facts:
        return facts
    f = facts.get("facts", {}) or {}
    ug = f.get("us-gaap", {}) or {}
    return {
        "cik": facts.get("cik"),
        "entityName": facts.get("entityName"),
        "facts": {
            "us-gaap": {k: v for k, v in ug.items() if k in SLIM_USGAAP_CONCEPTS},
            "dei": f.get("dei", {}),
        },
    }


def _download_company_facts(cik: int) -> dict:
    """Raw SEC companyfacts download (no cache, full blob). Used by the cache
    layer and the slim-vs-full verification harness.

    Every SEC fundamental on the dashboard depends on this fetch, so it gets
    the shared retry policy (it previously had ONE attempt while far less
    critical fetches retried three times)."""
    from data.http import get_with_retry
    url = SEC_COMPANY_FACTS_URL.format(cik=_pad_cik(cik))
    try:
        resp = get_with_retry(url, headers=HEADERS, timeout=20)
        return resp.json() if resp is not None else {}
    except Exception as e:
        print(f"[SEC] companyfacts fetch failed for CIK {cik}: {type(e).__name__}: {e}")
        return {}


# In-process memo (1h) on top of the Postgres/SQLite store: the persistent
# cache already spares the ~30s SEC download, but a warm cache HIT still pays a
# DB round-trip + slim-dict deserialization on EVERY rerun. This function feeds
# many Company tabs (valuation history, financial highlights, Company Reported
# extractors), so memoizing the parsed dict makes every tab switch that reads
# SEC facts skip that repeat cost. Companyfacts is quarterly — 1h staleness is
# invisible to values. Same pattern as get_latest_fundamentals below.
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_company_facts(cik: int) -> dict:
    """
    Fetch the XBRL facts the dashboard uses for a company, cached for
    FUNDAMENTAL_CACHE_TTL_HOURS (default 24h) in the Postgres/SQLite store.

    We cache a SLIM projection (the ~two dozen concepts we read, plus `dei`),
    not the full 6-8 MB blob — caching the full universe would be multiple GB
    and every cold load would pay a ~30s json.loads. The slim copy is
    ~30-50× smaller and parses instantly. Provenance (accession/form per
    value) is preserved because we keep each kept concept's full unit arrays.
    """
    from data import cache
    cache_key = f"sec_facts:{_SLIM_VER}:{cik}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    facts = _download_company_facts(cik)
    if not facts:
        return {}
    slim = _slim_facts(facts)
    try:
        cache.put(cache_key, slim)
    except Exception as e:
        # Cache failure shouldn't break the call — log and move on.
        print(f"[SEC] Cache put failed for CIK {cik}: {e}")
    return slim


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

    Most issuers never tag Q4 as a discrete 3-month fact — it exists only
    inside the FY duration of the 10-K. Naively summing "the 4 most recent
    quarterly entries" therefore drops Q4 and double-counts the year-ago
    quarter right after a Q1/Q2/Q3 filing (a 5-quarter window). So:
      1. Collect direct ~3-month facts.
      2. Derive missing quarters from same-start YTD differences
         (FY − 9M = Q4, 9M − H1 = Q3, H1 − Q1 = Q2).
      3. Sum the latest 4 quarters ONLY if they are consecutive
         (each gap ≈ 3 months).
      4. Otherwise fall back to the latest annual (~365-day) entry.

    Returns None if no path yields a value.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    units = us_gaap.get(concept, {}).get("units", {})

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

    # All durations within the lookback, de-duplicated by (start, end) with
    # the latest filing winning (restatements overwrite originals).
    durations: dict[tuple[str, str], dict] = {}
    for unit_type in ("USD", "USD/shares", "pure"):
        for e in units.get(unit_type, []):
            if e.get("form") not in ("10-K", "10-Q"):
                continue
            start, end, val = e.get("start"), e.get("end"), e.get("val")
            if not start or not end or end < cutoff or val is None:
                continue
            try:
                span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
            except ValueError:
                continue
            key = (start, end)
            prev = durations.get(key)
            if prev is None or e.get("filed", "") > prev["filed"]:
                durations[key] = {"val": val, "filed": e.get("filed", ""), "span": span}

    def _gap_days(a: str, b: str) -> int:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days

    # Direct ~3-month facts
    quarters: dict[str, float] = {
        end: d["val"] for (start, end), d in durations.items() if 80 <= d["span"] <= 100
    }

    # Derive missing quarters from same-start YTD pairs: a duration minus a
    # ~3-months-shorter duration with the same start is the quarter between
    # their end dates. Direct facts always beat derived ones.
    for (s1, e1), d1 in durations.items():
        if d1["span"] <= 100 or e1 in quarters:
            continue
        for (s2, e2), d2 in durations.items():
            if s2 != s1 or e2 >= e1:
                continue
            if 80 <= _gap_days(e2, e1) <= 100:
                quarters[e1] = d1["val"] - d2["val"]
                break

    # Path 1: latest 4 quarters, required consecutive (~3-month gaps)
    if len(quarters) >= 4:
        ends = sorted(quarters)[-4:]
        if all(80 <= _gap_days(a, b) <= 100 for a, b in zip(ends, ends[1:])):
            return float(sum(quarters[e] for e in ends))

    # Path 2: latest annual report
    annual = [
        {"end": end, "val": d["val"], "filed": d["filed"]}
        for (start, end), d in durations.items() if 350 <= d["span"] <= 380
    ]
    if annual:
        annual.sort(key=lambda x: (x["end"], x["filed"]), reverse=True)
        return float(annual[0]["val"])

    return None


def _extract_ttm_dividend(
    facts: dict, concept: str = "CommonStockDividendsPerShareDeclared",
) -> float | None:
    """
    Robust trailing-twelve-months dividends-per-share.

    Dividends need special handling vs other flow concepts: a bank can skip or
    cut a quarter (untagged → effectively $0), so naively summing the "4 most
    recent quarterly-tagged entries" pulls from a STALE window and overstates
    the dividend (e.g. BAFN: Q3/Q4-2025 were $0 and untagged, so the 4 newest
    tagged quarters were Q3-24..Q2-25 = $0.32 vs the true FY2025 of $0.16).

    Algorithm, anchored to the latest reported period-end E (so we never count
    stale quarters):
      1. If an annual (~365-day) entry ends at E → return it (authoritative).
      2. Else sum single-quarter entries ending within (E−370d, E].
      3. Else fall back to the latest year-to-date cumulative entry.
    """
    from datetime import datetime, timedelta
    units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {})
    entries: list[dict] = []
    for unit_type in ("USD/shares", "USD"):
        for e in units.get(unit_type, []):
            if e.get("form") not in ("10-K", "10-Q"):
                continue
            start, end, val = e.get("start"), e.get("end"), e.get("val")
            if not start or not end or val is None:
                continue
            try:
                span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
            except ValueError:
                continue
            entries.append({"start": start, "end": end, "val": val,
                            "span": span, "filed": e.get("filed", "")})
    if not entries:
        return None

    E = max(e["end"] for e in entries)
    Ed = datetime.fromisoformat(E)

    # Staleness guard: if the most recent dividend tag is too old (the bank
    # stopped tagging dividends in XBRL — e.g. CBNK after mid-2024), we don't
    # know the current dividend. Return None rather than present a stale value
    # as if it were current.
    if (datetime.now() - Ed).days > 400:
        return None

    # 1) Annual entry ending at the anchor — the authoritative full-year figure.
    annual_at_e = [e for e in entries if 350 <= e["span"] <= 380
                   and abs((datetime.fromisoformat(e["end"]) - Ed).days) <= 15]
    if annual_at_e:
        annual_at_e.sort(key=lambda x: x["filed"], reverse=True)
        return float(annual_at_e[0]["val"])

    # 2) Sum single-quarter entries within the trailing 12 months of the anchor.
    window_start = Ed - timedelta(days=370)
    by_end: dict[str, dict] = {}
    for e in entries:
        if 80 <= e["span"] <= 100:
            de = datetime.fromisoformat(e["end"])
            if window_start < de <= Ed:
                if e["end"] not in by_end or e["filed"] > by_end[e["end"]]["filed"]:
                    by_end[e["end"]] = e
    if by_end:
        return float(sum(v["val"] for v in by_end.values()))

    # 3) Latest year-to-date cumulative entry (best available).
    cumulative = [e for e in entries if e["span"] > 100]
    if cumulative:
        cumulative.sort(key=lambda x: (x["end"], x["filed"]), reverse=True)
        return float(cumulative[0]["val"])
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_latest_fundamentals(cik: int) -> dict:
    """
    Return latest key fundamentals as a dict.
    Keys match the short names in CONCEPTS_OF_INTEREST values.

    In-process memo (1h): these are filing-derived (quarterly) values, so a 1h
    memo is invisible to the data but spares every Company tab a repeated SEC
    round-trip on each rerun. (Outside Streamlit — jobs — this no-ops.)
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
    # INVARIANT: net_income is a 12-month value or it is None. _extract_ttm_value
    # already falls back to the latest annual internally, so None here means no
    # honest TTM exists — serving the latest single-period value instead would
    # understate ROATCE/ROE ~4× (a plausible-wrong number, worse than n/a).
    # The raw latest-period value stays available under net_income_latest_period.
    result["net_income_latest_period"] = result.get("net_income")
    result["net_income"] = ni_ttm
    result["net_income_ttm"] = ni_ttm
    result["net_income_is_ttm"] = ni_ttm is not None

    # Same TTM treatment for EPS — quarterly Q1/Q2/Q3 filings would otherwise
    # give a single-period EPS, producing P/E values 4× too high.
    # (e.g. WAL Q1 EPS $1.65 → P/E = $79/$1.65 = 48x; real TTM EPS ≈ $7,
    # P/E ≈ 11x.) Same invariant: TTM or None, never a single period.
    for concept, field in [
        ("EarningsPerShareDiluted", "eps"),
        ("EarningsPerShareBasic", "eps_basic"),
    ]:
        ttm_val = _extract_ttm_value(facts, concept)
        result[f"{field}_latest_period"] = result.get(field)
        result[field] = ttm_val
        result[f"{field}_ttm"] = ttm_val
        result[f"{field}_is_ttm"] = ttm_val is not None

    # Dividends per share: robust TTM anchored to the latest period (handles
    # cut/skipped quarters that would otherwise pull a stale 4-quarter window).
    # Authoritative: the robust TTM extractor's result wins even when it's None
    # (stale/unavailable), overriding the plain 3-year-max-age value the concept
    # loop set above — we'd rather show no yield than a stale one.
    result["dividends_per_share_latest_period"] = result.get("dividends_per_share")
    result["dividends_per_share"] = _extract_ttm_dividend(
        facts, "CommonStockDividendsPerShareDeclared")
    result["dividends_per_share_ttm"] = result["dividends_per_share"]

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

    # Cross-check against the 10-Q/10-K cover-page count (dei namespace).
    # Both are legitimate — balance-sheet date vs filing date — but a large
    # gap usually means a post-quarter issuance or buyback (e.g. SFST's
    # April-2026 raise: 8.25M at quarter-end vs 9.46M on the May cover).
    # Recorded here so validation can surface it; never silently "fixed".
    dei_node = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
    cover_rows = [r for u in dei_node.get("units", {}).values() for r in u]
    cover = max(cover_rows, key=lambda r: (r.get("end", ""), r.get("filed", "")), default=None)
    result["shares_outstanding_cover"] = cover.get("val") if cover else None
    sh, cov = result.get("shares_outstanding"), result.get("shares_outstanding_cover")
    result["shares_cover_divergence_pct"] = (
        abs(sh - cov) / cov * 100 if sh and cov else None
    )

    # Compute derived values
    equity = result.get("book_value_total")
    shares = result.get("shares_outstanding")

    # Robust intangible adjustment for TANGIBLE book value. Banks tag goodwill
    # and intangibles inconsistently, and missing either OVERSTATES TBV:
    #   • Some stop reporting plain `Goodwill` (e.g. BKU after 2019) and report
    #     `IntangibleAssetsNetIncludingGoodwill` (combined) instead.
    #   • Some report other intangibles under `FiniteLivedIntangibleAssetsNet`
    #     rather than `IntangibleAssetsNetExcludingGoodwill`.
    # Resolve the full goodwill + other-intangibles adjustment via fallbacks.
    intangible_adjustment = _resolve_intangible_adjustment(facts, result)

    # Preferred stock — book/tangible-book per share are per COMMON share, so
    # subtract preferred from total StockholdersEquity first. Skipping this
    # overstates both for every bank with preferred outstanding (FBIZ's $12M
    # pfd is ~$1.44/sh of common book). preferred_present says the filer HAS
    # preferred; preferred_stock is its carrying value (None if unresolved).
    preferred_stock, preferred_present = _resolve_preferred_stock(facts)
    result["preferred_stock"] = preferred_stock
    result["preferred_present"] = preferred_present

    # CARDINAL RULE: the filer reports preferred but we can't resolve its value.
    # A preferred-inclusive figure labeled "common tangible book" is a
    # plausible-wrong number — render n/a instead.
    preferred_unresolved = preferred_present and preferred_stock is None
    if equity is not None and preferred_unresolved:
        result["common_equity"] = None
        result["book_value_per_share"] = None
        result["tangible_book_value_per_share"] = None
    elif equity and shares and shares > 0:
        common_equity = equity - (preferred_stock or 0)
        result["common_equity"] = common_equity
        result["book_value_per_share"] = common_equity / shares
        result["tangible_book_value_per_share"] = (
            common_equity - intangible_adjustment) / shares
    else:
        result["common_equity"] = None
        result["book_value_per_share"] = None
        result["tangible_book_value_per_share"] = None

    # Stamp the latest filing end-date so downstream staleness checks have an
    # anchor. Use the equity concept (every bank reports it every quarter);
    # fall back to total assets, then net income.
    result["sec_as_of"] = (
        _latest_end_date(facts, "StockholdersEquity")
        or _latest_end_date(facts, "Assets")
        or _latest_end_date(facts, "NetIncomeLoss")
    )

    return result


def _resolve_intangible_adjustment(facts: dict, result: dict) -> float:
    """
    Return the total goodwill + other-intangibles to subtract from common
    equity for tangible book value, robust to inconsistent XBRL tagging.

    Resolution order:
      1. other-intangibles: `IntangibleAssetsNetExcludingGoodwill`, else
         `FiniteLivedIntangibleAssetsNet` (current only).
      2. If `Goodwill` is current → adjustment = Goodwill + other-intangibles.
      3. Else if `IntangibleAssetsNetIncludingGoodwill` is current → use it
         directly (it already bundles goodwill + intangibles).
      4. Else → just the other-intangibles (or 0).

    Updates result["goodwill"], result["intangibles"], and stores
    result["intangible_adjustment"] for traceability.
    """
    goodwill = result.get("goodwill")  # plain `Goodwill`, current only
    intangibles = result.get("intangibles")  # IntangibleAssetsNetExcludingGoodwill
    if intangibles is None:
        intangibles = _extract_latest_value(
            facts, "FiniteLivedIntangibleAssetsNet", max_age_years=1)
        result["intangibles"] = intangibles

    incl = _extract_latest_value(
        facts, "IntangibleAssetsNetIncludingGoodwill", max_age_years=1)

    # Guard: goodwill alone cannot exceed goodwill+intangibles. When the plain
    # `Goodwill` tag is larger than the combined figure, that tag is stale or
    # dimensional (e.g. PNFP reads $3.48B goodwill vs $1.88B combined) — trust
    # the combined figure.
    if goodwill is not None and incl is not None and goodwill > incl * 1.05:
        adjustment = incl
    else:
        # Otherwise take the MAX of the company's combined figure and our
        # piecewise (goodwill + other-intangibles) sum, so we never miss
        # intangibles whichever way the bank tags them.
        candidates = []
        if incl is not None:
            candidates.append(incl)
        if goodwill is not None:
            candidates.append(goodwill + (intangibles or 0))
        elif intangibles is not None:
            candidates.append(intangibles)
        adjustment = max(candidates) if candidates else 0.0

    result["intangible_adjustment"] = adjustment
    return adjustment


def _resolve_preferred_stock(facts: dict) -> tuple[float | None, bool]:
    """
    Return (preferred_carrying_value, filer_has_preferred).

    Book / tangible-book per share are per-COMMON-share, so preferred equity
    must be removed from total StockholdersEquity first. This resolves the
    dollar value to subtract and, separately, whether the filer even has
    preferred (so the caller can honor the cardinal rule: preferred present but
    value unresolved → render n/a rather than a preferred-inflated figure).

    Value fallbacks (current-only; the staleness guard in _extract_latest_value
    rejects abandoned tags, e.g. FBIZ's 2022 PreferredStockValueOutstanding,
    USB's 2013 PreferredStockValue). We want the CARRYING value in the equity
    section — par-only for simple issuers, par+APIC for the big banks:
      1. PreferredStockValue                                   (FBIZ, HBAN, C…)
      2. PreferredStockIncludingAdditionalPaidInCapital        (BAC)
      3. PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount (USB, JPM)
      4. PreferredStockValueOutstanding
      5. PreferredStockLiquidationPreferenceValue  (redemption value; last resort)

    "Has preferred" is true when a current PreferredStock* value tag resolves,
    or current PreferredStockShares(Outstanding|Issued) > 0. A filer with no
    preferred at all yields (0.0, False) → common_equity == equity, unchanged.
    """
    value = None
    for concept in (
        "PreferredStockValue",
        "PreferredStockIncludingAdditionalPaidInCapital",
        "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
        "PreferredStockValueOutstanding",
        "PreferredStockLiquidationPreferenceValue",
    ):
        # A par-only tag can read exactly 0 while the real carrying value sits
        # in an untagged APIC line (PNC: PreferredStockValue $0 but ~$4B pfd
        # outstanding). Treat 0 as "keep looking" — never as a resolved value.
        v = _extract_latest_value(facts, concept, max_age_years=1)
        if v:
            value = v
            break

    shares = (
        _extract_latest_value(facts, "PreferredStockSharesOutstanding", max_age_years=1)
        or _extract_latest_value(facts, "PreferredStockSharesIssued", max_age_years=1)
    )
    has_preferred = bool(value) or bool(shares and shares > 0)

    if not has_preferred:
        # No preferred outstanding — subtract nothing.
        return 0.0, False
    # Filer has preferred; value is None when only a par-zero/stale tag exists
    # (unresolved → caller renders n/a per the cardinal rule).
    return value, True


def _latest_end_date(facts: dict, concept: str) -> str | None:
    """Return the most recent period end-date ('YYYY-MM-DD') reported for a
    US-GAAP concept, or None. Used to stamp data freshness."""
    try:
        units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {})
        latest = None
        for entries in units.values():
            for e in entries:
                end = e.get("end")
                if end and (latest is None or end > latest):
                    latest = end
        return latest
    except Exception as e:
        # A missing as-of means downstream staleness validation silently
        # skips this bank — log so the gap is visible.
        print(f"[SEC] _latest_end_date failed for {concept}: {type(e).__name__}: {e}")
        return None


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

    # Preferred stock — book/tangible-book are per COMMON share, so subtract
    # preferred equity first (same resolution as get_latest_fundamentals).
    preferred_stock, preferred_present = _resolve_preferred_stock(facts)
    preferred_tup = _extract_latest_value_with_source(
        facts, "PreferredStockValue", max_age_years=1)
    result["preferred_stock"] = _wrap("preferred_stock", preferred_tup, "PreferredStockValue")
    preferred_unresolved = preferred_present and preferred_stock is None

    if equity and shares and shares > 0 and not preferred_unresolved:
        common_equity = equity - (preferred_stock or 0)
        tbvps = (common_equity - goodwill - intangibles) / shares
        bvps = common_equity / shares
        # Provenance of a computed value: combine the parents
        parents = (
            result["book_value_total"]["source"],
            result["shares_outstanding"]["source"],
            result["goodwill"]["source"],
            result["intangibles"]["source"],
            result["preferred_stock"]["source"],
        )
        result["book_value_per_share"] = {
            "value": bvps,
            "source": Source(
                origin="COMPUTED", concept="book_value_per_share",
                derived_from=(parents[0], parents[1], parents[4]),
                notes="= (StockholdersEquity − Preferred) / SharesOutstanding",
            ),
        }
        result["tangible_book_value_per_share"] = {
            "value": tbvps,
            "source": Source(
                origin="COMPUTED", concept="tangible_book_value_per_share",
                derived_from=parents,
                notes="= (StockholdersEquity − Preferred − Goodwill − Intangibles) / SharesOutstanding",
            ),
        }
    else:
        # Cardinal rule: preferred present but unresolved → n/a, not a
        # preferred-inflated "common" figure.
        note = ("Preferred present but value unresolved"
                if preferred_unresolved else "Insufficient inputs")
        result["book_value_per_share"] = {"value": None, "source": Source(origin="COMPUTED", concept="book_value_per_share", notes=note)}
        result["tangible_book_value_per_share"] = {"value": None, "source": Source(origin="COMPUTED", concept="tangible_book_value_per_share", notes=note)}

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
    # Shared retry (429 / timeouts) so the Filings page doesn't show
    # "Failed to load" on a single hiccup. The old inline loop here swallowed
    # ALL exceptions bare — including code bugs.
    from data.http import get_with_retry
    data = None
    try:
        resp = get_with_retry(url, headers=HEADERS, timeout=15)
        if resp is not None:
            data = resp.json()
    except Exception as e:
        print(f"[SEC] submissions fetch failed for CIK {cik}: {type(e).__name__}: {e}")
    if data is None:
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

    biz_addr = (data.get("addresses", {}) or {}).get("business", {}) or {}
    return {
        "name": data.get("name", ""),
        "cik": raw_cik,
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
        "fiscal_year_end": data.get("fiscalYearEnd", ""),
        "website": (data.get("website") or ""),
        "tickers": data.get("tickers", []),
        "exchanges": data.get("exchanges", []),
        # Corporate profile fields (SEC submissions also carries these).
        "phone": data.get("phone", "") or "",
        "state_of_incorp": data.get("stateOfIncorporation", "") or "",
        "entity_category": data.get("category", "") or "",
        "hq_street": biz_addr.get("street1", "") or "",
        "hq_city": biz_addr.get("city", "") or "",
        "hq_state": biz_addr.get("stateOrCountry", "") or "",
        "hq_zip": biz_addr.get("zipCode", "") or "",
        "recent_filings": filings,
    }


