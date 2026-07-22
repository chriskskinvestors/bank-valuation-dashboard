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


def _ffill_origin(d: dict, all_ends) -> dict:
    """Forward-fill carrying the ORIGIN end: {end: (val, origin_end)}. The
    intangible adjustment mixes several forward-filled series; vintage guards
    (same-origin MSR stripping, combined-not-staler-than-goodwill) need to know
    WHICH quarter each carried value actually came from."""
    out, last = {}, None
    for q in sorted(all_ends):
        if d.get(q) is not None:
            last = (d[q], q)
        out[q] = last
    return out


def _intangible_adj_at(q, gwo, exo, fino, inclo, msro):
    """Per-END goodwill+intangibles deduction, mirroring the audited main path
    (sec_client._resolve_intangible_adjustment) on forward-filled series
    (AUDIT-2026-07-02 #5 residual):
      • other-intangibles: the MSR-inclusive `…ExcludingGoodwill` rollup first,
        else `FiniteLivedIntangibleAssetsNet` (never contains MSRs).
      • MSRs STAY in tangible equity — net them out of a rollup only when
        unambiguously bundled: same ORIGIN quarter and 0 < MSR < tag.
      • A combined `…IncludingGoodwill` tag fills gaps: back goodwill out and
        take the larger other-intangibles, or stand alone when no goodwill tag
        exists. A combined value STALER than goodwill is ignored (its
        pre-acquisition sum would understate).
    Each argument is a forward-filled {end: (val, origin_end)} map."""
    def _get(m):
        pair = m.get(q)
        return pair if pair else (None, None)

    gw, gw_o = _get(gwo)
    ex, ex_o = _get(exo)
    fin, _ = _get(fino)
    incl, incl_o = _get(inclo)
    msr, msr_o = _get(msro)

    def _strip(val, val_origin):
        if val and msr and msr_o == val_origin and 0 < msr < val:
            return val - msr
        return val

    other = _strip(ex, ex_o) if ex is not None else fin

    if incl:
        if gw:
            if incl_o >= gw_o:                    # staler combined ignored
                backed = _strip(incl, incl_o)
                backed = max((backed or 0) - gw, 0)
                other = max(other or 0, backed)
            return gw + (other or 0)
        return max(_strip(incl, incl_o) or 0, other or 0)
    return (gw or 0) + (other or 0)


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

# A carried preferred value/presence signal goes stale after ~1y (leap-safe),
# mirroring the snapshot path's max_age_years=1 (_resolve_preferred_stock):
# annual-only taggers still fill their off-quarters, abandoned tags age out.
_PFD_MAX_AGE_DAYS = 366


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


def _equity_series(cik: int) -> dict:
    """Equity per end across the snapshot path's concept ladder (sec_client
    get_latest_fundamentals falls back to the including-NCI tag when the
    primary is missing/stale): AUBN/PRK tag ONLY the including-NCI concept, so
    a primary-only read rendered their entire per-share history n/a. Per-END
    merge (primary wins wherever both exist) so tag-switchers resolve both
    eras."""
    return _merged_series(cik, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ])


