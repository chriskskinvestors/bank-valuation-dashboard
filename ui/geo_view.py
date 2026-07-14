"""
Geographic view — multi-bank branch map + state/MSA bank lookup.

Two surfaces sharing one map:

1. State / MSA picker  → highlights branches in that geography +
   shows a ranked table of banks operating there with deposits + branch counts.

2. Multi-bank picker   → cross-section across selected tickers,
   color-coded on the map so you can see overlap and concentration.

Data source: the `branches` table populated by jobs/refresh_sod.py
(nightly Cloud Run Job). UI is read-only against Postgres so it's fast
regardless of FDIC API health.
"""

from __future__ import annotations
import streamlit as st
import pandas as pd
import plotly.express as px

from data.branches_store import (
    list_states, list_msas, list_counties, get_latest_year,
    get_branches_by_state, get_branches_by_msa, get_branches_by_county,
    get_banks_by_state, get_banks_by_msa, get_banks_by_county,
    get_branch_counts_by_ticker,
)
from ui.chrome import table_export, lazy_tabs


# ── Cached read wrappers ────────────────────────────────────────────────────
# Each Geographic pane re-queries Postgres on EVERY rerun (selectbox change,
# public-only checkbox, map zoom). st.cache_data keyed by region + year means
# only a genuine region/year change re-hits the DB; repeat renders are free.
@st.cache_data(ttl=900, show_spinner=False)
def _c_states(): return list_states()
@st.cache_data(ttl=900, show_spinner=False)
def _c_msas(): return list_msas()
@st.cache_data(ttl=900, show_spinner=False)
def _c_counties(): return list_counties()
@st.cache_data(ttl=900, show_spinner=False)
def _c_branch_counts(): return get_branch_counts_by_ticker()
@st.cache_data(ttl=900, show_spinner=False)
def _c_branches_state(state, year): return get_branches_by_state(state, year=year)
@st.cache_data(ttl=900, show_spinner=False)
def _c_banks_state(state, year): return get_banks_by_state(state, year=year)
@st.cache_data(ttl=900, show_spinner=False)
def _c_branches_msa(code, year): return get_branches_by_msa(code, year=year)
@st.cache_data(ttl=900, show_spinner=False)
def _c_banks_msa(code, year): return get_banks_by_msa(code, year=year)
@st.cache_data(ttl=900, show_spinner=False)
def _c_branches_county(fips, year): return get_branches_by_county(fips, year=year)
@st.cache_data(ttl=900, show_spinner=False)
def _c_banks_county(fips, year): return get_banks_by_county(fips, year=year)


# Shared universal-linking helpers (ui.chrome) — private banks (no ticker)
# render a blank cell; link cells open the Company page in a new tab.
from ui.chrome import ticker_company_url as _ticker_url
from ui.chrome import ticker_linkcol as _ticker_linkcol


def _fmt_dollars_k(thousands: float | int | None) -> str:
    """SOD deposits are in $thousands. Format with auto B/M/K scale."""
    if thousands is None or pd.isna(thousands):
        return "—"
    v = float(thousands) * 1000  # convert thousands to dollars
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.1f}M"
    if v >= 1e3:  return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _render_map(df: pd.DataFrame, title: str = ""):
    """Render a branch map from a DataFrame of branches."""
    if df.empty:
        st.info("No branches found for the selected filter.")
        return

    plot_df = df.dropna(subset=["lat", "lng"]).copy()
    if plot_df.empty:
        st.info("No branches with geographic coordinates available.")
        return

    # Size by deposits (clipped + log-scaled so big-bank branches don't
    # overwhelm small-bank ones visually)
    import numpy as np
    plot_df["size"] = np.log1p(plot_df["deposits"].clip(lower=0))
    plot_df["size"] = (plot_df["size"] / plot_df["size"].max() * 25 + 5).fillna(5)
    plot_df["deposits_fmt"] = plot_df["deposits"].apply(_fmt_dollars_k)

    hover = ["ticker", "bank_name", "branch_name", "address", "city",
             "state", "msa_name", "deposits_fmt"]

    fig = px.scatter_mapbox(
        plot_df,
        lat="lat", lon="lng",
        size="size",
        color="ticker",
        hover_data=hover,
        zoom=3,
        height=620,
        mapbox_style="carto-positron",
        title=title or None,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=40 if title else 0, b=0),
                       legend_title_text="Ticker")
    st.plotly_chart(fig, use_container_width=True)


