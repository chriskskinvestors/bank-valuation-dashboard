"""
Bank Deposit Lookup — branch map + deposit market share for any public bank.

Uses FDIC Summary of Deposits (SOD) data.
"""

import streamlit as st
import pandas as pd

from data.sod_client import (
    fetch_branches,
    fetch_county_market_share,
    fetch_msa_market_share,
    search_bank_by_name,
)
from data.bank_mapping import get_fdic_cert, get_name
from data.bank_universe import get_universe_tickers, get_universe_bank


def render_deposits_for_ticker(ticker: str):
    """Render deposit data for a specific ticker (no search UI)."""
    cert = get_fdic_cert(ticker)
    if not cert:
        st.warning(f"No FDIC cert found for {ticker}.")
        return
    name = get_name(ticker)
    _render_deposits_core(cert, name)


def render_deposit_lookup():
    """Render the deposit market share & branch map page with search."""

    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Deposit Market Share & Branch Map</h1>"
        "<p>Look up any FDIC-insured bank</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        search_query = st.text_input(
            "Search by bank name",
            placeholder="e.g. Southern First, JPMorgan Chase, Wells Fargo...",
            key="bank_search",
        )
    with col2:
        ticker_input = st.text_input(
            "Or enter ticker",
            placeholder="e.g. SFST, JPM, WFC",
            key="ticker_search",
        )

    selected_cert = None
    selected_name = None

    if ticker_input:
        ticker = ticker_input.strip().upper()
        cert = get_fdic_cert(ticker)
        if cert:
            selected_cert = cert
            selected_name = get_name(ticker) or ticker
        else:
            st.warning(f"Ticker '{ticker}' not found. Try searching by name instead.")

    elif search_query and len(search_query) >= 3:
        with st.spinner("Searching FDIC database..."):
            results = search_bank_by_name(search_query)
        if results:
            options = {f"{r['name']} (CERT: {r['cert']})": r for r in results}
            choice = st.selectbox(
                f"Found {len(results)} match{'es' if len(results) > 1 else ''}",
                options=list(options.keys()),
                key="bank_search_results",
            )
            if choice:
                selected_cert = options[choice]["cert"]
                selected_name = options[choice]["name"]
        else:
            st.info("No banks found. Try a different name.")

    if not selected_cert:
        st.info("Search for a bank above to see its branch map and deposit market share.")
        return

    _render_deposits_core(selected_cert, selected_name)


