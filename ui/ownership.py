"""
Institutional Ownership UI — 13F holdings for a bank.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name
from data.form13f_client import fetch_institutional_holdings, summarize_holdings
from utils.formatting import fmt_dollars


def render_ownership(ticker: str):
    """Render 13F institutional holdings panel."""
    name = get_name(ticker)

    st.subheader("🏛 Institutional Ownership (13F)")
    st.caption(
        "Top institutional holders from most recent 13F-HR filings (last ~90 days). "
        "Small banks may have limited 13F coverage."
    )

    with st.spinner("Fetching 13F filings from SEC EDGAR..."):
        holders = fetch_institutional_holdings(ticker, name, max_filers=30)
        summary = summarize_holdings(holders)

    if not holders:
        st.info(
            "No 13F filings found. This can happen for smaller banks with limited "
            "institutional coverage, or if the SEC full-text search fails for the ticker."
        )
        return

    # ── Headline metrics ───────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.metric("Institutional Filers", summary["total_filers"])
    with c2:
        st.metric("Total Shares Held", f"{summary['total_shares']:,.0f}")
    with c3:
        st.metric("Total Value", fmt_dollars(summary["total_value_usd"], 2))
    with c4:
        st.metric(
            "Top 5 Concentration",
            f"{summary['top_5_concentration']:.0f}%",
            delta="of institutional $", delta_color="off",
        )

    st.markdown("---")

    # ── Holders table ──────────────────────────────────────────────────
    rows = []
    total_val = summary["total_value_usd"] or 1
    for h in holders:
        pct_of_inst = (h["value_usd"] / total_val * 100) if total_val else 0
        rows.append({
            "Rank": len(rows) + 1,
            "Institution": h["filer_name"],
            "Date Filed": h.get("date_filed") or "—",
            "Shares": f"{h['shares']:,.0f}",
            "Value": fmt_dollars(h["value_usd"], 2),
            "% of Inst": f"{pct_of_inst:.1f}%",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        height=min(700, 50 + 32 * len(df)),
    )

    st.caption(
        "Note: 13F filings are only required for institutions managing >$100M. "
        "They cover equity holdings only (not derivatives), filed 45 days after quarter-end."
    )
