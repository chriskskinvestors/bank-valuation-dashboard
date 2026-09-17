"""
Bank Deposit Lookup — branch map + deposit market share for any public bank.

Uses FDIC Summary of Deposits (SOD) data.
"""

import streamlit as st
from ui.states import skeleton as _skeleton
import pandas as pd

from data.sod_client import fetch_branches, search_bank_by_name
from data.bank_mapping import get_fdic_cert, get_name
from data.bank_universe import get_universe_tickers, get_universe_bank
from ui.chrome import ledger, table_export, title_bar, lazy_tabs
from ui.tables import ksk_table, ticker_anchor_cells as _linked_tickers


def render_deposits_for_ticker(ticker: str):
    """Render deposit data for a specific ticker (no search UI)."""
    cert = get_fdic_cert(ticker)
    if not cert:
        st.warning(f"No FDIC cert found for {ticker}.")
        return
    name = get_name(ticker)
    title_bar(f"{name} ({ticker})", "Deposit/Loan Composition")

    # ── Deposit & loan composition (table left, trends right) ──
    # Full SNL-depth mix table (statement engine, Annual/Quarterly toggle) —
    # both trees reconcile to filed totals on the face (2026-07-13 rebuild).
    # The branch map + market-share summary live on Market Analysis ▸ Market
    # Share & Branches; deposit cost/beta charts stay on Deposit Trends.
    from ui.financials_statements import render_deposit_loan_composition
    render_deposit_loan_composition(ticker)


def render_market_share_for_ticker(ticker: str):
    """Deposit market share + branch map only — deposit trends/beta live under the
    Market Analysis ▸ Deposit Trends sub-tab, so this avoids duplicating them."""
    cert = get_fdic_cert(ticker)
    if not cert:
        st.warning(f"No FDIC cert found for {ticker}.")
        return
    title_bar(f"{get_name(ticker)} ({ticker})", "Market Share & Branches")
    _render_deposits_core(cert, get_name(ticker))


def render_deposit_lookup():
    """Render the deposit market share & branch map page with search."""

    title_bar("KSK Investors", "Deposit Market Share & Branch Map", ids_html="")

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
            from ui.states import empty_state
            empty_state('No banks found',
                        'Try a different name')

    if not selected_cert:
        st.info("Search for a bank above to see its branch map and deposit market share.")
        return

    _render_deposits_core(selected_cert, selected_name)


