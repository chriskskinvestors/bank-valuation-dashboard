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
                      max_age_years: int = 2,
                      min_recent_ends: int = 1) -> list[dict]:
    """
    Pull all entries for the first matching concept. Returns list of
    {end, filed, val, form} sorted by end date ascending.

    Skips concepts whose latest filing is older than max_age_years —
    this way we fall through to fresher concepts when a company has
    switched which XBRL tag they use (e.g., PNC stopped reporting
    NetIncomeLoss in 2014 and now uses ProfitLoss).

    min_recent_ends guards against SPARSE-but-fresh series: PNC's Q2-2026
    10-Q (new filing agent) re-tagged NetIncomeLoss after a 12-year gap
    with only two 6-month YTD rows — fresh enough to defeat the staleness
    guard, far too sparse to decompose into quarters (every quarter came
    out NaN). A concept must have at least this many distinct end dates
    inside the freshness window to be chosen over later fallbacks; if NO
    concept clears the bar (e.g. a young filer with 2 quarters of
    history), the first fresh one is used as before.
    """
    if units_priority is None:
        units_priority = ["USD", "USD/shares", "shares", "pure"]

    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=365 * max_age_years)).strftime("%Y-%m-%d")

    sparse_fallback = None
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
            recent_ends = {e.get("end") for e in entries
                           if (e.get("end") or "") >= cutoff}
            if len(recent_ends) < min_recent_ends:
                if sparse_fallback is None:
                    sparse_fallback = (concept, unit_type)
                continue
            picked = _filter_and_dedup(entries)
            if picked:
                return picked
    if sparse_fallback is not None:
        concept, unit_type = sparse_fallback
        return _filter_and_dedup(gaap[concept]["units"][unit_type])
    return []