def _bank_per_share(cik: int, ends: list) -> dict:
    """{quarter_end: {tbvps_hist, bvps_hist}} for the requested quarter-ends.

    Per-COMMON-share, like the audited snapshot path (sec_client): preferred
    equity is removed from StockholdersEquity, and the cardinal rule applies
    per quarter — preferred present but carrying value unresolved → n/a, never
    a preferred-inflated figure (AUDIT-2026-07-02 P1 #5). Preferred, like
    goodwill, is a slow-moving item sparsely tagged by some filers, so its
    value/presence forward-fill — but only ~1y (an abandoned tag is not
    presence, 2026-07-22: CTBI/WABC); equity and shares never fill."""
    eq = _equity_series(cik)
    if not eq:
        return {}
    sh = _series(cik, ["CommonStockSharesOutstanding"])
    issued = _series(cik, ["CommonStockSharesIssued"])
    treasury = _series(cik, ["TreasuryStockCommonShares"])
    wavg = _series(cik, ["WeightedAverageNumberOfDilutedSharesOutstanding"])
    gw = _series(cik, ["Goodwill"])
    # Separate ladders (AUDIT #5 residual): rollup-ness matters for MSR
    # stripping, and the combined tag fills BKU-class filers that tag only
    # IntangibleAssetsNetIncludingGoodwill (old behavior: TBVPS == BVPS).
    it_ex = _series(cik, ["IntangibleAssetsNetExcludingGoodwill"])
    it_fin = _series(cik, ["FiniteLivedIntangibleAssetsNet"])
    incl = _series(cik, ["IntangibleAssetsNetIncludingGoodwill"])
    msr = _series(cik, ["ServicingAssetAtFairValueAmount"])
    pfd = _merged_series(cik, _PREFERRED_VALUE_CONCEPTS)
    pfd_sh = _merged_series(cik, ["PreferredStockSharesOutstanding",
                                  "PreferredStockSharesIssued"])
    universe = (set(eq) | set(sh) | set(issued) | set(wavg) | set(gw)
                | set(it_ex) | set(it_fin) | set(incl) | set(msr)
                | set(pfd) | set(pfd_sh) | set(ends))
    # Slow balance-sheet items forward-fill WITH their origin end so the
    # adjustment's vintage guards hold across carried values.
    gwo = _ffill_origin(gw, universe)
    exo = _ffill_origin(it_ex, universe)
    fino = _ffill_origin(it_fin, universe)
    inclo = _ffill_origin(incl, universe)
    msro = _ffill_origin(msr, universe)
    # Preferred forward-fills WITH its origin end: a filer that tags preferred
    # only annually must not read as preferred-free in the off-quarters — but a
    # tag abandoned YEARS ago must not read as preferred-present forever either
    # (CTBI: a 2014 SharesIssued tag with explicit modern PreferredStockValue=0
    # rendered every modern quarter n/a; WABC same from 2009). Value and
    # presence carry at most _PFD_MAX_AGE_DAYS past their origin.
    pfdo = _ffill_origin(pfd, universe)
    psho = _ffill_origin({q: v for q, v in pfd_sh.items() if v}, universe)

    def _shares(q):
        """Common share count at exactly q. The cover-page count rounded to a
        whole hundred-million is a placeholder (USB: exactly 1,600,000,000 for
        years, real count ~1,555M) — prefer the same-end issued − treasury
        derivation, mirroring sec_client's rounded-placeholder guard. A filer
        with a treasury series but no same-end value must not derive with
        treasury=0 (overstates the count) — fall to the period-average diluted
        last resort, or n/a.

        Every candidate is CORROBORATED against the period's diluted average
        when one exists: a point-in-time count far off it is a wrong/partial
        tag, not a real share count (CCFN 2026-07-21: 3.54M tagged vs 10.6M
        implied by its own NI/EPS — BV/TBV history rendered ~3x high, TBV
        above BV). An uncorroboratable count falls through to the diluted
        average itself (within a few percent of outstanding for banks)."""
        w = wavg.get(q)

        def _ok(v):
            return bool(v) and (not w or 0.65 <= v / w <= 1.55)
        s = sh.get(q)
        if s and s % 100_000_000 != 0 and _ok(s):
            return s
        iss = issued.get(q)
        if iss:
            tre = treasury.get(q)
            if tre is not None or not treasury:
                v = iss - (tre or 0)
                if _ok(v):
                    return v
        return w              # last resort: period-average diluted

    def _fresh(pair, q):
        return bool(pair) and (q - pair[1]).days <= _PFD_MAX_AGE_DAYS

    out = {}
    for q in ends:
        e, s = eq.get(q), _shares(q)                         # NOT forward-filled
        row = {"tbvps_hist": None, "bvps_hist": None, "tce_hist": None}
        pv = pfdo.get(q)
        pval = pv[0] if _fresh(pv, q) else None
        present = bool(pval) or _fresh(psho.get(q), q)
        # Preferred outstanding but carrying value unresolved — n/a rather
        # than a preferred-inflated figure (cardinal rule). Total TCE only
        # needs equity resolvable; the per-share keys also need shares.
        if e and not (present and not pval):
            ce = e - (pval or 0)
            adj = _intangible_adj_at(q, gwo, exo, fino, inclo, msro)
            row["tce_hist"] = ce - adj
            if s:
                row["tbvps_hist"] = (ce - adj) / s
                row["bvps_hist"] = ce / s
        out[q] = row
    return out


def tangible_common_equity_at(cik: int, asof_iso: str,
                              max_age_days: int = 200) -> tuple[float | None, str | None]:
    """TOTAL tangible common equity (raw dollars, holdco) at the last
    reported quarter-end on or before ``asof_iso`` — the deal-comps P/TBV
    denominator (data/deal_comps). Reuses the audited _bank_per_share
    machinery (preferred removed, forward-filled intangible adjustment,
    same n/a-over-guess guards). Returns (tce, end_iso) or (None, None)
    when no fresh-enough equity end exists (staler than ``max_age_days``
    would price a deal off a stale balance sheet)."""
    try:
        asof = pd.Timestamp(asof_iso).normalize()
    except (TypeError, ValueError):
        return None, None
    eq_ends = [q for q in _equity_series(int(cik)) if q <= asof]
    if not eq_ends:
        return None, None
    q = max(eq_ends)
    if (asof - q).days > max_age_days:
        return None, None
    row = _bank_per_share(int(cik), [q]).get(q) or {}
    tce = row.get("tce_hist")
    if tce is None or tce <= 0:
        return None, None
    return float(tce), q.date().isoformat()


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
    # v5: equity reads fall back to the including-NCI tag (AUBN/PRK all-n/a)
    # and preferred presence/value staleness mirrors the snapshot path
    # (CTBI/WABC forever-n/a from abandoned tags) — the bump invalidates
    # pre-warmed v4 grids so the fixed values serve once re-warmed. (v4 was
    # the CCFN share-count corroboration bump, v3 the intangible-adjustment
    # main-path mirror.)
    key = f"sec_pershare:v5:{scope_id or _cohort_key(cik_to_id.keys())}:{n}"
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

    # Coverage OBSERVE-ONLY (audit #46): same shrunken-grid exposure as the FDIC
    # grid, but this baseline is UNMEASURED (per-CIK companyfacts with retries —
    # legitimately-empty CIKs unknown), so per the verify-baseline-before-arming
    # rule we log low coverage and still persist. Harden to a hard gate (mirror
    # as_of_metrics._MIN_GRID_COVERAGE) only after nightly logs show a stable
    # baseline comfortably above the would-be threshold.
    coverage = len(rows) / max(1, len(cik_to_id))
    if coverage < 0.90:
        print(f"[sec-pershare] coverage {len(rows)}/{len(cik_to_id)} "
              f"({coverage * 100:.0f}%) below 90% — persisting anyway (observe-only; "
              "see audit #46)", flush=True)
    payload = {"labels": labels, "rows": rows, "coverage": round(coverage, 3),
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
