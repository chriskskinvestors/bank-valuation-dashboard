"""
DCF / Warranted P/TBV Valuation for banks.

Two linked models:

1. **FCFE DCF (Free Cash Flow to Equity)** — 5-year explicit forecast
   then Gordon-growth terminal value.
   FCFE = Net Income − (Δ loans × target CET1 ratio)
   PV = Σ FCFE_t / (1+CoE)^t + TV / (1+CoE)^N

2. **Warranted P/TBV** — Gordon-equivalent for banks.
   Fair P/TBV = (ROATCE − g) / (CoE − g)
   Fair Price = Fair P/TBV × current TBV per share

Both produce a fair value per share. You compare to market price.
"""

from __future__ import annotations


def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:  # NaN check
            return default
        return f
    except (TypeError, ValueError):
        return default


# ── Warranted P/TBV ─────────────────────────────────────────────────────

def warranted_ptbv(
    roatce_pct: float,
    cost_of_equity_pct: float,
    terminal_growth_pct: float,
) -> float | None:
    """
    Warranted P/TBV using the Gordon-equivalent for banks:
        P/TBV = (ROATCE − g) / (CoE − g)

    When ROATCE = CoE: P/TBV = 1.0 (break-even)
    When ROATCE > CoE: P/TBV > 1 (value creator)
    When ROATCE < CoE: P/TBV < 1 (value destroyer)

    Returns None if inputs are invalid (e.g., g >= CoE).
    """
    r = _safe(roatce_pct)
    coe = _safe(cost_of_equity_pct)
    g = _safe(terminal_growth_pct, 0.0)
    if r is None or coe is None:
        return None
    if coe <= g:  # model breaks down
        return None
    return (r - g) / (coe - g)


def warranted_ptbv_scenarios(
    roatce_list: list[float],
    ptbv_list: list[float],
    tbvps: float,
) -> list[list[dict]]:
    """
    Build a grid of fair prices across ROATCE × P/TBV cells.
    Used for display — not the DCF, but for scenario visualization.

    Returns grid where grid[i][j] = {"ptbv": ptbv_list[j], "price": roatce * ptbv * adj}.
    """
    # This is a simple cross-tab: just multiplies tbvps × ptbv for each cell
    grid = []
    for r in roatce_list:
        row = []
        for p in ptbv_list:
            row.append({
                "roatce": r,
                "ptbv": p,
                "fair_price": tbvps * p if tbvps else None,
            })
        grid.append(row)
    return grid


# ── FCFE DCF ────────────────────────────────────────────────────────────

def project_earnings(
    base_eps: float,
    growth_rates: list[float],  # e.g., [0.10, 0.08, 0.06, 0.05, 0.04] for 5 years
) -> list[float]:
    """Project EPS forward given year-by-year growth rates."""
    projection = []
    current = base_eps
    for g in growth_rates:
        current = current * (1 + g)
        projection.append(current)
    return projection


def project_dividends(
    projected_eps: list[float],
    payout_ratio: float,
) -> list[float]:
    """Dividend per share = EPS × payout."""
    return [eps * payout_ratio for eps in projected_eps]


def project_retained_capital_per_share(
    projected_eps: list[float],
    payout_ratio: float,
) -> list[float]:
    """Retained capital = EPS × (1 − payout). Builds TBV."""
    return [eps * (1 - payout_ratio) for eps in projected_eps]


def project_fcfe_per_share(
    projected_eps: list[float],
    loan_growth_rates: list[float],  # year-by-year loan growth %
    starting_loans_per_share: float,
    target_cet1_pct: float,
) -> list[float]:
    """
    FCFE per share = EPS − capital required for loan growth per share.
    Capital required = Δ loans per share × target CET1 ratio.

    Assumes ~100% risk weight on loans (conservative approximation).
    """
    fcfe = []
    loans_prev = starting_loans_per_share
    for i, eps in enumerate(projected_eps):
        loans_new = loans_prev * (1 + loan_growth_rates[i])
        delta_loans = loans_new - loans_prev
        capital_need = delta_loans * (target_cet1_pct / 100)
        fcfe.append(eps - capital_need)
        loans_prev = loans_new
    return fcfe


