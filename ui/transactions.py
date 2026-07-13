"""Transactions — top-level section (docs/SNL-BUILD-PLAN.md §14).

Owner-decided structure (2026-07-13): the §14 five SNL sub-tabs plus the
existing universe insider feed KEPT as its own sub-tab. Sub-tabs render as
they are BUILT — no empty placeholders:

  Detailed M&A History — per-bank deal table (owner-decided scope: a bank
      picker in the sub-tab, SNL-style; universe-wide view is a later
      increment). Data: data/ma_history.get_ma_history — FDIC completed
      structure deals (whole-company + branch, both directions), announce
      dates + stated/computed values from EDGAR announcements, terminated
      deals via the per-holdco EFTS sweep. First uncached load fetches
      live (seconds; serial acquirers tens of seconds) then caches 7 days.
  Insider Activity — the pre-existing universe-wide open-market insider
      feed (unchanged; reads the Home-snapshot aggregate, zero per-bank
      I/O on render).

Rendering follows the house rules: lazy_tabs (only the active pane runs),
dense ksk-grid content-hug tables (never width:100%), n/a shown honestly,
every entity/document reference is a working link (universal linking rule).
"""
from __future__ import annotations

import html as _h

import streamlit as st

from data.bank_mapping import get_name
from data.form4_client import recent_open_market_universe
from ui.chrome import lazy_tabs
from ui.components import stat_pill, pill_row
from utils.formatting import fmt_dollars


def render_transactions():
    st.markdown("### Transactions")
    sel = lazy_tabs(["Detailed M&A History", "Insider Activity"],
                    key="transactions")
    if sel == "Detailed M&A History":
        _render_ma_history()
    else:
        _render_insider_feed()


# ── Detailed M&A History ──────────────────────────────────────────────────

def _fmt_bn(raw_dollars) -> str:
    """$ figure in $B/$M from raw dollars; em-dash for n/a."""
    if raw_dollars is None:
        return "—"
    if abs(raw_dollars) >= 1_000_000_000:
        return f"${raw_dollars / 1_000_000_000:,.2f}B"
    return f"${raw_dollars / 1_000_000:,.1f}M"


def _cert_ticker_map() -> dict[int, str]:
    """fdic_cert -> universe ticker for company-page deep links. Fail-open:
    get_universe serves the snapshot (stale-tolerant, never live-builds on
    the interactive path); any hiccup just means unlinked names."""
    try:
        from data.bank_universe import get_universe
        out: dict[int, str] = {}
        for t, info in get_universe().items():
            try:
                cert = int(info.get("fdic_cert") or 0)
            except (TypeError, ValueError):
                continue
            if cert:
                out.setdefault(cert, t)
        return out
    except Exception:
        return {}


def _party(name: str | None, cert, cert_map: dict[int, str]) -> str:
    """Escaped party name; a working company-page link when the cert maps to
    a covered ticker (universal linking rule)."""
    if not name:
        return "—"
    esc = _h.escape(name)
    tk = cert_map.get(cert or 0)
    if tk:
        return f'<a href="?s=Company&bank={_h.escape(tk)}" target="_self">{esc}</a>'
    return esc