# Store column -> the SOD field names this page renders with.
_STORE_TO_SOD = {"branch_name": "NAMEBR", "city": "CITYBR", "state": "STALPBR",
                 "county": "CNTYNAMB", "deposits": "DEPSUMBR",
                 "stcntybr": "STCNTYBR", "msa_code": "MSABR",
                 "msa_name": "MSANAMB", "lat": "SIMS_LATITUDE",
                 "lng": "SIMS_LONGITUDE", "year": "YEAR"}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_footprint(cert: int):
    """(branches in SOD field names, notes, survey year) — the bank's whole
    footprint from the owner-resolved SOD store: every charter the company
    owns plus branches re-attributed from post-survey mergers (Beacon
    Financial showed 28 of its 150 branches here, 2026-09-16). A cert the
    store doesn't hold falls back to its own live SOD filing (notes empty)."""
    from data.branches_store import get_bank_footprint
    roster, notes = get_bank_footprint(int(cert))
    if roster.empty:
        live = fetch_branches(cert)
        year = (int(pd.to_numeric(live["YEAR"], errors="coerce").max())
                if not live.empty and "YEAR" in live.columns else None)
        return live, [], year
    df = roster.rename(columns=_STORE_TO_SOD)
    for col in ("DEPSUMBR", "SIMS_LATITUDE", "SIMS_LONGITUDE"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["MSABR"] = pd.to_numeric(df["MSABR"], errors="coerce")
    return df, notes, int(roster["year"].iloc[0])


@st.cache_data(ttl=3600, show_spinner=False)
def _market_share(kind: str, key: str, year: int | None) -> pd.DataFrame:
    """Ranked market share for a county (stcntybr) or MSA (msa_code) from the
    SOD store, one row per OWNER — a company's charters and re-attributed
    branches count as one bank, matching every other ranking on the site.
    Columns: owner_key, CERT, TICKER, NAMEFULL, branches, deposits,
    market_share, rank."""
    from data.branches_store import get_banks_by_county, get_banks_by_msa
    df = (get_banks_by_county(str(key), year=year) if kind == "county"
          else get_banks_by_msa(str(key), year=year))
    if df.empty:
        return df
    df = df.rename(columns={"cert": "CERT", "ticker": "TICKER",
                            "bank_name": "NAMEFULL", "n_branches": "branches",
                            "total_deposits": "deposits"})
    df["deposits"] = pd.to_numeric(df["deposits"], errors="coerce").fillna(0)
    df = df.sort_values("deposits", ascending=False).reset_index(drop=True)
    total = df["deposits"].sum()
    df["market_share"] = (df["deposits"] / total * 100) if total > 0 else 0.0
    df["rank"] = range(1, len(df) + 1)
    return df


def _render_deposits_core(selected_cert: int, selected_name: str):
    """Core deposit rendering logic."""

    # ── Load branch data ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(f"{selected_name}")

    with _skeleton():
        branches_df, notes, sod_year = _fetch_footprint(selected_cert)

    if branches_df.empty:
        st.warning("No branch data found for this bank.")
        return

    # Summary stats
    total_deposits = branches_df["DEPSUMBR"].sum()
    num_branches = len(branches_df)
    states = branches_df["STALPBR"].nunique()
    counties = branches_df["STCNTYBR"].nunique()

    # FDIC SOD deposits are in thousands; auto-scale T / B / M
    from utils.formatting import fmt_dollars_from_thousands
    ledger("SUMMARY", [
        ("Branches", f"{num_branches}"),
        ("Total Deposits", fmt_dollars_from_thousands(total_deposits, 2)),
        ("States", f"{states}"),
        ("Counties", f"{counties}"),
    ])
    charters = [n for n in notes if n.get("kind") == "charter"]
    merged = [n for n in notes if n.get("kind") == "merged"]
    if charters:
        st.caption("Includes sibling charter" + ("s " if len(charters) > 1 else " ")
                   + ", ".join(f"{c['name']} ({c['n_branches']} branches)"
                               for c in charters)
                   + " — one company, counted as one bank.")
    if merged:
        parts = ", ".join(f"{m['n_branches']} branches of {m['name']} "
                          f"(merged in {m['date']})" for m in merged)
        st.caption(f"Includes {parts} — the merger closed after the SOD "
                   "survey date, so the survey still files those branches "
                   "under the absorbed charter; they are counted with the "
                   "bank that owns them today.")

    # The subject's identity in owner-grouped rankings: its company ticker,
    # else its (owning) cert — matches branches_store's owner key.
    _tk = (branches_df["ticker"].dropna().astype(str).str.strip()
           if "ticker" in branches_df.columns else pd.Series(dtype=str))
    _tk = _tk[_tk != ""]
    subject_okey = (_tk.iloc[0] if not _tk.empty
                    else f"c{int(branches_df['cert'].iloc[0])}"
                    if "cert" in branches_df.columns else None)
    from utils.formatting import fmt_dollars_from_thousands as _fdt
    _dep_fmt = lambda v: _fdt(v, 2)

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
        from ui.states import empty_state
        empty_state("No geographic data available for this bank's branches")

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

    ksk_table(branch_display, max_height_px=400)
    # Underlying numeric frame (deposits in $K, unformatted)
    table_export(
        branches_df[["NAMEBR", "CITYBR", "STALPBR", "CNTYNAMB", "DEPSUMBR"]],
        f"branch_details_cert{selected_cert}",
        key=f"exp_branch_details_cert{selected_cert}")

    # ── Market share by county ───────────────────────────────────────────
    st.markdown("---")
    st.subheader("Deposit Market Share")

    # Get unique counties for this bank
    county_options = branches_df[["STCNTYBR", "CNTYNAMB", "STALPBR"]].drop_duplicates()
    county_options = county_options.dropna(subset=["STCNTYBR"])
    county_options = county_options[~county_options["STCNTYBR"].astype(str)
                                    .str.strip().isin(["", "0"])]
    county_options["label"] = county_options.apply(
        lambda r: f"{r['CNTYNAMB']} County, {r['STALPBR']}", axis=1
    )

    # Also get unique MSAs
    msa_options = branches_df[["MSABR", "MSANAMB"]].drop_duplicates()
    msa_options = msa_options.dropna(subset=["MSABR"])
    msa_options = msa_options[msa_options["MSABR"] > 0]

    _dl_tabs = ["By County", "By MSA"]
    _dl_sel = lazy_tabs(_dl_tabs, key="deposit")

    if _dl_sel == _dl_tabs[0]:
        if county_options.empty:
            from ui.states import empty_state
            empty_state('No county data available')
        else:
            selected_county = st.selectbox(
                "Select county",
                options=county_options["STCNTYBR"].tolist(),
                format_func=lambda c: county_options[county_options["STCNTYBR"] == c]["label"].iloc[0],
                key="county_select",
            )

            if selected_county:
                with _skeleton():
                    ms_df = _market_share("county", str(selected_county), sod_year)

                if not ms_df.empty:
                    county_label = county_options[county_options["STCNTYBR"] == selected_county]["label"].iloc[0]
                    total_county_deps = ms_df["deposits"].sum()

                    # Highlight the selected bank
                    bank_row = ms_df[ms_df["owner_key"] == subject_okey]
                    if not bank_row.empty:
                        rank = bank_row.iloc[0]["rank"]
                        share = bank_row.iloc[0]["market_share"]
                        deps = bank_row.iloc[0]["deposits"]

                        st.markdown(
                            (f"**{selected_name}** ranks **#{int(rank)}** in {county_label} "
                             f"with **{share:.1f}%** market share "
                             f"({_dep_fmt(deps)} of {_dep_fmt(total_county_deps)} total)"
                             ).replace("$", "\\$")  # don't let $X of $Y render as LaTeX
                        )

                    # Display top banks
                    display = ms_df.head(25).copy()
                    display["deposits_fmt"] = display["deposits"].apply(_dep_fmt)
                    display["market_share_fmt"] = display["market_share"].apply(lambda v: f"{v:.1f}%")

                    show_df = display[["rank", "NAMEFULL", "branches", "deposits_fmt", "market_share_fmt"]].copy()
                    show_df.columns = ["Rank", "Bank", "Branches", "Deposits", "Market Share"]
                    # Universal linking rule: covered participants get a
                    # linked Ticker column (private banks show a blank cell).
                    show_df.insert(1, "Ticker", _linked_tickers(display["TICKER"]))

                    ksk_table(show_df, html_cols=("Ticker",),
                              max_height_px=600)
                    # Underlying numeric frame (deposits $K / share %)
                    table_export(
                        display[["rank", "TICKER", "NAMEFULL", "branches",
                                 "deposits", "market_share"]],
                        f"county_market_share_{selected_county}",
                        key=f"exp_county_market_share_{selected_county}")
                else:
                    st.warning("Could not load market share data for this county.")

    elif _dl_sel == _dl_tabs[1]:
        if msa_options.empty:
            from ui.states import empty_state
            empty_state('No MSA data available')
        else:
            selected_msa = st.selectbox(
                "Select MSA",
                options=msa_options["MSABR"].tolist(),
                format_func=lambda m: msa_options[msa_options["MSABR"] == m]["MSANAMB"].iloc[0],
                key="msa_select",
            )

            if selected_msa:
                with _skeleton():
                    ms_df = _market_share("msa", str(int(selected_msa)), sod_year)

                if not ms_df.empty:
                    msa_label = msa_options[msa_options["MSABR"] == selected_msa]["MSANAMB"].iloc[0]
                    total_msa_deps = ms_df["deposits"].sum()

                    _dep_fmt_msa = _dep_fmt

                    bank_row = ms_df[ms_df["owner_key"] == subject_okey]
                    if not bank_row.empty:
                        rank = bank_row.iloc[0]["rank"]
                        share = bank_row.iloc[0]["market_share"]
                        deps = bank_row.iloc[0]["deposits"]
                        st.markdown(
                            (f"**{selected_name}** ranks **#{int(rank)}** in {msa_label} "
                             f"with **{share:.1f}%** market share "
                             f"({_dep_fmt_msa(deps)} of {_dep_fmt_msa(total_msa_deps)} total)"
                             ).replace("$", "\\$")  # don't let $X of $Y render as LaTeX
                        )

                    display = ms_df.head(25).copy()
                    display["deposits_fmt"] = display["deposits"].apply(_dep_fmt_msa)
                    display["market_share_fmt"] = display["market_share"].apply(lambda v: f"{v:.1f}%")

                    show_df = display[["rank", "NAMEFULL", "branches", "deposits_fmt", "market_share_fmt"]].copy()
                    show_df.columns = ["Rank", "Bank", "Branches", "Deposits", "Market Share"]
                    show_df.insert(1, "Ticker", _linked_tickers(display["TICKER"]))

                    ksk_table(show_df, html_cols=("Ticker",),
                              max_height_px=600)
                    # Underlying numeric frame (deposits $K / share %)
                    table_export(
                        display[["rank", "TICKER", "NAMEFULL", "branches",
                                 "deposits", "market_share"]],
                        f"msa_market_share_{int(selected_msa)}",
                        key=f"exp_msa_market_share_{int(selected_msa)}")
                else:
                    st.warning("Could not load market share data for this MSA.")
