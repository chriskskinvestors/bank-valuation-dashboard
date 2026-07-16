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


def _infer_quarter(repdte) -> int | None:
    """Return quarter number (1-4) from an FDIC REPDTE value."""
    if repdte is None:
        return None
    try:
        # REPDTE can be Timestamp, date, or string like '20251231' or '2025-12-31'
        if hasattr(repdte, "month"):
            m = repdte.month
        else:
            s = str(repdte)
            if "-" in s:
                m = int(s.split("-")[1])
            else:
                m = int(s[4:6])
        return (m - 1) // 3 + 1
    except Exception:
        return None


def _annualize_ytd(ytd_value: float | None, quarter: int | None) -> float | None:
    """
    Convert an FDIC YTD cumulative value (NETINC, EINTEXP, etc.) to an
    annualized full-year equivalent.

    Q1 YTD = 3 months → × 4
    Q2 YTD = 6 months → × 2
    Q3 YTD = 9 months → × 4/3
    Q4 YTD = 12 months → × 1

    If quarter unknown, assume full-year (Q4).
    """
    if ytd_value is None:
        return None
    if quarter is None or not (1 <= quarter <= 4):
        return ytd_value
    return ytd_value * (4 / quarter)


def _derive_quarterly_value(field: str, fdic_hist: list[dict], idx: int) -> float | None:
    """
    Derive the single-quarter (non-YTD) value for a YTD-cumulative FDIC field
    (NETINC, INTINC, EINTEXP, NONII, NONIX, etc.).

    fdic_hist[idx] is the target period. We need YTD(idx) - YTD(prior_quarter_same_year).
    If idx is Q1, quarterly = YTD.
    """
    current = fdic_hist[idx] if idx < len(fdic_hist) else None
    if current is None:
        return None
    curr_ytd = current.get(field)
    if curr_ytd is None:
        return None

    curr_qtr = _infer_quarter(current.get("REPDTE"))
    if curr_qtr == 1 or curr_qtr is None:
        return curr_ytd

    # Find prior quarter in same calendar year within fdic_hist (which is desc-sorted)
    try:
        if hasattr(current.get("REPDTE"), "year"):
            curr_year = current["REPDTE"].year
        else:
            curr_year = int(str(current.get("REPDTE"))[:4])
    except Exception:
        return curr_ytd

    # fdic_hist is typically sorted most-recent-first
    for j in range(idx + 1, len(fdic_hist)):
        prior = fdic_hist[j]
        try:
            if hasattr(prior.get("REPDTE"), "year"):
                prior_year = prior["REPDTE"].year
            else:
                prior_year = int(str(prior.get("REPDTE"))[:4])
        except Exception:
            continue
        if prior_year != curr_year:
            break  # left this fiscal year
        prior_qtr = _infer_quarter(prior.get("REPDTE"))
        if prior_qtr == curr_qtr - 1:
            prior_ytd = prior.get(field)
            if prior_ytd is None:
                return None
            return curr_ytd - prior_ytd
    # Prior quarter missing from history (a data gap). We CANNOT recover the
    # single-quarter value — returning the cumulative YTD here would report a
    # 6-/9-month figure as one quarter, inflating TTM net income and the
    # fair-P/TBV "undervalued" flag it drives. n/a beats a guess (cardinal rule);
    # both callers skip a None quarter and annualize from the ones they have.
    return None


def compute_roatce(fdic_data: dict) -> float | None:
    """
    Compute annualized Return on Average Tangible Common Equity from FDIC data.

    ROATCE = (Annualized Net Income) / TCE × 100
    TCE = Total Equity − Total Intangibles (INTAN, incl. goodwill) — the house
    convention (CLAUDE.md; matches the Financials tab, Capital Dynamics, and the
    golden hand-check). INTANGW = goodwill only, which understated intangibles.

    NETINC is YTD (cumulative within calendar year). We annualize by multiplying
    by (4 / quarter_number) so mid-year numbers are comparable to Q4.
    """
    net_income = fdic_data.get("NETINC")
    equity = fdic_data.get("EQTOT")
    intangibles = fdic_data.get("INTAN") or 0

    if net_income is None or equity is None:
        return None

    tce = equity - intangibles
    if tce <= 0:
        return None

    quarter = _infer_quarter(fdic_data.get("REPDTE"))
    ni_annualized = _annualize_ytd(net_income, quarter)

    return (ni_annualized / tce) * 100


