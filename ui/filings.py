"""
SEC & FDIC Filings page — browse recent filings, earnings releases,
press releases, and regulatory documents for any bank in the watchlist.

Includes AI-generated summaries when Anthropic API key is configured.
"""

import streamlit as st
import pandas as pd

from data.sec_client import get_filing_info
from data.bank_mapping import get_cik, get_fdic_cert, get_name, get_ir_url
from data.filing_summarizer import (
    fetch_filing_text,
    find_press_release_url,
    summarize_filing,
)


# ── Color badges for form types ─────────────────────────────────────────
FORM_COLORS = {
    "10-K":     "#2e7d32",  # green
    "10-K/A":   "#2e7d32",
    "10-Q":     "#1565c0",  # blue
    "10-Q/A":   "#1565c0",
    "8-K":      "#616161",  # gray
    "8-K/A":    "#616161",
    "DEF 14A":  "#6a1b9a",  # purple
    "DEFA14A":  "#6a1b9a",
    "S-1":      "#e65100",  # orange
    "S-1/A":    "#e65100",
    "S-3":      "#e65100",
    "S-3/A":    "#e65100",
    "4":        "#795548",  # brown
    "3":        "#795548",
    "SC 13D":   "#00695c",  # teal
    "SC 13D/A": "#00695c",
    "SC 13G":   "#00695c",
    "SC 13G/A": "#00695c",
}

# 8-K item descriptions for human-readable labels
ITEM_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate an Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy Listing Standards",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "5.05": "Amendment to Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Nominations",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


def _form_badge(form: str, is_earnings: bool = False) -> str:
    """Return an HTML badge for the form type."""
    color = FORM_COLORS.get(form, "#757575")
    badge = (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:600;">{form}</span>'
    )
    if is_earnings:
        badge += (
            ' <span style="background:#ff6f00;color:white;padding:2px 6px;'
            'border-radius:4px;font-size:0.75em;">EARNINGS</span>'
        )
    return badge


def _items_description(items_str: str) -> str:
    """Convert 8-K item codes to human-readable descriptions."""
    if not items_str:
        return ""
    parts = []
    for item in items_str.split(","):
        item = item.strip()
        desc = ITEM_DESCRIPTIONS.get(item)
        if desc:
            parts.append(f"Item {item}: {desc}")
        elif item:
            parts.append(f"Item {item}")
    return " · ".join(parts)


def render_filings_for_ticker(ticker: str):
    """Render filings for a specific ticker (no bank selector UI)."""
    _render_filings_core(ticker)


def render_filings(watchlist: list[str]):
    """Render the SEC & FDIC filings page with bank selector."""

    st.markdown(
        '<div class="dashboard-header">'
        "<h1>SEC & FDIC Filings</h1>"
        "<p>Browse filings, earnings releases, press releases & regulatory documents</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        selected = st.selectbox(
            "Select bank",
            options=[""] + watchlist,
            format_func=lambda t: f"{t} — {get_name(t)}" if t else "Choose a bank...",
            key="filings_bank_select",
        )
    with col2:
        ticker_input = st.text_input(
            "Or enter any ticker",
            placeholder="e.g. JPM, WFC, SFST",
            key="filings_ticker_input",
        )

    ticker = None
    if ticker_input:
        ticker = ticker_input.strip().upper()
    elif selected:
        ticker = selected

    if not ticker:
        st.info("Select a bank above to view its SEC filings and earnings releases.")
        return

    _render_filings_core(ticker)