def present_value(
    cash_flows: list[float],
    discount_rate_pct: float,
) -> float:
    """PV of a cash flow stream."""
    r = discount_rate_pct / 100
    pv = 0.0
    for t, cf in enumerate(cash_flows, start=1):
        pv += cf / ((1 + r) ** t)
    return pv


def terminal_value(
    terminal_cf: float,
    discount_rate_pct: float,
    terminal_growth_pct: float,
) -> float | None:
    """Gordon growth terminal value at end of explicit period."""
    r = discount_rate_pct / 100
    g = terminal_growth_pct / 100
    if r <= g:
        return None
    return terminal_cf * (1 + g) / (r - g)


def run_fcfe_dcf(
    base_eps: float,
    eps_growth_rates: list[float],
    payout_ratio: float,
    loan_growth_rates: list[float],
    starting_loans_per_share: float,
    target_cet1_pct: float,
    cost_of_equity_pct: float,
    terminal_growth_pct: float,
    terminal_payout_ratio: float | None = None,
) -> dict:
    """
    Run a multi-year FCFE DCF.

    Returns:
        {
            "projected_eps": [...],
            "projected_fcfe": [...],
            "pv_explicit": float,  # PV of explicit forecast period
            "terminal_value": float,  # TV at end of year N
            "pv_terminal": float,  # TV discounted to today
            "fair_value_per_share": float,
        }
    """
    projected_eps = project_earnings(base_eps, eps_growth_rates)
    projected_fcfe = project_fcfe_per_share(
        projected_eps, loan_growth_rates,
        starting_loans_per_share, target_cet1_pct,
    )

    pv_explicit = present_value(projected_fcfe, cost_of_equity_pct)

    # Terminal FCFE — at steady state, loan growth = terminal growth
    # So FCFE_steady = EPS_terminal × (1 − retention_for_growth)
    # Use terminal payout if provided, else compute implied
    if terminal_payout_ratio is None:
        # Implied payout = 1 − (g/ROE); use average of last 2 years growth as ROE proxy
        terminal_payout_ratio = max(0.0, min(0.99,
            1.0 - (terminal_growth_pct / 100) / max(0.01, sum(eps_growth_rates[-2:]) / 2 + 0.01)
        ))

    terminal_eps = projected_eps[-1] * (1 + terminal_growth_pct / 100)
    terminal_fcfe = terminal_eps * terminal_payout_ratio

    tv = terminal_value(terminal_fcfe, cost_of_equity_pct, terminal_growth_pct)
    if tv is None:
        return {
            "projected_eps": projected_eps,
            "projected_fcfe": projected_fcfe,
            "pv_explicit": pv_explicit,
            "terminal_value": None,
            "pv_terminal": None,
            "fair_value_per_share": None,
            "error": "Terminal value undefined (discount rate ≤ terminal growth)",
        }

    n = len(projected_fcfe)
    pv_terminal = tv / ((1 + cost_of_equity_pct / 100) ** n)
    fair_value = pv_explicit + pv_terminal

    return {
        "projected_eps": projected_eps,
        "projected_fcfe": projected_fcfe,
        "pv_explicit": pv_explicit,
        "terminal_value": tv,
        "pv_terminal": pv_terminal,
        "fair_value_per_share": fair_value,
        "terminal_payout_ratio_used": terminal_payout_ratio,
        "terminal_eps": terminal_eps,
    }


# ── Sensitivity grids ──────────────────────────────────────────────────

def dcf_sensitivity_grid(
    base_params: dict,
    coe_range: list[float],
    growth_range: list[float],
) -> list[list[float | None]]:
    """
    2-way DCF sensitivity: cost of equity × terminal growth.

    base_params must contain all other DCF inputs. Grid rows = CoE, cols = growth.
    """
    grid = []
    for coe in coe_range:
        row = []
        for g in growth_range:
            params = {**base_params, "cost_of_equity_pct": coe, "terminal_growth_pct": g}
            try:
                result = run_fcfe_dcf(**params)
                row.append(result.get("fair_value_per_share"))
            except Exception:
                row.append(None)
        grid.append(row)
    return grid


