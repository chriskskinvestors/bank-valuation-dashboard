"""
Valuation computations combining live price data with fundamentals.
"""


def compute_pe_ratio(price: float | None, eps: float | None) -> float | None:
    if price is None or eps is None or eps <= 0:
        return None
    return price / eps


def compute_pb_ratio(price: float | None, bvps: float | None) -> float | None:
    if price is None or bvps is None or bvps <= 0:
        return None
    return price / bvps


def compute_ptbv_ratio(price: float | None, tbvps: float | None) -> float | None:
    if price is None or tbvps is None or tbvps <= 0:
        return None
    return price / tbvps


def compute_dividend_yield(price: float | None, dps: float | None) -> float | None:
    if price is None or dps is None or price <= 0:
        return None
    return (dps / price) * 100


def compute_market_cap(price: float | None, shares: float | None) -> float | None:
    if price is None or shares is None:
        return None
    return price * shares


def compute_change_pct(price: float | None, prev_close: float | None) -> float | None:
    if price is None or prev_close is None or prev_close == 0:
        return None
    return ((price - prev_close) / prev_close) * 100


def compute_roatce(fdic_data: dict) -> float | None:
    """
    Compute Return on Average Tangible Common Equity from FDIC data.
    ROATCE = Net Income / TCE, where TCE = Total Equity - Goodwill.
    """
    net_income = fdic_data.get("NETINC")
    equity = fdic_data.get("EQTOT")
    goodwill = fdic_data.get("INTANGW") or 0

    if net_income is None or equity is None:
        return None

    tce = equity - goodwill
    if tce <= 0:
        return None

    return (net_income / tce) * 100


