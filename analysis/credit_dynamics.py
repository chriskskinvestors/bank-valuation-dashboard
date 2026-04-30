"""
Credit Quality Dynamics — institutional-grade credit analysis.

Computes:
  - NPL/NCO trends by segment (CRE, resi, multifam, C&I, consumer)
  - Past-due migration (30-89, 90+)
  - Reserve coverage ratios with absolute + peer benchmarks
  - Segment hotspot detection (any segment NPL > 3x bank avg)
  - 4 credit alerts: NCO accelerating, PD migration, thin reserves, hotspot
"""

from __future__ import annotations
import pandas as pd


# FDIC field mapping for credit timeline
# All NPL/NCO fields are ratios (%) — FDIC pre-computes them
_CREDIT_FIELDS = {
    # Ratios (%)
    "npl_ratio": "NCLNLSR",       # Total NPL to loans
    "npl_cre": "NCRER",            # NPL CRE %
    "npl_resi": "NCRECONR",        # NPL Residential %
    "npl_multifam": "NCREMULR",    # NPL Multifam %
    "npl_nres_re": "NCRENRER",     # NPL NonRes RE %
    "npl_ci": "IDNCCIR",           # NPL C&I %
    "npl_consumer": "IDNCCONR",    # NPL Consumer %
    "nco_ratio": "NTLNLSR",        # Total NCO rate
    "nco_re": "NTRER",             # NCO RE %
    "nco_ci": "NTCOMRER",          # NCO C&I %
    "reserve_to_loans": "LNATRESR",  # Reserves / loans %
    "reserve_coverage": "IDERNCVR",   # Reserves / NPL (coverage ratio)
    "allowance_loans": "ELNANTR",     # ALLL / loans
    # Dollar amounts (in thousands)
    "past_due_30_89": "P3ASSET",
    "past_due_90": "P9ASSET",
    "total_loans": "LNLSNET",
}


def build_credit_timeline(hist_records: list[dict]) -> pd.DataFrame:
    """
    Build a quarterly credit-metric timeline from FDIC history.

    Returns DataFrame with date + all credit fields + computed QoQ and YoY changes.
    """
    if not hist_records:
        return pd.DataFrame()

    rows = []
    for r in hist_records:
        date = r.get("REPDTE")
        if date is None:
            continue

        row = {"date": pd.to_datetime(date, errors="coerce")}
        for key, fdic_field in _CREDIT_FIELDS.items():
            row[key] = r.get(fdic_field)

        # Past-due ratios as % of total loans
        total_loans = row.get("total_loans") or 1
        if row.get("past_due_30_89") is not None:
            row["past_due_30_89_pct"] = row["past_due_30_89"] / total_loans * 100
        if row.get("past_due_90") is not None:
            row["past_due_90_pct"] = row["past_due_90"] / total_loans * 100

        # Compute reserve coverage from constituent ratios — more reliable than
        # FDIC's IDERNCVR field which has scaling issues at holding-company level.
        # coverage % = (reserve / loans) / (NPL / loans) * 100
        rtl = row.get("reserve_to_loans")
        npl = row.get("npl_ratio")
        if rtl is not None and npl is not None and npl > 0:
            row["reserve_coverage"] = rtl / npl * 100

        rows.append(row)

    df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # QoQ changes for key alerts
    for col in ["nco_ratio", "npl_ratio", "past_due_30_89_pct", "past_due_90_pct", "reserve_coverage"]:
        if col in df.columns:
            df[f"{col}_qoq"] = df[col].diff()

    return df