def _render_filings_core(ticker: str):
    """Core filings rendering logic for a given ticker."""

    # ── Resolve CIK ──────────────────────────────────────────────────────
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK found for **{ticker}**. This bank may not file with the SEC.")
        return

    cert = get_fdic_cert(ticker)
    ir_url = get_ir_url(ticker)

    # ── Load filing data ─────────────────────────────────────────────────
    with st.spinner("Loading SEC filings..."):
        info = get_filing_info(cik, max_filings=80)

    if not info:
        st.error("Failed to load filing data from SEC EDGAR.")
        return

    # ── Quick links bar ──────────────────────────────────────────────────
    st.markdown("---")

    link_cols = st.columns(4)
    if ir_url:
        link_cols[0].link_button("🌐 IR Page", ir_url)
    else:
        link_cols[0].caption("No IR page mapped")

    edgar_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={cik}&type=&dateb=&owner=include&count=40"
    )
    link_cols[1].link_button("📋 EDGAR Company Page", edgar_url)

    if cert:
        fdic_url = f"https://www.fdic.gov/analysis/bank-research/bankfind/index.html?CERT={cert}"
        link_cols[2].link_button("🏦 FDIC BankFind", fdic_url)

        ffiec_url = "https://cdr.ffiec.gov/public/ManageFacsimiles.aspx"
        link_cols[3].link_button("📑 FFIEC Call Reports", ffiec_url)
    else:
        link_cols[2].caption("No FDIC cert")
        link_cols[3].caption("—")

    # ── Company info ─────────────────────────────────────────────────────
    name = info.get("name", ticker)
    sic_desc = info.get("sic_description", "")
    fy_end = info.get("fiscal_year_end", "")
    website = info.get("website", "")

    fy_display = ""
    if fy_end and len(fy_end) == 4:
        fy_display = f"{fy_end[:2]}/{fy_end[2:]}"

    info_parts = [f"**{name}** ({ticker})"]
    if sic_desc:
        info_parts.append(f"SIC: {sic_desc}")
    if fy_display:
        info_parts.append(f"FY End: {fy_display}")
    if website:
        info_parts.append(f"[{website}]({website})")

    st.markdown(" · ".join(info_parts))

    # ── Filing data ──────────────────────────────────────────────────────
    filings = info.get("recent_filings", [])
    if not filings:
        st.warning("No filings found.")
        return

    raw_cik = info.get("cik", cik)

    # Identify press releases and earnings
    press_releases = []
    for f in filings:
        if f["form"] in ("8-K", "8-K/A") and f.get("is_earnings"):
            pr_url = find_press_release_url(raw_cik, f["accession"])
            press_releases.append({**f, "press_release_url": pr_url})

    # ── Tabs ─────────────────────────────────────────────────────────────
    tab_all, tab_press, tab_earnings, tab_annual, tab_insider = st.tabs([
        f"All Filings ({len(filings)})",
        f"Press Releases ({len(press_releases)})",
        "Earnings Releases",
        "Annual & Quarterly",
        "👥 Insider Activity",
    ])

    with tab_all:
        _render_filings_section(filings, show_filters=True, key_prefix="all",
                                 ticker=ticker, cik=raw_cik)

    with tab_press:
        _render_press_releases(press_releases, ticker, raw_cik)

    with tab_insider:
        from ui.insider_activity import render_insider_activity
        render_insider_activity(ticker)

    with tab_earnings:
        earnings = [f for f in filings if f.get("is_earnings")]
        if earnings:
            st.markdown(
                f"**{len(earnings)}** earnings releases found "
                "(8-K filings with Item 2.02 — Results of Operations)"
            )
            _render_filings_table(earnings, key_prefix="earn",
                                   ticker=ticker, cik=raw_cik, show_summary=True)
        else:
            st.info("No earnings releases found in recent filings.")

    with tab_annual:
        annual_quarterly = [
            f for f in filings
            if f["form"] in ("10-K", "10-K/A", "10-Q", "10-Q/A")
        ]
        if annual_quarterly:
            _render_filings_table(annual_quarterly, key_prefix="aq",
                                   ticker=ticker, cik=raw_cik, show_summary=True)
        else:
            st.info("No 10-K or 10-Q filings found.")


def _render_press_releases(press_releases: list[dict], ticker: str, cik: int):
    """Render the press releases tab with AI summaries."""

    if not press_releases:
        st.info("No press releases found. Press releases are identified from 8-K earnings filings (Item 2.02).")
        return

    st.markdown(
        f"**{len(press_releases)}** earnings press releases found for **{ticker}**"
    )
    st.caption("Summaries are generated from the EX-99.1 exhibit of each 8-K filing.")

    for i, pr in enumerate(press_releases):
        date = pr.get("date", "")
        form = pr.get("form", "8-K")
        pr_url = pr.get("press_release_url")
        filing_url = pr.get("url", "")

        # Header with expandable detail
        with st.expander(f"📰 {date} — {ticker} Earnings Release", expanded=(i == 0)):
            # Links row
            link_parts = []
            if pr_url:
                link_parts.append(f"[📄 Press Release]({pr_url})")
            if filing_url:
                link_parts.append(f"[📋 8-K Filing]({filing_url})")
            if pr.get("index_url"):
                link_parts.append(f"[📁 All Documents]({pr['index_url']})")

            if link_parts:
                st.markdown(" · ".join(link_parts))

            # 8-K items
            items_desc = _items_description(pr.get("items", ""))
            if items_desc:
                st.caption(items_desc)

            # AI Summary
            st.markdown("**Summary:**")

            # Determine which URL to summarize
            summary_url = pr_url or filing_url
            if summary_url:
                with st.spinner("Generating summary..."):
                    text = fetch_filing_text(summary_url)
                    if text and not text.startswith("[Error"):
                        summary = summarize_filing(text, form, ticker)
                        st.markdown(summary)
                    else:
                        st.caption("Could not fetch filing content for summary.")
            else:
                st.caption("No document URL available.")


