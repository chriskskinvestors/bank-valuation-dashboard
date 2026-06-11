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
    from data import fdic_client

    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= min_quarters:
        return hist
    cert = get_fdic_cert(ticker)
    if not cert:
        return hist or []
    df = fdic_client.fetch_financials(cert, limit=limit)
    if df.empty:
        return hist or []
    records = df.to_dict("records")
    cache_put(f"fdic_hist:{ticker}", records)
    return records
