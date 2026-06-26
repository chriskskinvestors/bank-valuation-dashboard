"""Transactions — universe-wide insider activity (top-level section).

Reads the SAME pre-built open-market insider aggregate the Home feed uses
(data.form4_client.recent_open_market_universe) — one cache hit, ZERO per-bank
I/O on the render thread; the heavy Form-4 scan runs in jobs/refresh_home_snapshot.
This view adds a summary + filters + a dense, sortable table on top of that feed.

Institutional 13F flows are a separate, larger data pipeline (store holdings by
security + quarter-over-quarter deltas) and are intentionally NOT shown here yet —
a labeled note, never fake data.
"""
from __future__ import annotations

import html as _h

import streamlit as st

from data.bank_mapping import get_name
from data.form4_client import recent_open_market_universe
from utils.formatting import fmt_dollars
from ui.components import stat_pill, pill_row


def _fmt_shares(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def render_transactions():
    st.markdown("### Transactions")
    st.caption("Open-market insider buys & sells across the bank universe "
               "(SEC Form 4, codes P/S only — grants, awards and option "
               "exercises excluded). Source: SEC EDGAR.")

    rows = recent_open_market_universe(limit=250)
    if not rows:
        st.info("The insider feed populates from the nightly insider job and the "
                "Home snapshot warm. No recent open-market transactions are "
                "cached yet — check back after the next refresh.")
        _render_13f_note()
        return

    buys = [r for r in rows if r.get("direction") == "Buy"]
    sells = [r for r in rows if r.get("direction") == "Sell"]
    buy_val = sum(r.get("value_usd") or 0 for r in buys)
    sell_val = sum(r.get("value_usd") or 0 for r in sells)

    # Dense KPI strip (design-system pills, not boxed st.metric — the spec bans
    # st.metric and the big bordered cards read as a beginner Streamlit demo).
    net = buy_val - sell_val
    net_col = "var(--success,#059669)" if net >= 0 else "var(--danger,#dc2626)"
    pill_row([
        stat_pill("TRANSACTIONS", f"{len(rows):,}"),
        stat_pill("BUYS", f"{len(buys):,}"),
        stat_pill("SELLS", f"{len(sells):,}"),
        stat_pill("NET BUY − SELL",
                  f'<span style="color:{net_col};">{fmt_dollars(net)}</span>'),
    ], margin="2px 0 12px")

    # ── Filters ──────────────────────────────────────────────────────────
    fa, fb = st.columns([1, 2])
    with fa:
        side = st.radio("Show", ["All", "Buys", "Sells"], horizontal=True,
                        key="txn_side", label_visibility="collapsed")
    shown = rows
    if side == "Buys":
        shown = buys
    elif side == "Sells":
        shown = sells

    # ── Dense table ──────────────────────────────────────────────────────
    body = ""
    for r in shown:
        tk = r.get("ticker") or ""
        nm = get_name(tk) or tk
        who = r.get("insider") or "—"
        role = (r.get("role") or "").split(",")[0]
        buy = r.get("direction") == "Buy"
        act = ('<span style="color:var(--success,#059669);font-weight:600;">Buy</span>'
               if buy else
               '<span style="color:var(--danger,#dc2626);font-weight:600;">Sell</span>')
        body += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(str(r.get("date") or ""))}</td>'
            f'<td style="text-align:left;font-weight:600;">{_h.escape(tk)}</td>'
            f'<td style="text-align:left;">{_h.escape(nm)}</td>'
            f'<td style="text-align:left;">{_h.escape(who)}</td>'
            f'<td style="text-align:left;">{_h.escape(role)}</td>'
            f'<td style="text-align:left;">{act}</td>'
            f'<td style="text-align:right;">{_fmt_shares(r.get("shares"))}</td>'
            f'<td style="text-align:right;">{fmt_dollars(r.get("value_usd"))}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Date</th>'
        '<th style="text-align:left;">Ticker</th>'
        '<th style="text-align:left;">Bank</th>'
        '<th style="text-align:left;">Insider</th>'
        '<th style="text-align:left;">Role</th>'
        '<th style="text-align:left;">Action</th>'
        '<th style="text-align:right;">Shares</th>'
        '<th style="text-align:right;">Value</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(shown):,} transaction{'s' if len(shown) != 1 else ''} shown · "
               "newest first · open-market only.")
    _render_13f_note()


def _render_13f_note():
    st.markdown("#### Institutional (13F) flows")
    st.info("Cross-universe 13F institutional buying/selling — who's accumulating "
            "or trimming bank positions quarter-over-quarter — is a separate data "
            "pipeline (in progress) and is not shown here yet. Per-company 13F "
            "holders are available today under a bank's **Ownership → Institutional "
            "(13F)** tab.")
