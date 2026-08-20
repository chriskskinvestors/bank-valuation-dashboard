"""
HMDA Mortgages sub-tab (SNL-BUILD-PLAN §11): the bank's residential-mortgage
origination activity from the public CFPB HMDA data (data/hmda_client) —
originations count + $ volume by year, and the latest year's state breakdown.

Public-HMDA caveats surfaced in the caption: loan amounts are disclosed as
$10k-bucket MIDPOINTS (counts exact, volumes approximate by design), and only
originated loans (action taken = 1) are counted. A bank that has never filed
HMDA (many commercial banks genuinely don't) renders an honest n/a.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from utils.formatting import fmt_dollars


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _lei_for(ticker: str) -> str | None:
    from data.bank_mapping import get_fdic_cert
    from data.fdic_client import get_rssd_for_cert
    from data.hmda_client import resolve_lei
    cert = get_fdic_cert(ticker)
    if not cert:
        return None
    rssd = get_rssd_for_cert(cert)
    if not rssd:
        return None
    return resolve_lei(int(rssd))


def render_hmda_mortgages(ticker):
    lei = _lei_for(ticker)
    if not lei:
        st.caption(
            f"{ticker} has no HMDA lender record — n/a. (HMDA covers "
            "residential-mortgage lenders meeting the reporting thresholds; "
            "many commercial banks genuinely don't file.)")
        return
    from data.hmda_client import latest_breakdown, originations_by_year
    this_year = date.today().year
    years = list(range(this_year - 7, this_year))
    with st.spinner("Fetching HMDA originations..."):
        by_year = originations_by_year(lei, years)
    if not by_year:
        st.caption("No HMDA originations found for this lender in the last "
                   "7 reported years — n/a.")
        return

    rows = [{"Year": y,
             "Originations": f"{d['count']:,}",
             "Volume": fmt_dollars(d["volume_usd"], 2)}
            for y, d in sorted(by_year.items(), reverse=True)]
    latest_yr = max(by_year)

    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, CHART_HEIGHT_COMPACT,
                                   CATEGORICAL_PALETTE)
    xs = sorted(by_year)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=xs, y=[by_year[y]["volume_usd"] / 1e6 for y in xs],
        name="Volume ($M)", marker_color=CATEGORICAL_PALETTE[0], opacity=0.7))
    fig.add_trace(go.Scatter(
        x=xs, y=[by_year[y]["count"] for y in xs], name="Originations (#)",
        mode="lines+markers", yaxis="y2",
        line=dict(color=CATEGORICAL_PALETTE[1], width=2)))
    apply_standard_layout(fig, title="HMDA originations by year",
                          height=CHART_HEIGHT_COMPACT, yaxis_title="$M",
                          show_legend=True)
    fig.update_layout(yaxis2=dict(overlaying="y", side="right",
                                  showgrid=False, title="#"))

    lt, rt = st.columns([1, 1], vertical_alignment="top")
    with lt:
        st.markdown(f"**Residential-mortgage originations** — public CFPB "
                    f"HMDA, LEI `{lei}`")
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)
    with rt:
        st.plotly_chart(fig, use_container_width=True,
                        key=f"hmda_yr_{ticker}")

    bd = latest_breakdown(lei, latest_yr, by="state")
    if bd:
        tot = sum(r["volume_usd"] for r in bd) or 1
        tbl = pd.DataFrame([
            {"State": r["state"], "Originations": f"{r['count']:,}",
             "Volume": fmt_dollars(r["volume_usd"], 2),
             "% of volume": round(r["volume_usd"] / tot * 100, 1)}
            for r in sorted(bd, key=lambda r: -r["volume_usd"])[:15]])
        st.markdown(f"**{latest_yr} by state** (top {len(tbl)})")
        st.dataframe(tbl, use_container_width=True, hide_index=True)
    st.caption(
        "Originated loans only (HMDA action taken = 1). Public HMDA disclosure "
        "rounds each loan amount to its $10,000-bucket midpoint — counts are "
        "exact, dollar volumes approximate by design. Never FDIC/SEC data.")
