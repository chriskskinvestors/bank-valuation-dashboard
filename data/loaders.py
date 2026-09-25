"""
Shared per-bank data loaders for the UI layer.

Every ticker-scoped FDIC read belongs here, NOT on data.fdic_client directly:
fdic_client is cert-scoped by design, and a direct call shows ONE charter of a
multi-bank holdco. Six UI call sites bypassed load_fdic_hist that way, so the
Corporate Profile, Financial Highlights, statements and trend panels kept
rendering IBOC as $9.89B of $17.3B even after the cache producers were wired
(found 2026-08-04, third fix of the day — the first two repaired the nightly
itself and could never have moved these panels).
"""


def _scrub_capital(recs: list[dict]) -> list[dict]:
    """null_unreported_capital on COPIES of every record: cache entries written
    before that scrub existed (or by a path that skipped it) still carry the
    literal-0 capital ratios — BSBK, a CBLR filer (RWAJ=0), rendered
    "CET1 Ratio 0.00%" on Corporate Profile (UX-P0-13). Copies, because the
    cached objects are shared with every other caller."""
    from data.fdic_client import null_unreported_capital
    return [null_unreported_capital(dict(r)) for r in recs]


def load_fdic_hist(ticker: str, min_quarters: int = 8, limit: int = 20) -> list[dict]:
    """~20 quarters of FDIC history from the warm cache, fetching live when the
    cached series is shorter than ``min_quarters``.

    Five tab modules (rate_sensitivity, valuation_model, capital_dynamics,
    credit_dynamics, deposit_dynamics) previously carried verbatim copies of
    this function — with a silently divergent threshold (8 in four copies, 4 in
    valuation), so the same bank could render a full valuation model while the
    credit/capital tabs refetched. The threshold is now an explicit parameter.
    """
    from data.cache import get as cache_get, put as cache_put
    from data.bank_mapping import get_fdic_cert
    from data.cert_group import fetch_group_history

    # The warm window holds 20 quarters and the live fallback is capped at 20,
    # so a min_quarters above 20 can NEVER be satisfied by either: the call
    # refetched FDIC live on every render and rewrote the same 20 quarters
    # (Corporate Profile prefetch asked for 44 — REVIEW-2026-09-24 P1-3).
    # Cap here so every caller is safe; a deep request still serves the store
    # whenever it holds at least the warm window's depth.
    min_quarters = min(min_quarters, 20)

    # Deep requests (beyond the 20-quarter warm window) read the backfilled
    # history store (DEEP-HISTORY-PLAN.md, owner-approved 2026-09-08). The
    # ≤20-quarter path below is UNTOUCHED — current pages get zero slower.
    # An unpopulated store (pre-backfill, or a brand-new bank) returns [] and
    # falls through to the live 20-quarter path: honest available depth, no
    # live deep fetch on a render thread ever.
    if limit > 20:
        try:
            from data.fdic_history_store import deep_group_history
            deep = deep_group_history(ticker, limit=limit)
        except Exception:
            deep = []
        if len(deep) >= min_quarters:
            return _scrub_capital(deep)

    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= min_quarters:
        return _scrub_capital(hist)
    cert = get_fdic_cert(ticker)
    if not cert:
        return _scrub_capital(hist or [])
    # The WHOLE banking operation, not just the lead charter: 11 universe banks
    # are multi-bank holdcos and were showing one charter's figures (WTFC $9.3B
    # of $72.4B). fetch_group_history returns one consolidated record per
    # quarter, so every consumer of this list gets the real bank.
    # Live fallback stays capped at the warm window: a deep request with an
    # unpopulated store must NOT trigger a 140-quarter live fetch on a render
    # thread (jobs build, renders read) — it shows the honest 20 quarters
    # until the backfill job has run.
    records = fetch_group_history(ticker, limit=min(limit, 20), cert=cert)
    if not records:
        return _scrub_capital(hist or [])
    cache_put(f"fdic_hist:{ticker}", records)
    return _scrub_capital(records)


def load_fdic_hist_df(ticker: str, quarters: int):
    """DataFrame view of load_fdic_hist — the drop-in for per-cert
    fdic_client.get_historical_financials at ticker-scoped call sites.
    Same columns (raw FDIC fields), newest first, group-aware."""
    import pandas as pd
    # min_quarters is capped at the warm window: a deep request (44 / 84 /
    # 200 — the deep-history table ranges) must serve a bank whose WHOLE
    # stored history is shorter than asked (listed eight years ago → 32
    # quarters) rather than reject it and fall back to 20. It also stops
    # the unsatisfiable refetch the old threshold caused for young banks
    # (min 44 could never be met, so every render re-hit FDIC live).
    recs = load_fdic_hist(ticker, min_quarters=min(quarters, 20),
                          limit=max(quarters, 20))
    return pd.DataFrame(recs[:quarters]) if recs else pd.DataFrame()


def load_fdic_latest(ticker: str) -> dict:
    """Latest consolidated FDIC record — the drop-in for per-cert
    fdic_client.get_latest_financials at ticker-scoped call sites. Served
    from the warm nightly cache (no live FDIC round-trip on render)."""
    recs = load_fdic_hist(ticker, min_quarters=1)
    return dict(recs[0]) if recs else {}
