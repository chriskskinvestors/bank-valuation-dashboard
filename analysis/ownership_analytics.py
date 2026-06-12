"""
Phase-1 holder analytics for the Ownership tabs (docs/SNL-BUILD-PLAN.md §13).

Everything here is computed from OUR stored 13F data only:
  - latest window: {TICKER}.json (the largest filers found via EDGAR
    full-text search — a sample, NOT the complete institutional base)
  - quarter history: {TICKER}_{YYYYQn}.json snapshots assembled by
    data.form13f_client.get_holder_history (accumulates going forward)

Phase 2 (full-holder-book analysis: style/orientation from each holder's
entire 13F book) is a separate later build. Pure computation — no network;
storage access goes through data.form13f_client's bindings so tests can
fake the store the same way tests/test_form13f_history.py does.

DENOMINATOR HONESTY: every "pct" here is a share of TOTAL 13F-REPORTED
SHARES IN OUR STORED SAMPLE, not of shares outstanding. UI must label
accordingly (see SCOPE_LABEL).

Formulas (pinned by tests/test_ownership_analytics.py):
  turnover_pct = mean(|Δshares| between consecutive stored quarters)
                 ÷ mean(shares across stored quarters) × 100
    e.g. 100 → 150 → 100: mean |Δ| = (50+50)/2 = 50; mean position =
    350/3 = 116.67; turnover = 50/116.67 = 42.86% per period.
    A gap in stored quarters counts as one period (consecutive
    *available* observations) — coverage is sparse and we say so.
  HHI = Σ (holder share of sampled total × 100)² — 0–10,000 scale.
"""

from __future__ import annotations

import statistics

# Module reference (not from-imports) so the storage fakes that tests patch
# onto form13f_client (save_json/load_json/list_files) apply here too.
from data import form13f_client as f13

# One label for every UI surface that shows these numbers.
SCOPE_LABEL = (
    "Computed from stored 13F filings of the largest reporting institutions "
    "in our coverage — a sample, not the complete institutional base; "
    "percentages are of sampled 13F-reported shares, not shares outstanding."
)

TURNOVER_CATEGORIES = ("Very Low", "Low", "Moderate", "High")


def _log(msg: str) -> None:
    print(f"[OWN13F] {msg}")


def _latest_holders(ticker: str) -> list[dict]:
    """Holders from the latest-window store ({TICKER}.json), [] if absent."""
    if not ticker:
        return []
    snap = f13.load_json(f13.FORM13F_CACHE_PREFIX, f"{ticker.upper()}.json") or {}
    return snap.get("holders") or []


def holder_turnover(ticker: str) -> dict[str, dict]:
    """
    Per-holder position turnover from stored quarterly snapshots.

    Returns {holder_name: {"turnover_pct": float | None,
                           "category": str | None}} over the union of
    holders in the quarter history and the latest window.

    turnover_pct = mean(|Δshares| between consecutive stored quarters)
                   ÷ mean(shares across stored quarters) × 100.
    Requires >= 2 quarters of stored share counts; holders without that
    history get {"turnover_pct": None, "category": None} — honest, never
    imputed. Category is the quartile of turnover_pct WITHIN THIS BANK'S
    holder set (Very Low ≤ Q1 < Low ≤ median < Moderate ≤ Q3 < High);
    None when fewer than 2 holders have computable turnover (quartiles
    are meaningless on a single point).
    """
    history = f13.get_holder_history(ticker) if ticker else {}
    latest_names = {h.get("filer_name")
                    for h in _latest_holders(ticker) if h.get("filer_name")}
    all_names = set(history) | latest_names
    if not all_names:
        _log(f"holder_turnover: no stored 13F data for {ticker!r}")
        return {}

    raw: dict[str, float | None] = {}
    for name in all_names:
        per_q = history.get(name) or {}
        obs = [v["shares"] for _, v in sorted(per_q.items())
               if isinstance(v.get("shares"), (int, float))]
        if len(obs) < 2:
            raw[name] = None
            continue
        avg_position = sum(obs) / len(obs)
        if avg_position <= 0:
            raw[name] = None  # can't scale by a zero/negative position
            continue
        deltas = [abs(b - a) for a, b in zip(obs, obs[1:])]
        raw[name] = (sum(deltas) / len(deltas)) / avg_position * 100

    values = sorted(v for v in raw.values() if v is not None)
    qs = statistics.quantiles(values, n=4) if len(values) >= 2 else None

    def _category(v: float | None) -> str | None:
        if v is None or qs is None:
            return None
        if v <= qs[0]:
            return "Very Low"
        if v <= qs[1]:
            return "Low"
        if v <= qs[2]:
            return "Moderate"
        return "High"

    return {name: {"turnover_pct": v, "category": _category(v)}
            for name, v in raw.items()}


