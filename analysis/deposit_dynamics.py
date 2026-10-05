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


# Fed funds effective rate, quarterly average (%), from FRED FEDFUNDS monthly
# means. The hand-typed table this replaced was wrong for 13 of its 29
# quarters (e.g. 2026Q1 3.85 vs 3.64; 2022-23 entries were each quarter's LAST
# month, 2022Q2 1.21 vs 0.77) and drove the cycle-start detection and the
# "Fed funds fell N pp" caption (REVIEW 2026-10-05 P0-2).
_FED_FUNDS_LIVE: dict[str, float] = {}   # quarter-end -> average (memoized)


def _get_fed_funds(date_str: str) -> float | None:
    """Fed funds rate (quarterly average of FRED's monthly FEDFUNDS) for a
    quarter-end date, or None when FRED is unavailable (n/a, never a guess)."""
    if not date_str:
        return None
    # Handle pandas Timestamp
    if hasattr(date_str, "strftime"):
        date_str = date_str.strftime("%Y-%m-%d")
    elif isinstance(date_str, str) and len(date_str) > 10:
        date_str = date_str[:10]
    if date_str in _FED_FUNDS_LIVE:
        return _FED_FUNDS_LIVE[date_str]
    try:
        from data.fred_client import fetch_series
        # The FULL series in one read (deep-history ranges reach 1992):
        # every quarter's average is memoized at once, so an 80-quarter
        # timeline costs one series read, not eighty.
        df = fetch_series("FEDFUNDS", years=40)
        if df is not None and not df.empty:
            d = df.dropna(subset=["value"]).copy()
            d["_q"] = pd.to_datetime(d["date"], errors="coerce").dt.to_period("Q")
            for per, vals in d.dropna(subset=["_q"]).groupby("_q")["value"]:
                key = per.end_time.strftime("%Y-%m-%d")
                _FED_FUNDS_LIVE.setdefault(key, round(float(vals.mean()), 2))
            if date_str in _FED_FUNDS_LIVE:
                return _FED_FUNDS_LIVE[date_str]
    except Exception as e:
        print(f"[deposit_dynamics] FRED fed-funds lookup failed for {date_str}: "
              f"{type(e).__name__}: {e}")
    return None


def _interest_bearing_deposits(r: dict):
    """Domestic + foreign-office interest-bearing deposits ($K), or None.
    A record without DEPIFOR (fetched since 2026-10-05) counts foreign IB as 0
    only when the bank has no foreign-office deposits (DEP ≈ DEPDOM) — JPM
    carries $550B there, so guessing 0 would overstate its cost ~1.3x."""
    def _v(k):   # FDIC nulls arrive as None or NaN (domestic-only DEPIFOR)
        x = r.get(k)
        return None if x is None or pd.isna(x) else x
    dom = _v("DEPIDOM")
    if dom is None:
        return None
    foreign = _v("DEPIFOR")
    if foreign is None:
        dep, depdom = _v("DEP"), _v("DEPDOM")
        if dep is None or depdom is None or dep - depdom > 0.005 * dep:
            return None
        foreign = 0
    return dom + foreign


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

        # `is not None` numerators (audit P3, owner call): a genuine $0 (e.g.
        # zero brokered deposits) is data and renders 0%, never n/a. A falsy
        # total still yields None (zero denominator).
        nonint_pct = (nonint / total * 100) if (total and nonint is not None) else None
        # Same basis as analysis/valuation's uninsured_pct (THE definition):
        # uninsured ÷ the insurance base (insured + uninsured), not ÷ DEP.
        insured = r.get("DEPINS")
        uninsured_pct = (uninsured / (insured + uninsured) * 100) if (
            uninsured is not None and insured is not None
            and insured + uninsured > 0) else None
        brokered_pct = (brokered / total * 100) if (total and brokered is not None) else None

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
            "edep_ytd": r.get("EDEP"),
            "ib_dep": _interest_bearing_deposits(r),
        })

    df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Cost of INTEREST-BEARING deposits, SINGLE quarter, annualized: interest
    # on deposits (EDEP is calendar-YTD -> de-cumulated; Q1 as filed) x 4 /
    # average interest-bearing deposits (this and the prior quarter-end) — the
    # basis analysis/rate_sensitivity reads the cycle beta on (its int-bearing
    # deposit beta). It replaced FDIC's INTEXPY —
    # a YTD-annualized ratio over EARNING ASSETS — under which the cycle beta
    # subtracted a 9-month from a 6-month figure and reported deposit costs
    # RISING into a cut cycle (ONB +0.39pp vs +0.04 quarterly; REVIEW
    # 2026-10-05 P0-1). Both inputs are $K sums, so charter groups work too
    # (INTEXPY is average-based and was dropped for them). A quarter whose
    # prior quarter-end is missing is None, never a mixed span.
    by_date = {d: (e, t) for d, e, t in zip(df["date"], df["edep_ytd"], df["ib_dep"])}
    cod = []
    for d, e, t in zip(df["date"], df["edep_ytd"], df["ib_dep"]):
        prev = by_date.get(d - pd.offsets.QuarterEnd(1))
        q_int = None
        if e is not None and pd.notna(e):
            if d.quarter == 1:
                q_int = e
            elif prev and prev[0] is not None and pd.notna(prev[0]):
                q_int = e - prev[0]
        avg_dep = ((t + prev[1]) / 2
                   if prev and t is not None and prev[1] is not None
                   and pd.notna(t) and pd.notna(prev[1]) else None)
        cod.append(q_int * 4 / avg_dep * 100
                   if q_int is not None and avg_dep and avg_dep > 0 else None)
    df["cost_of_deposits"] = cod

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