def detect_segment_hotspots(timeline_df: pd.DataFrame, threshold_multiplier: float = 3.0) -> list[dict]:
    """
    Identify loan segments whose NPL rate is significantly higher than the bank's total NPL.

    A segment is a "hotspot" if its NPL % >= threshold_multiplier * total NPL %.
    Also flags segments with NPL > 2% regardless of multiple.
    """
    if timeline_df.empty:
        return []

    latest = timeline_df.iloc[-1]
    total_npl = latest.get("npl_ratio")
    if total_npl is None or total_npl <= 0:
        return []

    segments = {
        "CRE": latest.get("npl_cre"),
        "Residential": latest.get("npl_resi"),
        "Multifamily": latest.get("npl_multifam"),
        "Non-Res RE": latest.get("npl_nres_re"),
        "C&I": latest.get("npl_ci"),
        "Consumer": latest.get("npl_consumer"),
    }

    hotspots = []
    for seg_name, seg_npl in segments.items():
        if seg_npl is None or seg_npl <= 0:
            continue
        ratio = seg_npl / total_npl if total_npl > 0 else 0
        if ratio >= threshold_multiplier or seg_npl > 2.0:
            hotspots.append({
                "segment": seg_name,
                "npl_pct": seg_npl,
                "vs_total_multiple": ratio,
                "is_hotspot": True,
            })

    return sorted(hotspots, key=lambda x: x["npl_pct"], reverse=True)


def detect_credit_alerts(
    timeline_df: pd.DataFrame,
    peer_reserve_coverage_median: float | None = None,
) -> list[dict]:
    """
    Run 4 credit alerts on a bank's timeline.

    Returns list of {severity, code, message, value}.
    """
    alerts = []
    if timeline_df.empty or len(timeline_df) < 2:
        return alerts

    latest = timeline_df.iloc[-1]

    # 1. NCO accelerating (3+ consecutive quarters rising)
    if "nco_ratio" in timeline_df.columns and len(timeline_df) >= 4:
        nco_series = timeline_df["nco_ratio"].dropna()
        if len(nco_series) >= 4:
            last_4 = nco_series.tail(4).values
            # Check if each value is higher than the previous for last 3 transitions
            consecutive_rising = all(last_4[i+1] > last_4[i] for i in range(len(last_4)-1))
            total_increase = last_4[-1] - last_4[0]
            if consecutive_rising and total_increase > 0.05:
                alerts.append({
                    "severity": "high" if total_increase > 0.25 else "medium",
                    "code": "nco_accelerating",
                    "message": f"NCO accelerating: {last_4[0]:.2f}% → {last_4[-1]:.2f}% over 3 consecutive quarters (+{total_increase*100:.0f}bps)",
                    "value": total_increase,
                })

    # 2. Past due 30-89 migration (rising QoQ meaningfully)
    if "past_due_30_89_pct_qoq" in timeline_df.columns:
        pd_qoq = latest.get("past_due_30_89_pct_qoq")
        pd_pct = latest.get("past_due_30_89_pct")
        if pd_qoq is not None and pd_qoq > 0.10 and pd_pct is not None:
            alerts.append({
                "severity": "medium",
                "code": "pd_migration",
                "message": f"Past due 30-89 rose +{pd_qoq:.2f}pp QoQ to {pd_pct:.2f}% of loans — early warning of credit deterioration",
                "value": pd_qoq,
            })

    # 3. Reserve coverage thinning
    reserve_cov = latest.get("reserve_coverage")
    if reserve_cov is not None:
        if reserve_cov < 100:
            alerts.append({
                "severity": "high",
                "code": "under_reserved",
                "message": f"Reserves at {reserve_cov:.0f}% of NPLs — below 100% minimum; under-reserved for current NPLs",
                "value": reserve_cov,
            })
        elif peer_reserve_coverage_median is not None and reserve_cov < peer_reserve_coverage_median * 0.75:
            alerts.append({
                "severity": "medium",
                "code": "thin_reserves_vs_peers",
                "message": f"Reserves/NPL at {reserve_cov:.0f}% vs peer median {peer_reserve_coverage_median:.0f}% — thin vs peers",
                "value": reserve_cov,
            })

    # 4. Segment hotspot
    hotspots = detect_segment_hotspots(timeline_df)
    for hs in hotspots[:2]:  # top 2 hotspots
        if hs["vs_total_multiple"] >= 3.0:
            alerts.append({
                "severity": "high",
                "code": "segment_hotspot",
                "message": f"{hs['segment']} NPL at {hs['npl_pct']:.2f}% — {hs['vs_total_multiple']:.1f}x bank total NPL",
                "value": hs["npl_pct"],
            })
        elif hs["npl_pct"] > 3.0:
            alerts.append({
                "severity": "medium",
                "code": "segment_elevated",
                "message": f"{hs['segment']} NPL elevated at {hs['npl_pct']:.2f}%",
                "value": hs["npl_pct"],
            })

    return alerts


