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

    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= min_quarters:
        return hist
    cert = get_fdic_cert(ticker)
    if not cert:
        return hist or []
    # The WHOLE banking operation, not just the lead charter: 11 universe banks
    # are multi-bank holdcos and were showing one charter's figures (WTFC $9.3B
    # of $72.4B). fetch_group_history returns one consolidated record per
    # quarter, so every consumer of this list gets the real bank.
    records = fetch_group_history(ticker, limit=limit, cert=cert)
    if not records:
        return hist or []
    cache_put(f"fdic_hist:{ticker}", records)
    return records


def load_fdic_hist_df(ticker: str, quarters: int):
    """DataFrame view of load_fdic_hist — the drop-in for per-cert
    fdic_client.get_historical_financials at ticker-scoped call sites.
    Same columns (raw FDIC fields), newest first, group-aware."""
    import pandas as pd
    recs = load_fdic_hist(ticker, min_quarters=quarters,
                          limit=max(quarters, 20))
    return pd.DataFrame(recs[:quarters]) if recs else pd.DataFrame()


def load_fdic_latest(ticker: str) -> dict:
    """Latest consolidated FDIC record — the drop-in for per-cert
    fdic_client.get_latest_financials at ticker-scoped call sites. Served
    from the warm nightly cache (no live FDIC round-trip on render)."""
    recs = load_fdic_hist(ticker, min_quarters=1)
    return dict(recs[0]) if recs else {}