def compute_4q_avg(fdic_hist: list[dict], field: str) -> float | None:
    """Average a FDIC field over last 4 quarters."""
    values = [q.get(field) for q in fdic_hist[:4] if q.get(field) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def compute_roatce_4q(fdic_hist: list[dict]) -> float | None:
    """Average ROATCE over last 4 quarters from FDIC historical data."""
    values = []
    for q in fdic_hist[:4]:
        ni = q.get("NETINC")
        eq = q.get("EQTOT")
        gw = q.get("INTANGW") or 0
        if ni is not None and eq is not None:
            tce = eq - gw
            if tce > 0:
                values.append((ni / tce) * 100)
    if not values:
        return None
    return sum(values) / len(values)


# ── Fair Value Screening ────────────────────────────────────────────────
#
# Model: A bank's fair P/TBV multiple is a function of its profitability.
#   - 10% ROATCE → 1.0x TBV
#   - 12% ROATCE → 1.2x TBV
#   - 15% ROATCE → 1.5x TBV
#   - Linear: fair_ptbv = blended_roatce / 10
#
# Blended ROATCE weights the trailing 4Q average at 75% (smooths noise)
# and the most recent quarter at 25% (captures inflection points).
#
# A bank is flagged as undervalued when its actual P/TBV is more than
# 15% below the fair P/TBV implied by its blended ROATCE.


def compute_roatce_blended(
    roatce_current: float | None,
    roatce_4q: float | None,
) -> float | None:
    """
    Compute blended ROATCE: 75% trailing 4Q average + 25% current quarter.

    Falls back gracefully:
      - Both available: 0.75 * 4Q + 0.25 * current
      - Only 4Q available: use 4Q (100%)
      - Only current available: use current (100%)
      - Neither: None
    """
    if roatce_4q is not None and roatce_current is not None:
        return 0.75 * roatce_4q + 0.25 * roatce_current
    elif roatce_4q is not None:
        return roatce_4q
    elif roatce_current is not None:
        return roatce_current
    return None


def compute_fair_ptbv(roatce_blended: float | None) -> float | None:
    """
    Implied fair P/TBV multiple from blended ROATCE.

    Linear model: fair_ptbv = roatce / 10
      10% ROATCE → 1.0x
      12% ROATCE → 1.2x
      15% ROATCE → 1.5x

    Floors at 0.0x for negative/zero ROATCE (no meaningful fair value).
    """
    if roatce_blended is None:
        return None
    if roatce_blended <= 0:
        return 0.0
    return roatce_blended / 10.0


def compute_ptbv_discount(
    actual_ptbv: float | None,
    fair_ptbv: float | None,
) -> float | None:
    """
    Discount of actual P/TBV vs fair P/TBV, as a percentage.

    Positive = undervalued (actual is below fair).
    Negative = overvalued (actual is above fair).

    Example: fair=1.5x, actual=1.2x → discount = 20% (undervalued by 20%)
    Example: fair=1.0x, actual=1.3x → discount = -30% (overvalued by 30%)
    """
    if actual_ptbv is None or fair_ptbv is None:
        return None
    if fair_ptbv <= 0:
        return None
    return ((fair_ptbv - actual_ptbv) / fair_ptbv) * 100


def compute_fair_value_price(
    fair_ptbv: float | None,
    tbvps: float | None,
) -> float | None:
    """
    Implied fair price = fair P/TBV × TBV per share.

    Gives the price at which the bank would trade at its
    ROATCE-implied fair multiple.
    """
    if fair_ptbv is None or tbvps is None or tbvps <= 0:
        return None
    return fair_ptbv * tbvps


def compute_all_valuations(price_data: dict, sec_data: dict, fdic_data: dict, fdic_hist: list[dict] | None = None) -> dict:
    """
    Compute all derived valuation metrics from raw data sources.

    Returns a dict of computed metric values keyed by metric key.
    """
    price = price_data.get("price")
    prev_close = price_data.get("close")

    eps = sec_data.get("eps")
    bvps = sec_data.get("book_value_per_share")
    tbvps = sec_data.get("tangible_book_value_per_share")
    dps = sec_data.get("dividends_per_share")
    shares = sec_data.get("shares_outstanding")

    # ── Deposit composition ─────────────────────────────────────────────
    dep = fdic_data.get("DEP")
    uninsured = fdic_data.get("DEPUNINS")
    coredep = fdic_data.get("COREDEP")
    brokered = fdic_data.get("BRO")
    depnidom = fdic_data.get("DEPNIDOM")

    uninsured_pct = (uninsured / dep * 100) if (dep and uninsured is not None) else None
    core_dep_pct = (coredep / dep * 100) if (dep and coredep is not None) else None
    brokered_pct = (brokered / dep * 100) if (dep and brokered is not None) else None
    nonint_dep_pct = (depnidom / dep * 100) if (dep and depnidom is not None) else None

    # ── Loan concentration ───────────────────────────────────────────────
    loans_gross = fdic_data.get("LNLSGR") or fdic_data.get("LNLSNET")
    lnre = fdic_data.get("LNRE")
    lnrenres = fdic_data.get("LNRENRES")
    lnreres = fdic_data.get("LNRERES")
    lnremult = fdic_data.get("LNREMULT")
    lnrecons = fdic_data.get("LNRECONS")
    lnci = fdic_data.get("LNCI")
    lncon = fdic_data.get("LNCON")
    eq = fdic_data.get("EQTOT")

    def _pct(part, whole):
        if part is not None and whole and whole > 0:
            return part / whole * 100
        return None

    ln_re_pct = _pct(lnre, loans_gross)
    ln_cre_pct = _pct(lnrenres, loans_gross)
    ln_resi_pct = _pct(lnreres, loans_gross)
    ln_multifam_pct = _pct(lnremult, loans_gross)
    ln_construct_pct = _pct(lnrecons, loans_gross)
    ln_ci_pct = _pct(lnci, loans_gross)
    ln_consumer_pct = _pct(lncon, loans_gross)

    # CRE concentration: regulators flag at 300% of capital
    cre_to_capital = _pct(lnrenres, eq) if (lnrenres and eq and eq > 0) else None

    # ── Securities composition ───────────────────────────────────────────
    sc_total = fdic_data.get("SC")
    sc_htm = fdic_data.get("SCHA")
    asset = fdic_data.get("ASSET")

    sec_to_assets_pct = _pct(sc_total, asset)
    htm_pct = _pct(sc_htm, sc_total) if (sc_htm is not None and sc_total and sc_total > 0) else None

    # ── NIM metrics ──────────────────────────────────────────────────────
    intincy = fdic_data.get("INTINCY")   # earning asset yield
    intexpy = fdic_data.get("INTEXPY")   # cost of int-bearing liabilities
    noniiay = fdic_data.get("NONIIAY")   # non-int income / assets
    nonixay = fdic_data.get("NONIXAY")   # non-int expense / assets

    nim_spread = (intincy - intexpy) if (intincy is not None and intexpy is not None) else None

    # Cost of funds = int expense / (deposits + borrowings)
    eintexp = fdic_data.get("EINTEXP")
    cost_of_funds = None
    if eintexp is not None and dep and dep > 0:
        # Use total deposits + fed funds as funding base (approximation)
        funding = dep + (fdic_data.get("FREPO") or 0)
        if funding > 0:
            cost_of_funds = (eintexp / funding) * 100

    # Non-interest burden = non-int expense - non-int income, as % of assets
    nonint_burden = None
    if nonixay is not None and noniiay is not None:
        nonint_burden = nonixay - noniiay

    # Core valuation ratios
    actual_ptbv = compute_ptbv_ratio(price, tbvps)

    # Profitability
    roatce_current = compute_roatce(fdic_data)
    roatce_4q = compute_roatce_4q(fdic_hist or [])

    # Fair value screening
    roatce_blended = compute_roatce_blended(roatce_current, roatce_4q)
    fair_ptbv = compute_fair_ptbv(roatce_blended)
    ptbv_discount = compute_ptbv_discount(actual_ptbv, fair_ptbv)
    fair_price = compute_fair_value_price(fair_ptbv, tbvps)

    return {
        "price": price,
        "change_pct": compute_change_pct(price, prev_close),
        "volume": price_data.get("volume"),
        "market_cap": compute_market_cap(price, shares),
        "pe_ratio": compute_pe_ratio(price, eps),
        "pb_ratio": compute_pb_ratio(price, bvps),
        "ptbv_ratio": compute_ptbv_ratio(price, tbvps),
        "dividend_yield": compute_dividend_yield(price, dps),
        "roatce": roatce_current,
        "roaa_4q": compute_4q_avg(fdic_hist or [], "ROA"),
        "roatce_4q": roatce_4q,
        "nim_4q": compute_4q_avg(fdic_hist or [], "NIMY"),
        "uninsured_pct": uninsured_pct,
        "core_dep_pct": core_dep_pct,
        "brokered_pct": brokered_pct,
        "nonint_dep_pct": nonint_dep_pct,
        "ln_re_pct": ln_re_pct,
        "ln_cre_pct": ln_cre_pct,
        "ln_resi_pct": ln_resi_pct,
        "ln_multifam_pct": ln_multifam_pct,
        "ln_construct_pct": ln_construct_pct,
        "ln_ci_pct": ln_ci_pct,
        "ln_consumer_pct": ln_consumer_pct,
        "cre_to_capital": cre_to_capital,
        "sec_to_assets_pct": sec_to_assets_pct,
        "htm_pct": htm_pct,
        "nim_spread": nim_spread,
        "cost_of_funds": cost_of_funds,
        "nonint_burden": nonint_burden,
        "roatce_blended": roatce_blended,
        "fair_ptbv": fair_ptbv,
        "fair_price": fair_price,
        "ptbv_discount": ptbv_discount,
    }
