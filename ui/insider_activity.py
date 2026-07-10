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
from utils.chart_style import (apply_standard_layout, CHART_HEIGHT_COMPACT,
                               COLOR_SUCCESS, COLOR_DANGER, COLOR_PRIMARY)
from ui.chrome import table_export, ledger, title_bar


def _window_aggregates(txs: list[dict], days: int, today=None) -> dict:
    """Open-market (P/S) aggregates over a trailing window: value bought/sold,
    distinct buyer/seller counts, net. Pure — unit-tested directly. Mirrors the
    6M summary's convention (only P/S market trades count; grants, withholdings
    and exercises are excluded)."""
    today = today or datetime.now().date()
    cutoff = (today - timedelta(days=days)).isoformat()
    buys = sells = 0.0
    buyers, sellers = set(), set()
    for t in txs:
        if t.get("code") not in ("P", "S"):
            continue
        d, v = t.get("date"), t.get("value_usd")
        if not d or d < cutoff or not v:
            continue
        if t.get("direction") == "Buy":
            buys += v
            buyers.add(t.get("insider"))
        elif t.get("direction") == "Sell":
            sells += v
            sellers.add(t.get("insider"))
    return {"buys_usd": buys, "sells_usd": sells, "net_usd": buys - sells,
            "buyers": len(buyers), "sellers": len(sellers)}


