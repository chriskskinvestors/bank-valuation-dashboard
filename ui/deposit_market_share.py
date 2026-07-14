"""U.S. Deposit Market Share sub-tab (Market Analysis) — SNL plan §11.

The all-markets view the existing picker tab doesn't give: every county and
MSA the subject bank operates in as one row — deposits, market total, share,
rank, bank count, market HHI, and the largest competitor — computed from the
universe-wide SOD branches store (refresh-sod keeps it current; deposits are
the June-30 SOD survey, $thousands at the source).
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_fdic_cert
from utils.formatting import fmt_dollars_from_thousands
from ui.chrome import title_bar, table_export


def _share_rows(df: pd.DataFrame, subject_cert: int) -> list[dict]:
    """Per-market share rows from the participants frame (market × bank).

    HHI = Σ (share_i %)² over ALL banks in the market (0–10,000 scale, the
    DOJ convention). Top competitor = the largest bank that isn't the
    subject. Markets where the subject has no deposits recorded, or with a
    non-positive total, are skipped — never a fabricated share.
    """
    rows: list[dict] = []
    if df is None or df.empty:
        return rows
    # Prod Postgres returns SUM(BIGINT) as NUMERIC → decimal.Decimal objects
    # (sqlite returns ints); Decimal can't mix with float in the share/HHI
    # math below, so coerce once at the boundary.
    df = df.assign(deposits=df["deposits"].astype(float))
    for key, m in df.groupby("market_key", sort=False):
        total = m["deposits"].sum()
        if not total or total <= 0:
            continue
        subj = m[m["cert"] == subject_cert]
        if subj.empty or not subj["deposits"].iloc[0]:
            continue
        subj_dep = float(subj["deposits"].iloc[0])
        ranked = m.sort_values("deposits", ascending=False).reset_index(drop=True)
        rank = int(ranked.index[ranked["cert"] == subject_cert][0]) + 1
        hhi = float(((m["deposits"] / total * 100.0) ** 2).sum())
        comp = ranked[ranked["cert"] != subject_cert]
        top_name = comp["bank_name"].iloc[0] if not comp.empty else None
        top_share = (float(comp["deposits"].iloc[0]) / total * 100.0
                     if not comp.empty else None)
        try:
            top_cert = int(comp["cert"].iloc[0]) if not comp.empty else None
        except (TypeError, ValueError):
            top_cert = None
        rows.append({
            "market_key": key,
            "market": m["market_label"].iloc[0],
            "subj_branches": int(subj["n_branches"].iloc[0]),
            "subj_deposits_k": subj_dep,
            "market_total_k": float(total),
            "share_pct": subj_dep / total * 100.0,
            "rank": rank,
            "n_banks": int(m["cert"].nunique()),
            "hhi": hhi,
            "top_competitor": top_name,
            "top_competitor_share_pct": top_share,
            "top_competitor_cert": top_cert,
        })
    rows.sort(key=lambda r: -r["subj_deposits_k"])
    return rows


def _render_market_table(rows: list[dict], heading: str, key: str):
    st.markdown(f"#### {heading}")
    if not rows:
        st.info("No markets with recorded deposits for this bank at this level.")
        return
    from data.bank_universe import cert_ticker_map
    cmap = cert_ticker_map()
    body = ""
    for r in rows:
        # Universal linking rule: a covered competitor's name deep-links to
        # its Company page; a private bank links to its FDIC profile.
        comp_name = _h.escape(r["top_competitor"]) if r["top_competitor"] else ""
        tk = cmap.get(r.get("top_competitor_cert") or 0)
        if comp_name and tk:
            comp_name = (f'<a href="?bank={_h.escape(tk, quote=True)}" '
                         f'target="_self" title="Open {_h.escape(tk)} company '
                         f'page">{comp_name}</a>')
        elif comp_name and r.get("top_competitor_cert"):
            comp_name = (f'<a href="https://banks.data.fdic.gov/bankfind-suite/'
                         f'bankfind/details/{int(r["top_competitor_cert"])}" '
                         f'target="_blank" rel="noopener">{comp_name}</a>')
        comp = (f'{comp_name} ({r["top_competitor_share_pct"]:.1f}%)'
                if r["top_competitor"] else "—")
        body += ("<tr>"
                 f'<td style="text-align:left;">{_h.escape(str(r["market"]))}</td>'
                 f'<td style="text-align:right;">{r["subj_branches"]}</td>'
                 f'<td style="text-align:right;">{fmt_dollars_from_thousands(r["subj_deposits_k"], 1)}</td>'
                 f'<td style="text-align:right;">{fmt_dollars_from_thousands(r["market_total_k"], 1)}</td>'
                 f'<td style="text-align:right;">{r["share_pct"]:.1f}%</td>'
                 f'<td style="text-align:right;">#{r["rank"]} of {r["n_banks"]}</td>'
                 f'<td style="text-align:right;">{r["hhi"]:,.0f}</td>'
                 f'<td style="text-align:left;">{comp}</td>'
                 "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Market</th>'
        '<th style="text-align:right;">Branches</th>'
        '<th style="text-align:right;">Deposits</th>'
        '<th style="text-align:right;">Market Total</th>'
        '<th style="text-align:right;">Share</th>'
        '<th style="text-align:right;">Rank</th>'
        '<th style="text-align:right;">HHI</th>'
        '<th style="text-align:left;">Top Competitor</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)
    table_export(pd.DataFrame(rows), f"deposit_share_{key}",
                 key=f"exp_depshare_{key}")


def render_deposit_market_share(ticker: str):
    from data.branches_store import get_market_participants, get_latest_year

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Deposit Market Share")

    cert = get_fdic_cert(ticker)
    if not cert:
        st.info("No FDIC certificate mapping for this company — deposit "
                "market share needs SOD branch data.")
        return

    with st.spinner("Aggregating deposit markets from the SOD store…"):
        county = get_market_participants(cert, kind="county")
        msa = get_market_participants(cert, kind="msa")
        year = get_latest_year()

    county_rows = _share_rows(county, cert)
    msa_rows = _share_rows(msa, cert)
    if not county_rows and not msa_rows:
        st.info("No branch/deposit records for this bank in the SOD store "
                "yet — the store fills from the nightly refresh-sod job.")
        return

    _render_market_table(county_rows, "By County", f"county_{ticker}")
    _render_market_table(msa_rows, "By MSA", f"msa_{ticker}")

    st.caption(
        f"FDIC Summary of Deposits, {year or 'latest'} survey (June 30 "
        "deposits). HHI is the market's Herfindahl index over all insured "
        "institutions (0–10,000; DOJ screens: <1,500 unconcentrated, "
        ">2,500 highly concentrated). Rank counts insured institutions "
        "with branches in the market."
    )