def compute_peer_reserve_median(all_bank_hist: dict[str, list[dict]]) -> float | None:
    """Compute peer median reserve coverage from a dict of ticker → history."""
    coverages = []
    for ticker, hist in all_bank_hist.items():
        if not hist:
            continue
        latest = hist[0] if hist else {}
        cov = latest.get("IDERNCVR")
        if cov is not None and cov > 0:
            coverages.append(cov)
    if not coverages:
        return None
    s = pd.Series(coverages)
    return float(s.median())


def summarize_bank_credit(
    hist_records: list[dict],
    peer_reserve_median: float | None = None,
) -> dict:
    """Full credit-dynamics summary for one bank."""
    timeline = build_credit_timeline(hist_records)
    if timeline.empty:
        return {
            "timeline": timeline,
            "alerts": [],
            "hotspots": [],
            "latest": {},
            "peer_reserve_median": peer_reserve_median,
        }

    return {
        "timeline": timeline,
        "alerts": detect_credit_alerts(timeline, peer_reserve_median),
        "hotspots": detect_segment_hotspots(timeline),
        "latest": timeline.iloc[-1].to_dict(),
        "peer_reserve_median": peer_reserve_median,
    }


def compute_credit_screening_metrics(hist_records: list[dict]) -> dict:
    """
    Compute summary credit metrics for the Credit Dynamics screening table.

    Returns: nco_4q_trend_bps, npl_trend_bps, pd_migration_bps, credit_alerts_count
    """
    if not hist_records:
        return {
            "nco_4q_trend_bps": None,
            "npl_trend_bps": None,
            "pd_migration_bps": None,
            "credit_alerts_count": None,
            "reserve_coverage_pct": None,
            "worst_segment_npl": None,
        }

    timeline = build_credit_timeline(hist_records)
    if timeline.empty:
        return {
            "nco_4q_trend_bps": None, "npl_trend_bps": None,
            "pd_migration_bps": None, "credit_alerts_count": 0,
            "reserve_coverage_pct": None, "worst_segment_npl": None,
        }

    latest = timeline.iloc[-1]

    # 4Q trend in NCO (current vs 4Q ago, in bps)
    nco_trend_bps = None
    if len(timeline) >= 5:
        nco_now = timeline["nco_ratio"].iloc[-1]
        nco_4q_ago = timeline["nco_ratio"].iloc[-5]
        if nco_now is not None and nco_4q_ago is not None:
            nco_trend_bps = (nco_now - nco_4q_ago) * 100

    # NPL QoQ change (bps)
    npl_trend_bps = None
    if len(timeline) >= 2:
        npl_qoq = latest.get("npl_ratio_qoq")
        if npl_qoq is not None:
            npl_trend_bps = npl_qoq * 100

    # PD 30-89 QoQ change (bps)
    pd_migration_bps = None
    pd_qoq = latest.get("past_due_30_89_pct_qoq")
    if pd_qoq is not None:
        pd_migration_bps = pd_qoq * 100

    # Worst segment NPL
    hotspots = detect_segment_hotspots(timeline)
    worst = hotspots[0]["npl_pct"] if hotspots else None

    alerts = detect_credit_alerts(timeline)

    return {
        "nco_4q_trend_bps": nco_trend_bps,
        "npl_trend_bps": npl_trend_bps,
        "pd_migration_bps": pd_migration_bps,
        "credit_alerts_count": len(alerts),
        "reserve_coverage_pct": latest.get("reserve_coverage"),
        "worst_segment_npl": worst,
    }