def render_geo_view():
    """Main entry point — wired from app.py."""
    st.markdown(
        '<div class="dashboard-header">'
        '<h1>Geographic</h1>'
        '<p>Multi-bank branch map + state/MSA deposit rankings</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    year = get_latest_year()
    if year is None:
        st.warning(
            "No branch data ingested yet. The nightly **refresh-sod** Cloud Run Job "
            "populates this view. Manual run: `gcloud run jobs execute refresh-sod`."
        )
        return

    st.caption(f"Data as of FDIC Summary of Deposits, year {year}.")

    # lazy_tabs (not st.tabs): render ONLY the active geography pane. st.tabs ran
    # all four — State, MSA, County, Bank(s) — every rerun, each doing its own
    # Postgres branch query + mapbox build (~2.3s warm). (docs/PERFORMANCE.md
    # lever 1.)
    _geo_tab = lazy_tabs(["By State", "By MSA", "By County", "By Bank(s)"],
                         key="geo")

    # ───────── State view ─────────
    if _geo_tab == "By State":
        states = _c_states()
        if not states:
            st.info("No states loaded yet — wait for the refresh job to finish.")
        else:
            col1, col2 = st.columns([1, 3])
            with col1:
                state = st.selectbox("State", states, key="geo_state",
                                      index=states.index("CA") if "CA" in states else 0)
            with col2:
                st.write("")  # spacing

            branches = _c_branches_state(state, year)
            banks = _c_banks_state(state, year)

            # Public-only toggle (state view) — defaults to OFF so users
            # see the full deposit landscape including private/community banks.
            public_only_state = st.checkbox(
                "Public-traded banks only", value=False,
                key=f"geo_state_public_{state}",
                help="Filter to banks with a public ticker. Off = include "
                      "all FDIC-insured banks operating in the state.",
            )
            if public_only_state and not banks.empty:
                banks_disp = banks[banks["ticker"].notna() & (banks["ticker"] != "")]
                branches_disp = branches[branches["ticker"].notna() & (branches["ticker"] != "")]
            else:
                banks_disp = banks
                branches_disp = branches

            st.markdown(f"### Banks operating in {state} — {len(banks_disp)} institutions")
            if not banks_disp.empty:
                table = banks_disp.copy()
                # Public banks' tickers deep-link to their Company page;
                # private banks (ticker=None) render a blank cell.
                table["ticker"] = table["ticker"].map(_ticker_url)
                table["Deposits"] = table["total_deposits"].apply(_fmt_dollars_k)
                table = table.rename(columns={
                    "ticker": "Ticker", "bank_name": "Bank",
                    "n_branches": "Branches",
                })[["Ticker", "Bank", "Branches", "Deposits"]]
                st.dataframe(table, use_container_width=True, hide_index=True,
                              height=min(500, 38 * (len(table) + 1) + 4),
                              column_config=_ticker_linkcol())
                # Underlying numeric frame (deposits in $K, unformatted)
                table_export(banks_disp, f"banks_by_state_{state}",
                             key=f"exp_banks_by_state_{state}")

            st.markdown(f"### Branch map — {len(branches_disp):,} branches")
            _render_map(branches_disp)

    # ───────── MSA view ─────────
    elif _geo_tab == "By MSA":
        msas_df = _c_msas()
        if msas_df.empty:
            st.info("No MSAs loaded yet — wait for the refresh job.")
        else:
            opts = msas_df.to_dict("records")
            opts.sort(key=lambda r: r["msa_name"])
            labels = [f"{r['msa_name']}" for r in opts]
            label_to_code = {f"{r['msa_name']}": r["msa_code"] for r in opts}

            col1, col2 = st.columns([2, 2])
            with col1:
                default_idx = next(
                    (i for i, r in enumerate(opts) if "New York" in r["msa_name"]), 0,
                )
                msa_label = st.selectbox("MSA", labels, key="geo_msa", index=default_idx)
            msa_code = label_to_code[msa_label]

            branches = _c_branches_msa(msa_code, year)
            banks = _c_banks_msa(msa_code, year)

            public_only_msa = st.checkbox(
                "Public-traded banks only", value=False,
                key=f"geo_msa_public_{msa_code}",
                help="Off = include all FDIC-insured banks in the MSA.",
            )
            if public_only_msa and not banks.empty:
                banks_disp = banks[banks["ticker"].notna() & (banks["ticker"] != "")]
                branches_disp = branches[branches["ticker"].notna() & (branches["ticker"] != "")]
            else:
                banks_disp = banks
                branches_disp = branches

            st.markdown(f"### Banks operating in {msa_label} — {len(banks_disp)} institutions")
            if not banks_disp.empty:
                table = banks_disp.copy()
                table["ticker"] = table["ticker"].map(_ticker_url)
                table["Deposits"] = table["total_deposits"].apply(_fmt_dollars_k)
                table = table.rename(columns={
                    "ticker": "Ticker", "bank_name": "Bank",
                    "n_branches": "Branches",
                })[["Ticker", "Bank", "Branches", "Deposits"]]
                st.dataframe(table, use_container_width=True, hide_index=True,
                              height=min(500, 38 * (len(table) + 1) + 4),
                              column_config=_ticker_linkcol())
                # Underlying numeric frame (deposits in $K, unformatted)
                table_export(banks_disp, f"banks_by_msa_{msa_code}",
                             key=f"exp_banks_by_msa_{msa_code}")

            st.markdown(f"### Branch map — {len(branches_disp):,} branches")
            _render_map(branches_disp)

    # ───────── County view ─────────
    elif _geo_tab == "By County":
        counties_df = _c_counties()
        if counties_df.empty:
            st.info("No counties loaded yet — wait for the refresh job.")
        else:
            opts = counties_df.to_dict("records")
            labels = [f"{r['county']}, {r['state']}" for r in opts]
            label_to_fips = {lbl: r["stcntybr"] for lbl, r in zip(labels, opts)}

            col1, col2 = st.columns([2, 2])
            with col1:
                default_idx = next(
                    (i for i, r in enumerate(opts)
                     if r["county"] and "Los Angeles" in r["county"]), 0,
                )
                county_label = st.selectbox("County", labels, key="geo_county",
                                            index=default_idx)
            stcntybr = label_to_fips[county_label]

            branches = _c_branches_county(stcntybr, year)
            banks = _c_banks_county(stcntybr, year)

            public_only_county = st.checkbox(
                "Public-traded banks only", value=False,
                key=f"geo_county_public_{stcntybr}",
                help="Off = include all FDIC-insured banks in the county.",
            )
            if public_only_county and not banks.empty:
                banks_disp = banks[banks["ticker"].notna() & (banks["ticker"] != "")]
                branches_disp = branches[branches["ticker"].notna() & (branches["ticker"] != "")]
            else:
                banks_disp = banks
                branches_disp = branches

            st.markdown(f"### Banks operating in {county_label} — {len(banks_disp)} institutions")
            if not banks_disp.empty:
                table = banks_disp.copy()
                table["ticker"] = table["ticker"].map(_ticker_url)
                table["Deposits"] = table["total_deposits"].apply(_fmt_dollars_k)
                table = table.rename(columns={
                    "ticker": "Ticker", "bank_name": "Bank",
                    "n_branches": "Branches",
                })[["Ticker", "Bank", "Branches", "Deposits"]]
                st.dataframe(table, use_container_width=True, hide_index=True,
                              height=min(500, 38 * (len(table) + 1) + 4),
                              column_config=_ticker_linkcol())
                table_export(banks_disp, f"banks_by_county_{stcntybr}",
                             key=f"exp_banks_by_county_{stcntybr}")

            st.markdown(f"### Branch map — {len(branches_disp):,} branches")
            _render_map(branches_disp)

    # ───────── Multi-bank view ─────────
    elif _geo_tab == "By Bank(s)":
        coverage = _c_branch_counts()
        if coverage.empty:
            st.info("No banks loaded yet.")
            return

        all_tickers = sorted(coverage["ticker"].dropna().unique().tolist())
        # default to top-5 by deposits
        top5 = coverage.head(5)["ticker"].tolist()
        selected = st.multiselect(
            "Banks to show on the map",
            options=all_tickers,
            default=top5,
            key="geo_banks_select",
        )
        if not selected:
            st.info("Pick one or more banks above.")
            return

        # Pull branches for the selected tickers across all states
        # (use the by_state query with no state restriction by querying each)
        from data.branches_store import _q_to_df
        from data.branches_store import _USE_POSTGRES
        params: dict = {}
        if _USE_POSTGRES:
            params["tickers"] = [t.upper() for t in selected]
            params["year"] = year
            sql = ("SELECT * FROM branches "
                   "WHERE ticker = ANY(:tickers) AND year = :year "
                   "ORDER BY deposits DESC")
        else:
            placeholders = ",".join(f":t{i}" for i in range(len(selected)))
            for i, t in enumerate(selected):
                params[f"t{i}"] = t.upper()
            params["year"] = year
            sql = (f"SELECT * FROM branches WHERE ticker IN ({placeholders}) "
                   f"AND year = :year ORDER BY deposits DESC")
        branches = _q_to_df(sql, params)

        # Summary table per selected bank
        if not branches.empty:
            agg = (branches.groupby(["ticker", "bank_name"])
                   .agg(n_branches=("brnum", "count"),
                        total_deposits=("deposits", "sum"))
                   .reset_index()
                   .sort_values("total_deposits", ascending=False))
            agg["Deposits"] = agg["total_deposits"].apply(_fmt_dollars_k)
            agg = agg.rename(columns={
                "ticker": "Ticker", "bank_name": "Bank",
                "n_branches": "Branches",
            })[["Ticker", "Bank", "Branches", "Deposits"]]
            st.markdown(f"### Selected banks — combined {len(branches):,} branches")
            # Display copy gets link URLs; the export keeps plain tickers.
            agg_disp = agg.copy()
            agg_disp["Ticker"] = agg_disp["Ticker"].map(_ticker_url)
            st.dataframe(agg_disp, use_container_width=True, hide_index=True,
                          height=min(280, 38 * (len(agg) + 1) + 4),
                          column_config=_ticker_linkcol())
            table_export(agg, "selected_banks_branch_summary",
                         key="exp_selected_banks_branch_summary")

        _render_map(branches)
