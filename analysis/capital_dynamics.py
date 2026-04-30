"""
Capital Dynamics — institutional-grade capital analysis.

Computes:
  - CET1 trend (with multi-quarter decline detection)
  - TBV per share growth (annualized)
  - Capital retention rate (implied from equity changes)
  - Organic capital need (to fund loan growth at current CET1)
  - Buyback capacity = retained earnings - organic capital need
  - Payout ratio (implied from retained earnings vs net income)
  - Peer-relative capital adequacy
"""

from __future__ import annotations
import pandas as pd


# CET1 ratio regulatory minimum + conservation buffer = 4.5% + 2.5% = 7.0% (basic)
# Typical stressed buffer: ~1-2% additional → effective "comfort zone" is ~8-9%
CET1_REG_MIN = 7.0
CET1_BUFFER_FLOOR = 8.0  # below this = restricted buyback/dividend capacity


def build_capital_timeline(hist_records: list[dict], shares_outstanding: float | None = None) -> pd.DataFrame:
    """
    Build quarterly capital timeline from FDIC history.

    Columns: date, cet1_pct, total_cap_pct, leverage_pct, equity, goodwill,
             tbv, tbv_per_share, net_income, retained_earnings, payout_ratio,
             equity_qoq, loan_growth_qoq_pct, loan_growth_qoq_usd, cet1_qoq
    """
    if not hist_records:
        return pd.DataFrame()

    rows = []
    for r in hist_records:
        date = r.get("REPDTE")
        if date is None:
            continue

        equity = r.get("EQTOT") or 0      # thousands
        goodwill = r.get("INTANGW") or 0  # thousands (goodwill only)
        intangibles = r.get("INTAN") or 0  # thousands (total intangibles incl goodwill)
        net_income = r.get("NETINC") or 0  # thousands (YTD)
        total_loans = r.get("LNLSNET") or 0
        cet1 = r.get("IDT1CER")
        total_cap = r.get("RBCRWAJ")
        leverage = r.get("RBCT1JR")

        tbv = equity - max(goodwill, intangibles)  # use max to be conservative

        rows.append({
            "date": pd.to_datetime(date, errors="coerce"),
            "cet1_pct": cet1,
            "total_cap_pct": total_cap,
            "leverage_pct": leverage,
            "equity_k": equity,
            "goodwill_k": goodwill,
            "intangibles_k": intangibles,
            "tbv_k": tbv,
            "net_income_k_ytd": net_income,
            "total_loans_k": total_loans,
        })

    df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return df

    # Derive quarterly (non-YTD) net income from YTD NI
    # YTD resets every year in Q1; Q2/Q3/Q4 are cumulative
    df["quarter"] = df["date"].dt.quarter
    df["year"] = df["date"].dt.year

    # For Q1: quarterly = YTD; for Q2-Q4: quarterly = YTD(current) - YTD(prior quarter same year)
    def _compute_quarterly_ni(group):
        group = group.sort_values("date").reset_index(drop=True)
        qtrly = [None] * len(group)
        for i, row in enumerate(group.itertuples()):
            if row.quarter == 1:
                qtrly[i] = row.net_income_k_ytd
            else:
                # Walk backward to find the PRIOR QUARTER in the same year.
                # Don't just take i-1 — data may be sparse.
                prior_ytd = None
                for j in range(i - 1, -1, -1):
                    p = group.iloc[j]
                    if p["year"] != row.year:
                        break
                    if p["quarter"] == row.quarter - 1:
                        prior_ytd = p["net_income_k_ytd"]
                        break
                if prior_ytd is not None:
                    qtrly[i] = row.net_income_k_ytd - prior_ytd
                else:
                    qtrly[i] = None
        group = group.copy()
        group["net_income_k_qtr"] = qtrly
        return group

    df = df.groupby("year", group_keys=False).apply(_compute_quarterly_ni).reset_index(drop=True)

    # Equity QoQ change
    df["equity_qoq_k"] = df["equity_k"].diff()
    df["tbv_qoq_k"] = df["tbv_k"].diff()

    # Implied dividends + buybacks = NI - ΔEquity (assumes no AOCI/other capital actions)
    # This is a rough approximation; captures "capital returned to shareholders"
    df["capital_returned_k"] = df["net_income_k_qtr"] - df["equity_qoq_k"]
    df["retention_ratio"] = 1 - (df["capital_returned_k"] / df["net_income_k_qtr"])

    # Loan growth
    df["loan_growth_qoq_k"] = df["total_loans_k"].diff()
    df["loan_growth_qoq_pct"] = df["total_loans_k"].pct_change() * 100

    # CET1 QoQ change (pp)
    df["cet1_qoq_pp"] = df["cet1_pct"].diff()

    # TBV per share (only if shares provided)
    if shares_outstanding and shares_outstanding > 0:
        # TBV stored in thousands → /1000 → millions; divide by shares in millions
        df["tbv_per_share"] = df["tbv_k"] * 1000 / shares_outstanding
        df["tbv_per_share_qoq"] = df["tbv_per_share"].diff()

    return df


