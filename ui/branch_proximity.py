"""Branch Proximity sub-tab (Market Analysis) — SNL plan §11.

Competitor branches within a chosen radius of EACH of the subject bank's
branches, from the SOD branches store: a two-color branch map (subject vs
in-range competitors), a "who competes in range" rollup, and a per-branch
competitor table. Distance math and the bounding-box search live in
data/branches_store (get_branch_competitors / get_nearest_branches) — this
module only renders. Deposits are the June-30 SOD survey, $thousands at
the source; distances are great-circle miles.

Coordinate honesty: SOD rows without lat/lng CANNOT be evaluated for
distance — the store excludes and counts them, and the counts are
captioned here. They are never treated as far away.

Speed: what the page shows is built by analysis/branch_proximity — stored
nightly by jobs/refresh_sod for every universe bank × radius and served while
the branches store is unchanged since; otherwise built live by the same code.
The Export builds the full pair list on click.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_fdic_cert
from utils.formatting import fmt_dollars_from_thousands
from ui.chrome import title_bar, table_export
from ui.components import stat_pill, pill_row
from ui.merger_planning import bank_link


from analysis.branch_proximity import (  # noqa: E402
    RADII, TABLE_MAX_BRANCHES as _TABLE_MAX_BRANCHES,
    TABLE_MAX_COMPETITORS as _TABLE_MAX_COMPETITORS,
    ROLLUP_MAX_BANKS as _ROLLUP_MAX_BANKS, MAP_COLS as _MAP_COLS,
    bank_rollup as _bank_rollup, map_competitors as _map_competitors,
    select_branch_rows as _select_branch_rows, load_view)

_RADII = list(RADII)

# Source row on every SOD export (one constant in ui.export).
from ui.export import SOD_SOURCE as _SOD_SOURCE

# branches_store._COMPETITOR_PAIR_COLS → export headers: subject branch
# (subj_*) then the competitor branch. Deposits are FDIC $thousands (header
# says so); distance is great-circle miles.
_EXPORT_COLS = {
    "subj_brnum": "Subject branch number",
    "subj_branch_name": "Subject branch", "subj_address": "Subject address",
    "subj_city": "Subject city", "subj_state": "Subject state",
    "subj_lat": "Subject latitude", "subj_lng": "Subject longitude",
    "subj_deposits": "Subject deposits ($K)",
    "cert": "Competitor FDIC cert", "brnum": "Competitor branch number",
    "ticker": "Competitor ticker", "bank_name": "Competitor bank",
    "branch_name": "Competitor branch", "address": "Competitor address",
    "city": "Competitor city", "state": "Competitor state",
    "zip": "Competitor ZIP", "deposits": "Competitor deposits ($K)",
    "lat": "Competitor latitude", "lng": "Competitor longitude",
    "serv_type": "Competitor service type (SOD code)",
    "distance_miles": "Distance (mi)",
}
_EXPORT_FORMATS = {
    "Subject branch number": "int", "Subject deposits ($K)": "usd_k",
    "Competitor FDIC cert": "int", "Competitor branch number": "int",
    "Competitor ZIP": "text", "Competitor deposits ($K)": "usd_k",
    "Competitor service type (SOD code)": "text", "Distance (mi)": "num",
}


def _proximity_map(subject_label: str, subj_plot: pd.DataFrame,
                   comp_plot: pd.DataFrame, n_comp_branches: int) -> None:
    """Two-color branch map: subject branches + in-range competitor
    branches, via the shared ui.geo_view map (the codebase's existing
    color-by-column pattern — no new charting code). Subject branches are
    always all plotted; competitors arrive capped (the view's comp_points,
    analysis.branch_proximity.map_competitors) out of n_comp_branches."""
    from ui.geo_view import _render_map

    frames = []
    if not subj_plot.empty:
        frames.append(subj_plot[_MAP_COLS].assign(
            Role=f"{subject_label} branches"))
    if not comp_plot.empty:
        frames.append(comp_plot[_MAP_COLS].assign(
            Role="Competitors in range"))
    if not frames:
        from ui.states import empty_state
        empty_state('No branches with coordinates to map')
        return
    _render_map(pd.concat(frames, ignore_index=True),
                color_col="Role")
    if len(comp_plot) < n_comp_branches:
        st.caption(
            f"Map shows {len(comp_plot):,} of {n_comp_branches:,} competitor "
            f"branches in range — those of the top {_ROLLUP_MAX_BANKS} "
            "competitor banks by in-range deposits; every "
            f"{subject_label} branch with coordinates is plotted. The export "
            "has every in-range pair.")


def _nearest_fallback(cert: int, ticker: str, subj: pd.DataFrame,
                      radius: float) -> None:
    """When the radius returns nothing: the honest nearest-competitor view,
    anchored on the subject's largest branch with coordinates."""
    from data.branches_store import get_nearest_branches

    subj_plot = subj[subj["lat"].notna() & subj["lng"].notna()]
    if subj_plot.empty:
        from ui.states import empty_state
        empty_state('No subject branches carry coordinates in the SOD store — proximity cannot be computed for this bank')
        return
    anchor = subj_plot.sort_values("deposits", ascending=False).iloc[0]
    where = (f"{anchor['branch_name']} ({anchor['city']}, "
             f"{anchor['state']})")
    near = get_nearest_branches(int(cert), float(anchor["lat"]),
                                float(anchor["lng"]), limit=10,
                                max_miles=25.0)
    nb = near["branches"]
    if nb.empty:
        st.info(f"No competitor branches within {radius:g} mi of any "
                f"{ticker} branch — and none within 25 mi of the "
                f"largest branch, {where}.")
        return
    d0 = float(nb["distance_miles"].iloc[0])
    st.info(f"No competitor branches within {radius:g} mi of any "
            f"{ticker} branch. The nearest competitor to the largest "
            f"branch, {where}, is {d0:.1f} mi away:")
    body = ""
    for r in nb.itertuples(index=False):
        body += ("<tr>"
                 f'<td style="text-align:left;">{bank_link(r.bank_name, r.cert, r.ticker)}</td>'
                 f'<td style="text-align:left;">{_h.escape(str(r.branch_name or ""))}</td>'
                 f'<td style="text-align:left;">{_h.escape(str(r.city or ""))}, {_h.escape(str(r.state or ""))}</td>'
                 f'<td style="text-align:right;">{r.distance_miles:.1f} mi</td>'
                 f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.deposits, 1)}</td>'
                 "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Bank</th>'
        '<th style="text-align:left;">Branch</th>'
        '<th style="text-align:left;">Location</th>'
        '<th style="text-align:right;">Distance</th>'
        '<th style="text-align:right;">Deposits</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)


