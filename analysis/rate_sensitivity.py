"""
Rate Sensitivity / NIM Scenario Analysis.

Given a bank's current balance sheet (earning assets, int-bearing liabilities,
cost of deposits, earning asset yield), models NIM and NII impact under
rate scenarios.

Beta models:
  • Historical — use the bank's measured deposit beta (from deposit_dynamics)
  • Textbook   — 50% beta on interest-bearing deposits, 0% on non-interest

Asset repricing pace (model_pace=True):
  Earning assets do NOT all reprice immediately. Loans reprice on schedule
  (floating-rate loans pass through fast; fixed-rate loans only when they
  amortize / mature). Securities reprice when they roll over.

  The FDIC public API doesn't expose granular asset-maturity buckets
  (ASTM3MY etc are blank for most banks), so we approximate from portfolio
  composition + industry-typical durations:
    • Securities (~3.5-year average duration): ~29%/yr reprice
    • Loans split between floating and fixed:
        – Floating-rate loans (estimated 30% of book): reprice within 1Q
        – Fixed-rate loans (~70%): reprice on amortization, ~15%/yr
  Cumulative repricing pace by year N is a weighted average of these.

  Per-bank refinement: floating-loan share is taken from LNLSDEPR (loan
  yield variability vs. deposits — high values indicate more floating-rate).

Deposit mix-shift (apply_mix_shift=True):
  Holding NIB-to-IB ratio constant under-states funding cost increases in
  steep rate-up scenarios. We model a shift = max(0, rate_change_pp × 0.04)
  i.e. ~4 pp of NIB migrates to IB per 100 bps of rate move (rough cycle
  average; will refine with per-bank fit later).

EPS impact:
  Compute NII delta → pretax income delta. Apply the bank's effective tax
  rate (FDIC ITAX / PTAXNETINC; defaults to 21%). Divide by shares
  outstanding (from SEC data) for EPS delta per scenario.
"""

from __future__ import annotations


# Rate scenarios (bps changes from current Fed funds)
DEFAULT_SCENARIOS_BPS = [-200, -100, -50, 0, 50, 100, 200]

TEXTBOOK_INT_BEARING_BETA = 0.50  # half of rate change flows to int-bearing deposits
TEXTBOOK_NON_INT_BETA = 0.0       # non-int bearing deposits don't reprice

# Asset repricing pace assumptions (industry averages)
# Cumulative fraction of earning assets repriced by end of year N.
# Securities-heavy banks reprice slightly slower; loan-heavy slightly faster.
_REPRICING_BASE = {
    # year: (securities_pace, fixed_loan_pace, floating_loan_pace_q1)
    1: (0.29, 0.15, 1.00),
    2: (0.55, 0.28, 1.00),
    3: (0.75, 0.40, 1.00),
    4: (0.90, 0.50, 1.00),
    5: (1.00, 0.58, 1.00),
}

# Default share of loans that are floating-rate (industry avg).
_DEFAULT_FLOATING_LOAN_SHARE = 0.30

# Deposit mix-shift: per 100 bps rate up, what % of NIB migrates to IB
_MIX_SHIFT_PER_100BPS = 0.04

