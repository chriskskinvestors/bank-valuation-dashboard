"""Historical per-share series from SEC companyfacts for the Trends view.

The FDIC engine has no historical share count, so per-share trends (TBV/share, book
value/share) come from SEC companyfacts (HoldCo basis). Goodwill / intangibles are
often tagged only in the annual 10-K, so a naive per-quarter join overstates TBV/
share in the off-quarters — these slow-moving balance-sheet items are FORWARD-FILLED
(carried from their last reported value); equity and shares (reported every quarter)
are NOT filled — a quarter missing either is left n/a rather than guessed.

Per-bank companyfacts fetch (cached per CIK), so an all-banks build is heavy and is
pre-warmed by jobs/refresh_trends; scoped cohorts build live. Cached per (scope, n).
"""
from __future__ import annotations

import pandas as pd

from data.sec_client import get_historical_fundamentals

# Trends metrics sourced from SEC (key, label). HoldCo per-share, labelled (SEC).
SEC_TREND_METRICS = [
    ("tbvps_hist", "TBV / share (SEC)"),
    ("bvps_hist", "Book value / share (SEC)"),
]
SEC_TREND_KEYS = [k for k, _ in SEC_TREND_METRICS]
SEC_TREND_FMT = {"tbvps_hist": ("currency", 2), "bvps_hist": ("currency", 2)}


def _series(cik: int, concepts) -> dict:
    """{quarter_end(Timestamp): value} for the first concept that has data."""
    for con in concepts:
        df = get_historical_fundamentals(cik, con)
        if df is not None and not df.empty:
            return {pd.Timestamp(r["end"]).normalize(): r["val"]
                    for _, r in df.iterrows() if r.get("val") is not None}
    return {}


def _ffill(d: dict, all_ends) -> dict:
    """Forward-fill a sparse {end: val} across all_ends (carry last known value)."""
    out, last = {}, None
    for q in sorted(all_ends):
        if d.get(q) is not None:
            last = d[q]
        out[q] = last
    return out


def _bank_per_share(cik: int, ends: list) -> dict:
    """{quarter_end: {tbvps_hist, bvps_hist}} for the requested quarter-ends."""
    eq = _series(cik, ["StockholdersEquity"])
    sh = _series(cik, ["CommonStockSharesOutstanding",
                       "WeightedAverageNumberOfDilutedSharesOutstanding"])
    if not eq or not sh:
        return {}
    gw = _series(cik, ["Goodwill"])
    it = _series(cik, ["IntangibleAssetsNetExcludingGoodwill",
                       "FiniteLivedIntangibleAssetsNet"])
    universe = set(eq) | set(sh) | set(gw) | set(it) | set(ends)
    gwf, itf = _ffill(gw, universe), _ffill(it, universe)   # slow items only
    out = {}
    for q in ends:
        e, s = eq.get(q), sh.get(q)                          # NOT forward-filled
        if not e or not s:
            out[q] = {"tbvps_hist": None, "bvps_hist": None}
            continue
        g, i = (gwf.get(q) or 0), (itf.get(q) or 0)
        out[q] = {"tbvps_hist": (e - g - i) / s, "bvps_hist": e / s}
    return out


def sec_per_share_grid(cik_to_id: dict, n_quarters: int = 20, *,
                       build_if_missing: bool = True, scope_id: str | None = None):
    """Full SEC per-share series for a cohort → same payload shape as
    as_of_metrics.quarterly_series: {"labels", "rows":[{ticker,_fdic_cert,series}]}.
    None when not cached and build_if_missing is False (all-banks is pre-warmed)."""
    from data import cache
    from data.freshness import is_fresh
    from data.as_of_metrics import recent_quarter_ends, quarter_label, _cohort_key, _GRID_TTL_S

    n = max(int(n_quarters), 1)
    ends = [pd.Timestamp(e).normalize() for e in recent_quarter_ends(n)]
    labels = [quarter_label(e) for e in ends]
    key = f"sec_pershare:{scope_id or _cohort_key(cik_to_id.keys())}:{n}"
    cached = cache.get(key)
    if is_fresh(cached, _GRID_TTL_S) and isinstance(cached.get("rows"), list):
        return cached
    if not build_if_missing:
        return None

    rows = []
    for cik, id_ in cik_to_id.items():
        per = _bank_per_share(int(cik), ends)
        if not per:
            continue
        series = {k: [per.get(e, {}).get(k) for e in ends] for k in SEC_TREND_KEYS}
        if any(v is not None for vs in series.values() for v in vs):
            rows.append({"ticker": id_, "_fdic_cert": None, "series": series})

    payload = {"labels": labels, "rows": rows,
               "cached_at": pd.Timestamp.today().isoformat()}
    cache.put(key, payload)
    return payload


def sec_metric_grid(metric_key: str, cik_to_id: dict, n_quarters: int = 20, *,
                    build_if_missing: bool = True, scope_id: str | None = None):
    """One SEC per-share metric → (labels, rows) like as_of_metrics.metric_grid."""
    data = sec_per_share_grid(cik_to_id, n_quarters, build_if_missing=build_if_missing,
                              scope_id=scope_id)
    if data is None:
        return None, None
    labels = data["labels"]
    out = []
    for r in data["rows"]:
        ser = r.get("series", {}).get(metric_key) or []
        row = {"ticker": r["ticker"], "_fdic_cert": None}
        row.update({lb: (ser[i] if i < len(ser) else None) for i, lb in enumerate(labels)})
        out.append(row)
    return labels, out
