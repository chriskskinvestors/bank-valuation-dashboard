"""
ETF look-through valuation for the Market & Macro "Bank Sector" section
(docs/HOME-MACRO-PLAN.md §2). An ETF has no direct P/E or P/TBV, so we
compute the blended figures from the actual book: pull holdings + weights,
fetch each constituent's TTM ratios, and aggregate.

Requires the FMP Ultimate plan (the /etf/holdings endpoint is Ultimate-only;
denied on Premium — owner upgraded 2026-06-16). No access / no key → holdings
comes back empty and the renderer shows n/a, never a fabricated ratio.

Aggregation (the correct way to blend ratios across a portfolio):
  • P/E and P/TBV — HARMONIC, weighted by holding weight: an index's P/E is
    total price / total earnings, i.e. 1 / Σ(wᵢ · Eᵢ/Pᵢ). Arithmetic-averaging
    P/E overweights expensive names and is wrong.
  • Dividend yield — arithmetic weighted average (a portfolio's yield IS the
    weighted average of its components' yields).
Each metric normalizes weights over only the holdings that actually carry it,
so missing constituent data shrinks coverage rather than distorting the blend.

Constituent metrics come from data.fmp_client.get_fundamentals_batch (TTM
ratios, parallel + 6h-cached per ticker). P/TBV per name is derived as
price/TBVPS = (P/B) · BVPS/TBVPS, since FMP exposes P/B, BVPS and TBVPS but
not P/TBV directly.

The blended result is cached per ETF for 6h (the underlying ratios move
quarterly; holdings drift slowly), so only the first view per ETF per window
pays the fan-out cost.
"""

from __future__ import annotations

from datetime import datetime

from data.fmp_client import _get, get_fundamentals_batch

CACHE_KEY = "etf_lookthrough_valuation"
CACHE_TTL_SECONDS = 21600  # 6h


def get_holdings(ticker: str) -> list[tuple[str, float]]:
    """[(constituent_ticker, weight_percent), ...] for an ETF via FMP
    /etf/holdings. Cash / money-market rows (no `asset` ticker) are dropped.
    Empty list when the endpoint is denied (non-Ultimate), keyless, or fails."""
    data = _get("etf/holdings", {"symbol": ticker.upper()})
    if not isinstance(data, list):
        return []
    out: list[tuple[str, float]] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        asset = (r.get("asset") or "").strip().upper()
        w = r.get("weightPercentage")
        if asset and w is not None:
            try:
                out.append((asset, float(w)))
            except (TypeError, ValueError):
                continue
    return out


def blend_valuation(holdings: list[tuple[str, float]], metrics: dict) -> dict:
    """Blend constituent ratios into look-through P/E, P/TBV, dividend yield.

    holdings: [(ticker, weight%)]. metrics: {ticker: {pe_ratio, pb_ratio, bvps,
    tbvps, dividend_yield}} (dividend_yield in percent, per get_fundamentals).
    Pure / unit-tested. Returns blended values (None when no holding carries the
    metric) plus coverage counts.
    """
    pe_num = pe_den = 0.0
    ptbv_num = ptbv_den = 0.0
    dy_num = dy_den = 0.0
    n_pe = n_ptbv = n_dy = 0

    for tk, w in holdings:
        if w is None or w <= 0:
            continue
        m = metrics.get(tk) or metrics.get(tk.upper()) or {}

        pe = m.get("pe_ratio")
        if pe is not None and pe > 0:
            pe_num += w
            pe_den += w / pe
            n_pe += 1

        pb, bvps, tbvps = m.get("pb_ratio"), m.get("bvps"), m.get("tbvps")
        if pb is not None and pb > 0 and bvps and tbvps and tbvps > 0:
            ptbv = pb * bvps / tbvps
            if ptbv > 0:
                ptbv_num += w
                ptbv_den += w / ptbv
                n_ptbv += 1

        dy = m.get("dividend_yield")
        if dy is not None:
            dy_num += w * dy
            dy_den += w
            n_dy += 1

    return {
        "pe": (pe_num / pe_den) if pe_den > 0 else None,
        "ptbv": (ptbv_num / ptbv_den) if ptbv_den > 0 else None,
        "dividend_yield": (dy_num / dy_den) if dy_den > 0 else None,
        "n_holdings": len(holdings),
        "n_pe": n_pe,
        "n_ptbv": n_ptbv,
        "n_dy": n_dy,
    }


def _empty() -> dict:
    return {"pe": None, "ptbv": None, "dividend_yield": None,
            "n_holdings": 0, "n_pe": 0, "n_ptbv": 0, "n_dy": 0}


def get_etf_valuation(ticker: str) -> dict:
    """Look-through valuation for `ticker` (blended P/E, P/TBV, dividend yield),
    cached 6h per ETF. All-None shape when holdings are unavailable."""
    from data import cache
    from data.freshness import is_fresh

    ticker = ticker.upper()
    key = f"{CACHE_KEY}:{ticker}"
    cached = cache.get(key)
    if is_fresh(cached, CACHE_TTL_SECONDS) and cached.get("val"):
        return cached["val"]

    holdings = get_holdings(ticker)
    if not holdings:
        return _empty()
    metrics = get_fundamentals_batch([t for t, _ in holdings])
    val = blend_valuation(holdings, metrics)
    cache.put(key, {"cached_at": datetime.now().isoformat(), "val": val})
    return val