def _render_deposits_core(selected_cert: int, selected_name: str):
    """Core deposit rendering logic."""

    # ── Load branch data ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(f"📍 {selected_name}")

    with st.spinner("Loading branch data..."):
        branches_df = fetch_branches(selected_cert)

    if branches_df.empty:
        st.warning("No branch data found for this bank.")
        return

    # Summary stats
    total_deposits = branches_df["DEPSUMBR"].sum()
    num_branches = len(branches_df)
    states = branches_df["STALPBR"].nunique()
    counties = branches_df["STCNTYBR"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Branches", f"{num_branches}")
    # FDIC deposits are in thousands → divide by 1e6 to get billions
    if total_deposits >= 1e6:
        c2.metric("Total Deposits", f"${total_deposits / 1e6:,.1f}B")
    else:
        c2.metric("Total Deposits", f"${total_deposits / 1e3:,.0f}M")
    c3.metric("States", f"{states}")
    c4.metric("Counties", f"{counties}")

    # ── Branch map ───────────────────────────────────────────────────────
    st.subheader("Branch Map")

    map_df = branches_df[
        branches_df["SIMS_LATITUDE"].notna() & branches_df["SIMS_LONGITUDE"].notna()
    ].copy()

    if not map_df.empty:
        map_df = map_df.rename(columns={
            "SIMS_LATITUDE": "latitude",
            "SIMS_LONGITUDE": "longitude",
        })
        # Size points by deposits
        max_dep = map_df["DEPSUMBR"].max()
        if max_dep > 0:
            map_df["size"] = (map_df["DEPSUMBR"] / max_dep * 800).clip(lower=50)
        else:
            map_df["size"] = 100

        st.map(map_df, latitude="latitude", longitude="longitude", size="size")
    else:
        st.info("No geographic data available for this bank's branches.")

    # ── Branch detail table ──────────────────────────────────────────────
    st.subheader("Branch Details")

    branch_display = branches_df[[
        "NAMEBR", "CITYBR", "STALPBR", "CNTYNAMB", "DEPSUMBR",
    ]].copy()
    branch_display.columns = ["Branch", "City", "State", "County", "Deposits ($K)"]
    branch_display["Deposits ($K)"] = branch_display["Deposits ($K)"].apply(
        lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
    )
    branch_display = branch_display.sort_values("Branch").reset_index(drop=True)

    st.dataframe(
        branch_display,
        use_container_width=True,
        hide_index=True,
        height=min(400, 40 + 35 * len(branch_display)),
    )

    # ── Market share by county ───────────────────────────────────────────
    st.markdown("---")
    st.subheader("Deposit Market Share")

    # Get unique counties for this bank
    county_options = branches_df[["STCNTYBR", "CNTYNAMB", "STALPBR"]].drop_duplicates()
    county_options = county_options.dropna(subset=["STCNTYBR"])
    county_options["label"] = county_options.apply(
        lambda r: f"{r['CNTYNAMB']} County, {r['STALPBR']}", axis=1
    )

    # Also get unique MSAs
    msa_options = branches_df[["MSABR", "MSANAMB"]].drop_duplicates()
    msa_options = msa_options.dropna(subset=["MSABR"])
    msa_options = msa_options[msa_options["MSABR"] > 0]

    tab1, tab2 = st.tabs(["By County", "By MSA"])

    with tab1:
        if county_options.empty:
            st.info("No county data available.")
        else:
            selected_county = st.selectbox(
                "Select county",
                options=county_options["STCNTYBR"].tolist(),
                format_func=lambda c: county_options[county_options["STCNTYBR"] == c]["label"].iloc[0],
                key="county_select",
            )

            if selected_county:
                with st.spinner("Loading county market share..."):
                    ms_df = fetch_county_market_share(str(int(selected_county)))

                if not ms_df.empty:
                    county_label = county_options[county_options["STCNTYBR"] == selected_county]["label"].iloc[0]
                    total_county_deps = ms_df["deposits"].sum()

                    # Highlight the selected bank
                    bank_row = ms_df[ms_df["CERT"] == selected_cert]
                    if not bank_row.empty:
                        rank = bank_row.iloc[0]["rank"]
                        share = bank_row.iloc[0]["market_share"]
                        deps = bank_row.iloc[0]["deposits"]
                        def _dep_fmt(v):
                            """Format deposits (in thousands) to human-readable."""
                            if v >= 1e6:
                                return f"${v / 1e6:,.1f}B"
                            return f"${v / 1e3:,.0f}M"

                        st.markdown(
                            f"**{selected_name}** ranks **#{int(rank)}** in {county_label} "
                            f"with **{share:.1f}%** market share "
                            f"({_dep_fmt(deps)} of {_dep_fmt(total_county_deps)} total)"
                        )

                    # Display top banks
                    display = ms_df.head(25).copy()
                    display["deposits_fmt"] = display["deposits"].apply(_dep_fmt)
                    display["market_share_fmt"] = display["market_share"].apply(lambda v: f"{v:.1f}%")

                    show_df = display[["rank", "NAMEFULL", "branches", "deposits_fmt", "market_share_fmt"]].copy()
                    show_df.columns = ["Rank", "Bank", "Branches", "Deposits", "Market Share"]

                    st.dataframe(
                        show_df,
                        use_container_width=True,
                        hide_index=True,
                        height=min(600, 40 + 35 * len(show_df)),
                    )
                else:
                    st.warning("Could not load market share data for this county.")

    with tab2:
        if msa_options.empty:
            st.info("No MSA data available.")
        else:
            selected_msa = st.selectbox(
                "Select MSA",
                options=msa_options["MSABR"].tolist(),
                format_func=lambda m: msa_options[msa_options["MSABR"] == m]["MSANAMB"].iloc[0],
                key="msa_select",
            )

            if selected_msa:
                with st.spinner("Loading MSA market share..."):
                    ms_df = fetch_msa_market_share(int(selected_msa))

                if not ms_df.empty:
                    msa_label = msa_options[msa_options["MSABR"] == selected_msa]["MSANAMB"].iloc[0]
                    total_msa_deps = ms_df["deposits"].sum()

                    def _dep_fmt_msa(v):
                        if v >= 1e6:
                            return f"${v / 1e6:,.1f}B"
                        return f"${v / 1e3:,.0f}M"

                    bank_row = ms_df[ms_df["CERT"] == selected_cert]
                    if not bank_row.empty:
                        rank = bank_row.iloc[0]["rank"]
                        share = bank_row.iloc[0]["market_share"]
                        deps = bank_row.iloc[0]["deposits"]
                        st.markdown(
                            f"**{selected_name}** ranks **#{int(rank)}** in {msa_label} "
                            f"with **{share:.1f}%** market share "
                            f"({_dep_fmt_msa(deps)} of {_dep_fmt_msa(total_msa_deps)} total)"
                        )

                    display = ms_df.head(25).copy()
                    display["deposits_fmt"] = display["deposits"].apply(_dep_fmt_msa)
                    display["market_share_fmt"] = display["market_share"].apply(lambda v: f"{v:.1f}%")

                    show_df = display[["rank", "NAMEFULL", "branches", "deposits_fmt", "market_share_fmt"]].copy()
                    show_df.columns = ["Rank", "Bank", "Branches", "Deposits", "Market Share"]

                    st.dataframe(
                        show_df,
                        use_container_width=True,
                        hide_index=True,
                        height=min(600, 40 + 35 * len(show_df)),
                    )
                else:
                    st.warning("Could not load market share data for this MSA.")