def _render_ma_history():
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.bank_universe import get_universe_tickers
    from data.ma_history import get_ma_history

    st.caption("Completed whole-company and branch deals from FDIC structure "
               "history; announce dates and deal values from the announcement "
               "8-K (EDGAR full-text, 2001+); terminated deals from the "
               "holdco's termination 8-Ks. Values labeled stated (verbatim "
               "from the press release) or computed (exchange ratio × price "
               "× shares — hover for the formula). n/a = not sourceable, "
               "never estimated.")

    tickers = get_universe_tickers()
    default = st.session_state.get("company_pick")
    idx = tickers.index(default) if default in tickers else None
    ticker = st.selectbox(
        "Bank", options=tickers, index=idx,
        format_func=lambda t: f"{t} — {get_name(t)}" if get_name(t) != t else t,
        placeholder="Pick a bank…", key="txn_ma_bank")
    if not ticker:
        st.info("Pick a bank to load its deal history.")
        return

    cert = get_fdic_cert(ticker)
    cik = get_cik(ticker)
    if not cert:
        st.warning(f"No FDIC cert resolved for {ticker} — deal history "
                   "unavailable.")
        return

    with st.spinner("Assembling deal history — first load pulls FDIC "
                    "structure records and EDGAR announcement filings "
                    "(cached 7 days)…"):
        deals = get_ma_history(cert, cik=cik)
    if not deals:
        st.info(f"No structure deals on record for {get_name(ticker)} "
                f"(FDIC cert {cert}).")
        return

    subject = get_name(ticker) or ticker
    cert_map = _cert_ticker_map()

    completed = [d for d in deals if d["status"] == "completed"]
    acq = [d for d in completed if d["direction"] == "acquisition"]
    branch = [d for d in completed if d["deal_kind"] == "branch"]
    terminated = [d for d in deals if d["status"] == "terminated"]
    pill_row([
        stat_pill("DEALS", f"{len(deals):,}"),
        stat_pill("COMPLETED ACQUISITIONS", f"{len(acq):,}"),
        stat_pill("BRANCH DEALS", f"{len(branch):,}"),
        stat_pill("TERMINATED", f"{len(terminated):,}"),
    ], margin="2px 0 12px")

    body = ""
    for d in deals:
        cp = d.get("counterparty") or {}
        cp_html = _party(cp.get("name"), cp.get("cert"), cert_map)
        me = _h.escape(subject)
        sale = d["direction"] == "sale"
        if d["deal_kind"] == "whole_company":
            n = d.get("branch_count")
            if d["status"] == "terminated":
                # Direction is often unknowable for cash deals — show the
                # counterparty on the known side only.
                target, buyer = (me, cp_html) if sale else \
                                (cp_html, me) if d["direction"] else \
                                (f"{me} / {cp_html}", "—")
                seller = "—"
            elif sale:
                target, buyer, seller = me, cp_html, "—"
            else:
                target, buyer, seller = cp_html, me, "—"
            kind = "Whole company"
        else:
            n = d.get("branch_count")
            kind = f"Branch ({n})" if n else "Branch"
            target = kind
            buyer, seller = (cp_html, me) if sale else (me, cp_html)
            kind = "Branch"
        if d["status"] == "terminated":
            status = ('<span style="color:var(--danger,#dc2626);font-weight:600;"'
                      f' title="Terminated {_h.escape(d["termination_date"] or "")}">'
                      "Terminated</span>")
            completed_cell = _h.escape(d["termination_date"] or "—")
        else:
            status = "Completed"
            completed_cell = _h.escape(d["completion_date"] or "—")
        ann = d.get("announce_date")
        if ann and d.get("announce_url"):
            ann_cell = (f'<a href="{_h.escape(d["announce_url"])}" '
                        f'target="_blank">{_h.escape(ann)}</a>')
        else:
            ann_cell = _h.escape(ann) if ann else "—"
        val = d.get("value_usd")
        if val is not None:
            basis = d.get("value_basis") or ""
            note = d.get("value_note") or f"{basis} in the announcement release"
            sup = "*" if basis == "computed" else ""
            val_cell = (f'<span title="{_h.escape(note)}">{_fmt_bn(val)}{sup}'
                        "</span>")
        else:
            val_cell = "—"
        assets = d.get("target_assets")
        if assets is not None:
            asof = d.get("target_assets_repdte") or ""
            assets_cell = (f'<span title="FDIC total assets at {_h.escape(asof)}">'
                           f"{_fmt_bn(assets)}</span>")
        else:
            assets_cell = "—"
        desc = _h.escape(d.get("event_desc") or "")
        body += (
            f'<tr title="{desc}">'
            f'<td style="text-align:left;">{ann_cell}</td>'
            f'<td style="text-align:left;">{completed_cell}</td>'
            f'<td style="text-align:left;">{status}</td>'
            f'<td style="text-align:left;">{target}</td>'
            f'<td style="text-align:left;">{buyer}</td>'
            f'<td style="text-align:left;">{seller}</td>'
            f'<td style="text-align:left;">{kind}</td>'
            f'<td style="text-align:right;">{val_cell}</td>'
            f'<td style="text-align:right;">{assets_cell}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Announced</th>'
        '<th style="text-align:left;">Completed</th>'
        '<th style="text-align:left;">Status</th>'
        '<th style="text-align:left;">Target</th>'
        '<th style="text-align:left;">Buyer</th>'
        '<th style="text-align:left;">Seller</th>'
        '<th style="text-align:left;">Type</th>'
        '<th style="text-align:right;">Value</th>'
        '<th style="text-align:right;">Target assets</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(deals):,} deals · newest first · sources: FDIC "
               "structure history (completions), EDGAR 8-K/EX-99 "
               "(announcements, values, terminations), FDIC financials "
               "(target assets at announcement, or at completion when no "
               "announcement resolved — hover the figure for the as-of "
               "date). Announce dates predate 2001 only in filings EDGAR "
               "full-text search does not cover — shown as n/a. * = value "
               "computed from the announced exchange ratio (hover for the "
               "formula).")


# ── Insider Activity (pre-existing universe feed, unchanged) ─────────────

def _fmt_shares(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _render_insider_feed():
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
