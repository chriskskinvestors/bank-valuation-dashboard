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


# Preferred carrying-value ladder — same order and semantics as
# sec_client._resolve_preferred_stock: equity-section carrying value first
# (par-only for simple issuers, par+APIC for the big banks), liquidation
# preference last. A par-zero tag is "keep looking", never a resolved value.
_PREFERRED_VALUE_CONCEPTS = [
    "PreferredStockValue",
    "PreferredStockIncludingAdditionalPaidInCapital",
    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
]


def _merged_series(cik: int, concepts) -> dict:
    """{end: first nonzero value across the concept ladder, per END}. Unlike
    _series (whole-series first-hit), this merges per quarter-end: a filer that
    abandoned an early ladder tag years ago (USB's PreferredStockValue stops in
    2013) still resolves modern ends from the tag it uses now."""
    out: dict = {}
    for con in concepts:
        for q, v in _series(cik, [con]).items():
            if v and q not in out:
                out[q] = v
    return out


def _bank_per_share(cik: int, ends: list) -> dict:
    """{quarter_end: {tbvps_hist, bvps_hist}} for the requested quarter-ends.

    Per-COMMON-share, like the audited snapshot path (sec_client): preferred
    equity is removed from StockholdersEquity, and the cardinal rule applies
    per quarter — preferred present but carrying value unresolved → n/a, never
    a preferred-inflated figure (AUDIT-2026-07-02 P1 #5). Preferred, like
    goodwill, is a slow-moving item sparsely tagged by some filers, so its
    value/presence forward-fill; equity and shares never do."""
    eq = _series(cik, ["StockholdersEquity"])
    if not eq:
        return {}
    sh = _series(cik, ["CommonStockSharesOutstanding"])
    issued = _series(cik, ["CommonStockSharesIssued"])
    treasury = _series(cik, ["TreasuryStockCommonShares"])
    wavg = _series(cik, ["WeightedAverageNumberOfDilutedSharesOutstanding"])
    gw = _series(cik, ["Goodwill"])
    it = _series(cik, ["IntangibleAssetsNetExcludingGoodwill",
                       "FiniteLivedIntangibleAssetsNet"])
    pfd = _merged_series(cik, _PREFERRED_VALUE_CONCEPTS)
    pfd_sh = _merged_series(cik, ["PreferredStockSharesOutstanding",
                                  "PreferredStockSharesIssued"])
    universe = (set(eq) | set(sh) | set(issued) | set(wavg) | set(gw) | set(it)
                | set(pfd) | set(pfd_sh) | set(ends))
    gwf, itf = _ffill(gw, universe), _ffill(it, universe)   # slow items only
    pfdf = _ffill(pfd, universe)
    # Presence forward-fills WITH the value: a filer that tags preferred only
    # annually must not read as preferred-free in the off-quarters.
    presf = _ffill({q: True for q in (set(pfd) | {q for q, v in pfd_sh.items() if v})},
                   universe)

    def _shares(q):
        """Common share count at exactly q. The cover-page count rounded to a
        whole hundred-million is a placeholder (USB: exactly 1,600,000,000 for
        years, real count ~1,555M) — prefer the same-end issued − treasury
        derivation, mirroring sec_client's rounded-placeholder guard. A filer
        with a treasury series but no same-end value must not derive with
        treasury=0 (overstates the count) — fall to the period-average diluted
        last resort, or n/a."""
        s = sh.get(q)
        if s and s % 100_000_000 != 0:
            return s
        iss = issued.get(q)
        if iss:
            tre = treasury.get(q)
            if tre is not None or not treasury:
                return iss - (tre or 0)
        return wavg.get(q)    # last resort: period-average diluted

    out = {}
    for q in ends:
        e, s = eq.get(q), _shares(q)                         # NOT forward-filled
        if not e or not s:
            out[q] = {"tbvps_hist": None, "bvps_hist": None}
            continue
        if presf.get(q) and not pfdf.get(q):
            # Preferred outstanding but carrying value unresolved — n/a rather
            # than a preferred-inflated per-share figure (cardinal rule).
            out[q] = {"tbvps_hist": None, "bvps_hist": None}
            continue
        ce = e - (pfdf.get(q) or 0)
        g, i = (gwf.get(q) or 0), (itf.get(q) or 0)
        out[q] = {"tbvps_hist": (ce - g - i) / s, "bvps_hist": ce / s}
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
    # v2: per-common-share conventions (preferred subtracted, placeholder
    # share counts derived) — the bump invalidates pre-warmed v1 grids so the
    # old preferred-inflated values don't serve until the next nightly run.
    key = f"sec_pershare:v2:{scope_id or _cohort_key(cik_to_id.keys())}:{n}"
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
