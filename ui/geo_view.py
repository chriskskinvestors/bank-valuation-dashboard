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

from utils.chart_style import CATEGORICAL_PALETTE

from data.branches_store import (
    list_states, list_msas, list_counties, get_latest_year,
    get_branches_by_state, get_branches_by_msa, get_branches_by_county,
    get_banks_by_state, get_banks_by_msa, get_banks_by_county,
    get_branch_counts_by_bank,
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
def _c_branch_counts(): return get_branch_counts_by_bank()
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


def _bank_option_label(row) -> str:
    """How one institution reads in the bank picker: 'JPM — JPMorgan Chase Bank'
    for a public bank, the plain name for a private one. The ticker leads when
    present because that is how a covered bank is searched for; private banks
    have no ticker and are found by name."""
    name = (row.get("bank_name") or "").strip() or f"Cert {row.get('cert')}"
    tk = row.get("ticker")
    tk = "" if tk is None or (isinstance(tk, float) and pd.isna(tk)) else str(tk).strip()
    return f"{tk} — {name}" if tk else name


def _bank_options(coverage) -> tuple[list[str], dict[str, int]]:
    """(labels deposits-descending, label → cert). Labels are de-duplicated with
    the cert appended, because distinct institutions genuinely share a name
    ("First National Bank") and a multiselect keyed on a repeated label could
    not tell them apart."""
    labels: list[str] = []
    by_label: dict[str, int] = {}
    for row in coverage.to_dict("records"):
        cert = row.get("cert")
        if cert is None or (isinstance(cert, float) and pd.isna(cert)):
            continue
        label = _bank_option_label(row)
        if label in by_label:
            label = f"{label} (cert {int(cert)})"
        if label in by_label:      # same name AND cert twice — nothing to add
            continue
        by_label[label] = int(cert)
        labels.append(label)
    return labels, by_label


def _default_bank_picks(coverage, options: list[str], n: int = 5) -> list[str]:
    """The `n` highest-deposit entries of `coverage` that are actually IN
    `options` — the multiselect's default must be a SUBSET of its options.

    A default outside options is a hard StreamlitAPIException, and it took the
    whole By Bank(s) tab down in production (2026-07-28): the old ticker-keyed
    coverage query collapsed every branch with no ticker into ONE null-ticker row
    whose SUMMED deposits ranked it near the top, `options` dropped it via
    dropna(), and the default (a plain .head(5)) kept the NaN. Same guard
    ui/filings.py applies to its form filter. Filtering before the slice (not
    after) keeps a full n picks instead of silently returning fewer.

    Works on whatever column identifies a row in `options` — labels now, tickers
    before — so the invariant is enforced independently of the identity model."""
    avail = set(options)
    picks: list[str] = []
    for t in coverage:
        if t in avail and t not in picks:
            picks.append(t)
            if len(picks) == n:
                break
    return picks


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


def _render_map(df: pd.DataFrame, title: str = "",
                color_col: str = "ticker", color_label: str = "Ticker"):
    """Render a branch map from a DataFrame of branches.

    `color_col` defaults to ticker (the whole-market tabs plot every bank in a
    state/MSA/county, where one shared colour for the unlisted banks keeps the
    legend readable). The By Bank(s) tab plots a handful of DELIBERATELY chosen
    institutions instead, so it passes a per-bank label — otherwise every private
    bank would land in a single "nan" colour group, indistinguishable from each
    other on the map the user picked them for."""
    if df.empty:
        from ui.states import empty_state
        empty_state('No branches found for the selected filter')
        return

    plot_df = df.dropna(subset=["lat", "lng"]).copy()
    if plot_df.empty:
        from ui.states import empty_state
        empty_state('No branches with geographic coordinates available')
        return

    # Uniform small dots (owner decision 2026-08-03, after a side-by-side
    # mockup): deposit-proportional markers blotted into unreadable blobs in
    # dense metros — JPM's Chicago footprint rendered as one blue mass — and
    # the Market Analysis st.map look (small crisp dots) was preferred. A
    # branch's deposits stay in the hover and in the ranked tables beside each
    # map; the dots answer WHERE, the tables answer HOW MUCH.
    plot_df["deposits_fmt"] = plot_df["deposits"].apply(_fmt_dollars_k)

    if color_col not in plot_df.columns:
        color_col = "ticker"

    center, zoom = _fit_viewport(plot_df["lat"], plot_df["lng"])

    fig = px.scatter_mapbox(
        plot_df,
        lat="lat", lon="lng",
        color=color_col,
        color_discrete_sequence=CATEGORICAL_PALETTE,
        custom_data=["bank_name", "branch_name", "city", "state",
                     "deposits_fmt"],
        center=center,
        zoom=zoom,
        height=620,
        mapbox_style="carto-positron",
        title=title or None,
    )
    # A labelled tooltip instead of plotly's raw "column=value" dump.
    fig.update_traces(
        marker=dict(size=7, opacity=0.9),
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "%{customdata[1]}<br>"
            "%{customdata[2]}, %{customdata[3]}<br>"
            "Deposits: %{customdata[4]}<extra></extra>"
        ),
    )
    # A legend with one entry per bank is unreadable once a market has dozens
    # of them, and it eats the map's width. Past ~15 banks the colours still
    # separate them visually and hover names them, so drop the legend and give
    # the space back to the map — the whole-market tabs are the common case.
    n_series = plot_df[color_col].nunique(dropna=True)
    fig.update_layout(
        margin=dict(l=0, r=0, t=40 if title else 0, b=0),
        showlegend=n_series <= 15,
        legend_title_text=color_label,
        font=dict(family="Inter, -apple-system, system-ui, sans-serif", size=12),
        hoverlabel=dict(bgcolor="#ffffff",
                        font=dict(family="Inter, system-ui, sans-serif",
                                  size=12)),
        legend=dict(bgcolor="rgba(255,255,255,0.85)", borderwidth=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def _fit_viewport(lats, lngs) -> tuple[dict, float]:
    """(center, zoom) framing the plotted branches — what st.map does natively.

    The map used a hardcoded zoom=3 (continental US) for every view, so picking
    a single county, MSA or community bank rendered the whole country with a
    speck on it. Zoom is derived from the bounding box: mapbox halves the
    visible span per level, so log2(span_at_z0 / span) fits it, less a margin
    so markers aren't flush to the edge.
    """
    import math
    try:
        la_min, la_max = float(lats.min()), float(lats.max())
        lo_min, lo_max = float(lngs.min()), float(lngs.max())
    except (TypeError, ValueError):
        return {"lat": 39.5, "lon": -98.35}, 3.0          # continental US
    if not all(map(math.isfinite, (la_min, la_max, lo_min, lo_max))):
        return {"lat": 39.5, "lon": -98.35}, 3.0

    center = {"lat": (la_min + la_max) / 2, "lon": (lo_min + lo_max) / 2}
    lat_span = max(la_max - la_min, 1e-4)
    lon_span = max(lo_max - lo_min, 1e-4)
    zoom = min(math.log2(360.0 / lon_span), math.log2(180.0 / lat_span)) - 0.6
    # Floor keeps a nationwide branch network whole; ceiling stops a
    # single-branch bank zooming to rooftop level, where the map is useless.
    return center, max(3.0, min(zoom, 11.0))


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
            from ui.states import empty_state
            empty_state('No states loaded yet — wait for the refresh job to finish')
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
            from ui.states import empty_state
            empty_state('No MSAs loaded yet — wait for the refresh job')
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
            from ui.states import empty_state
            empty_state('No counties loaded yet — wait for the refresh job')
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
            from ui.states import empty_state
            empty_state('No banks loaded yet')
            return

        # Keyed on CERT, not ticker: every FDIC institution has a cert, only the
        # ~300 covered ones have a ticker, and refresh_sod already stores SOD for
        # all ~4,500 (ticker=None for the rest). Keying on ticker made the ~4,200
        # private banks unselectable here even though their deposits were sitting
        # in the same table the other three tabs already show them from.
        labels, label_to_cert = _bank_options(coverage)
        default_labels = _default_bank_picks(labels, labels)
        selected = st.multiselect(
            "Banks to show on the map",
            options=labels,
            default=default_labels,
            key="geo_banks_select_v2",   # new identity model — don't restore
                                         # v1's stored tickers into label options
            help="Every FDIC-insured institution, public or private — type to "
                 "search by ticker or name.",
        )
        if not selected:
            st.info("Pick one or more banks above.")
            return

        certs = [label_to_cert[s] for s in selected if s in label_to_cert]
        if not certs:
            st.info("Pick one or more banks above.")
            return

        # Pull branches for the selected certs across all states
        from data.branches_store import _q_to_df
        from data.branches_store import _USE_POSTGRES
        params: dict = {"year": year}
        if _USE_POSTGRES:
            params["certs"] = certs
            sql = ("SELECT * FROM branches "
                   "WHERE cert = ANY(:certs) AND year = :year "
                   "ORDER BY deposits DESC")
        else:
            placeholders = ",".join(f":c{i}" for i in range(len(certs)))
            for i, c in enumerate(certs):
                params[f"c{i}"] = c
            sql = (f"SELECT * FROM branches WHERE cert IN ({placeholders}) "
                   f"AND year = :year ORDER BY deposits DESC")
        branches = _q_to_df(sql, params)

        # Summary table per selected bank. Grouped on CERT (dropna=False so a
        # null ticker can't drop the row): two distinct private banks can share a
        # name, and grouping by name would silently merge their deposits.
        if not branches.empty:
            agg = (branches.groupby("cert", dropna=False)
                   .agg(ticker=("ticker", "first"),
                        bank_name=("bank_name", "first"),
                        n_branches=("brnum", "count"),
                        total_deposits=("deposits", "sum"))
                   .reset_index()
                   .sort_values("total_deposits", ascending=False))
            agg["Deposits"] = agg["total_deposits"].apply(_fmt_dollars_k)
            agg = agg.rename(columns={
                "cert": "Cert", "ticker": "Ticker", "bank_name": "Bank",
                "n_branches": "Branches",
            })[["Ticker", "Bank", "Cert", "Branches", "Deposits"]]
            st.markdown(f"### Selected banks — combined {len(branches):,} branches")
            # Display copy gets link URLs (blank for private banks — they have no
            # Company page); the export keeps plain tickers and carries Cert, the
            # only identifier every institution has.
            agg_disp = agg.copy()
            agg_disp["Ticker"] = agg_disp["Ticker"].map(_ticker_url)
            st.dataframe(agg_disp, use_container_width=True, hide_index=True,
                          height=min(280, 38 * (len(agg) + 1) + 4),
                          column_config=_ticker_linkcol())
            table_export(agg, "selected_banks_branch_summary",
                         key="exp_selected_banks_branch_summary")

        # Colour each SELECTED institution separately — ticker when it has one,
        # otherwise its name, so private banks are distinguishable from each
        # other rather than sharing one "nan" group. (An empty frame falls
        # through to _render_map's own "no branches" message.)
        if not branches.empty:
            branches = branches.copy()
            branches["Bank"] = [
                _bank_option_label(r) for r in branches.to_dict("records")]
        _render_map(branches, color_col="Bank", color_label="Bank")