def holder_concentration(ticker: str) -> dict | None:
    """
    Concentration of the latest stored 13F window.

    Returns {"top5_pct", "top10_pct", "hhi", "n_holders"} where every pct
    is a share of TOTAL 13F-REPORTED SHARES IN OUR STORED SAMPLE (the
    largest filers found) — NOT shares outstanding. HHI is the sum of
    squared percentage shares (0–10,000; 10,000 = one holder owns the
    whole sampled base). None (with one log line) when nothing is stored.
    """
    holders = _latest_holders(ticker)
    shares = sorted((h.get("shares") for h in holders
                     if (h.get("shares") or 0) > 0), reverse=True)
    if not shares:
        _log(f"holder_concentration: no stored 13F snapshot for {ticker!r}")
        return None
    total = sum(shares)
    pcts = [s / total * 100 for s in shares]
    return {
        "top5_pct": sum(pcts[:5]),
        "top10_pct": sum(pcts[:10]),
        "hhi": sum(p * p for p in pcts),
        "n_holders": len(shares),
    }


def crossholdings(ticker: str, universe_tickers: list[str],
                  min_holders: int = 2) -> list[dict]:
    """
    Which other covered banks this bank's 13F holders also own, from the
    stored latest snapshots of each ticker (the SNL Crossholdings tab,
    inferred via cross-join of our universe's 13F data — SNL itself shows
    this name-only; the overlap counts are our beat-SNL analytic).

    Returns [{"other_ticker", "shared_holders", "names_sample"}] sorted by
    shared_holders desc (ticker asc tiebreak). Holders are matched by
    filer CIK; names_sample lists up to 5 shared holders ordered by their
    position value in THIS bank. Peers below min_holders shared holders
    are dropped; self is excluded. [] (one log line) when this bank has
    no stored snapshot. Coverage caveat: both sides are samples of the
    largest filers, so overlap counts are floors, not totals.
    """
    base = _latest_holders(ticker)
    if not base:
        _log(f"crossholdings: no stored 13F snapshot for {ticker!r}")
        return []
    base_by_cik = {h["filer_cik"]: h for h in base if h.get("filer_cik")}

    out = []
    seen = set()
    for other in universe_tickers or []:
        ot = (other or "").upper()
        if not ot or ot == ticker.upper() or ot in seen:
            continue
        seen.add(ot)
        other_ciks = {h.get("filer_cik")
                      for h in _latest_holders(ot) if h.get("filer_cik")}
        shared = set(base_by_cik) & other_ciks
        if len(shared) < min_holders:
            continue
        ranked = sorted(shared, reverse=True,
                        key=lambda c: base_by_cik[c].get("value_usd") or 0)
        out.append({
            "other_ticker": ot,
            "shared_holders": len(shared),
            "names_sample": [base_by_cik[c].get("filer_name")
                             for c in ranked[:5]],
        })
    out.sort(key=lambda r: (-r["shared_holders"], r["other_ticker"]))
    return out


def ownership_buckets(ticker: str) -> dict:
    """
    Institutional-ownership summary feeding the future Ownership Summary tab.

    Returns {"institutional_shares": float | None,
             "by_quarter_change": {quarter: {"total_shares", "change_pct"}}}.
    institutional_shares = total shares across the latest stored window
    (None when nothing stored). by_quarter_change sums stored shares per
    quarter, oldest→newest, with change_pct vs the prior stored quarter
    (None for the first quarter or a zero prior). Per-quarter totals
    reflect STORED COVERAGE — a holder appearing/vanishing can be a
    coverage gap, not a trade; UI must carry SCOPE_LABEL.
    """
    holders = _latest_holders(ticker)
    institutional_shares = (sum(h.get("shares") or 0 for h in holders)
                            if holders else None)

    history = f13.get_holder_history(ticker) if ticker else {}
    totals: dict[str, float] = {}
    for per_q in history.values():
        for quarter, v in per_q.items():
            s = v.get("shares")
            if isinstance(s, (int, float)):
                totals[quarter] = totals.get(quarter, 0.0) + s

    by_quarter_change: dict[str, dict] = {}
    prev = None
    for quarter in sorted(totals):
        cur = totals[quarter]
        chg = ((cur - prev) / prev * 100) if prev else None
        by_quarter_change[quarter] = {"total_shares": cur, "change_pct": chg}
        prev = cur

    if not holders and not totals:
        _log(f"ownership_buckets: no stored 13F data for {ticker!r}")
    return {"institutional_shares": institutional_shares,
            "by_quarter_change": by_quarter_change}