def warranted_ptbv_grid(
    roatce_range: list[float],
    coe_range: list[float],
    terminal_growth_pct: float,
    tbvps: float,
) -> list[list[float | None]]:
    """
    2-way Warranted P/TBV sensitivity: ROATCE × CoE.
    Returns fair prices (= fair P/TBV × TBVPS).
    Rows = ROATCE, columns = CoE.
    """
    grid = []
    for r in roatce_range:
        row = []
        for c in coe_range:
            ptbv = warranted_ptbv(r, c, terminal_growth_pct)
            price = ptbv * tbvps if (ptbv is not None and tbvps) else None
            row.append(price)
        grid.append(row)
    return grid


# ── Tornado sensitivity ────────────────────────────────────────────────

def tornado_sensitivity(base_params: dict, perturbations: dict | None = None) -> list[dict]:
    """
    One-at-a-time sensitivity: how much does each input move fair value?

    perturbations: {input_name: (low_adj, high_adj)}
        For list-type inputs (e.g. eps_growth_rates), adjustments are applied
        uniformly across all elements. For scalars, they're added.

    Returns list of {input, low_fv, high_fv, range, base_fv}
    sorted by range descending.
    """
    base_result = run_fcfe_dcf(**base_params)
    base_fv = base_result.get("fair_value_per_share") or 0

    if perturbations is None:
        # Each tuple is (low_adj, high_adj): the adjustment that produces the
        # LOW FV case vs the HIGH FV case. So for inputs where "lower input =
        # higher FV" (CoE, target CET1, loan growth), the low_adj is positive.
        perturbations = {
            "eps_growth_rates": (-0.03, 0.03),      # ±3pp: slower growth = low FV
            "loan_growth_rates": (0.03, -0.03),     # ±3pp: faster growth = more cap need = low FV
            "payout_ratio": (-0.15, 0.15),          # ±15pp payout (doesn't affect FCFE)
            "cost_of_equity_pct": (1.0, -1.0),      # ±1pp: higher CoE = low FV
            "terminal_growth_pct": (-0.5, 0.5),     # ±0.5pp: lower g = low FV
            "target_cet1_pct": (1.0, -1.0),         # ±1pp: higher target = more cap need = low FV
        }

    results = []

    def _apply_adj(params: dict, key: str, adj: float) -> dict:
        out = dict(params)
        val = out.get(key)
        if isinstance(val, list):
            out[key] = [v + adj for v in val]
        elif isinstance(val, (int, float)):
            out[key] = val + adj
        return out

    for key, (low_adj, high_adj) in perturbations.items():
        try:
            low_params = _apply_adj(base_params, key, low_adj)
            high_params = _apply_adj(base_params, key, high_adj)
            low_fv = run_fcfe_dcf(**low_params).get("fair_value_per_share")
            high_fv = run_fcfe_dcf(**high_params).get("fair_value_per_share")
        except Exception:
            continue

        if low_fv is None or high_fv is None:
            continue

        results.append({
            "input": key,
            "low_fv": low_fv,
            "high_fv": high_fv,
            "low_adj": low_adj,
            "high_adj": high_adj,
            "range": abs(high_fv - low_fv),
            "low_delta_pct": (low_fv / base_fv - 1) * 100 if base_fv else 0,
            "high_delta_pct": (high_fv / base_fv - 1) * 100 if base_fv else 0,
            "base_fv": base_fv,
        })

    return sorted(results, key=lambda r: r["range"], reverse=True)


# ── Implied IRR ────────────────────────────────────────────────────────

