"""
Historical Financials — quarterly and annual data with trend charts.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data.bank_mapping import get_fdic_cert, get_name
from data import fdic_client


# Metrics to pull from FDIC for historical view
HIST_FIELDS = (
    "CERT,REPDTE,ASSET,DEP,LNLSNET,EQTOT,NETINC,NIM,NIMY,ROA,ROE,"
    "EEFFR,NCLNLSR,IDT1CER,INTINCY,INTEXPY,NONIIAY,NONIXAY,"
    "LNRE,LNCI,SC,COREDEP,BRO,INTINC,EINTEXP,NONII,NONIX,"
    "ELNATR,LNLSGR,DEPDOM,EQCDIV,INTANGW"
)

# Display configuration: (fdic_field, display_name, format, category)
HIST_METRICS = [
    # Income & Profitability
    ("NETINC",   "Net Income ($K)",      "${:,.0f}",  "Income"),
    ("ROA",      "ROAA (%)",             "{:.2f}%",   "Income"),
    ("NIMY",     "NIM (%)",              "{:.2f}%",   "Income"),
    ("EEFFR",    "Efficiency Ratio (%)", "{:.1f}%",   "Income"),
    ("INTINC",   "Interest Income ($K)", "${:,.0f}",  "Income"),
    ("EINTEXP",  "Interest Expense ($K)","${:,.0f}",  "Income"),
    ("NONII",    "Noninterest Inc ($K)", "${:,.0f}",  "Income"),
    ("NONIX",    "Noninterest Exp ($K)", "${:,.0f}",  "Income"),
    # Balance Sheet
    ("ASSET",    "Total Assets ($K)",    "${:,.0f}",  "Balance Sheet"),
    ("LNLSNET",  "Net Loans ($K)",       "${:,.0f}",  "Balance Sheet"),
    ("DEP",      "Total Deposits ($K)",  "${:,.0f}",  "Balance Sheet"),
    ("EQTOT",    "Total Equity ($K)",    "${:,.0f}",  "Balance Sheet"),
    ("SC",       "Securities ($K)",      "${:,.0f}",  "Balance Sheet"),
    ("COREDEP",  "Core Deposits ($K)",   "${:,.0f}",  "Balance Sheet"),
    ("BRO",      "Brokered Deposits ($K)","${:,.0f}", "Balance Sheet"),
    # Credit Quality
    ("NCLNLSR",  "NPL Ratio (%)",        "{:.2f}%",  "Credit"),
    ("ELNATR",   "Net Charge-Offs ($K)", "${:,.0f}",  "Credit"),
    # Capital
    ("IDT1CER",  "CET1 Ratio (%)",       "{:.2f}%",  "Capital"),
    # NIM Components
    ("INTINCY",  "Earning Asset Yield (%)","{:.2f}%", "NIM"),
    ("INTEXPY",  "Cost of Funds (%)",     "{:.2f}%",  "NIM"),
]


@st.cache_data(ttl=3600, show_spinner="Loading historical data...")
def fetch_historical(cert: int, quarters: int = 12) -> pd.DataFrame:
    """Fetch quarterly historical financials from FDIC."""
    import requests

    params = {
        "filters": f"CERT:{cert}",
        "fields": HIST_FIELDS,
        "sort_by": "REPDTE",
        "sort_order": "DESC",
        "limit": quarters,
    }
    try:
        resp = requests.get(
            "https://banks.data.fdic.gov/api/financials",
            params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = [r["data"] for r in data.get("data", [])]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        # Convert REPDTE to readable period labels
        df["Period"] = df["REPDTE"].apply(lambda d: f"{str(d)[:4]}Q{(int(str(d)[4:6])-1)//3+1}")
        # Convert numeric columns
        for col in df.columns:
            if col not in ("CERT", "REPDTE", "Period", "ID"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as e:
        st.error(f"Error loading historical data: {e}")
        return pd.DataFrame()


def _annualize(df: pd.DataFrame) -> pd.DataFrame:
    """Group quarterly data into annual summaries."""
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["Year"] = df["REPDTE"].apply(lambda d: str(d)[:4])

    # For stock metrics (ratios, %): average the year's quarters
    avg_fields = ["ROA", "NIMY", "EEFFR", "NCLNLSR", "IDT1CER", "INTINCY", "INTEXPY", "NONIIAY", "NONIXAY"]
    # For flow metrics ($): use Q4 value (YTD annual)
    q4_fields = ["NETINC", "INTINC", "EINTEXP", "NONII", "NONIX", "ELNATR"]
    # For point-in-time: use Q4 value
    pit_fields = ["ASSET", "LNLSNET", "DEP", "EQTOT", "SC", "COREDEP", "BRO", "LNLSGR"]

    annual_rows = []
    for year, group in df.groupby("Year", sort=False):
        row = {"Period": year}
        for f in avg_fields:
            if f in group.columns:
                row[f] = group[f].mean()
        # Q4 = largest REPDTE in the year
        q4 = group.sort_values("REPDTE", ascending=False).iloc[0]
        for f in q4_fields + pit_fields:
            if f in group.columns:
                row[f] = q4[f]
        annual_rows.append(row)

    result = pd.DataFrame(annual_rows)
    return result.sort_values("Period", ascending=False).reset_index(drop=True)


def render_historicals(ticker: str):
    """Render historical financials for a single bank."""

    bank_name = get_name(ticker)
    cert = get_fdic_cert(ticker)

    st.markdown(
        f'<div class="dashboard-header">'
        f"<h1>{ticker} — Historical Financials</h1>"
        f"<p>{bank_name}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if not cert:
        st.warning(f"No FDIC data available for {ticker}.")
        return

    # Fetch data
    df = fetch_historical(cert, quarters=20)
    if df.empty:
        st.warning("No historical data found.")
        return

    annual_df = _annualize(df)

    # Three sections
    tab1, tab2, tab3 = st.tabs(["📈 Trend Charts", "📋 Quarterly Detail", "📊 Annual Summary"])

    # ── TAB 1: Trend Charts ──────────────────────────────────────────────
    with tab1:
        periods = df["Period"].tolist()[::-1]  # chronological

        # Chart 1: Profitability
        fig1 = make_subplots(rows=1, cols=2, subplot_titles=("NIM & Earning Yield", "ROAA & Efficiency"))

        nim = df["NIMY"].tolist()[::-1]
        ea_yield = df["INTINCY"].tolist()[::-1]
        cof = df["INTEXPY"].tolist()[::-1]
        fig1.add_trace(go.Scatter(x=periods, y=nim, name="NIM", line=dict(color="#1a73e8", width=2)), row=1, col=1)
        fig1.add_trace(go.Scatter(x=periods, y=ea_yield, name="EA Yield", line=dict(color="#2e7d32", width=1.5, dash="dot")), row=1, col=1)
        fig1.add_trace(go.Scatter(x=periods, y=cof, name="Cost of Funds", line=dict(color="#c62828", width=1.5, dash="dot")), row=1, col=1)

        roa = df["ROA"].tolist()[::-1]
        eff = df["EEFFR"].tolist()[::-1]
        fig1.add_trace(go.Scatter(x=periods, y=roa, name="ROAA", line=dict(color="#1a73e8", width=2)), row=1, col=2)
        fig1.add_trace(go.Scatter(x=periods, y=eff, name="Efficiency", line=dict(color="#e65100", width=2)), row=1, col=2)

        fig1.update_layout(height=320, margin=dict(t=40, b=30, l=40, r=20), font_size=11, showlegend=True, legend=dict(font_size=10))
        st.plotly_chart(fig1, use_container_width=True)

        # Chart 2: Balance Sheet Growth
        fig2 = make_subplots(rows=1, cols=2, subplot_titles=("Assets & Loans", "Deposits"))

        assets = [v / 1e6 if v else None for v in df["ASSET"].tolist()[::-1]]
        loans = [v / 1e6 if v else None for v in df["LNLSNET"].tolist()[::-1]]
        deps = [v / 1e6 if v else None for v in df["DEP"].tolist()[::-1]]
        core = [v / 1e6 if v else None for v in df["COREDEP"].tolist()[::-1]] if "COREDEP" in df.columns else []

        fig2.add_trace(go.Bar(x=periods, y=assets, name="Assets ($B)", marker_color="#1a73e8", opacity=0.7), row=1, col=1)
        fig2.add_trace(go.Bar(x=periods, y=loans, name="Loans ($B)", marker_color="#2e7d32", opacity=0.7), row=1, col=1)
        fig2.add_trace(go.Bar(x=periods, y=deps, name="Deposits ($B)", marker_color="#1a73e8", opacity=0.7), row=1, col=2)
        if core:
            fig2.add_trace(go.Bar(x=periods, y=core, name="Core ($B)", marker_color="#2e7d32", opacity=0.7), row=1, col=2)

        fig2.update_layout(height=300, margin=dict(t=40, b=30, l=40, r=20), font_size=11, barmode="group", showlegend=True, legend=dict(font_size=10))
        st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: Credit Quality
        fig3 = go.Figure()
        npl = df["NCLNLSR"].tolist()[::-1]
        fig3.add_trace(go.Scatter(x=periods, y=npl, name="NPL Ratio", fill="tozeroy", line=dict(color="#c62828", width=2), fillcolor="rgba(198,40,40,0.1)"))
        if "IDT1CER" in df.columns:
            cet1 = df["IDT1CER"].tolist()[::-1]
            fig3.add_trace(go.Scatter(x=periods, y=cet1, name="CET1 Ratio", line=dict(color="#2e7d32", width=2)))
        fig3.update_layout(height=280, title="Credit Quality & Capital", margin=dict(t=40, b=30, l=40, r=20), font_size=11, legend=dict(font_size=10))
        st.plotly_chart(fig3, use_container_width=True)

    # ── TAB 2: Quarterly Detail ──────────────────────────────────────────
    with tab2:
        st.caption(f"Last {len(df)} quarters · amounts in thousands ($K)")

        # Build display table — periods as columns, metrics as rows
        display_data = {"Metric": []}
        for _, row in df.iterrows():
            display_data[row["Period"]] = []

        for field, label, fmt, category in HIST_METRICS:
            if field not in df.columns:
                continue
            display_data["Metric"].append(label)
            for _, row in df.iterrows():
                val = row.get(field)
                if pd.notna(val):
                    try:
                        display_data[row["Period"]].append(fmt.format(val))
                    except (ValueError, TypeError):
                        display_data[row["Period"]].append(str(val))
                else:
                    display_data[row["Period"]].append("—")

        if display_data["Metric"]:
            qtr_df = pd.DataFrame(display_data)
            st.dataframe(
                qtr_df.style.set_properties(**{"font-size": "0.7rem", "padding": "2px 5px"}),
                use_container_width=True,
                hide_index=True,
                height=min(700, 32 + 24 * len(qtr_df)),
            )

    # ── TAB 3: Annual Summary ────────────────────────────────────────────
    with tab3:
        if annual_df.empty:
            st.info("Not enough data for annual summary.")
            return

        st.caption("Annual figures · amounts in thousands ($K)")

        ann_display = {"Metric": []}
        for _, row in annual_df.iterrows():
            ann_display[row["Period"]] = []

        for field, label, fmt, category in HIST_METRICS:
            if field not in annual_df.columns:
                continue
            ann_display["Metric"].append(label)
            for _, row in annual_df.iterrows():
                val = row.get(field)
                if pd.notna(val):
                    try:
                        ann_display[row["Period"]].append(fmt.format(val))
                    except (ValueError, TypeError):
                        ann_display[row["Period"]].append(str(val))
                else:
                    ann_display[row["Period"]].append("—")

        if ann_display["Metric"]:
            ann_df_display = pd.DataFrame(ann_display)
            st.dataframe(
                ann_df_display.style.set_properties(**{"font-size": "0.7rem", "padding": "2px 5px"}),
                use_container_width=True,
                hide_index=True,
                height=min(700, 32 + 24 * len(ann_df_display)),
            )
