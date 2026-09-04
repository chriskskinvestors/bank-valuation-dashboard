"""Merger Planning (HHI) sub-tab (Market Analysis) — SNL plan §11.

Pro-forma DOJ merger screening for the company-page bank against ANY FDIC
institution (public or private): every county / MSA where both take SOD
deposits, with each party's deposits + share, pre/post-merger HHI, delta,
concentration band, and the DOJ screen flag. All math lives in
analysis/merger_hhi (the shared HHI layer) — this module only renders.
Deposits are the June-30 SOD survey, $thousands at the source.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_fdic_cert
from utils.formatting import fmt_dollars_from_thousands
from ui.chrome import title_bar, table_export, lazy_tabs
from ui.components import stat_pill, pill_row


_BAND_SHORT = {"highly concentrated": "High",
               "moderately concentrated": "Moderate",
               "unconcentrated": "Unconc."}


def bank_link(name, cert, ticker) -> str:
    """Escaped bank-name cell under the universal linking rule: a covered
    bank deep-links to its Company page, a private bank to its FDIC BankFind
    profile — never dead blue text. Shared with ui/branch_proximity."""
    esc = _h.escape(str(name)) if name else "—"
    tk = "" if ticker is None or (isinstance(ticker, float) and pd.isna(ticker)) \
        else str(ticker).strip()
    if tk and tk.lower() != "nan":
        return (f'<a href="?bank={_h.escape(tk, quote=True)}" target="_self" '
                f'title="Open {_h.escape(tk)} company page">{esc}</a>')
    if cert:
        return (f'<a href="https://banks.data.fdic.gov/bankfind-suite/'
                f'bankfind/details/{int(cert)}" target="_blank" '
                f'rel="noopener">{esc}</a>')
    return esc


def _partner_picker(subject_cert: int):
    """(cert, name, ticker) of the picked partner, or (None, None, None).

    Options = every FDIC institution in the SOD store (public AND private —
    private banks are real merger counterparties), deposits-descending, via
    the shared cert-keyed label convention (ui.geo_view._bank_options).
    No selection → no further queries run."""
    from data.branches_store import get_branch_counts_by_bank
    from ui.geo_view import _bank_options

    coverage = get_branch_counts_by_bank()
    if coverage is None or coverage.empty:
        st.info("The SOD branches store is empty — the nightly refresh-sod "
                "job fills it.")
        return None, None, None
    coverage = coverage[pd.to_numeric(coverage["cert"], errors="coerce")
                        != subject_cert]
    labels, by_label = _bank_options(coverage)
    label = st.selectbox(
        "Merger partner", options=labels, index=None,
        placeholder="Pick a merger partner (any FDIC institution)…",
        help="Every FDIC-insured institution with SOD branches, public or "
             "private — type to search by ticker or name.",
        key="mp_partner")
    if not label:
        return None, None, None
    cert = by_label[label]
    row = coverage[pd.to_numeric(coverage["cert"], errors="coerce")
                   == cert].iloc[0]
    tk = row.get("ticker")
    tk = None if tk is None or (isinstance(tk, float) and pd.isna(tk)) \
        else str(tk).strip() or None
    return int(cert), (row.get("bank_name") or label), tk


def _skipped_note(skipped: list[dict]) -> None:
    if not skipped:
        return
    shown = "; ".join(f'{s["market_label"]} — {s["reason"]}'
                      for s in skipped[:5])
    more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
    st.caption(f"{len(skipped)} market(s) skipped rather than shown with a "
               f"fabricated share: {shown}{more}.")


def render_merger_planning(ticker: str):
    from analysis.merger_hhi import pro_forma_hhi

    title_bar(f"{get_name(ticker) or ticker} ({ticker})",
              "Merger Planning (HHI)")

    cert = get_fdic_cert(ticker)
    if not cert:
        from ui.states import empty_state
        empty_state('No FDIC certificate mapping for this company — merger screening needs SOD branch data')
        return

    st.caption("Screen a hypothetical combination on FDIC Summary-of-"
               "Deposits shares: every market where both banks take "
               "deposits, with the DOJ HHI screen applied per market.")

    p_cert, p_name, p_tk = _partner_picker(int(cert))
    if not p_cert:
        st.info("Pick a merger partner to screen the combination.")
        return

    kind_label = lazy_tabs(["By County", "By MSA"], key="mp_kind")
    kind = "county" if kind_label == "By County" else "msa"

    with st.spinner("Screening overlapping deposit markets…"):
        res = pro_forma_hhi(int(cert), int(p_cert), kind=kind)

    df = res["markets"]
    if df.empty:
        reason = res["reason"] or "no overlapping markets"
        st.info(reason[0].upper() + reason[1:] + ".")
        _skipped_note(res["skipped"])
        return

    n_flagged = int(df["screen_flag"].sum())
    combined_k = float((df["deposits_a"] + df["deposits_b"]).sum())
    flag_val = (f'<span style="color:var(--danger,#dc2626);">{n_flagged}'
                f'</span>' if n_flagged else "0")
    pill_row([
        stat_pill("OVERLAPPING MARKETS", f"{len(df):,}"),
        stat_pill("DOJ-FLAGGED", flag_val),
        stat_pill("COMBINED DEPOSITS IN OVERLAP",
                  fmt_dollars_from_thousands(combined_k, 1)),
        stat_pill("SOD SURVEY", str(res["year"])),
    ], margin="2px 0 10px")

    subj_link = bank_link(get_name(ticker) or ticker, int(cert), ticker)
    partner_link = bank_link(p_name, int(p_cert), p_tk)
    st.markdown(f'<div style="margin:0 0 8px;font-size:var(--fs-sm);">'
                f'<strong>A</strong> = {subj_link} · '
                f'<strong>B</strong> = {partner_link}</div>',
                unsafe_allow_html=True)

    body = ""
    for r in df.itertuples(index=False):
        if r.screen_flag:
            flag_cell = (f'<span style="color:var(--danger,#dc2626);'
                         f'font-weight:600;" '
                         f'title="{_h.escape(r.screen_reason or "")}">'
                         f'Flagged</span>')
        else:
            flag_cell = "—"
        band = str(r.concentration_post)
        band_cell = (f'<span title="{_h.escape(band)}">'
                     f'{_BAND_SHORT.get(band, band)}</span>')
        body += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(str(r.market_label))}</td>'
            f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.deposits_a, 1)}</td>'
            f'<td style="text-align:right;">{r.share_a_pct:.1f}%</td>'
            f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.deposits_b, 1)}</td>'
            f'<td style="text-align:right;">{r.share_b_pct:.1f}%</td>'
            f'<td style="text-align:right;">{r.combined_share_pct:.1f}%</td>'
            f'<td style="text-align:right;">{fmt_dollars_from_thousands(r.market_total, 1)}</td>'
            f'<td style="text-align:right;">{r.hhi_pre:,.0f}</td>'
            f'<td style="text-align:right;">{r.hhi_post:,.0f}</td>'
            f'<td style="text-align:right;">{r.hhi_delta:+,.0f}</td>'
            f'<td style="text-align:left;">{band_cell}</td>'
            f'<td style="text-align:left;">{flag_cell}</td>'
            "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Market</th>'
        '<th style="text-align:right;">A Deposits</th>'
        '<th style="text-align:right;">A Share</th>'
        '<th style="text-align:right;">B Deposits</th>'
        '<th style="text-align:right;">B Share</th>'
        '<th style="text-align:right;">Combined</th>'
        '<th style="text-align:right;">Market Total</th>'
        '<th style="text-align:right;">HHI Pre</th>'
        '<th style="text-align:right;">HHI Post</th>'
        '<th style="text-align:right;">&Delta;HHI</th>'
        '<th style="text-align:left;">Band</th>'
        '<th style="text-align:left;">Screen</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)
    table_export(df, f"merger_hhi_{ticker}_{int(p_cert)}_{kind}",
                 key=f"mp_export_{kind}")
    _skipped_note(res["skipped"])

    st.caption(
        f"FDIC Summary of Deposits, {res['year']} survey (June 30 branch "
        "deposits). HHI is the market's Herfindahl index over all insured "
        "institutions (0–10,000; concentration bands: <1,500 unconcentrated, "
        ">2,500 highly concentrated). Merger screen — the DOJ/Fed banking "
        "guideline: flagged when post-merger HHI > 1,800 AND ΔHHI > 200; "
        "post-merger HHI combines the two banks into one participant with "
        "every other participant unchanged. Sorted by ΔHHI. A flag is a "
        "deposit-share screening indicator, not a legal determination."
    )
