"""
Insider Activity UI — Form 4 trades for a specific bank.
Renders as a sub-panel inside Filings tab OR standalone Insiders tab.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from data.bank_mapping import get_cik, get_name
from data.form4_client import fetch_insider_trades, summarize_insider_activity
from utils.formatting import fmt_dollars
from utils.chart_style import apply_standard_layout, CHART_HEIGHT_COMPACT


def render_insider_activity(ticker: str):
    """Render insider-trading panel for a bank."""
    cik = get_cik(ticker)
    if not cik:
        st.info("No SEC CIK available for this ticker.")
        return

    st.subheader("👥 Insider Trading (Form 4)")

    with st.spinner("Fetching insider trades from SEC EDGAR..."):
        txs = fetch_insider_trades(cik, months_back=12)
        summary = summarize_insider_activity(txs)

    if not txs:
        st.info("No Form 4 filings found in the last 12 months.")
        return

    # ── Headline metrics ───────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.metric("Total Txns (12M)", summary["total_transactions"])
    with c2:
        st.metric(
            "6M Buys",
            fmt_dollars(summary["buys_6m_usd"], 2),
            delta=f"{summary['buyer_count_6m']} insiders" if summary["buyer_count_6m"] else None,
            delta_color="off",
        )
    with c3:
        st.metric(
            "6M Sells",
            fmt_dollars(summary["sells_6m_usd"], 2),
            delta=f"{summary['seller_count_6m']} insiders" if summary["seller_count_6m"] else None,
            delta_color="off",
        )
    with c4:
        net = summary["net_flow_6m_usd"]
        st.metric(
            "Net Flow (6M)",
            fmt_dollars(net, 2),
            delta="Bullish" if net > 0 else ("Bearish" if net < 0 else "Neutral"),
            delta_color="normal" if net > 0 else "inverse",
        )
    with c5:
        # Buy/sell ratio
        if summary["sells_6m_usd"] > 0:
            ratio = summary["buys_6m_usd"] / summary["sells_6m_usd"]
            st.metric("Buy/Sell Ratio", f"{ratio:.2f}x")
        else:
            st.metric("Buy/Sell Ratio", "∞" if summary["buys_6m_usd"] > 0 else "—")

    st.markdown("---")

    # ── Filter controls ─────────────────────────────────────────────────
    f1, f2, f3 = st.columns([1.5, 1.5, 1])
    with f1:
        txn_filter = st.selectbox(
            "Show", ["All transactions", "Market trades only (P/S)", "Buys only", "Sells only"],
            key=f"insider_filter_{ticker}",
        )
    with f2:
        role_filter = st.selectbox(
            "Role", ["All", "Officers only", "Directors only"],
            key=f"insider_role_{ticker}",
        )
    with f3:
        show_limit = st.selectbox(
            "Limit", [20, 50, 100, "All"],
            key=f"insider_limit_{ticker}",
        )

    # Apply filters
    filtered = txs
    if txn_filter == "Market trades only (P/S)":
        filtered = [t for t in filtered if t.get("code") in ("P", "S")]
    elif txn_filter == "Buys only":
        filtered = [t for t in filtered if t.get("direction") == "Buy"]
    elif txn_filter == "Sells only":
        filtered = [t for t in filtered if t.get("direction") == "Sell"]

    if role_filter == "Officers only":
        filtered = [t for t in filtered if "Director" not in (t.get("role") or "") or "Officer" in (t.get("role") or "")]
    elif role_filter == "Directors only":
        filtered = [t for t in filtered if "Director" in (t.get("role") or "")]

    limit = len(filtered) if show_limit == "All" else show_limit

    # ── Transactions table ─────────────────────────────────────────────
    if not filtered:
        st.info("No transactions match the current filter.")
    else:
        rows = []
        for t in filtered[:limit]:
            rows.append({
                "Date": t.get("date") or "—",
                "Insider": t.get("insider") or "—",
                "Role": t.get("role") or "—",
                "Type": t.get("type") or "—",
                "Direction": t.get("direction") or "—",
                "Shares": f"{t['shares']:,.0f}" if t.get("shares") else "—",
                "Price": f"${t['price']:.2f}" if t.get("price") else "—",
                "Value": fmt_dollars(t.get("value_usd"), 2) if t.get("value_usd") else "—",
                "Shares After": f"{t['shares_after']:,.0f}" if t.get("shares_after") else "—",
            })

        df = pd.DataFrame(rows)

        def _color_direction(row):
            d = row.get("Direction", "")
            if d == "Buy":
                return ["background-color: #e8f5e9; color: #1b5e20;"] * len(row)
            elif d == "Sell":
                return ["background-color: #ffebee; color: #b71c1c;"] * len(row)
            elif d == "Exercise":
                return ["background-color: #fff8e1;"] * len(row)
            return [""] * len(row)

        styled = df.style.apply(_color_direction, axis=1).set_properties(
            **{"font-size": "0.80rem", "padding": "3px 8px"}
        )
        st.dataframe(
            styled, use_container_width=True, hide_index=True,
            height=min(600, 50 + 30 * len(df)),
        )

    # ── Insider summary table ──────────────────────────────────────────
    if summary["insiders"]:
        with st.expander(f"📋 Activity by insider ({len(summary['insiders'])} people)"):
            insider_rows = []
            for ins in summary["insiders"]:
                net_ins = ins["buy_usd"] - ins["sell_usd"]
                insider_rows.append({
                    "Insider": ins["name"],
                    "Role": ins.get("role") or "—",
                    "Buys ($)": fmt_dollars(ins["buy_usd"], 2) if ins["buy_usd"] else "—",
                    "Sells ($)": fmt_dollars(ins["sell_usd"], 2) if ins["sell_usd"] else "—",
                    "Net": fmt_dollars(net_ins, 2),
                    "Txns": ins["txn_count"],
                })
            idf = pd.DataFrame(insider_rows)

            def _color_net(row):
                net_str = row.get("Net", "")
                try:
                    is_positive = net_str and not net_str.startswith("-")
                    is_nonzero = net_str not in ("$0", "—")
                    if is_positive and is_nonzero:
                        return ["background-color: #e8f5e9;"] * len(row)
                    elif not is_positive and is_nonzero:
                        return ["background-color: #ffebee;"] * len(row)
                except Exception:
                    pass
                return [""] * len(row)

            styled = idf.style.apply(_color_net, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)

    st.caption(
        "Form 4 trades filed with SEC EDGAR. Only non-derivative market trades (P=purchase, S=sale) "
        "are counted in the 6M buy/sell summary. Grants, tax withholdings, and option exercises are excluded."
    )
