"""
Deposit Dynamics analysis — institutional-grade deposit composition,
cost-of-deposits trends, and deposit beta calculations.

Deposit beta measures how much of a change in the Fed funds rate flows
through to deposit costs. A beta of 0.40 means 40% of rate changes
are passed to depositors — lower is better (stickier deposits).

Two beta calculations provided:
  - Cycle beta: cumulative since Fed started current direction
  - Rolling beta: trailing 4-quarter regression
"""

from __future__ import annotations
import pandas as pd


# Fed Funds Effective Rate (quarterly average, %) — public data from FRED FEDFUNDS series.
# Used as the benchmark for deposit beta calculations. Keys are quarter-end dates.
FED_FUNDS_QUARTERLY = {
    "2019-03-31": 2.40, "2019-06-30": 2.40, "2019-09-30": 2.13, "2019-12-31": 1.64,
    "2020-03-31": 1.25, "2020-06-30": 0.06, "2020-09-30": 0.09, "2020-12-31": 0.09,
    "2021-03-31": 0.08, "2021-06-30": 0.07, "2021-09-30": 0.09, "2021-12-31": 0.08,
    "2022-03-31": 0.20, "2022-06-30": 1.21, "2022-09-30": 2.56, "2022-12-31": 4.10,
    "2023-03-31": 4.65, "2023-06-30": 5.08, "2023-09-30": 5.33, "2023-12-31": 5.33,
    "2024-03-31": 5.33, "2024-06-30": 5.33, "2024-09-30": 5.07, "2024-12-31": 4.50,
    "2025-03-31": 4.33, "2025-06-30": 4.33, "2025-09-30": 4.12, "2025-12-31": 4.00,
    "2026-03-31": 3.85,
}


def _get_fed_funds(date_str: str) -> float | None:
    """Look up Fed funds rate for a given quarter-end date."""
    if not date_str:
        return None
    # Handle pandas Timestamp
    if hasattr(date_str, "strftime"):
        date_str = date_str.strftime("%Y-%m-%d")
    elif isinstance(date_str, str) and len(date_str) > 10:
        date_str = date_str[:10]
    return FED_FUNDS_QUARTERLY.get(date_str)


def _cost_of_funding(row: dict) -> float | None:
    """
    Compute annualized cost of interest-bearing liabilities %.

    Note: FDIC's INTEXPY covers ALL interest-bearing liabilities — deposits +
    borrowings + fed funds purchased + repo liabilities. For pure-deposit banks
    this approximates deposit cost well, but for banks with heavy wholesale
    funding (large money-center banks), INTEXPY understates the true pure
    deposit cost because low-cost repos dilute the blended average.

    The cycle beta we compute from this is therefore a "funding beta", not a
    "deposit beta" in the pure sense. For banks with predominantly deposit
    funding (>80% of liabilities), the two are nearly equivalent.
    """
    intexpy = row.get("INTEXPY")
    if intexpy is not None:
        return intexpy
    return None