def _render_filings_section(filings: list[dict], show_filters: bool = False,
                             key_prefix: str = "", ticker: str = "", cik: int = 0):
    """Render a filterable filings section."""

    filtered = filings

    if show_filters:
        all_forms = sorted(set(f["form"] for f in filings))
        major_forms = ["10-K", "10-Q", "8-K"]

        # Only default-include forms that actually exist in the filings
        # (avoids "default not in options" crash when some major forms aren't present)
        default_forms = [f for f in major_forms if f in all_forms] or all_forms
        col1, col2 = st.columns([3, 1])
        with col1:
            selected_forms = st.multiselect(
                "Filter by form type",
                options=all_forms,
                default=default_forms,
                key=f"{key_prefix}_form_filter",
            )
        with col2:
            earnings_only = st.checkbox(
                "Earnings only",
                key=f"{key_prefix}_earnings_only",
            )

        if selected_forms:
            filtered = [f for f in filtered if f["form"] in selected_forms]
        if earnings_only:
            filtered = [f for f in filtered if f.get("is_earnings")]

    _render_filings_table(filtered, key_prefix=key_prefix,
                           ticker=ticker, cik=cik, show_summary=False)


def _render_filings_table(filings: list[dict], key_prefix: str = "",
                           ticker: str = "", cik: int = 0,
                           show_summary: bool = False):
    """Render a styled filings table with links and optional summaries."""

    if not filings:
        st.info("No filings match the current filters.")
        return

    # Build HTML table
    rows_html = []
    for f in filings:
        form_badge = _form_badge(f["form"], f.get("is_earnings", False))
        date = f.get("date", "")
        report_date = f.get("report_date", "")
        desc = f.get("description", "")
        url = f.get("url", "")
        index_url = f.get("index_url", "")
        items = f.get("items", "")

        # Show 8-K item descriptions
        items_html = ""
        if items and f["form"] in ("8-K", "8-K/A"):
            items_desc = _items_description(items)
            if items_desc:
                items_html = f'<div style="color:#90a4ae;font-size:0.75em;margin-top:2px;">{items_desc}</div>'

        # Truncate long descriptions
        if len(desc) > 60:
            desc = desc[:57] + "..."

        link_html = ""
        if url:
            link_html = f'<a href="{url}" target="_blank" style="color:#4fc3f7;">View</a>'
            if index_url:
                link_html += f' · <a href="{index_url}" target="_blank" style="color:#90a4ae;">Index</a>'

        rows_html.append(
            f"<tr>"
            f"<td style='padding:6px 10px;white-space:nowrap;'>{date}</td>"
            f"<td style='padding:6px 10px;'>{form_badge}</td>"
            f"<td style='padding:6px 10px;'>{desc}{items_html}</td>"
            f"<td style='padding:6px 10px;white-space:nowrap;'>{report_date}</td>"
            f"<td style='padding:6px 10px;'>{link_html}</td>"
            f"</tr>"
        )

    table_html = f"""
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:0.9em;">
    <thead>
    <tr style="border-bottom:2px solid #444;">
        <th style="padding:8px 10px;text-align:left;">Filed</th>
        <th style="padding:8px 10px;text-align:left;">Form</th>
        <th style="padding:8px 10px;text-align:left;">Description</th>
        <th style="padding:8px 10px;text-align:left;">Report Date</th>
        <th style="padding:8px 10px;text-align:left;">Links</th>
    </tr>
    </thead>
    <tbody>
    {"".join(rows_html)}
    </tbody>
    </table>
    </div>
    """

    st.markdown(table_html, unsafe_allow_html=True)
    st.caption(f"Showing {len(filings)} filing{'s' if len(filings) != 1 else ''}")

    # Expandable summaries below the table
    if show_summary and filings:
        st.markdown("")
        st.markdown("#### Filing Summaries")
        st.caption("Click a filing to see its AI-generated summary")

        for f in filings[:10]:  # Limit to most recent 10
            url = f.get("url", "")
            if not url:
                continue

            label = f"{f.get('date', '')} — {f['form']}"
            if f.get("is_earnings"):
                label += " (Earnings)"

            with st.expander(label):
                with st.spinner("Generating summary..."):
                    text = fetch_filing_text(url)
                    if text and not text.startswith("[Error"):
                        summary = summarize_filing(text, f["form"], ticker)
                        st.markdown(summary)
                    else:
                        st.warning("Could not fetch filing content.")

                # Document links
                links = []
                if url:
                    links.append(f"[📄 Filing]({url})")
                if f.get("index_url"):
                    links.append(f"[📁 All Documents]({f['index_url']})")
                if links:
                    st.markdown(" · ".join(links))