def compute_roatce_holdco(sec_data: dict) -> float | None:
    """
    Compute HOLDING-COMPANY ROATCE using SEC data (what shareholders actually own).

    For money-center banks, HoldCo includes non-bank operations (investment
    banking, asset management) that aren't in the FDIC subsidiary-bank data.
    Using HoldCo metrics is the institutional standard for stock valuation.

    ROATCE = Return on Average Tangible COMMON Equity:
        (NI available to common) / (common equity − goodwill − intangibles) × 100

    BOTH sides are common-basis. Subtract preferred from the numerator (use
    NI-to-common, not total NI) AND the denominator (common equity, not total
    equity). Using total NI over total equity leaves two offsetting errors that
    only partially cancel; the common basis is the metric's actual definition and
    ties TBV/share and the golden hand-check. Cardinal rule: when the filer HAS
    preferred but its carrying value is unresolved (par-zero/stale), the common
    basis is unknowable → return None (n/a), never a preferred-inflated ROATCE.
    """
    if not sec_data:
        return None
    equity = sec_data.get("book_value_total")
    if equity is None:
        return None

    preferred_present = sec_data.get("preferred_present")
    preferred_stock = sec_data.get("preferred_stock")
    if preferred_present and preferred_stock is None:
        return None  # preferred present but unresolved → common basis unknowable

    # Numerator: NI available to common. For preferred issuers use the to-common
    # TTM; with no preferred, to-common == total NI, so fall back to it.
    ni = sec_data.get("net_income_to_common_ttm")
    if ni is None:
        if preferred_present:
            return None  # has preferred but no to-common figure → no honest common return
        ni = sec_data.get("net_income")
    if ni is None:
        return None

    # Denominator: common tangible equity. Use the robust intangible adjustment
    # (goodwill + intangibles resolved across alternate XBRL tags, MSR-excluded)
    # so TCE matches the tangible-book calc; fall back to raw fields.
    common_equity = equity - (preferred_stock or 0)
    adj = sec_data.get("intangible_adjustment")
    if adj is None:
        adj = (sec_data.get("goodwill") or 0) + (sec_data.get("intangibles") or 0)
    tce = common_equity - adj
    if tce <= 0:
        return None
    return (ni / tce) * 100


