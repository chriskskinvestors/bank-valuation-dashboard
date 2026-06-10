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

    # ── Headline metrics (click any value for its source) ──
    from ui.source_trace import render_traceable_cards, make_calc
    entity = f"{name} ({ticker})"
    SRC = "SEC 13F-HR filings (EDGAR full-text search)"
    nf = summary["total_filers"]
    tot_val = summary.get("total_value_usd") or 0
    top5_val = sum(h["value_usd"] for h in holders[:5] if h.get("value_usd"))

    def own_card(label, value, definition, terms, op=None):
        return {"label": label, "value": value,
                "calc": make_calc(label, value, entity=entity, source=SRC,
                                  asof="last ~90 days", unit="", ref="aggregated 13F-HR filings",
                                  definition=definition, terms=terms, op=op, reported=(op is None))}

    cards = [
        own_card("Institutional Filers", str(nf),
                 "Number of institutions (>$100M AUM) reporting a position in their latest "
                 "13F-HR filing over the last ~90 days.",
                 [{"label": "13F-HR filers", "val": str(nf)}]),
        own_card("Total Shares Held", f"{summary['total_shares']:,.0f}",
                 "Total shares held across all reporting institutions.",
                 [{"label": "Shares (summed across filers)", "val": f"{summary['total_shares']:,.0f}",
                   "sub": f"across {nf} 13F-HR filings"}]),
        own_card("Total Value", fmt_dollars(tot_val, 2),
                 "Total reported market value of institutional holdings.",
                 [{"label": "Value (summed across filers)", "val": fmt_dollars(tot_val, 2),
                   "sub": f"across {nf} 13F-HR filings"}]),
        own_card("Top 5 Concentration", f"{summary['top_5_concentration']:.0f}%",
                 "Share of total institutional dollar value held by the five largest holders — "
                 "a concentration/crowding gauge.",
                 [{"label": "Top-5 holders' value", "val": fmt_dollars(top5_val, 2)},
                  {"label": "Total institutional value", "val": fmt_dollars(tot_val, 2)}],
                 op="Top-5 value ÷ total institutional value × 100"),
    ]
    render_traceable_cards(cards, key=f"ownership_{ticker}", columns=4)

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