def build_deposit_timeline(hist_records: list[dict]) -> pd.DataFrame:
    """
    Build a quarterly timeline of deposit metrics from FDIC history.

    Input: list of quarterly FDIC records (most recent first).
    Returns DataFrame with columns:
        date, total_dep, nonint_dep, int_bearing_dep, uninsured_dep,
        brokered_dep, nonint_dep_pct, uninsured_pct, brokered_pct,
        cost_of_deposits, fed_funds, dep_qoq_growth
    """
    if not hist_records:
        return pd.DataFrame()

    rows = []
    for r in hist_records:
        date = r.get("REPDTE")
        total = r.get("DEP")
        nonint = r.get("DEPNIDOM")
        intbear = r.get("DEPIDOM")
        uninsured = r.get("DEPUNINS")
        brokered = r.get("BRO")
        cod = _cost_of_funding(r)

        nonint_pct = (nonint / total * 100) if total and nonint else None
        uninsured_pct = (uninsured / total * 100) if total and uninsured else None
        brokered_pct = (brokered / total * 100) if total and brokered else None

        rows.append({
            "date": pd.to_datetime(date, errors="coerce") if date is not None else None,
            "total_dep": total,
            "nonint_dep": nonint,
            "int_bearing_dep": intbear,
            "uninsured_dep": uninsured,
            "brokered_dep": brokered,
            "nonint_dep_pct": nonint_pct,
            "uninsured_pct": uninsured_pct,
            "brokered_pct": brokered_pct,
            "cost_of_deposits": cod,
        })

    df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Attach Fed funds
    df["fed_funds"] = df["date"].apply(_get_fed_funds)

    # Coerce numerics — any column may contain None from sparse FDIC history.
    for col in ("total_dep", "cost_of_deposits", "fed_funds"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # QoQ deposit growth
    df["dep_qoq_growth"] = df["total_dep"].pct_change() * 100

    # QoQ change in cost of deposits
    df["cod_qoq_change"] = df["cost_of_deposits"].diff()

    # QoQ change in Fed funds (basis points)
    df["fed_funds_qoq_change"] = df["fed_funds"].diff()

    return df


def compute_cycle_beta(timeline_df: pd.DataFrame) -> dict:
    """
    Cycle deposit beta: cumulative Δ cost / cumulative Δ Fed funds
    since the Fed started the current rate direction (last inflection).

    Returns dict: {beta, start_date, end_date, cod_change, ff_change, cycle_direction}
    """
    if timeline_df.empty or len(timeline_df) < 2:
        return {}

    df = timeline_df.dropna(subset=["fed_funds", "cost_of_deposits"]).copy()
    if len(df) < 2:
        return {}

    # Find the most recent inflection point in Fed funds direction.
    # Look backward for the last sign change in quarterly changes.
    ff_changes = df["fed_funds"].diff()

    # Find direction of most recent non-zero change.
    # Threshold of 3bps catches moderate moves while ignoring noise.
    INFLECTION_BPS = 0.03
    last_direction = None
    for val in reversed(ff_changes.tolist()):
        if val is not None and abs(val) > INFLECTION_BPS:
            last_direction = "up" if val > 0 else "down"
            break

    if last_direction is None:
        return {}

    # Walk backward from the end, find where direction last flipped
    start_idx = 0
    for i in range(len(df) - 1, 0, -1):
        change = ff_changes.iloc[i]
        if change is None or pd.isna(change):
            continue
        direction = "up" if change > INFLECTION_BPS else ("down" if change < -INFLECTION_BPS else None)
        if direction is not None and direction != last_direction:
            start_idx = i
            break

    cycle_df = df.iloc[start_idx:]
    if len(cycle_df) < 2:
        return {}

    ff_change = cycle_df["fed_funds"].iloc[-1] - cycle_df["fed_funds"].iloc[0]
    cod_change = cycle_df["cost_of_deposits"].iloc[-1] - cycle_df["cost_of_deposits"].iloc[0]

    # Lowered from 25bps → 15bps so we don't discard moderate-magnitude cycles
    # (e.g., shallow 2024 cut cycle quarters).
    if abs(ff_change) < 0.15:
        return {}

    beta = cod_change / ff_change if ff_change != 0 else None

    return {
        "beta": beta,
        "start_date": cycle_df["date"].iloc[0],
        "end_date": cycle_df["date"].iloc[-1],
        "ff_change": ff_change,
        "cod_change": cod_change,
        "cycle_direction": last_direction,
        "n_quarters": len(cycle_df),
    }


def compute_rolling_beta(timeline_df: pd.DataFrame, window: int = 4) -> dict:
    """
    Rolling deposit beta: linear regression slope of Δ cost vs Δ Fed funds
    over the last `window` quarters.

    Returns dict: {beta, r_squared, n}
    """
    if timeline_df.empty or len(timeline_df) < window + 1:
        return {}

    df = timeline_df.tail(window + 1).copy()
    df = df.dropna(subset=["cod_qoq_change", "fed_funds_qoq_change"])

    if len(df) < 3:
        return {}

    x = df["fed_funds_qoq_change"].values
    y = df["cod_qoq_change"].values

    # Simple linear regression
    n = len(x)
    x_mean = x.mean()
    y_mean = y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom == 0:
        return {}

    beta = ((x - x_mean) * (y - y_mean)).sum() / denom

    # R-squared
    y_pred = y_mean + beta * (x - x_mean)
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - y_mean) ** 2).sum()
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else None

    return {
        "beta": beta,
        "r_squared": r_squared,
        "n": n,
        "window": window,
    }


