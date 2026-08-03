"""
Shared per-bank data loaders for the UI layer.
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