def _rollup_table(ro_top: pd.DataFrame, n_rollup: int, radius: float) -> None:
    """Compact 'who competes in range': one row per competitor bank —
    unique in-range branches + their total deposits (the view's top
    _ROLLUP_MAX_BANKS of n_rollup banks)."""
    st.markdown(f"#### Who competes within {radius:g} miles")
    body = ""
    for r in ro_top.head(_ROLLUP_MAX_BANKS).itertuples(index=False):
        body += ("<tr>"
                 f'<td style="text-align:left;">{bank_link(r.bank_name, r.cert, r.ticker)}</td>'
                 f'<td style="text-align:right;">{int(r.n_branches)}</td>'
                 f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.deposits, 1)}</td>'
                 "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Competitor</th>'
        '<th style="text-align:right;">Branches in Range</th>'
        '<th style="text-align:right;">In-Range Deposits</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)
    if n_rollup > _ROLLUP_MAX_BANKS:
        st.caption(f"Showing the top {_ROLLUP_MAX_BANKS} of {n_rollup:,} "
                   "competitor banks by in-range deposits — the export has "
                   "every in-range pair.")


def _per_branch_table(shown: pd.DataFrame, n_in_range: dict, n_br: int,
                      n_pairs: int) -> None:
    """The per-branch detail: a group-header row per subject branch, then
    its in-range competitors nearest-first — the view's capped slice (the
    largest subject branches, their nearest competitors;
    analysis.branch_proximity.select_branch_rows), with the cap captioned.
    n_in_range: {subj_brnum: pairs in range} for the shown branches; n_br:
    subject branches with any competitor in range; n_pairs: all pairs."""
    st.markdown("#### Competitors by branch")
    body = ""
    for key, g in shown.groupby("subj_brnum", sort=True):
        s = g.iloc[0]
        n = int(n_in_range[int(key)])
        cnt = f"{n:,} in range" + (f", nearest {len(g)} shown"
                                   if len(g) < n else "")
        hdr = (f'{s["subj_branch_name"]} — {s["subj_address"]}, '
               f'{s["subj_city"]}, {s["subj_state"]}')
        body += (f'<tr><td colspan="4" style="text-align:left;'
                 f'font-weight:600;background:var(--bg-surface);">'
                 f'{_h.escape(hdr)} '
                 f'<span style="color:var(--text-muted);font-weight:500;">'
                 f'({cnt})</span></td></tr>')
        for r in g.itertuples(index=False):
            body += ("<tr>"
                     f'<td style="text-align:left;">{bank_link(r.bank_name, r.cert, r.ticker)}</td>'
                     f'<td style="text-align:left;">{_h.escape(str(r.branch_name or ""))} · {_h.escape(str(r.city or ""))}, {_h.escape(str(r.state or ""))}</td>'
                     f'<td style="text-align:right;">{r.distance_miles:.1f} mi</td>'
                     f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.deposits, 1)}</td>'
                     "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Competitor Bank</th>'
        '<th style="text-align:left;">Branch</th>'
        '<th style="text-align:right;">Distance</th>'
        '<th style="text-align:right;">Deposits</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)
    if len(shown) < n_pairs:
        shown_keys = shown["subj_brnum"].unique()
        which = (f"the {_TABLE_MAX_BRANCHES} largest (by branch deposits) of "
                 f"{n_br:,} branches with competitors in range"
                 if n_br > _TABLE_MAX_BRANCHES
                 else f"all {n_br:,} branches with competitors in range")
        each = (f"up to the {_TABLE_MAX_COMPETITORS} nearest competitors each"
                if any(n_in_range[int(k)] > _TABLE_MAX_COMPETITORS
                       for k in shown_keys)
                else "every in-range competitor")
        st.caption(f"Showing {which}, {each} — the export has all "
                   f"{n_pairs:,} pairs.")