def implied_irr(
    current_price: float,
    base_params: dict,
    max_iter: int = 60,
    tolerance: float = 0.01,
) -> float | None:
    """
    Find the cost of equity at which DCF fair value = current market price.

    This is the return an investor earns if the DCF assumptions (ex-CoE) prove correct.
    Uses bisection between 3% and 25%.

    Returns IRR as % (e.g. 11.5 for 11.5%) or None if cannot solve.
    """
    if not current_price or current_price <= 0:
        return None

    def _fv_at(coe_pct: float) -> float | None:
        params = {**base_params, "cost_of_equity_pct": coe_pct}
        try:
            r = run_fcfe_dcf(**params)
            return r.get("fair_value_per_share")
        except Exception:
            return None

    # Bracket: lower CoE = higher FV, higher CoE = lower FV
    low, high = 3.0, 30.0
    low_fv = _fv_at(low)
    high_fv = _fv_at(high)

    if low_fv is None or high_fv is None:
        return None

    # If market price is outside the bracket's FV range, can't solve
    if current_price > low_fv:
        return 2.0  # below 3% — essentially unmodelable (very high FV at any rate)
    if current_price < high_fv:
        return 30.0  # above 30% — extreme

    # Bisection
    for _ in range(max_iter):
        mid = (low + high) / 2
        mid_fv = _fv_at(mid)
        if mid_fv is None:
            return None
        if abs(mid_fv - current_price) < tolerance:
            return mid
        if mid_fv > current_price:
            low = mid
        else:
            high = mid

    return (low + high) / 2


# ── Peer-relative warranted multiples ──────────────────────────────────

def rank_peer_warranted_ptbv(
    peer_metrics: list[dict],
    cost_of_equity_pct: float,
    terminal_growth_pct: float,
) -> list[dict]:
    """
    For each peer bank, compute warranted P/TBV vs actual P/TBV to rank
    them by implied upside/downside.

    peer_metrics: list of bank metric dicts (each with ticker, roatce, ptbv_ratio, tbvps, price)
    Returns list sorted by upside descending.
    """
    results = []
    for m in peer_metrics:
        roatce = m.get("roatce") or m.get("roatce_blended")
        ptbv_actual = m.get("ptbv_ratio")
        tbvps = m.get("tbvps")
        price = m.get("price")
        if roatce is None or tbvps is None or tbvps <= 0:
            continue

        warranted = warranted_ptbv(roatce, cost_of_equity_pct, terminal_growth_pct)
        if warranted is None:
            continue

        fair_price = warranted * tbvps
        upside_pct = ((fair_price / price) - 1) * 100 if (price and price > 0) else None

        gap_vs_actual = None
        if ptbv_actual is not None and ptbv_actual > 0:
            gap_vs_actual = (warranted / ptbv_actual - 1) * 100

        results.append({
            "ticker": m.get("ticker"),
            "name": m.get("bank") or m.get("ticker"),
            "roatce": roatce,
            "ptbv_actual": ptbv_actual,
            "ptbv_warranted": warranted,
            "price": price,
            "fair_price": fair_price,
            "upside_pct": upside_pct,
            "gap_vs_actual_pct": gap_vs_actual,
        })

    results.sort(key=lambda r: (r["upside_pct"] is None, -(r["upside_pct"] or 0)))
    return results


# ── Scenarios (bull / base / bear) ──────────────────────────────────────

def run_scenarios(
    base_params: dict,
    bull_adjustments: dict,   # e.g., {"eps_growth_rates": [+0.02, ...], "cost_of_equity_pct": -1.0}
    bear_adjustments: dict,
) -> dict:
    """
    Run bull, base, bear DCF scenarios.

    Adjustments are deltas applied to base parameters.
    """
    def _apply(params, adj):
        out = {**params}
        for k, v in adj.items():
            if isinstance(v, list) and isinstance(out.get(k), list):
                out[k] = [a + b for a, b in zip(out[k], v)]
            elif isinstance(v, (int, float)) and isinstance(out.get(k), (int, float)):
                out[k] = out[k] + v
            else:
                out[k] = v
        return out

    base = run_fcfe_dcf(**base_params)
    bull = run_fcfe_dcf(**_apply(base_params, bull_adjustments))
    bear = run_fcfe_dcf(**_apply(base_params, bear_adjustments))

    return {"bull": bull, "base": base, "bear": bear}