def detect_alerts(timeline_df: pd.DataFrame) -> list[dict]:
    """
    Run the 4 standard deposit alerts on a bank's timeline.

    Returns list of {severity, code, message, value}.
    """
    alerts = []
    if timeline_df.empty:
        return alerts

    latest = timeline_df.iloc[-1]

    # 1. QoQ deposit outflows > 2%
    qoq = latest.get("dep_qoq_growth")
    if qoq is not None and qoq < -2.0:
        alerts.append({
            "severity": "high" if qoq < -5.0 else "medium",
            "code": "deposit_outflow",
            "message": f"Deposits declined {qoq:+.1f}% QoQ",
            "value": qoq,
        })

    # 2. Rising cost of deposits (accelerating — last 2Q change > trailing 4Q avg)
    if len(timeline_df) >= 5:
        last_2q_change = timeline_df["cod_qoq_change"].tail(2).mean()
        prior_4q_change = timeline_df["cod_qoq_change"].iloc[-6:-2].mean() if len(timeline_df) >= 6 else None
        if last_2q_change is not None and prior_4q_change is not None:
            if last_2q_change > 0.10 and last_2q_change > prior_4q_change * 1.5:
                alerts.append({
                    "severity": "medium",
                    "code": "cost_accelerating",
                    "message": f"Cost of deposits accelerating: +{last_2q_change*100:.0f}bps last 2Q avg vs +{prior_4q_change*100:.0f}bps prior",
                    "value": last_2q_change,
                })

    # 3. Declining non-interest-bearing deposit %
    if len(timeline_df) >= 5:
        nii_now = latest.get("nonint_dep_pct")
        nii_yr_ago = timeline_df["nonint_dep_pct"].iloc[-5] if len(timeline_df) >= 5 else None
        if nii_now is not None and nii_yr_ago is not None:
            change = nii_now - nii_yr_ago
            if change < -3.0:
                alerts.append({
                    "severity": "medium",
                    "code": "nii_declining",
                    "message": f"Non-int-bearing deposits fell {change:+.1f}pp YoY to {nii_now:.1f}% — raises deposit beta risk",
                    "value": change,
                })

    # 4. Uninsured deposit concentration > 40%
    unins_pct = latest.get("uninsured_pct")
    if unins_pct is not None and unins_pct > 40.0:
        alerts.append({
            "severity": "high" if unins_pct > 55.0 else "medium",
            "code": "uninsured_high",
            "message": f"Uninsured deposits at {unins_pct:.0f}% of total — elevated run risk",
            "value": unins_pct,
        })

    return alerts


def summarize_bank_deposits(hist_records: list[dict]) -> dict:
    """
    Build a full deposit-dynamics summary for one bank.

    Returns dict with timeline, cycle_beta, rolling_beta, alerts, latest snapshot.
    """
    timeline = build_deposit_timeline(hist_records)
    if timeline.empty:
        return {
            "timeline": timeline,
            "cycle_beta": {},
            "rolling_beta": {},
            "alerts": [],
            "latest": {},
        }

    return {
        "timeline": timeline,
        "cycle_beta": compute_cycle_beta(timeline),
        "rolling_beta": compute_rolling_beta(timeline),
        "alerts": detect_alerts(timeline),
        "latest": timeline.iloc[-1].to_dict(),
    }