def _export_pairs(cert: int, radius: float, year: int | None) -> pd.DataFrame:
    """Every in-range pair for the Export — built when the button is
    clicked (JPM: ~334k pairs at 5 mi), from the live store."""
    from data.branches_store import get_branch_competitors
    return (get_branch_competitors(int(cert), radius_miles=float(radius),
                                   year=year)["pairs"]
            .rename(columns=_EXPORT_COLS))


def render_branch_proximity(ticker: str):
    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Branch Proximity")

    cert = get_fdic_cert(ticker)
    if not cert:
        from ui.states import empty_state
        empty_state('No FDIC certificate mapping for this company — branch proximity needs SOD branch data')
        return

    radius = st.selectbox("Competitor radius (miles)", _RADII, index=2,
                          key="bp_radius")

    with st.spinner("Finding competitor branches in range…"):
        view, _source = load_view(int(cert), float(radius))
    if view["year"] is None:
        st.info("The SOD branches store is empty — the nightly refresh-sod "
                "job fills it.")
        return
    if view["n_subject_branches"] == 0:
        reason = view["reason"] or f"no SOD branches on record for {ticker}"
        st.info(reason[0].upper() + reason[1:] + ".")
        return

    pill_row([
        stat_pill("SUBJECT BRANCHES", f"{view['n_subject_branches']:,}"),
        stat_pill("COMPETITOR BANKS IN RANGE", f"{view['n_comp_banks']:,}"),
        stat_pill("COMPETITOR BRANCHES IN RANGE",
                  f"{view['n_comp_branches']:,}"),
        stat_pill("IN-RANGE COMPETITOR DEPOSITS",
                  fmt_dollars_from_thousands(view["comp_deposits"], 1)
                  if view["n_comp_branches"] else "—"),
        stat_pill("SOD SURVEY", str(view["year"])),
    ], margin="2px 0 10px")

    _proximity_map(ticker, view["subj_points"], view["comp_points"],
                   view["n_comp_branches"])

    if view["n_pairs"] == 0:
        _nearest_fallback(int(cert), ticker, view["subj_points"], float(radius))
    else:
        _rollup_table(view["rollup"], view["n_rollup"], float(radius))
        _per_branch_table(view["shown"], view["shown_n_in_range"],
                          view["n_branches_with_comp"], view["n_pairs"])
        year = view["year"]
        table_export(lambda: _export_pairs(int(cert), float(radius), year),
                     f"branch_proximity_{ticker}_{radius}mi_{year}",
                     key=f"bp_export_{radius}",
                     formats=_EXPORT_FORMATS,
                     provenance={"Page": "Branch Proximity",
                                 "Ticker": ticker,
                                 "Company": get_name(ticker),
                                 "FDIC cert": int(cert),
                                 "Radius (mi)": radius,
                                 "Source": _SOD_SOURCE,
                                 "SOD survey year": year})

    cov = []
    if view["n_subject_missing_coords"]:
        cov.append(f"{view['n_subject_missing_coords']} of the subject's "
                   "branches lack coordinates and are excluded as search "
                   "centers")
    if view["n_competitor_missing_coords"]:
        cov.append(f"{view['n_competitor_missing_coords']:,} branch records "
                   "store-wide (all other banks, this survey year) lack "
                   "coordinates and cannot be evaluated for distance")
    cov_txt = ("; ".join(cov) + " — excluded and counted, never treated as "
               "far away" if cov
               else "every row in scope carries coordinates")
    st.caption(
        f"FDIC Summary of Deposits, {view['year']} survey (June 30 branch "
        "deposits). Distances are great-circle miles between branch "
        f"coordinates. Coordinate coverage: {cov_txt}."
    )