def compute_4q_avg(fdic_hist: list[dict], field: str) -> float | None:
    """
    Average a FDIC field over last 4 quarters.

    For FDIC ratio fields that are already annualized (NIMY, ROA, ROE, EEFFR, etc.),
    this is fine — just average. For YTD cumulative fields, use compute_4q_avg_annualized.
    """
    values = [q.get(field) for q in fdic_hist[:4] if q.get(field) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def compute_roatce_4q(fdic_hist: list[dict]) -> float | None:
    """
    Trailing 4-quarter ROATCE: sum of last 4 QUARTERLY net incomes (annualized)
    divided by average TCE across those quarters, expressed as %.

    This is the canonical "TTM ROATCE" analysts use — it smooths quarter-to-quarter
    noise while reflecting a full year of earnings power on current-era equity.
    """
    if not fdic_hist or len(fdic_hist) < 1:
        return None

    # Derive actual single-quarter NI for last 4 quarters
    # (fdic_hist is desc-sorted: index 0 = most recent)
    ttm_ni = 0.0
    tce_values = []
    count = 0
    for i in range(min(4, len(fdic_hist))):
        ni_q = _derive_quarterly_value("NETINC", fdic_hist, i)
        eq = fdic_hist[i].get("EQTOT")
        intan = fdic_hist[i].get("INTAN") or 0  # total intangibles (house TCE convention)
        if ni_q is None or eq is None:
            continue
        ttm_ni += ni_q
        tce_values.append(eq - intan)
        count += 1

    if count == 0 or not tce_values:
        return None
    avg_tce = sum(tce_values) / len(tce_values)
    if avg_tce <= 0:
        return None

    # TTM net income is already a "full year" — no annualization needed
    # If we have fewer than 4 quarters, scale up to annualized
    if count < 4:
        ttm_ni = ttm_ni * (4 / count)

    return (ttm_ni / avg_tce) * 100


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


def _normalized_earnings_factor(fdic_hist: list[dict] | None) -> float:
    """
    Dampening factor (0.2–1.0) that strips one-time earnings spikes out of the
    TTM net income that drives fair value.

    A non-recurring gain — a large loan recovery, a tax benefit, a securities
    gain — inflates a single quarter, which inflates TTM ROATCE, which the
    linear fair-P/TBV model extrapolates into an absurd fair value and a FALSE
    "undervalued" signal (e.g. Carter Bankshares' Q1-2026 loan recovery drove
    ROATCE to 21.6% and fair value to $49 vs a ~$23 normalized value).

    Method: winsorize each of the 4 most-recent single-quarter net incomes at
    3× the trailing-8-quarter median, then return normalized_TTM / raw_TTM.
    Returns 1.0 (no change) for banks with steady earnings.
    """
    if not fdic_hist or len(fdic_hist) < 5:
        return 1.0
    import statistics
    qtrs = []
    for i in range(min(8, len(fdic_hist))):
        v = _derive_quarterly_value("NETINC", fdic_hist, i)
        if v is not None:
            qtrs.append(v)
    if len(qtrs) < 5:
        return 1.0
    med = statistics.median(qtrs)
    if med <= 0:
        return 1.0
    raw_ttm = sum(qtrs[:4])
    if raw_ttm <= 0:
        return 1.0
    # Cap each quarter at 3x the trailing median: ordinary growth/volatility
    # (a quarter up to ~3x a normal one) passes through untouched; only an
    # extreme, almost-certainly-non-recurring spike (loan recovery, big tax
    # benefit) gets winsorized. Avoids over-penalizing genuine improvement.
    cap = 3.0 * med
    norm_ttm = sum(min(q, cap) for q in qtrs[:4])
    return max(0.2, min(1.0, norm_ttm / raw_ttm))


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
    # Cap at 2.5x: even a sustainably high-ROATCE bank rarely warrants more
    # than ~2.5x tangible book, and the cap is a backstop against any residual
    # earnings distortion slipping through normalization.
    return min(2.5, roatce_blended / 10.0)


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


def compute_all_valuations(price_data: dict, sec_data: dict, fdic_data: dict,
                             fdic_hist: list[dict] | None = None,
                             ticker: str | None = None) -> dict:
    """
    Compute all derived valuation metrics from raw data sources.

    Returns a dict of computed metric values keyed by metric key.
    """
    price = price_data.get("price")
    prev_close = price_data.get("close")

    eps = sec_data.get("eps")
    bvps = sec_data.get("book_value_per_share")
    # Company-Reported principle: prefer the bank's OWN reported tangible book
    # value per common share (from its earnings release) over our reconstruction.
    # The reconstruction is the FALLBACK when the bank doesn't disclose or the
    # reported figure fails a sanity gate. See _resolve_tbvps.
    reconstructed_tbvps = sec_data.get("tangible_book_value_per_share")
    tbvps, tbvps_source = _resolve_tbvps(ticker, reconstructed_tbvps, bvps)
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

    # CRE concentration: regulators flag at 300% of capital. No falsy-zero
    # wrapper (audit P3, owner call): a genuine $0 CRE book is DATA — it renders
    # 0% and classifies as no-CRE; _pct already handles None/zero-denominator.
    cre_to_capital = _pct(lnrenres, eq)

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

    # Cost of funds — prefer FDIC's pre-computed INTEXPY (annualized cost of
    # interest-bearing liabilities). Only fall back to a manual calculation
    # if INTEXPY is unavailable, and annualize properly from YTD EINTEXP.
    # The fallback denominator must cover the SAME base as INTEXPY — ALL
    # interest-bearing liabilities (IB deposits + fed funds/repo + FHLB/other
    # borrowings + sub-debt), not IB deposits alone: EINTEXP includes borrowing
    # interest, so the deposits-only denominator overstated the rate (measured
    # 2026-07-10: median +3.5%, p90 +13.8%, max +37% across the universe;
    # latent — INTEXPY was present for all 324 banks that quarter).
    cost_of_funds = intexpy
    if cost_of_funds is None:
        eintexp = fdic_data.get("EINTEXP")
        ib_liabs = fdic_data.get("DEPIDOM")
        if ib_liabs and ib_liabs > 0:
            ib_liabs += sum(fdic_data.get(f) or 0
                            for f in ("FREPO", "OTHBFHLB", "SUBND"))
        if eintexp is not None and ib_liabs and ib_liabs > 0:
            quarter = _infer_quarter(fdic_data.get("REPDTE"))
            eintexp_annualized = _annualize_ytd(eintexp, quarter)
            cost_of_funds = (eintexp_annualized / ib_liabs) * 100

    # Non-interest burden = non-int expense - non-int income, as % of assets
    nonint_burden = None
    if nonixay is not None and noniiay is not None:
        nonint_burden = nonixay - noniiay

    # Core valuation ratios
    actual_ptbv = compute_ptbv_ratio(price, tbvps)

    # Profitability
    roatce_current = compute_roatce(fdic_data)   # sub-bank
    roatce_4q = compute_roatce_4q(fdic_hist or [])   # sub-bank TTM
    roatce_holdco = compute_roatce_holdco(sec_data)  # HoldCo (what stock represents)

    # Fair value screening — use HoldCo ROATCE when available (what investors
    # price off); fall back to sub-bank blended if SEC data is missing.
    roatce_blended = roatce_holdco if roatce_holdco is not None else compute_roatce_blended(roatce_current, roatce_4q)

    # Normalize away one-time earnings spikes (loan recoveries, tax benefits,
    # securities gains) before deriving fair value — otherwise a non-recurring
    # gain inflates ROATCE and produces a false "undervalued" signal.
    earnings_norm_factor = _normalized_earnings_factor(fdic_hist)
    roatce_normalized = (
        roatce_blended * earnings_norm_factor if roatce_blended is not None else None
    )
    fair_ptbv = compute_fair_ptbv(roatce_normalized)
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
        # The RESOLVED tangible book value — the same figure ptbv_ratio and
        # the fair-value chain price against (previously the display key read
        # the raw reconstruction and could differ from the ratio's input).
        # tbvps_source: "reported_8k" | "reconstructed" | "company_release"
        # (non-SEC filers — the wire release is their primary disclosure).
        "tbvps": tbvps,
        "tbvps_source": tbvps_source,
        # Computed from our own robust TTM dividend-per-share (anchored to the
        # latest period; handles cut/skipped quarters). FMP's dividend figure is
        # ALSO unreliable, so we derive from the filing rather than adopt it.
        "dividend_yield": compute_dividend_yield(price, dps),
        "roatce": roatce_current,
        "roatce_holdco": roatce_holdco,
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
        "roatce_normalized": roatce_normalized,
        "earnings_norm_factor": earnings_norm_factor,
        # True when a one-time item materially inflated TTM earnings (the
        # normalization removed >15%). Drives the "⚠ one-time item" flag the UI
        # shows next to the reported ROATCE across all views.
        "earnings_distorted": (
            earnings_norm_factor is not None and earnings_norm_factor < 0.85
        ),
        "fair_ptbv": fair_ptbv,
        "fair_price": fair_price,
        "ptbv_discount": ptbv_discount,
        # ── Deposit Dynamics (computed from fdic_hist) ──
        **_compute_deposit_dynamics(fdic_hist),
        # ── Credit Dynamics (computed from fdic_hist) ──
        **_compute_credit_dynamics(fdic_hist),
        # ── Capital Dynamics (computed from fdic_hist + SEC shares) ──
        **_compute_capital_dynamics(fdic_hist, sec_data.get("shares_outstanding")),
        # ── Capital Return Attribution (SEC XBRL: divs + buybacks + shares) ──
        **_compute_capital_return_for_ticker(ticker, price_data, sec_data),
    }


