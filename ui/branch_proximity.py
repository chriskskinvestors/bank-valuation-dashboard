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


_RADII = [1, 3, 5, 10]
_MAP_COLS = ["bank_name", "branch_name", "city", "state", "deposits",
             "lat", "lng"]


def _proximity_map(subject_label: str, subj: pd.DataFrame,
                   uniq_comp: pd.DataFrame) -> None:
    """Two-color branch map: subject branches + in-range competitor
    branches, via the shared ui.geo_view map (the codebase's existing
    color-by-column pattern — no new charting code)."""
    from ui.geo_view import _render_map

    subj_plot = subj[subj["lat"].notna() & subj["lng"].notna()]
    frames = []
    if not subj_plot.empty:
        frames.append(subj_plot[_MAP_COLS].assign(
            Role=f"{subject_label} branches"))
    if not uniq_comp.empty:
        frames.append(uniq_comp[_MAP_COLS].assign(
            Role="Competitors in range"))
    if not frames:
        from ui.states import empty_state
        empty_state('No branches with coordinates to map')
        return
    _render_map(pd.concat(frames, ignore_index=True),
                color_col="Role", color_label="")


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


def _rollup_table(uniq_comp: pd.DataFrame, radius: float) -> None:
    """Compact 'who competes in range': one row per competitor bank —
    unique in-range branches + their total deposits."""
    st.markdown(f"#### Who competes within {radius:g} miles")
    ro = (uniq_comp.groupby("cert", dropna=False)
          .agg(ticker=("ticker", "first"), bank_name=("bank_name", "first"),
               n_branches=("brnum", "count"), deposits=("deposits", "sum"))
          .reset_index()
          .sort_values("deposits", ascending=False))
    body = ""
    for r in ro.itertuples(index=False):
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


def _per_branch_table(pairs: pd.DataFrame) -> None:
    """The per-branch detail: a group-header row per subject branch, then
    its in-range competitors nearest-first (pairs is already sorted by
    (subj_brnum, distance))."""
    st.markdown("#### Competitors by branch")
    body = ""
    for _key, g in pairs.groupby("subj_brnum", sort=True):
        s = g.iloc[0]
        hdr = (f'{s["subj_branch_name"]} — {s["subj_address"]}, '
               f'{s["subj_city"]}, {s["subj_state"]}')
        body += (f'<tr><td colspan="4" style="text-align:left;'
                 f'font-weight:600;background:var(--bg-surface);">'
                 f'{_h.escape(hdr)} '
                 f'<span style="color:var(--text-muted);font-weight:500;">'
                 f'({len(g)} in range)</span></td></tr>')
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


def render_branch_proximity(ticker: str):
    from data.branches_store import get_branch_competitors, _q_to_df

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Branch Proximity")

    cert = get_fdic_cert(ticker)
    if not cert:
        from ui.states import empty_state
        empty_state('No FDIC certificate mapping for this company — branch proximity needs SOD branch data')
        return

    radius = st.selectbox("Competitor radius (miles)", _RADII, index=2,
                          key="bp_radius")

    with st.spinner("Finding competitor branches in range…"):
        res = get_branch_competitors(int(cert), radius_miles=float(radius))
    if res["year"] is None:
        st.info("The SOD branches store is empty — the nightly refresh-sod "
                "job fills it.")
        return
    if res["n_subject_branches"] == 0:
        reason = res["reason"] or f"no SOD branches on record for {ticker}"
        st.info(reason[0].upper() + reason[1:] + ".")
        return

    pairs = res["pairs"]
    # A competitor branch in range of two subject branches appears in two
    # pairs — the rollup and map count each BRANCH once, keyed (cert, brnum).
    uniq_comp = pairs.drop_duplicates(subset=["cert", "brnum"])
    subj = _q_to_df(
        "SELECT * FROM branches WHERE cert = :cert AND year = :year",
        {"cert": int(cert), "year": int(res["year"])})

    pill_row([
        stat_pill("SUBJECT BRANCHES", f"{res['n_subject_branches']:,}"),
        stat_pill("COMPETITOR BANKS IN RANGE",
                  f"{uniq_comp['cert'].nunique():,}"),
        stat_pill("COMPETITOR BRANCHES IN RANGE", f"{len(uniq_comp):,}"),
        stat_pill("IN-RANGE COMPETITOR DEPOSITS",
                  fmt_dollars_from_thousands(float(uniq_comp["deposits"]
                                                   .astype(float).sum()), 1)
                  if not uniq_comp.empty else "—"),
        stat_pill("SOD SURVEY", str(res["year"])),
    ], margin="2px 0 10px")

    _proximity_map(ticker, subj, uniq_comp)

    if pairs.empty:
        _nearest_fallback(int(cert), ticker, subj, float(radius))
    else:
        _rollup_table(uniq_comp, float(radius))
        _per_branch_table(pairs)
        table_export(pairs, f"branch_proximity_{ticker}_{radius}mi",
                     key=f"bp_export_{radius}")

    cov = []
    if res["n_subject_missing_coords"]:
        cov.append(f"{res['n_subject_missing_coords']} of the subject's "
                   "branches lack coordinates and are excluded as search "
                   "centers")
    if res["n_competitor_missing_coords"]:
        cov.append(f"{res['n_competitor_missing_coords']:,} branch records "
                   "store-wide (all other banks, this survey year) lack "
                   "coordinates and cannot be evaluated for distance")
    cov_txt = ("; ".join(cov) + " — excluded and counted, never treated as "
               "far away" if cov
               else "every row in scope carries coordinates")
    st.caption(
        f"FDIC Summary of Deposits, {res['year']} survey (June 30 branch "
        "deposits). Distances are great-circle miles between branch "
        f"coordinates. Coordinate coverage: {cov_txt}."
    )