def compute_organic_capital_need(
    loan_growth_qoq_k: float | None,
    cet1_target: float = 10.0,
) -> float | None:
    """
    Capital required to fund loan growth at a target CET1 ratio.

    Assumes ~100% risk-weight on loan growth (conservative). Actual RWA
    depends on loan type but 100% is a reasonable approximation.
    """
    if loan_growth_qoq_k is None or loan_growth_qoq_k <= 0:
        return 0.0
    # capital needed = new loans * cet1_target (both in same units)
    return loan_growth_qoq_k * (cet1_target / 100)


def compute_buyback_capacity(
    quarterly_ni_k: float | None,
    capital_returned_k: float | None,
    organic_need_k: float | None,
) -> dict:
    """
    Buyback capacity = NI - (actual capital returned) - organic need.

    Returns:
      retained_after_div: NI minus dividends (approximation from capital returned)
      organic_need: capital locked in for loan growth
      free_capital: remaining for buybacks
    """
    if quarterly_ni_k is None:
        return {"retained": None, "organic_need": None, "free_capital": None}

    retained = quarterly_ni_k - (capital_returned_k or 0)
    organic = organic_need_k or 0
    free = quarterly_ni_k - (capital_returned_k or 0) - organic

    return {
        "retained": retained,
        "organic_need": organic,
        "free_capital": free,
    }


def compute_tbv_cagr(timeline_df: pd.DataFrame, periods: int = 4) -> float | None:
    """Trailing N-quarter TBV/share CAGR (annualized)."""
    if "tbv_per_share" not in timeline_df.columns or len(timeline_df) < periods + 1:
        return None
    df = timeline_df.dropna(subset=["tbv_per_share"]).copy()
    if len(df) < periods + 1:
        return None
    start_tbv = df["tbv_per_share"].iloc[-periods - 1]
    end_tbv = df["tbv_per_share"].iloc[-1]
    if start_tbv is None or start_tbv <= 0:
        return None
    qtrs = periods
    years = qtrs / 4
    try:
        cagr = ((end_tbv / start_tbv) ** (1 / years) - 1) * 100
        return cagr
    except (ValueError, ZeroDivisionError):
        return None


def detect_capital_alerts(
    timeline_df: pd.DataFrame,
    peer_cet1_median: float | None = None,
) -> list[dict]:
    """4+ capital adequacy alerts."""
    alerts = []
    if timeline_df.empty:
        return alerts

    latest = timeline_df.iloc[-1]

    # 1. Low CET1 — absolute
    cet1 = latest.get("cet1_pct")
    if cet1 is not None:
        if cet1 < CET1_REG_MIN:
            alerts.append({
                "severity": "high",
                "code": "cet1_critical",
                "message": f"CET1 at {cet1:.2f}% — below regulatory minimum ({CET1_REG_MIN}%)",
                "value": cet1,
            })
        elif cet1 < CET1_BUFFER_FLOOR:
            alerts.append({
                "severity": "medium",
                "code": "cet1_low",
                "message": f"CET1 at {cet1:.2f}% — in buffer zone (<{CET1_BUFFER_FLOOR}%); buyback/dividend capacity restricted",
                "value": cet1,
            })

    # 2. CET1 declining 4+ consecutive quarters
    if "cet1_pct" in timeline_df.columns and len(timeline_df) >= 5:
        recent = timeline_df["cet1_pct"].tail(5).dropna().values
        if len(recent) == 5:
            declining = all(recent[i+1] < recent[i] for i in range(4))
            total_decline = recent[0] - recent[-1]
            if declining and total_decline > 0.25:
                alerts.append({
                    "severity": "high",
                    "code": "cet1_eroding",
                    "message": f"CET1 declined 4 consecutive quarters: {recent[0]:.2f}% → {recent[-1]:.2f}% (−{total_decline:.2f}pp)",
                    "value": -total_decline,
                })

    # 3. TBV per share declining
    if "tbv_per_share" in timeline_df.columns and len(timeline_df) >= 5:
        tbv_series = timeline_df["tbv_per_share"].dropna()
        if len(tbv_series) >= 5:
            tbv_now = tbv_series.iloc[-1]
            tbv_yr_ago = tbv_series.iloc[-5]
            if tbv_now < tbv_yr_ago:
                change = (tbv_now - tbv_yr_ago) / tbv_yr_ago * 100
                alerts.append({
                    "severity": "medium",
                    "code": "tbv_eroding",
                    "message": f"TBV/share declined {change:+.1f}% YoY — capital returns exceeding earnings power",
                    "value": change,
                })

    # 4. High payout ratio (>80%)
    if "retention_ratio" in timeline_df.columns and len(timeline_df) >= 4:
        recent_retention = timeline_df["retention_ratio"].tail(4).dropna()
        if len(recent_retention) >= 2:
            avg_retention = recent_retention.mean()
            avg_payout = (1 - avg_retention) * 100
            if avg_payout > 80:
                alerts.append({
                    "severity": "medium",
                    "code": "high_payout",
                    "message": f"Capital-return ratio at {avg_payout:.0f}% of net income (4Q avg) — may limit balance sheet growth",
                    "value": avg_payout,
                })

    # 5. Below peer median CET1
    if cet1 is not None and peer_cet1_median is not None:
        if cet1 < peer_cet1_median * 0.85:
            gap = peer_cet1_median - cet1
            alerts.append({
                "severity": "medium",
                "code": "cet1_below_peers",
                "message": f"CET1 at {cet1:.2f}% vs peer median {peer_cet1_median:.2f}% (−{gap:.2f}pp)",
                "value": -gap,
            })

    return alerts