def _resolve_tbvps(
    ticker: str | None,
    reconstructed: float | None,
    bvps: float | None,
) -> tuple[float | None, str | None]:
    """(Tangible book value per common share, source), preferring the bank's
    OWN reported figure (earnings-release non-GAAP line) over our
    reconstruction.

    Company-Reported principle: take the disclosed number when it's cleanly
    found AND ties out (see data.sec_earnings_8k.reported_tbvps gates);
    otherwise fall back to `reconstructed` (data.sec_client's corrected
    reconstruction). If the reported extractor errors, the reconstruction
    stands — never a regression. NON-SEC filers (cik=None: PBAM class) have
    no reconstruction at all — their wire earnings release is the primary
    disclosure and the only per-share source (owner decision 2026-07-16);
    the guarded data/otc_release extraction supplies it, staleness-gated.
    Sources: "reported_8k" | "reconstructed" | "company_release" | None."""
    cik = None
    if ticker:
        try:
            from data.bank_mapping import get_cik
            cik = get_cik(ticker)
        except Exception:
            cik = None
    if cik:
        try:
            from data.sec_earnings_8k import reported_tbvps
            reported = reported_tbvps(cik, reconstructed=reconstructed, bvps=bvps)
            if reported is not None:
                return reported, "reported_8k"
        except Exception as e:
            print(f"[valuation] reported_tbvps lookup failed for {ticker}: "
                  f"{type(e).__name__}: {e}")
    elif ticker:
        try:
            otc = _otc_tbvps(ticker)
            if otc is not None:
                return otc, "company_release"
        except Exception as e:
            print(f"[valuation] otc tbvps lookup failed for {ticker}: "
                  f"{type(e).__name__}: {e}")
    return reconstructed, ("reconstructed" if reconstructed is not None else None)