def _filing_url(cik, accession: str | None) -> str | None:
    """EDGAR filing-index URL for a Form 4 accession."""
    if not accession or not cik:
        return None
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def render_insider_activity(ticker: str, show_title: bool = True):
    """Render insider-trading panel for a bank.

    show_title=False when embedded as a sub-panel under another page (e.g. the
    Filings tab), so the SNL title bar is not repeated mid-page.
    """
    cik = get_cik(ticker)
    if not cik:
        st.info("No SEC CIK available for this ticker.")
        return

    if show_title:
        title_bar(f"{get_name(ticker)} ({ticker})", "Insider Activity")
    st.subheader("Insider Trading (Form 4)")

    with st.spinner("Fetching insider trades from SEC EDGAR..."):
        txs = fetch_insider_trades(cik, months_back=12)
        summary = summarize_insider_activity(txs)

    if not txs:
        st.info("No Form 4 filings found in the last 12 months.")
        return

    # ── Headline metrics (boxless ledger) ───────────────────────────────
    _m = "color:var(--text-muted);font-size:var(--fs-xs)"
    net = summary["net_flow_6m_usd"]
    if net > 0:
        _net_val = f'{fmt_dollars(net, 2)} <span style="color:var(--success);font-size:var(--fs-xs)">Bullish</span>'
    elif net < 0:
        _net_val = f'{fmt_dollars(net, 2)} <span style="color:var(--danger);font-size:var(--fs-xs)">Bearish</span>'
    else:
        _net_val = f'{fmt_dollars(net, 2)} <span style="{_m}">Neutral</span>'
    if summary["sells_6m_usd"] > 0:
        _ratio_val = f'{summary["buys_6m_usd"] / summary["sells_6m_usd"]:.2f}x'
    else:
        _ratio_val = "∞" if summary["buys_6m_usd"] > 0 else "—"
    ledger("Summary", [
        ("Total Txns (12M)", str(summary["total_transactions"])),
        ("6M Buys", fmt_dollars(summary["buys_6m_usd"], 2)
         + (f' <span style="{_m}">{summary["buyer_count_6m"]} insiders</span>'
            if summary["buyer_count_6m"] else "")),
        ("6M Sells", fmt_dollars(summary["sells_6m_usd"], 2)
         + (f' <span style="{_m}">{summary["seller_count_6m"]} insiders</span>'
            if summary["seller_count_6m"] else "")),
        ("Net Flow (6M)", _net_val),
        ("Buy/Sell Ratio", _ratio_val),
    ])

    # ── Windowed aggregates (SNL spec: value bought/sold, buyers:sellers) ──
    win_rows = []
    for label, days in [("3M", 91), ("6M", 182), ("1Y", 365)]:
        w = _window_aggregates(txs, days)
        win_rows.append({
            "Window": label,
            "Bought": fmt_dollars(w["buys_usd"], 2) if w["buys_usd"] else "—",
            "Sold": fmt_dollars(w["sells_usd"], 2) if w["sells_usd"] else "—",
            "Net": fmt_dollars(w["net_usd"], 2) if (w["buys_usd"] or w["sells_usd"]) else "—",
            "Buyers : Sellers": (f"{w['buyers']} : {w['sellers']}"
                                 if (w["buyers"] or w["sellers"]) else "—"),
        })
    st.dataframe(pd.DataFrame(win_rows), use_container_width=True,
                 hide_index=True, height=36 + 35 * len(win_rows))
    st.caption(
        "Open-market P/S trades only. The 12-month fetch reads each CIK's 30 "
        "most recent Form 4s, so very active filers may truncate the older "
        "window; the 5Y aggregate needs the deeper EDGAR backfill (phase 2)."
    )

    # ── Price graph with buy/sell markers (SNL spec) ────────────────────
    try:
        from data.fmp_client import get_history
        import plotly.graph_objects as go
        hist_px = get_history(ticker, "1Y")
    except Exception:
        hist_px = None
    if hist_px is not None and not hist_px.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist_px["date"], y=hist_px["close"], name="Price", mode="lines",
            line=dict(color=COLOR_PRIMARY, width=1.6)))
        mk = [t for t in txs if t.get("code") in ("P", "S")
              and t.get("date") and t.get("price")]
        buys_m = [t for t in mk if t.get("direction") == "Buy"]
        sells_m = [t for t in mk if t.get("direction") == "Sell"]
        if buys_m:
            fig.add_trace(go.Scatter(
                x=[t["date"] for t in buys_m], y=[t["price"] for t in buys_m],
                name="Buy", mode="markers",
                marker=dict(symbol="triangle-up", size=9, color=COLOR_SUCCESS),
                text=[f"{t['insider']}: +{t['shares']:,.0f} sh" for t in buys_m],
                hovertemplate="%{text}<br>%{x} @ $%{y:.2f}<extra>Buy</extra>"))
        if sells_m:
            fig.add_trace(go.Scatter(
                x=[t["date"] for t in sells_m], y=[t["price"] for t in sells_m],
                name="Sell", mode="markers",
                marker=dict(symbol="triangle-down", size=9, color=COLOR_DANGER),
                text=[f"{t['insider']}: −{t['shares']:,.0f} sh" for t in sells_m],
                hovertemplate="%{text}<br>%{x} @ $%{y:.2f}<extra>Sell</extra>"))
        apply_standard_layout(fig, title="Price with insider buys / sells (1Y)",
                              height=CHART_HEIGHT_COMPACT, show_legend=True)
        fig.update_yaxes(tickprefix="$")
        st.plotly_chart(fig, use_container_width=True)
        if not mk:
            st.caption("No open-market trades in the window — markers appear "
                       "when insiders buy or sell at market.")

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
                "Filing": _filing_url(cik, t.get("accession")),
            })

        df = pd.DataFrame(rows)

        def _color_direction(row):
            d = row.get("Direction", "")
            if d == "Buy":
                return ["background-color: rgba(5, 150, 105, 0.08); color: #059669;"] * len(row)
            elif d == "Sell":
                return ["background-color: rgba(220, 38, 38, 0.08); color: #dc2626;"] * len(row)
            elif d == "Exercise":
                return ["background-color: rgba(217, 119, 6, 0.08);"] * len(row)
            return [""] * len(row)

        styled = df.style.apply(_color_direction, axis=1).set_properties(
            **{"font-size": "0.80rem", "padding": "3px 8px"}
        )
        st.dataframe(
            styled, use_container_width=True, hide_index=True,
            height=min(600, 50 + 30 * len(df)),
            column_config={
                "Filing": st.column_config.LinkColumn(
                    "Filing", help="Open the source Form 4 on SEC EDGAR",
                    display_text="SEC ↗", width="small"),
            },
        )
        # Underlying numeric transactions (unformatted shares/price/value)
        table_export(pd.DataFrame(filtered[:limit]),
                     f"insider_transactions_{ticker}",
                     key=f"exp_insider_transactions_{ticker}")

    # ── Insider summary table ──────────────────────────────────────────
    if summary["insiders"]:
        with st.expander(f"Activity by insider ({len(summary['insiders'])} people)"):
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
                        return ["background-color: rgba(5, 150, 105, 0.08);"] * len(row)
                    elif not is_positive and is_nonzero:
                        return ["background-color: rgba(220, 38, 38, 0.08);"] * len(row)
                except Exception:
                    pass
                return [""] * len(row)

            styled = idf.style.apply(_color_net, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)
            # Underlying numeric per-insider totals (unformatted USD)
            table_export(pd.DataFrame(summary["insiders"]),
                         f"insider_summary_{ticker}",
                         key=f"exp_insider_summary_{ticker}")

    st.caption(
        "Form 4 trades filed with SEC EDGAR. Only non-derivative market trades (P=purchase, S=sale) "
        "are counted in the 6M buy/sell summary. Grants, tax withholdings, and option exercises are excluded."
    )
