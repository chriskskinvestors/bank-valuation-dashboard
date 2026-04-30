"""
Capital Return Attribution — decompose shareholder capital returns.

Sources:
- SEC XBRL for authoritative holding-company numbers:
    - PaymentsOfDividendsCommonStock  (dividends paid to common shareholders, $)
    - PaymentsForRepurchaseOfCommonStock  (share buybacks, $)
    - CommonStockSharesOutstanding  (point-in-time share count, for buyback inference)
    - CommonStockDividendsPerShareDeclared  (DPS)
    - NetIncomeLoss  (net income)
    - StockholdersEquity  (book equity)
    - CommonStockSharesRepurchased  (alternative share-count measure)

Key outputs per period:
    - Dividends paid ($)
    - Buybacks paid ($)
    - Total capital returned ($)
    - Total shareholder yield % = (div + buyback) / market cap
    - Payout ratio = div / NI
    - Buyback ratio = buyback / NI
    - Total return ratio = (div + buyback) / NI
    - Share count change (issuance vs repurchase)
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime

from data.sec_client import fetch_company_facts


# XBRL concepts we look up, in priority order (first successful match wins).
#
# IMPORTANT data semantics:
#   - PaymentsOfDividendsCommonStock       = common dividends only (preferred)
#   - PaymentsOfDividends                  = total (common + preferred)
#   - PaymentsOfDividendsPreferredStockAndPreferenceStock = preferred only
# Most large banks file only `PaymentsOfDividends` (total). When we fall back
# to that, the output includes preferred dividends (JPM = ~5% overstatement).
# For banks with `PaymentsOfDividendsCommonStock` filed, we use the specific
# common number. For banks that file both total AND preferred, we subtract.
_DIVIDEND_COMMON_CONCEPTS = [
    "PaymentsOfDividendsCommonStock",
    "DividendsCommonStockCash",
]
_DIVIDEND_TOTAL_CONCEPTS = [
    "PaymentsOfDividends",
]
_DIVIDEND_PREFERRED_CONCEPTS = [
    "PaymentsOfDividendsPreferredStockAndPreferenceStock",
    "DividendsPreferredStockCash",
]

# Buyback concepts — only common stock, never preferred.
#   - PaymentsForRepurchaseOfCommonStock = what matters (net cash paid)
#   - StockRepurchasedAndRetiredDuringPeriodValue = retired value (same
#     thing at most banks)
#   - PaymentsForRepurchaseOfEquity = DO NOT USE — includes preferred
#     redemptions and employee tax withholdings
_BUYBACK_CONCEPTS = [
    "PaymentsForRepurchaseOfCommonStock",
    "StockRepurchasedAndRetiredDuringPeriodValue",  # cross-check / fallback
]
_NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",                                      # standard — most banks
    "NetIncomeLossAvailableToCommonStockholdersBasic",    # PNC-style — NI to common
    "ProfitLoss",                                         # broadest — includes minority int
]
_EQUITY_CONCEPTS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]
_SHARES_CONCEPTS = [
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
]
_DPS_CONCEPTS = [
    "CommonStockDividendsPerShareDeclared",
    "CommonStockDividendsPerShareCashPaid",
]


def _extract_series(gaap: dict, concept_names: list[str],
                      units_priority: list[str] = None,
                      max_age_years: int = 2) -> list[dict]:
    """
    Pull all entries for the first matching concept. Returns list of
    {end, filed, val, form} sorted by end date ascending.

    Skips concepts whose latest filing is older than max_age_years —
    this way we fall through to fresher concepts when a company has
    switched which XBRL tag they use (e.g., PNC stopped reporting
    NetIncomeLoss in 2014 and now uses ProfitLoss).
    """
    if units_priority is None:
        units_priority = ["USD", "USD/shares", "shares", "pure"]

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

    for concept in concept_names:
        concept_data = gaap.get(concept, {})
        units = concept_data.get("units", {})
        for unit_type in units_priority:
            entries = units.get(unit_type, [])
            if not entries:
                continue
            # Staleness guard: if most recent entry is older than cutoff, skip
            # this concept entirely so we try the next fallback.
            max_end = max((e.get("end", "") for e in entries), default="")
            if max_end < cutoff:
                continue
            # Filter to 10-K / 10-Q
            filed = [
                {
                    "end": e.get("end"),
                    "filed": e.get("filed"),
                    "val": e.get("val"),
                    "form": e.get("form"),
                    "fp": e.get("fp"),
                    "fy": e.get("fy"),
                    "start": e.get("start"),  # for cash flow / income statement items
                }
                for e in entries
                if e.get("form") in ("10-K", "10-Q")
            ]
            if filed:
                filed.sort(key=lambda x: (x["end"] or "", x["filed"] or ""))
                # Deduplicate by end: keep most recently filed for each end date
                dedup = {}
                for e in filed:
                    end = e["end"]
                    if end not in dedup or e["filed"] > dedup[end]["filed"]:
                        dedup[end] = e
                return sorted(dedup.values(), key=lambda x: x["end"])
    return []


def _is_quarterly_cf(entry: dict) -> bool:
    """
    Cash-flow statement entries are CUMULATIVE within fiscal year.
    Q1 covers 3 months, Q2 covers 6 months, etc.
    Same YTD semantics as FDIC NETINC.
    This function returns False if entry is YTD cumulative (default for
    FCF statement items), True if it's somehow already period-only.
    """
    # Heuristic: if start/end span ~90 days, it's quarterly; >90 it's YTD
    try:
        start = datetime.strptime(entry.get("start", ""), "%Y-%m-%d")
        end = datetime.strptime(entry.get("end", ""), "%Y-%m-%d")
        days = (end - start).days
        return days <= 100
    except Exception:
        return False


def _derive_quarterly_from_ytd(entries: list[dict]) -> list[dict]:
    """
    For cash-flow statement concepts (YTD-cumulative), derive the single-
    quarter values by subtracting prior quarter within the same fiscal year.

    Returns entries with 'val_quarterly' field added.
    """
    # Group by fiscal year
    by_year = {}
    for e in entries:
        try:
            year = int(e["end"][:4]) if e.get("end") else None
        except Exception:
            year = None
        if year is None:
            continue
        # Infer fiscal quarter from fp field or end-month
        fp = e.get("fp", "")
        if fp in ("Q1", "Q2", "Q3"):
            qtr = int(fp[1])
        elif fp == "FY":
            qtr = 4
        else:
            # Derive from end month
            try:
                m = int(e["end"][5:7])
                qtr = (m - 1) // 3 + 1
            except Exception:
                qtr = None
        if qtr is None:
            continue
        e = {**e, "quarter": qtr, "year": year}
        by_year.setdefault(year, []).append(e)

    out = []
    for year, group in by_year.items():
        group = sorted(group, key=lambda x: x["quarter"])
        for i, e in enumerate(group):
            if _is_quarterly_cf(e):
                # Already quarterly — use as-is
                e["val_quarterly"] = e["val"]
            elif e["quarter"] == 1:
                e["val_quarterly"] = e["val"]
            else:
                # Find prior quarter in same year
                prior = None
                for j in range(i - 1, -1, -1):
                    if group[j]["quarter"] == e["quarter"] - 1:
                        prior = group[j]
                        break
                if prior is not None and e.get("val") is not None and prior.get("val") is not None:
                    e["val_quarterly"] = e["val"] - prior["val"]
                else:
                    e["val_quarterly"] = None
            out.append(e)
    out.sort(key=lambda x: (x["year"], x["quarter"]))
    return out


def build_capital_return_timeline(cik: int, lookback_quarters: int = 20) -> pd.DataFrame:
    """
    Build a quarterly timeline of capital return data.

    Columns:
        date, year, quarter, net_income_q, dividends_q, buybacks_q,
        total_returned_q, shares_outstanding, dps_declared,
        equity, share_change (QoQ)
    """
    if not cik:
        return pd.DataFrame()

    facts = fetch_company_facts(cik)
    if not facts:
        return pd.DataFrame()
    gaap = facts.get("facts", {}).get("us-gaap", {})

    # ── DIVIDEND RESOLUTION ────────────────────────────────────────────
    # Strategy:
    #   1. If common-specific concept exists, use it → pure common dividends
    #   2. Else if total AND preferred are both filed, compute common = total − preferred
    #   3. Else fall back to total (includes preferred; will slightly overstate
    #      common dividends for banks with significant preferred stock)
    divs_common_raw = _extract_series(gaap, _DIVIDEND_COMMON_CONCEPTS)
    if divs_common_raw:
        divs = _derive_quarterly_from_ytd(divs_common_raw)
        dividend_source = "common-specific"
    else:
        total_divs = _extract_series(gaap, _DIVIDEND_TOTAL_CONCEPTS)
        pref_divs = _extract_series(gaap, _DIVIDEND_PREFERRED_CONCEPTS)
        if total_divs and pref_divs:
            # Subtract preferred from total to estimate common.
            # Guard against None values on either side (some banks file
            # a total without a matching preferred at every period).
            by_end = {e["end"]: e for e in total_divs}
            for p in pref_divs:
                if p["end"] in by_end:
                    total = by_end[p["end"]].copy()
                    tv = total.get("val")
                    pv = p.get("val")
                    if tv is None:
                        continue  # can't subtract from nothing
                    total["val"] = tv - (pv or 0)  # treat missing preferred as 0
                    by_end[p["end"]] = total
            divs = _derive_quarterly_from_ytd(sorted(by_end.values(), key=lambda x: x["end"]))
            dividend_source = "total minus preferred"
        elif total_divs:
            divs = _derive_quarterly_from_ytd(total_divs)
            dividend_source = "total (includes preferred)"
        else:
            divs = []
            dividend_source = "unavailable"

    buybacks = _derive_quarterly_from_ytd(_extract_series(gaap, _BUYBACK_CONCEPTS))
    ni = _derive_quarterly_from_ytd(_extract_series(gaap, _NET_INCOME_CONCEPTS))
    # Shares and equity are point-in-time, not YTD
    shares = _extract_series(gaap, _SHARES_CONCEPTS)
    equity = _extract_series(gaap, _EQUITY_CONCEPTS)
    dps = _extract_series(gaap, _DPS_CONCEPTS)

    # Merge on 'end' date
    rows = {}
    for src, key in [(divs, "dividends_q"), (buybacks, "buybacks_q"), (ni, "net_income_q")]:
        for e in src:
            end = e["end"]
            if end not in rows:
                rows[end] = {"end": end, "year": e.get("year"), "quarter": e.get("quarter")}
            rows[end][key] = e.get("val_quarterly")
            rows[end][f"{key}_ytd"] = e.get("val")

    for src, key in [(shares, "shares_outstanding"), (equity, "equity"), (dps, "dps_declared")]:
        for e in src:
            end = e["end"]
            if end not in rows:
                rows[end] = {"end": end}
            rows[end][key] = e.get("val")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows.values()).sort_values("end").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.dropna(subset=["date"])

    # Ensure all expected columns exist (some banks don't report everything)
    for col in ["dividends_q", "buybacks_q", "net_income_q",
                "shares_outstanding", "equity", "dps_declared"]:
        if col not in df.columns:
            df[col] = None

    # Fill shares & equity forward where reported less frequently
    for col in ["shares_outstanding", "equity"]:
        df[col] = df[col].ffill()

    # Compute total capital returned per quarter
    df["total_returned_q"] = (
        df["dividends_q"].fillna(0) + df["buybacks_q"].fillna(0)
    )

    # Compute payout / buyback / return ratios
    ni_q = df["net_income_q"]
    safe_ni = ni_q.where(ni_q > 0)
    df["payout_ratio_q"] = df["dividends_q"] / safe_ni
    df["buyback_ratio_q"] = df["buybacks_q"] / safe_ni
    df["total_return_ratio_q"] = df["total_returned_q"] / safe_ni

    # Share count change (QoQ). pd.Series.diff() breaks on None values in
    # object dtype — convert to numeric first (coercing errors to NaN).
    df["shares_outstanding"] = pd.to_numeric(df["shares_outstanding"], errors="coerce")
    df["share_change"] = df["shares_outstanding"].diff()
    df["share_change_pct"] = df["shares_outstanding"].pct_change() * 100

    # Keep only most recent N quarters
    df = df.tail(lookback_quarters).reset_index(drop=True)
    # Attach provenance as attribute so caller can display it
    df.attrs["dividend_source"] = dividend_source
    return df


def compute_ttm_capital_return(timeline: pd.DataFrame) -> dict:
    """
    Trailing 12-month summary of capital return activity.
    """
    if timeline.empty:
        return {}
    ttm = timeline.tail(4)  # last 4 quarters

    ni_ttm = ttm["net_income_q"].sum(skipna=True) if "net_income_q" in ttm.columns else None
    divs_ttm = ttm["dividends_q"].sum(skipna=True) if "dividends_q" in ttm.columns else None
    bb_ttm = ttm["buybacks_q"].sum(skipna=True) if "buybacks_q" in ttm.columns else None
    total_ttm = (divs_ttm or 0) + (bb_ttm or 0)

    # Share count change TTM
    share_start = ttm["shares_outstanding"].iloc[0] if "shares_outstanding" in ttm.columns and len(ttm) > 0 else None
    share_end = ttm["shares_outstanding"].iloc[-1] if "shares_outstanding" in ttm.columns and len(ttm) > 0 else None
    share_chg_pct = None
    if share_start and share_end and share_start > 0:
        share_chg_pct = (share_end / share_start - 1) * 100

    # Latest DPS (quarterly)
    dps_latest = ttm["dps_declared"].dropna().iloc[-1] if "dps_declared" in ttm.columns and ttm["dps_declared"].notna().any() else None
    dps_ttm = ttm["dps_declared"].sum(skipna=True) if "dps_declared" in ttm.columns else None

    return {
        "net_income_ttm": ni_ttm,
        "dividends_ttm": divs_ttm,
        "buybacks_ttm": bb_ttm,
        "total_returned_ttm": total_ttm,
        "dps_ttm": dps_ttm,
        "dps_latest_quarterly": dps_latest,
        "payout_ratio_ttm": (divs_ttm / ni_ttm) if ni_ttm and divs_ttm and ni_ttm > 0 else None,
        "buyback_ratio_ttm": (bb_ttm / ni_ttm) if ni_ttm and bb_ttm and ni_ttm > 0 else None,
        "total_return_ratio_ttm": (total_ttm / ni_ttm) if ni_ttm and ni_ttm > 0 else None,
        "shares_start": share_start,
        "shares_end": share_end,
        "share_change_pct_ttm": share_chg_pct,
    }


def compute_yoy_growth(timeline: pd.DataFrame) -> dict:
    """YoY growth in DPS, buybacks, total return. Useful for dividend growth stories."""
    if timeline.empty or len(timeline) < 8:
        return {}

    # Last 4Q TTM vs 4Q prior
    last_4 = timeline.tail(4)
    prior_4 = timeline.iloc[-8:-4] if len(timeline) >= 8 else pd.DataFrame()

    def _ttm(df, col):
        if col not in df.columns:
            return None
        v = df[col].sum(skipna=True)
        return v if v else None

    def _growth(curr, prior):
        if curr is None or prior is None or prior == 0:
            return None
        return (curr / prior - 1) * 100

    div_curr = _ttm(last_4, "dividends_q")
    div_prior = _ttm(prior_4, "dividends_q")
    bb_curr = _ttm(last_4, "buybacks_q")
    bb_prior = _ttm(prior_4, "buybacks_q")

    # DPS growth
    dps_curr_ttm = _ttm(last_4, "dps_declared")
    dps_prior_ttm = _ttm(prior_4, "dps_declared")

    return {
        "dividends_yoy_pct": _growth(div_curr, div_prior),
        "buybacks_yoy_pct": _growth(bb_curr, bb_prior),
        "total_return_yoy_pct": _growth(
            (div_curr or 0) + (bb_curr or 0), (div_prior or 0) + (bb_prior or 0)
        ) if (div_curr or bb_curr) and (div_prior or bb_prior) else None,
        "dps_yoy_pct": _growth(dps_curr_ttm, dps_prior_ttm),
    }


def compute_shareholder_yield(timeline: pd.DataFrame, market_cap: float | None) -> dict:
    """
    Compute total shareholder yield:
        shareholder_yield = (TTM dividends + TTM buybacks) / market cap

    Break apart into dividend yield and buyback yield.
    """
    ttm = compute_ttm_capital_return(timeline)
    if not market_cap or market_cap <= 0:
        return {
            "dividend_yield_pct": None,
            "buyback_yield_pct": None,
            "total_shareholder_yield_pct": None,
        }
    divs = ttm.get("dividends_ttm") or 0
    bb = ttm.get("buybacks_ttm") or 0
    total = ttm.get("total_returned_ttm") or 0
    return {
        "dividend_yield_pct": (divs / market_cap) * 100 if divs else 0,
        "buyback_yield_pct": (bb / market_cap) * 100 if bb else 0,
        "total_shareholder_yield_pct": (total / market_cap) * 100 if total else 0,
    }


def summarize_capital_return(cik: int, market_cap: float | None = None,
                               lookback_quarters: int = 20) -> dict:
    """Top-level helper: returns timeline + TTM + YoY growth + shareholder yield."""
    timeline = build_capital_return_timeline(cik, lookback_quarters)
    if timeline.empty:
        return {"timeline": pd.DataFrame(), "ttm": {}, "growth": {}, "yield": {},
                "dividend_source": "unavailable"}
    return {
        "timeline": timeline,
        "ttm": compute_ttm_capital_return(timeline),
        "growth": compute_yoy_growth(timeline),
        "yield": compute_shareholder_yield(timeline, market_cap),
        "dividend_source": timeline.attrs.get("dividend_source", "unknown"),
    }