def _otc_tbvps(ticker: str) -> float | None:
    """A non-SEC bank's tangible book value per share from its latest wire
    earnings release (guarded extraction, band-checked at the source).
    STALENESS GATE: a release quarter-end older than ~200 days means the
    bank stopped publishing — pricing today's quote against that TBV drifts,
    so None (n/a) instead."""
    from datetime import date
    from data.otc_release import otc_release_metrics
    val = otc_release_metrics(ticker) or {}
    tbv = (val.get("metrics") or {}).get("tbv_ps")
    qend = val.get("qend")
    if tbv is None or not qend:
        return None
    try:
        age_days = (date.today() - date.fromisoformat(qend)).days
    except ValueError:
        return None
    return tbv if age_days <= 200 else None


def _compute_capital_return_for_ticker(ticker: str | None, price_data: dict, sec_data: dict) -> dict:
    """Wrapper: resolve CIK from ticker, pass market cap, call capital-return compute."""
    if not ticker:
        return dict(_CAPITAL_RETURN_DEFAULTS)
    try:
        from data.bank_mapping import get_cik
        cik = get_cik(ticker)
        if not cik:
            return dict(_CAPITAL_RETURN_DEFAULTS)
        price = price_data.get("price") if price_data else None
        shares = sec_data.get("shares_outstanding") if sec_data else None
        mcap = (price * shares) if (price and shares) else None
        return _compute_capital_return(cik, mcap)
    except Exception:
        return dict(_CAPITAL_RETURN_DEFAULTS)


def _compute_capital_dynamics(fdic_hist: list[dict] | None, shares: float | None) -> dict:
    """Compute capital-dynamics metrics for screening."""
    if not fdic_hist:
        return {
            "cet1_current": None, "cet1_qoq_pp": None,
            "tbv_cagr_1y": None, "payout_ratio_4q": None,
            "buyback_capacity_usd": None, "capital_alerts_count": None,
        }
    try:
        from analysis.capital_dynamics import compute_capital_screening_metrics
        return compute_capital_screening_metrics(fdic_hist, shares)
    except Exception:
        return {
            "cet1_current": None, "cet1_qoq_pp": None,
            "tbv_cagr_1y": None, "payout_ratio_4q": None,
            "buyback_capacity_usd": None, "capital_alerts_count": None,
        }


