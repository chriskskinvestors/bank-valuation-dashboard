"""
Rate Sensitivity / NIM Scenario Analysis.

Given a bank's current balance sheet (earning assets, int-bearing liabilities,
cost of deposits, earning asset yield), models NIM and NII impact under
rate scenarios.

Two beta models:
  - Historical: use the bank's measured deposit beta (from deposit_dynamics)
  - Textbook:   50% beta on interest-bearing deposits, 0% on non-interest

Asset repricing: assume 100% beta on earning assets (simplification).
Real-world asset repricing is slower due to fixed-rate loans, but this is
a first-order approximation used across sell-side research.
"""

from __future__ import annotations


# Rate scenarios (bps changes from current Fed funds)
DEFAULT_SCENARIOS_BPS = [-200, -100, -50, 0, 50, 100, 200]

TEXTBOOK_INT_BEARING_BETA = 0.50  # half of rate change flows to int-bearing deposits
TEXTBOOK_NON_INT_BETA = 0.0       # non-int bearing deposits don't reprice


def _safe(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def build_rate_sensitivity_inputs(
    fdic_latest: dict,
    fdic_hist: list[dict] | None = None,
) -> dict:
    """
    Extract the balance-sheet inputs needed for rate sensitivity from FDIC data.

    All dollar amounts are in raw dollars (not thousands).
    """
    total_assets_k = _safe(fdic_latest.get("ASSET"))  # thousands
    total_loans_k = _safe(fdic_latest.get("LNLSNET"))
    securities_k = _safe(fdic_latest.get("SC"))
    cash_k = _safe(fdic_latest.get("CHBAL"))
    total_deposits_k = _safe(fdic_latest.get("DEP"))
    int_bearing_dep_k = _safe(fdic_latest.get("DEPIDOM"))
    non_int_dep_k = _safe(fdic_latest.get("DEPNIDOM"))
    brokered_k = _safe(fdic_latest.get("BRO"))

    # Earning assets: prefer FDIC's ERNAST field (true earning assets, excludes
    # non-earning cash and vault cash). Fall back to loans + securities only
    # (excluding cash) if unavailable. Adding CHBAL would overstate earning
    # assets because vault cash and required reserves are mostly non-earning.
    ernast_k = _safe(fdic_latest.get("ERNAST"))
    if ernast_k:
        earning_assets_k = ernast_k
    else:
        earning_assets_k = total_loans_k + securities_k

    # Yields / costs (%)
    earning_asset_yield = fdic_latest.get("INTINCY")  # int income yield
    cost_of_int_bearing = fdic_latest.get("INTEXPY")  # cost of int-bearing liabilities
    current_nim = fdic_latest.get("NIMY")              # current NIM

    return {
        "total_assets_usd": total_assets_k * 1000,
        "earning_assets_usd": earning_assets_k * 1000,
        "total_deposits_usd": total_deposits_k * 1000,
        "int_bearing_dep_usd": int_bearing_dep_k * 1000,
        "non_int_dep_usd": non_int_dep_k * 1000,
        "brokered_usd": brokered_k * 1000,
        "earning_asset_yield_pct": earning_asset_yield,
        "cost_of_int_bearing_pct": cost_of_int_bearing,
        "current_nim_pct": current_nim,
    }


def compute_historical_deposit_beta(fdic_hist: list[dict] | None) -> float | None:
    """Pull the cycle beta computed in deposit_dynamics module."""
    if not fdic_hist:
        return None
    try:
        from analysis.deposit_dynamics import summarize_bank_deposits
        summary = summarize_bank_deposits(fdic_hist)
        cycle = summary.get("cycle_beta", {})
        return cycle.get("beta")
    except Exception:
        return None


def apply_rate_scenario(
    inputs: dict,
    rate_change_bps: float,
    deposit_beta_int_bearing: float,
    deposit_beta_non_int: float = 0.0,
    asset_beta: float = 1.0,
) -> dict:
    """
    Apply a rate scenario and return the projected NIM + NII impact.

    Args:
        inputs: dict from build_rate_sensitivity_inputs()
        rate_change_bps: basis points change (e.g., 100 = +1% parallel shift)
        deposit_beta_int_bearing: 0-1, % of rate change that flows to int-bearing deposits
        deposit_beta_non_int: 0-1, for non-interest-bearing deposits (typically ~0)
        asset_beta: 0-1, % of rate change that flows to earning asset yields
                    (1.0 = full pass-through; lower = slower repricing)

    Returns:
        {
            "rate_change_bps": 100,
            "earning_asset_yield_new_pct": 5.75,
            "cost_of_funds_new_pct": 2.80,
            "nim_new_pct": 2.95,
            "nim_delta_bps": 12,
            "nii_current_usd": ...,
            "nii_new_usd": ...,
            "nii_delta_usd": ...,
        }
    """
    # Current state
    ea_yield = _safe(inputs.get("earning_asset_yield_pct"))
    cost_ibl = _safe(inputs.get("cost_of_int_bearing_pct"))
    current_nim = _safe(inputs.get("current_nim_pct"))
    earning_assets = _safe(inputs.get("earning_assets_usd"))
    int_bearing_dep = _safe(inputs.get("int_bearing_dep_usd"))
    non_int_dep = _safe(inputs.get("non_int_dep_usd"))
    total_dep = _safe(inputs.get("total_deposits_usd"))

    # Current NII = earning assets × NIM
    nii_current = earning_assets * (current_nim / 100)

    # Rate move in pp
    rate_pp = rate_change_bps / 100.0

    # New earning asset yield
    ea_yield_new = ea_yield + rate_pp * asset_beta

    # New cost of int-bearing liabilities
    cost_ibl_new = cost_ibl + rate_pp * deposit_beta_int_bearing

    # Effective cost of total deposits (weighted by int-bearing vs non-int)
    if total_dep > 0:
        ib_weight = int_bearing_dep / total_dep
        ni_weight = non_int_dep / total_dep
        # Approximate current blended cost of deposits
        current_blended_cof = (ib_weight * cost_ibl) + (ni_weight * 0.0)  # non-int = 0
        new_blended_cof = (
            ib_weight * cost_ibl_new +
            ni_weight * (rate_pp * deposit_beta_non_int)
        )
        cof_change = new_blended_cof - current_blended_cof
    else:
        cof_change = rate_pp * deposit_beta_int_bearing

    # NIM delta: asset yield up, cost up — spread impact
    # Assume earning assets ≈ funded by deposits (simplification)
    yield_delta = rate_pp * asset_beta
    nim_delta_pp = yield_delta - cof_change
    nim_new = current_nim + nim_delta_pp

    # NII impact
    nii_new = earning_assets * (nim_new / 100)
    nii_delta = nii_new - nii_current

    return {
        "rate_change_bps": rate_change_bps,
        "earning_asset_yield_new_pct": ea_yield_new,
        "cost_of_funds_new_pct": (cost_ibl_new if int_bearing_dep > 0 else None),
        "nim_current_pct": current_nim,
        "nim_new_pct": nim_new,
        "nim_delta_bps": nim_delta_pp * 100,
        "nii_current_usd": nii_current,
        "nii_new_usd": nii_new,
        "nii_delta_usd": nii_delta,
    }


# ── Curve-based scenarios (3M × 5Y) ────────────────────────────────────

def apply_curve_scenario(
    inputs: dict,
    short_rate_change_bps: float,  # 3M change — drives funding costs
    long_rate_change_bps: float,   # 5Y change — drives asset yields
    deposit_beta_int_bearing: float,
    deposit_beta_non_int: float = 0.0,
    asset_beta: float = 1.0,  # 0-1: how much of long-rate change flows to asset yields
) -> dict:
    """
    Apply a non-parallel (curve) rate scenario.

    Banks are typically short-funded and long-invested, so:
      - 3M (short-end) rate changes drive cost of funds via deposit beta
      - 5Y (medium-end) rate changes drive earning-asset yields via asset beta

    A steepening curve (5Y up more than 3M) widens NIM.
    A flattening curve compresses NIM.
    """
    ea_yield = _safe(inputs.get("earning_asset_yield_pct"))
    cost_ibl = _safe(inputs.get("cost_of_int_bearing_pct"))
    current_nim = _safe(inputs.get("current_nim_pct"))
    earning_assets = _safe(inputs.get("earning_assets_usd"))
    int_bearing_dep = _safe(inputs.get("int_bearing_dep_usd"))
    non_int_dep = _safe(inputs.get("non_int_dep_usd"))
    total_dep = _safe(inputs.get("total_deposits_usd"))

    nii_current = earning_assets * (current_nim / 100)

    short_pp = short_rate_change_bps / 100.0
    long_pp = long_rate_change_bps / 100.0

    # Asset yield follows the long rate (scaled by asset_beta)
    ea_yield_new = ea_yield + long_pp * asset_beta

    # Cost of interest-bearing liabilities follows the short rate
    cost_ibl_new = cost_ibl + short_pp * deposit_beta_int_bearing

    # Blended cost of funds
    if total_dep > 0:
        ib_weight = int_bearing_dep / total_dep
        ni_weight = non_int_dep / total_dep
        current_blended_cof = ib_weight * cost_ibl
        new_blended_cof = (
            ib_weight * cost_ibl_new +
            ni_weight * (short_pp * deposit_beta_non_int)
        )
        cof_change = new_blended_cof - current_blended_cof
    else:
        cof_change = short_pp * deposit_beta_int_bearing

    yield_delta = long_pp * asset_beta
    nim_delta_pp = yield_delta - cof_change
    nim_new = current_nim + nim_delta_pp

    nii_new = earning_assets * (nim_new / 100)
    nii_delta = nii_new - nii_current

    return {
        "short_change_bps": short_rate_change_bps,
        "long_change_bps": long_rate_change_bps,
        "earning_asset_yield_new_pct": ea_yield_new,
        "cost_of_funds_new_pct": cost_ibl_new if int_bearing_dep > 0 else None,
        "nim_current_pct": current_nim,
        "nim_new_pct": nim_new,
        "nim_delta_bps": nim_delta_pp * 100,
        "nii_current_usd": nii_current,
        "nii_new_usd": nii_new,
        "nii_delta_usd": nii_delta,
    }


# Named curve scenarios — standard analyst preset pairs
NAMED_SCENARIOS = [
    {"name": "Parallel +100", "short_bps": 100, "long_bps": 100,
     "description": "All rates up 100bps (unchanged curve shape)."},
    {"name": "Parallel -100", "short_bps": -100, "long_bps": -100,
     "description": "All rates down 100bps."},
    {"name": "Bull Steepener", "short_bps": -100, "long_bps": -25,
     "description": "Short rates fall faster than long — curve steepens via Fed cuts."},
    {"name": "Bear Steepener", "short_bps": 25, "long_bps": 100,
     "description": "Long rates rise faster than short — curve steepens via growth/inflation."},
    {"name": "Bull Flattener", "short_bps": -25, "long_bps": -100,
     "description": "Long rates fall faster than short — curve flattens via growth scare."},
    {"name": "Bear Flattener", "short_bps": 100, "long_bps": 25,
     "description": "Short rates rise faster than long — curve flattens via Fed hikes."},
    {"name": "Curve Inversion", "short_bps": 100, "long_bps": -50,
     "description": "Short up, long down — recession-signal curve."},
    {"name": "Curve Normalization", "short_bps": -100, "long_bps": 25,
     "description": "Short down, long up — post-inversion recovery."},
]


def run_curve_sensitivity(
    fdic_latest: dict,
    fdic_hist: list[dict] | None = None,
    beta_mode: str = "historical",
    custom_deposit_beta: float | None = None,
    asset_beta: float = 1.0,
    scenarios: list[dict] | None = None,
) -> dict:
    """
    Run curve-shift scenarios (non-parallel 3M × 5Y).
    Returns same shape as run_rate_sensitivity with per-scenario NIM/NII impacts.
    """
    inputs = build_rate_sensitivity_inputs(fdic_latest, fdic_hist)
    beta_int, beta_ni, resolved_mode = _resolve_deposit_beta(
        inputs, fdic_hist, beta_mode, custom_deposit_beta
    )

    if scenarios is None:
        scenarios = NAMED_SCENARIOS

    results = []
    for s in scenarios:
        r = apply_curve_scenario(
            inputs,
            short_rate_change_bps=s["short_bps"],
            long_rate_change_bps=s["long_bps"],
            deposit_beta_int_bearing=beta_int,
            deposit_beta_non_int=beta_ni,
            asset_beta=asset_beta,
        )
        r["name"] = s["name"]
        r["description"] = s.get("description", "")
        results.append(r)

    return {
        "inputs": inputs,
        "beta_used": beta_int,
        "beta_mode": resolved_mode,
        "asset_beta": asset_beta,
        "scenarios": results,
    }


def run_curve_matrix(
    fdic_latest: dict,
    fdic_hist: list[dict] | None = None,
    short_bps_range: list[int] | None = None,
    long_bps_range: list[int] | None = None,
    beta_mode: str = "historical",
    custom_deposit_beta: float | None = None,
    asset_beta: float = 1.0,
) -> dict:
    """
    Build a 2D matrix of NIM deltas across (short, long) rate combinations.
    Rows = short rate change, columns = long rate change.
    """
    inputs = build_rate_sensitivity_inputs(fdic_latest, fdic_hist)
    beta_int, beta_ni, resolved_mode = _resolve_deposit_beta(
        inputs, fdic_hist, beta_mode, custom_deposit_beta
    )

    if short_bps_range is None:
        short_bps_range = [-100, -50, 0, 50, 100]
    if long_bps_range is None:
        long_bps_range = [-100, -50, 0, 50, 100]

    nim_matrix = []
    nii_matrix = []
    for s_bps in short_bps_range:
        nim_row = []
        nii_row = []
        for l_bps in long_bps_range:
            r = apply_curve_scenario(
                inputs, s_bps, l_bps,
                deposit_beta_int_bearing=beta_int,
                deposit_beta_non_int=beta_ni,
                asset_beta=asset_beta,
            )
            nim_row.append(r["nim_delta_bps"])
            nii_row.append(r["nii_delta_usd"])
        nim_matrix.append(nim_row)
        nii_matrix.append(nii_row)

    return {
        "inputs": inputs,
        "beta_used": beta_int,
        "beta_mode": resolved_mode,
        "asset_beta": asset_beta,
        "short_bps_range": short_bps_range,
        "long_bps_range": long_bps_range,
        "nim_delta_matrix_bps": nim_matrix,
        "nii_delta_matrix_usd": nii_matrix,
    }


def _resolve_deposit_beta(
    inputs: dict, fdic_hist: list[dict] | None,
    beta_mode: str, custom_deposit_beta: float | None,
) -> tuple[float, float, str]:
    """
    Shared beta resolution logic. Returns (beta_int, beta_ni, resolved_mode).

    Note: deposit_dynamics._cost_of_deposits returns INTEXPY (FDIC's cost of
    interest-bearing liabilities, already annualized %). So the cycle beta is
    already the beta for int-bearing deposits — do NOT divide by ib_weight.
    """
    if custom_deposit_beta is not None:
        return (max(0.0, min(1.0, custom_deposit_beta)), 0.0, "custom")
    if beta_mode == "textbook":
        return (TEXTBOOK_INT_BEARING_BETA, TEXTBOOK_NON_INT_BETA, "textbook")
    # Historical — measured on INTEXPY (int-bearing cost), so it's already
    # the int-bearing deposit beta. Use directly.
    hist_beta = compute_historical_deposit_beta(fdic_hist)
    if hist_beta is not None:
        beta_int = max(-0.20, min(1.50, hist_beta))
        return (beta_int, 0.0, "historical")
    return (TEXTBOOK_INT_BEARING_BETA, TEXTBOOK_NON_INT_BETA, "textbook_fallback")


def run_rate_sensitivity(
    fdic_latest: dict,
    fdic_hist: list[dict] | None = None,
    beta_mode: str = "historical",  # "historical" or "textbook"
    scenarios_bps: list[int] | None = None,
    custom_deposit_beta: float | None = None,
) -> dict:
    """
    Run the full rate sensitivity analysis across scenarios.

    Returns:
        {
            "inputs": {...},
            "beta_used": 0.42,
            "beta_mode": "historical",
            "scenarios": [{rate_change_bps: -100, ...}, ...],
            "asymmetry_bps": float  # up scenario vs down scenario NIM delta
        }
    """
    if scenarios_bps is None:
        scenarios_bps = DEFAULT_SCENARIOS_BPS

    inputs = build_rate_sensitivity_inputs(fdic_latest, fdic_hist)

    # Decide deposit beta
    if custom_deposit_beta is not None:
        beta_int = max(0.0, min(1.0, custom_deposit_beta))
        beta_ni = 0.0
    elif beta_mode == "textbook":
        beta_int = TEXTBOOK_INT_BEARING_BETA
        beta_ni = TEXTBOOK_NON_INT_BETA
    else:  # historical
        hist_beta = compute_historical_deposit_beta(fdic_hist)
        if hist_beta is not None:
            # Historical beta measures "cost response to Fed funds change"
            # We need to translate that to int-bearing deposit beta
            # The measured beta ≈ blended deposit beta; so we adjust:
            # measured = ib_weight * beta_int + ni_weight * 0
            # => beta_int = measured / ib_weight
            total_dep = inputs.get("total_deposits_usd") or 0
            int_bearing = inputs.get("int_bearing_dep_usd") or 0
            ib_weight = int_bearing / total_dep if total_dep > 0 else 1.0
            beta_int = hist_beta / ib_weight if ib_weight > 0 else hist_beta
            # Clip to reasonable range — very negative = anomaly
            beta_int = max(-0.20, min(1.50, beta_int))
            beta_ni = 0.0
        else:
            # Fall back to textbook
            beta_int = TEXTBOOK_INT_BEARING_BETA
            beta_ni = TEXTBOOK_NON_INT_BETA
            beta_mode = "textbook_fallback"

    scenario_results = [
        apply_rate_scenario(inputs, bps, beta_int, beta_ni, asset_beta=1.0)
        for bps in scenarios_bps
    ]

    # Asymmetry: compare NIM delta at +100 vs -100
    up_100 = next((s for s in scenario_results if s["rate_change_bps"] == 100), None)
    dn_100 = next((s for s in scenario_results if s["rate_change_bps"] == -100), None)
    asymmetry_bps = None
    if up_100 and dn_100:
        asymmetry_bps = up_100["nim_delta_bps"] + dn_100["nim_delta_bps"]

    return {
        "inputs": inputs,
        "beta_used": beta_int,
        "beta_mode": beta_mode,
        "scenarios": scenario_results,
        "asymmetry_bps": asymmetry_bps,
    }