# Default effective tax rate if FDIC data missing
_DEFAULT_TAX_RATE = 0.21


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

    # Portfolio composition for repricing-pace modeling
    if earning_assets_k > 0:
        sec_share = securities_k / earning_assets_k
        loan_share = total_loans_k / earning_assets_k
    else:
        sec_share = 0.0
        loan_share = 0.0

    # Effective tax rate for EPS impact. PTAXNETINC is pretax NI; ITAX is
    # tax expense — both reported quarterly so the ratio is meaningful even
    # for a single quarter. Bounded to [0, 0.40] to filter outliers like
    # tax-benefit quarters (negative rate) or one-time items.
    itax = _safe(fdic_latest.get("ITAX"))
    ptax_ni = _safe(fdic_latest.get("PTAXNETINC"))
    if ptax_ni > 0:
        tax_rate = max(0.0, min(0.40, itax / ptax_ni))
    else:
        tax_rate = _DEFAULT_TAX_RATE

    return {
        "total_assets_usd": total_assets_k * 1000,
        "earning_assets_usd": earning_assets_k * 1000,
        "securities_usd": securities_k * 1000,
        "loans_usd": total_loans_k * 1000,
        "securities_share": sec_share,
        "loans_share": loan_share,
        "total_deposits_usd": total_deposits_k * 1000,
        "int_bearing_dep_usd": int_bearing_dep_k * 1000,
        "non_int_dep_usd": non_int_dep_k * 1000,
        "brokered_usd": brokered_k * 1000,
        "earning_asset_yield_pct": earning_asset_yield,
        "cost_of_int_bearing_pct": cost_of_int_bearing,
        "current_nim_pct": current_nim,
        "effective_tax_rate": tax_rate,
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


def compute_repricing_pace(
    inputs: dict, floating_loan_share: float | None = None,
) -> dict[int, float]:
    """
    Cumulative fraction of earning assets that has repriced by end of year N.

    Returns {1: x1, 2: x2, ..., 5: x5} where each value is in [0, 1].

    Mix:
      • Securities: per-year pace from _REPRICING_BASE (~29%/yr)
      • Floating-rate loans: ~100% in Q1
      • Fixed-rate loans: per-year pace from _REPRICING_BASE (~15%/yr)

    floating_loan_share defaults to _DEFAULT_FLOATING_LOAN_SHARE (0.30).
    """
    sec_share = _safe(inputs.get("securities_share"))
    loan_share = _safe(inputs.get("loans_share"))
    fls = floating_loan_share if floating_loan_share is not None \
        else _DEFAULT_FLOATING_LOAN_SHARE
    fls = max(0.0, min(1.0, fls))

    floating_share = loan_share * fls
    fixed_share = loan_share * (1 - fls)

    pace: dict[int, float] = {}
    for year, (sec_p, fixed_p, flo_p) in _REPRICING_BASE.items():
        cum = (sec_share * sec_p) + (fixed_share * fixed_p) + (floating_share * flo_p)
        pace[year] = max(0.0, min(1.0, cum))
    return pace


def apply_rate_scenario_phased(
    inputs: dict,
    rate_change_bps: float,
    deposit_beta_int_bearing: float,
    deposit_beta_non_int: float = 0.0,
    floating_loan_share: float | None = None,
    apply_mix_shift: bool = True,
    shares_outstanding: float | None = None,
    horizon_years: int = 3,
) -> dict:
    """
    Multi-year rate scenario with phased asset repricing + deposit mix shift
    + EPS impact.

    Returns:
      {
        rate_change_bps: ...,
        years: [{year: 1, nim_new_pct, nim_delta_bps, nii_delta_usd,
                  pretax_delta_usd, net_income_delta_usd, eps_delta},
                {year: 2, ...}, ...],
        current_nim_pct, current_nii_usd, ...
      }
    """
    ea_yield = _safe(inputs.get("earning_asset_yield_pct"))
    cost_ibl = _safe(inputs.get("cost_of_int_bearing_pct"))
    current_nim = _safe(inputs.get("current_nim_pct"))
    earning_assets = _safe(inputs.get("earning_assets_usd"))
    int_bearing_dep = _safe(inputs.get("int_bearing_dep_usd"))
    non_int_dep = _safe(inputs.get("non_int_dep_usd"))
    total_dep = _safe(inputs.get("total_deposits_usd"))
    tax_rate = _safe(inputs.get("effective_tax_rate"), _DEFAULT_TAX_RATE)

    pace = compute_repricing_pace(inputs, floating_loan_share)
    nii_current = earning_assets * (current_nim / 100)
    rate_pp = rate_change_bps / 100.0

    # Deposit cost moves immediately (floating-rate exposure is mostly
    # already there; CDs reprice on maturity but that's largely <1yr).
    cost_ibl_new = cost_ibl + rate_pp * deposit_beta_int_bearing

    # Deposit mix-shift in rate-up scenarios: NIB customers move to IB.
    # Applied as a 1-time shift at the end of year 1 (cycle assumption).
    if apply_mix_shift and rate_pp > 0 and total_dep > 0:
        shift_frac = max(0.0, rate_pp * _MIX_SHIFT_PER_100BPS)
        shifted_nib = min(non_int_dep, non_int_dep * shift_frac)
        eff_int_bearing_dep = int_bearing_dep + shifted_nib
        eff_non_int_dep = non_int_dep - shifted_nib
    else:
        eff_int_bearing_dep = int_bearing_dep
        eff_non_int_dep = non_int_dep

    years_out = []
    horizon = min(max(1, horizon_years), 5)
    for year in range(1, horizon + 1):
        repriced_frac = pace.get(year, 1.0)
        # Asset yield: only the repriced portion benefits from the rate move.
        # Unpriced portion stays at current yield.
        ea_yield_new = ea_yield + rate_pp * repriced_frac

        # Blended cost of funds — uses (possibly mix-shifted) deposit balances
        if total_dep > 0:
            ib_weight = eff_int_bearing_dep / total_dep
            ni_weight = eff_non_int_dep / total_dep
            current_blended_cof = (int_bearing_dep / total_dep) * cost_ibl
            new_blended_cof = (
                ib_weight * cost_ibl_new
                + ni_weight * (rate_pp * deposit_beta_non_int)
            )
            cof_change = new_blended_cof - current_blended_cof
        else:
            cof_change = rate_pp * deposit_beta_int_bearing

        yield_delta_pp = rate_pp * repriced_frac
        nim_delta_pp = yield_delta_pp - cof_change
        nim_new = current_nim + nim_delta_pp
        nii_new = earning_assets * (nim_new / 100)
        nii_delta = nii_new - nii_current

        # EPS impact: NII delta → pretax (assume opex/fees unchanged) → net
        # income → divide by shares. Pretax delta == NII delta because we're
        # only modeling the rate-sensitive component.
        pretax_delta = nii_delta
        net_income_delta = pretax_delta * (1 - tax_rate)
        eps_delta = (net_income_delta / shares_outstanding) if shares_outstanding else None

        years_out.append({
            "year": year,
            "repriced_fraction": repriced_frac,
            "nim_new_pct": nim_new,
            "nim_delta_bps": nim_delta_pp * 100,
            "earning_asset_yield_new_pct": ea_yield_new,
            "cost_of_funds_new_pct": cost_ibl_new,
            "nii_new_usd": nii_new,
            "nii_delta_usd": nii_delta,
            "pretax_delta_usd": pretax_delta,
            "net_income_delta_usd": net_income_delta,
            "eps_delta": eps_delta,
        })

    return {
        "rate_change_bps": rate_change_bps,
        "current_nim_pct": current_nim,
        "current_nii_usd": nii_current,
        "deposit_beta_used": deposit_beta_int_bearing,
        "floating_loan_share_used": floating_loan_share if floating_loan_share is not None
                                      else _DEFAULT_FLOATING_LOAN_SHARE,
        "mix_shift_applied": apply_mix_shift and rate_pp > 0,
        "shares_outstanding": shares_outstanding,
        "tax_rate_used": tax_rate,
        "years": years_out,
    }


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


def run_rate_sensitivity_phased(
    fdic_latest: dict,
    fdic_hist: list[dict] | None = None,
    sec_data: dict | None = None,
    beta_mode: str = "historical",
    scenarios_bps: list[int] | None = None,
    custom_deposit_beta: float | None = None,
    floating_loan_share: float | None = None,
    apply_mix_shift: bool = True,
    horizon_years: int = 3,
) -> dict:
    """
    Run the enhanced phased rate sensitivity across scenarios.

    Returns the same structure as run_rate_sensitivity but each scenario
    now has a `years` list with per-year NIM/NII/EPS impacts.
    """
    if scenarios_bps is None:
        scenarios_bps = DEFAULT_SCENARIOS_BPS

    inputs = build_rate_sensitivity_inputs(fdic_latest, fdic_hist)
    beta_int, beta_ni, beta_mode_used = _resolve_deposit_beta(
        inputs, fdic_hist, beta_mode, custom_deposit_beta,
    )

    shares = None
    if sec_data:
        shares = sec_data.get("shares_outstanding")

    scenario_results = [
        apply_rate_scenario_phased(
            inputs,
            rate_change_bps=bps,
            deposit_beta_int_bearing=beta_int,
            deposit_beta_non_int=beta_ni,
            floating_loan_share=floating_loan_share,
            apply_mix_shift=apply_mix_shift,
            shares_outstanding=shares,
            horizon_years=horizon_years,
        )
        for bps in scenarios_bps
    ]

    return {
        "inputs": inputs,
        "beta_used": beta_int,
        "beta_mode": beta_mode_used,
        "repricing_pace": compute_repricing_pace(inputs, floating_loan_share),
        "scenarios": scenario_results,
        "horizon_years": horizon_years,
        "shares_outstanding": shares,
        "tax_rate_used": inputs.get("effective_tax_rate"),
    }


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
