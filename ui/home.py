"""
Home page — branded landing with live summary stats, top opportunities,
recent filings, and navigation cards.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name, get_cik
from data.sec_client import get_filing_info
from data.filing_summarizer import fetch_filing_text, find_press_release_url, summarize_filing


def render_home(all_metrics: list[dict], watchlist: list[str]):
    """Render the home/dashboard page."""

    # ── Hero header ──────────────────────────────────────────────────────
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #1a237e 0%, #0d47a1 50%, #01579b 100%);
            padding: 2.5rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
        ">
            <h1 style="color: white; margin: 0; font-size: 2.2rem;">
                KSK Investors
            </h1>
            <p style="color: #b3d4fc; margin: 0.5rem 0 0 0; font-size: 1.1rem;">
                Bank Valuation & Analysis Platform
            </p>
            <p style="color: #90caf9; margin: 0.3rem 0 0 0; font-size: 0.85rem;">
                Live FDIC · SEC EDGAR · IBKR &nbsp;|&nbsp; {count} banks tracked
            </p>
        </div>
        """.format(count=len(watchlist)),
        unsafe_allow_html=True,
    )

    # ── Summary metrics row ──────────────────────────────────────────────
    if all_metrics:
        df = pd.DataFrame(all_metrics)

        # Aggregate stats
        banks_with_data = len(df[df.get("roatce_blended", pd.Series(dtype=float)).notna()]) if "roatce_blended" in df.columns else 0
        avg_roatce = df["roatce_blended"].mean() if "roatce_blended" in df.columns and df["roatce_blended"].notna().any() else None
        avg_nim = df["nim"].mean() if "nim" in df.columns and df["nim"].notna().any() else None
        avg_efficiency = df["efficiency_ratio"].mean() if "efficiency_ratio" in df.columns and df["efficiency_ratio"].notna().any() else None
        median_ptbv = df["fair_ptbv"].median() if "fair_ptbv" in df.columns and df["fair_ptbv"].notna().any() else None

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Banks Tracked", f"{len(watchlist)}")
        c2.metric("Avg ROATCE", f"{avg_roatce:.1f}%" if avg_roatce else "—")
        c3.metric("Avg NIM", f"{avg_nim:.2f}%" if avg_nim else "—")
        c4.metric("Avg Efficiency", f"{avg_efficiency:.1f}%" if avg_efficiency else "—")

    st.markdown("")

    # ── Two-column layout: Opportunities + Recent Filings ────────────────
    col_left, col_right = st.columns(2)

    # ── Top undervalued banks ────────────────────────────────────────────
    with col_left:
        st.markdown("### Top Opportunities")
        st.caption("Banks trading >15% below fair P/TBV")

        if all_metrics:
            df = pd.DataFrame(all_metrics)
            if "ptbv_discount" in df.columns:
                opps = df[df["ptbv_discount"].notna() & (df["ptbv_discount"] > 0)].copy()
                opps = opps.sort_values("ptbv_discount", ascending=False).head(10)

                if not opps.empty:
                    for _, row in opps.iterrows():
                        ticker = row.get("ticker", "")
                        name = row.get("bank", ticker)
                        discount = row.get("ptbv_discount", 0)
                        fair_p = row.get("fair_price")
                        blended = row.get("roatce_blended")

                        flag = "🟢" if discount > 15 else "🟡"
                        fair_str = f" · Fair: ${fair_p:.2f}" if fair_p else ""
                        roatce_str = f" · ROATCE: {blended:.1f}%" if blended else ""

                        st.markdown(
                            f"{flag} **{ticker}** — {discount:.0f}% below fair"
                            f"{fair_str}{roatce_str}"
                        )
                else:
                    st.info("No undervalued banks found. Connect IBKR for live P/TBV data.")
            else:
                st.info("Fair value data not yet computed.")
        else:
            st.info("Loading metrics...")

    # ── Recent filings & press releases ─────────────────────────────────
    with col_right:
        st.markdown("### Recent Filings & Press Releases")
        st.caption("Latest across all banks · click to expand summary")

        # Sample banks to show recent filings
        sample_tickers = watchlist[:10]
        recent_filings = []

        for ticker in sample_tickers:
            cik = get_cik(ticker)
            if not cik:
                continue
            try:
                info = get_filing_info(cik, max_filings=5)
                for f in info.get("recent_filings", []):
                    if f["form"] in ("10-K", "10-Q", "8-K"):
                        recent_filings.append({
                            "ticker": ticker,
                            "cik": info.get("cik", cik),
                            "form": f["form"],
                            "date": f["date"],
                            "is_earnings": f.get("is_earnings", False),
                            "url": f.get("url", ""),
                            "accession": f.get("accession", ""),
                            "items": f.get("items", ""),
                            "description": f.get("description", ""),
                        })
            except Exception:
                continue

        # Sort by date descending, show top 10
        recent_filings.sort(key=lambda x: x["date"], reverse=True)
        recent_filings = recent_filings[:10]

        if recent_filings:
            for i, f in enumerate(recent_filings):
                earnings_tag = " EARNINGS" if f["is_earnings"] else ""
                form_icon = {"10-K": "📗", "10-Q": "📘", "8-K": "📄"}.get(f["form"], "📄")

                label = f"{form_icon} {f['ticker']} {f['form']}{earnings_tag} — {f['date']}"

                with st.expander(label, expanded=False):
                    # Links
                    links = []
                    if f["url"]:
                        links.append(f"[📄 Filing]({f['url']})")

                    # For earnings 8-Ks, find press release
                    if f["is_earnings"] and f.get("accession"):
                        pr_url = find_press_release_url(f["cik"], f["accession"])
                        if pr_url:
                            links.append(f"[📰 Press Release]({pr_url})")

                    if links:
                        st.markdown(" · ".join(links))

                    # Summary
                    summary_url = None
                    if f["is_earnings"] and f.get("accession"):
                        summary_url = find_press_release_url(f["cik"], f["accession"]) or f["url"]
                    else:
                        summary_url = f["url"]

                    if summary_url:
                        with st.spinner("Summarizing..."):
                            text = fetch_filing_text(summary_url)
                            if text and not text.startswith("[Error"):
                                summary = summarize_filing(text, f["form"], f["ticker"])
                                st.markdown(summary)
                            else:
                                st.caption("Could not fetch filing content.")
                    else:
                        st.caption("No document available.")
        else:
            st.info("Loading recent filings...")

    st.markdown("")
    st.markdown("---")

    # ── Navigation cards ─────────────────────────────────────────────────
    st.markdown("### Explore")

    cards = [
        ("📊", "Valuation & Performance",
         "P/E, P/TBV, ROATCE, fair value screening, dividend yields across all banks"),
        ("🏦", "Balance Sheet",
         "Loan mix, deposit composition, securities, capital ratios, credit quality"),
        ("💰", "Deposits & Loans",
         "Detailed deposit ratios, loan concentration, CRE exposure, brokered deposits"),
        ("📈", "NIM & Income",
         "Yield curves, cost of funds, net interest margin, efficiency ratios"),
        ("🔍", "Deposit Lookup",
         "Branch map, deposit market share by county/MSA for any FDIC-insured bank"),
        ("📄", "SEC & FDIC Filings",
         "Browse 10-K, 10-Q, 8-K filings with earnings release flagging and IR links"),
    ]

    # 3 cards per row
    for i in range(0, len(cards), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j < len(cards):
                icon, title, desc = cards[i + j]
                col.markdown(
                    f"""
                    <div style="
                        background: rgba(255,255,255,0.05);
                        border: 1px solid rgba(255,255,255,0.1);
                        border-radius: 8px;
                        padding: 1.2rem;
                        height: 140px;
                    ">
                        <div style="font-size: 1.5rem; margin-bottom: 0.3rem;">{icon}</div>
                        <div style="font-weight: 600; margin-bottom: 0.3rem;">{title}</div>
                        <div style="color: #aaa; font-size: 0.8rem;">{desc}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("")
    st.caption("Use the View dropdown in the sidebar to navigate between sections.")