def compute_peer_cet1_median(all_bank_hist: dict[str, list[dict]]) -> float | None:
    """Compute peer median CET1 ratio from dict of ticker→history."""
    cet1s = []
    for ticker, hist in all_bank_hist.items():
        if not hist:
            continue
        latest = hist[0]
        c = latest.get("IDT1CER")
        if c is not None and c > 0:
            cet1s.append(c)
    if not cet1s:
        return None
    return float(pd.Series(cet1s).median())


def summarize_bank_capital(
    hist_records: list[dict],
    shares_outstanding: float | None = None,
    peer_cet1_median: float | None = None,
) -> dict:
    """Full capital-dynamics summary for one bank."""
    timeline = build_capital_timeline(hist_records, shares_outstanding)
    if timeline.empty:
        return {
            "timeline": timeline, "alerts": [], "latest": {},
            "tbv_cagr_1y": None, "tbv_cagr_2y": None,
            "buyback_capacity": {"retained": None, "organic_need": None, "free_capital": None},
            "peer_cet1_median": peer_cet1_median,
        }

    latest = timeline.iloc[-1].to_dict()

    # Buyback capacity for latest quarter
    organic_need = compute_organic_capital_need(latest.get("loan_growth_qoq_k"))
    bb_cap = compute_buyback_capacity(
        latest.get("net_income_k_qtr"),
        latest.get("capital_returned_k"),
        organic_need,
    )

    return {
        "timeline": timeline,
        "alerts": detect_capital_alerts(timeline, peer_cet1_median),
        "latest": latest,
        "tbv_cagr_1y": compute_tbv_cagr(timeline, 4),
        "tbv_cagr_2y": compute_tbv_cagr(timeline, 8),
        "buyback_capacity": bb_cap,
        "peer_cet1_median": peer_cet1_median,
    }


def compute_capital_screening_metrics(
    hist_records: list[dict],
    shares_outstanding: float | None = None,
) -> dict:
    """Summary metrics for the Capital Dynamics screening table."""
    if not hist_records:
        return {
            "cet1_current": None, "cet1_qoq_pp": None,
            "tbv_cagr_1y": None, "payout_ratio_4q": None,
            "buyback_capacity_k": None, "capital_alerts_count": None,
        }

    timeline = build_capital_timeline(hist_records, shares_outstanding)
    if timeline.empty:
        return {
            "cet1_current": None, "cet1_qoq_pp": None,
            "tbv_cagr_1y": None, "payout_ratio_4q": None,
            "buyback_capacity_k": None, "capital_alerts_count": 0,
        }

    latest = timeline.iloc[-1]
    alerts = detect_capital_alerts(timeline)

    # Payout ratio 4Q avg
    retention_4q = timeline["retention_ratio"].tail(4).dropna()
    payout_ratio = None
    if len(retention_4q) > 0:
        payout_ratio = (1 - retention_4q.mean()) * 100
        # Clip to realistic bounds
        if payout_ratio < 0:
            payout_ratio = 0
        elif payout_ratio > 200:
            payout_ratio = None  # data issue

    # Buyback capacity (latest qtr)
    organic_need = compute_organic_capital_need(latest.get("loan_growth_qoq_k"))
    bb = compute_buyback_capacity(
        latest.get("net_income_k_qtr"),
        latest.get("capital_returned_k"),
        organic_need,
    )

    # Convert free_capital from thousands → raw dollars for display
    free_capital_k = bb.get("free_capital")
    free_capital_usd = free_capital_k * 1000 if free_capital_k is not None else None

    return {
        "cet1_current": latest.get("cet1_pct"),
        "cet1_qoq_pp": latest.get("cet1_qoq_pp"),
        "tbv_cagr_1y": compute_tbv_cagr(timeline, 4),
        "payout_ratio_4q": payout_ratio,
        "buyback_capacity_usd": free_capital_usd,
        "capital_alerts_count": len(alerts),
    }