def _filter_and_dedup(entries: list[dict]) -> list[dict]:
    """Keep 10-K/10-Q rows, most recently filed per end date, sorted by end."""
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
    if not filed:
        return []
    filed.sort(key=lambda x: (x["end"] or "", x["filed"] or ""))
    # Deduplicate by (end, start), NOT end alone: duration concepts carry BOTH
    # a ~3-month and a YTD-cumulative fact at the same end date, and collapsing
    # by end kept an arbitrary one — summing a mix of quarterly and cumulative
    # per-share dividends produced e.g. a $14.60 JPM "DPS TTM" (true ~$5.70).
    # _derive_quarterly_from_ytd picks the right duration per quarter; here
    # every duration must survive. Most-recently-filed still wins per key.
    dedup = {}
    for e in filed:
        key = (e["end"], e.get("start"))
        if key not in dedup or e["filed"] > dedup[key]["filed"]:
            dedup[key] = e
    return sorted(dedup.values(), key=lambda x: x["end"])


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
    Derive single-quarter values for duration concepts. Entries may carry BOTH
    a direct ~3-month fact and a YTD-cumulative fact per quarter (the (end,
    start) dedup keeps both): the direct quarterly fact wins outright; else the
    quarter is YTD(this quarter) − YTD(prior quarter) — both cumulative, same
    fiscal year — the same same-start differencing sec_client uses. A quarter
    that can't be formed either way is None, never a mixed-duration
    subtraction (that mislabeled cumulative dividends as quarterly).

    Returns one entry per (year, quarter) with 'val_quarterly' added ('val'
    keeps the YTD-cumulative figure when that's what the filer tagged).
    """
    by_yq: dict = {}
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
            try:
                m = int(e["end"][5:7])
                qtr = (m - 1) // 3 + 1
            except Exception:
                qtr = None
        if qtr is None:
            continue
        by_yq.setdefault((year, qtr), []).append({**e, "quarter": qtr, "year": year})

    def _latest_filed(cands):
        return sorted(cands, key=lambda x: x.get("filed") or "")[-1]

    out = []
    for (year, qtr) in sorted(by_yq):
        cands = by_yq[(year, qtr)]
        direct = [c for c in cands if _is_quarterly_cf(c)]
        ytd = [c for c in cands if not _is_quarterly_cf(c)]
        if direct:
            e = _latest_filed(direct)
            e["val_quarterly"] = e["val"]
            if ytd:                       # keep the cumulative figure alongside
                e["val"] = _latest_filed(ytd)["val"]
        elif qtr == 1:
            e = _latest_filed(ytd)
            e["val_quarterly"] = e["val"]  # Q1 cumulative == Q1 quarter
        else:
            e = _latest_filed(ytd)
            prior_ytd = [c for c in by_yq.get((year, qtr - 1), [])
                         if not _is_quarterly_cf(c)]
            # Q1 has no separate YTD fact — its direct fact IS the cumulative.
            if not prior_ytd and qtr == 2:
                prior_ytd = by_yq.get((year, 1), [])
            if prior_ytd and e.get("val") is not None:
                pv = _latest_filed(prior_ytd).get("val")
                e["val_quarterly"] = (e["val"] - pv) if pv is not None else None
            else:
                e["val_quarterly"] = None
        out.append(e)
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
    # min_recent_ends=4: a decomposable NI series needs ~quarterly coverage;
    # without it PNC's sparse re-tagged NetIncomeLoss (two 6M-YTD rows) wins
    # on freshness and every quarter derives to NaN.
    ni = _derive_quarterly_from_ytd(
        _extract_series(gaap, _NET_INCOME_CONCEPTS, min_recent_ends=4))
    # DPS is a DURATION concept (declared per share over a period) tagged in
    # both 3-month and YTD-cumulative durations — it needs the same quarterly
    # derivation as the flows. Merging the raw fact used to mix durations and
    # sum cumulatives into "DPS TTM" (JPM: $14.60 vs true ~$5.70).
    dps = _derive_quarterly_from_ytd(_extract_series(gaap, _DPS_CONCEPTS))
    # Shares and equity are point-in-time, not YTD
    shares = _extract_series(gaap, _SHARES_CONCEPTS)
    equity = _extract_series(gaap, _EQUITY_CONCEPTS)

    # Merge on 'end' date
    rows = {}
    for src, key in [(divs, "dividends_q"), (buybacks, "buybacks_q"),
                     (ni, "net_income_q"), (dps, "dps_declared")]:
        for e in src:
            end = e["end"]
            if end not in rows:
                rows[end] = {"end": end, "year": e.get("year"), "quarter": e.get("quarter")}
            rows[end][key] = e.get("val_quarterly")
            rows[end][f"{key}_ytd"] = e.get("val")

    for src, key in [(shares, "shares_outstanding"), (equity, "equity")]:
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

    # Total capital returned per quarter. One known component treats the other
    # as 0 (a bank tagging dividends but no buyback concept genuinely didn't
    # buy back — same convention as total_returned_ttm); BOTH unknown is no
    # observation at all → NaN, never a fabricated $0 quarter.
    _dv = pd.to_numeric(df["dividends_q"], errors="coerce")
    _bb = pd.to_numeric(df["buybacks_q"], errors="coerce")
    df["total_returned_q"] = (_dv.fillna(0) + _bb.fillna(0)).where(
        _dv.notna() | _bb.notna())

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


def _full_window_sum(window: pd.DataFrame, col: str):
    """Sum `col` over the TTM window ONLY when it is FOUR CONSECUTIVE
    quarters, each observed. pandas sums an all-NaN window to 0.0 — a
    plausible-wrong $0 (PNC Q2-2026) — and a partial or gapped window to a
    mislabeled "TTM" (3 quarters presented as twelve months understates ~25%;
    audit A21: 12 months or None). Supersedes the earlier any-quarter-sums
    rule (test_capital_return_pnc_regression pins the supersession)."""
    if col not in window.columns or len(window) < 4:
        return None
    s = pd.to_numeric(window[col], errors="coerce")
    if not s.notna().all():
        return None
    ends = pd.to_datetime(window["end"], errors="coerce") if "end" in window.columns else None
    if ends is None or ends.isna().any():
        return None
    if ends.sort_values().diff().dropna().dt.days.max() > 100:  # quarterly cadence
        return None
    return float(s.sum())


def compute_ttm_capital_return(timeline: pd.DataFrame) -> dict:
    """
    Trailing 12-month summary of capital return activity. Every TTM figure is
    a full-window sum or None (see _full_window_sum) — never a partial total.
    """
    if timeline.empty:
        return {}
    ttm = timeline.tail(4)  # last 4 quarters

    def _sum_or_none(col):
        return _full_window_sum(ttm, col)

    ni_ttm = _sum_or_none("net_income_q")
    divs_ttm = _sum_or_none("dividends_q")
    bb_ttm = _sum_or_none("buybacks_q")
    # One known component treats the other as 0 (banks that never buy back);
    # BOTH unknown is not a $0 return — it's no observation at all (PNC tags
    # no cash-flow dividend/buyback concepts) → None, so the UI shows n/a
    # instead of "returns 0% of income".
    total_ttm = (None if (divs_ttm is None and bb_ttm is None)
                 else (divs_ttm or 0) + (bb_ttm or 0))

    # Share count change TTM
    share_start = ttm["shares_outstanding"].iloc[0] if "shares_outstanding" in ttm.columns and len(ttm) > 0 else None
    share_end = ttm["shares_outstanding"].iloc[-1] if "shares_outstanding" in ttm.columns and len(ttm) > 0 else None
    share_chg_pct = None
    if share_start and share_end and share_start > 0:
        share_chg_pct = (share_end / share_start - 1) * 100

    # Latest DPS (quarterly)
    dps_latest = ttm["dps_declared"].dropna().iloc[-1] if "dps_declared" in ttm.columns and ttm["dps_declared"].notna().any() else None
    dps_ttm = _sum_or_none("dps_declared")

    return {
        "net_income_ttm": ni_ttm,
        "dividends_ttm": divs_ttm,
        "buybacks_ttm": bb_ttm,
        "total_returned_ttm": total_ttm,
        "dps_ttm": dps_ttm,
        "dps_latest_quarterly": dps_latest,
        "payout_ratio_ttm": (divs_ttm / ni_ttm) if ni_ttm and divs_ttm and ni_ttm > 0 else None,
        "buyback_ratio_ttm": (bb_ttm / ni_ttm) if ni_ttm and bb_ttm and ni_ttm > 0 else None,
        "total_return_ratio_ttm": ((total_ttm / ni_ttm)
                                   if total_ttm is not None and ni_ttm and ni_ttm > 0
                                   else None),
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
        # Full consecutive 4-quarter windows only — a partial-window sum would
        # produce a plausible-wrong growth % (same rule as _sum_or_none).
        if col not in df.columns or len(df) < 4:
            return None
        s = pd.to_numeric(df[col], errors="coerce")
        if not s.notna().all():
            return None
        v = float(s.sum())
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
    no_observation = (ttm.get("dividends_ttm") is None
                      and ttm.get("buybacks_ttm") is None)
    if not market_cap or market_cap <= 0 or no_observation:
        # no_observation: neither dividend nor buyback dollars are tagged at
        # all (PNC) — a 0.00% yield would claim the company returns nothing;
        # unknown renders n/a.
        return {
            "dividend_yield_pct": None,
            "buyback_yield_pct": None,
            "total_shareholder_yield_pct": None,
        }
    # None component → None yield: absent XBRL data is UNKNOWN, and a
    # fabricated "0.00%" shareholder yield reads as a real figure (it showed
    # for every major bank whose dividend/buyback cash-flow lines aren't in
    # undimensioned companyfacts — JPM/BAC/WFC/C/USB tag them dimensionally).
    def _yld(v):
        return (v / market_cap) * 100 if v is not None else None
    return {
        "dividend_yield_pct": _yld(ttm.get("dividends_ttm")),
        "buyback_yield_pct": _yld(ttm.get("buybacks_ttm")),
        "total_shareholder_yield_pct": _yld(ttm.get("total_returned_ttm")),
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