_CAPITAL_RETURN_DEFAULTS = {
    "shareholder_yield": None, "dividend_yield_sec": None, "buyback_yield": None,
    "payout_ratio_ttm": None, "total_return_ratio_ttm": None,
    "share_change_pct_ttm": None, "dps_yoy_pct": None,
    "dividends_ttm": None, "buybacks_ttm": None,
}


def _compute_capital_return(cik: int | None, market_cap: float | None) -> dict:
    """Compute SEC-sourced capital return metrics for screening."""
    if not cik:
        return dict(_CAPITAL_RETURN_DEFAULTS)
    try:
        from analysis.capital_return import summarize_capital_return
        res = summarize_capital_return(cik, market_cap=market_cap, lookback_quarters=12)
        ttm = res.get("ttm") or {}
        yld = res.get("yield") or {}
        growth = res.get("growth") or {}
        return {
            "shareholder_yield": yld.get("total_shareholder_yield_pct"),
            "dividend_yield_sec": yld.get("dividend_yield_pct"),
            "buyback_yield": yld.get("buyback_yield_pct"),
            "payout_ratio_ttm": (ttm.get("payout_ratio_ttm") or 0) * 100 if ttm.get("payout_ratio_ttm") is not None else None,
            "total_return_ratio_ttm": (ttm.get("total_return_ratio_ttm") or 0) * 100 if ttm.get("total_return_ratio_ttm") is not None else None,
            "share_change_pct_ttm": ttm.get("share_change_pct_ttm"),
            "dps_yoy_pct": growth.get("dps_yoy_pct"),
            "dividends_ttm": ttm.get("dividends_ttm"),
            "buybacks_ttm": ttm.get("buybacks_ttm"),
        }
    except Exception as e:
        print(f"[capital_return] error for CIK {cik}: {e}")
        return dict(_CAPITAL_RETURN_DEFAULTS)


def _compute_credit_dynamics(fdic_hist: list[dict] | None) -> dict:
    """Compute credit-quality trend metrics for screening."""
    if not fdic_hist:
        return {
            "nco_4q_trend_bps": None,
            "npl_trend_bps": None,
            "pd_migration_bps": None,
            "credit_alerts_count": None,
            "reserve_coverage_pct": None,
            "worst_segment_npl": None,
        }
    try:
        from analysis.credit_dynamics import compute_credit_screening_metrics
        return compute_credit_screening_metrics(fdic_hist)
    except Exception:
        return {
            "nco_4q_trend_bps": None, "npl_trend_bps": None,
            "pd_migration_bps": None, "credit_alerts_count": None,
            "reserve_coverage_pct": None, "worst_segment_npl": None,
        }


def _compute_deposit_dynamics(fdic_hist: list[dict] | None) -> dict:
    """Compute deposit-beta and QoQ metrics for screening."""
    if not fdic_hist:
        return {
            "deposit_cycle_beta": None,
            "deposit_rolling_beta": None,
            "dep_qoq_growth": None,
            "cod_qoq_bps": None,
            "deposit_alerts_count": None,
        }
    try:
        from analysis.deposit_dynamics import summarize_bank_deposits
        summary = summarize_bank_deposits(fdic_hist)
        timeline = summary.get("timeline")
        if timeline is None or timeline.empty:
            return {
                "deposit_cycle_beta": None, "deposit_rolling_beta": None,
                "dep_qoq_growth": None, "cod_qoq_bps": None,
                "deposit_alerts_count": 0,
            }

        latest = summary["latest"]
        cycle = summary["cycle_beta"]
        rolling = summary["rolling_beta"]
        cod_change = latest.get("cod_qoq_change")

        return {
            "deposit_cycle_beta": cycle.get("beta"),
            "deposit_rolling_beta": rolling.get("beta"),
            "dep_qoq_growth": latest.get("dep_qoq_growth"),
            "cod_qoq_bps": cod_change * 100 if cod_change is not None else None,
            "deposit_alerts_count": len(summary.get("alerts", [])),
        }
    except Exception:
        return {
            "deposit_cycle_beta": None, "deposit_rolling_beta": None,
            "dep_qoq_growth": None, "cod_qoq_bps": None,
            "deposit_alerts_count": None,
        }
