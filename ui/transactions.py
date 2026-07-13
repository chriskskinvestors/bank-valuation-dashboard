"""Transactions — top-level section (docs/SNL-BUILD-PLAN.md §14).

Owner-decided structure (2026-07-13): the §14 five SNL sub-tabs plus the
existing universe insider feed KEPT as its own sub-tab. Sub-tabs render as
they are BUILT — no empty placeholders:

  Transactions Summary — per-bank aggregate view with the SNL
      Aggregate/Details toggle: transaction volume chart (M&A value bars +
      all-transaction count line, multi-decade), Top Transactions by
      Value, transaction-type pie (M&A / branch / terminated from our deal
      data; Offerings 424B [ECM/DCM unsplit until the Detailed Offerings
      leg] / Shelf / Buyback from filing types — our own classification,
      labeled), and the buyback-announcement feed (data/ma_summary).
      Details = the same deal table as Detailed M&A History (shared
      renderer). "Top Advisers" panels SKIPPED per owner.
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

Both per-bank panes share ONE picker widget key, so a bank picked in
either follows to the other.

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
    sel = lazy_tabs(["Transactions Summary", "Detailed M&A History",
                     "Insider Activity"], key="transactions")
    if sel == "Transactions Summary":
        _render_summary()
    elif sel == "Detailed M&A History":
        _render_ma_history()
    else:
        _render_insider_feed()


# ── Transactions Summary ──────────────────────────────────────────────────

def _deal_year(d: dict) -> int | None:
    dt = d.get("completion_date") or d.get("termination_date") or ""
    try:
        return int(dt[:4])
    except (TypeError, ValueError):
        return None


def _render_summary():
    import plotly.graph_objects as go
    from data.ma_summary import get_summary
    from utils.chart_style import CATEGORICAL_PALETTE, apply_standard_layout

    st.caption("Per-bank transaction aggregates: M&A deals (FDIC structure "
               "history + EDGAR announcements), shelf registrations "
               "(S-1/S-3 family) and 424B offering takedowns (ECM/DCM "
               "unsplit until the Detailed Offerings build), and "
               "buyback-program 8-Ks (EDGAR full-text 2001+, earnings "
               "filings excluded) — type classification is our own from "
               "filing types and item codes.")

    ticker = _bank_picker()
    if not ticker:
        st.info("Pick a bank to load its transaction summary.")
        return
    deals, cert, cik = _load_deals(ticker)
    if deals is None:
        return

    view = st.radio("View", ["Aggregate", "Details"], horizontal=True,
                    key="txn_summary_view", label_visibility="collapsed")
    subject = get_name(ticker) or ticker
    cert_map = _cert_ticker_map()

    if view == "Details":
        if not deals:
            st.info(f"No structure deals on record for {subject} "
                    f"(FDIC cert {cert}).")
        else:
            _render_deal_table(deals, subject, cert_map)
        return

    with st.spinner("Loading filing history and buyback announcements "
                    "(cached 7 days)…"):
        summ = get_summary(cik) if cik else None
    if summ is None and cik:
        st.warning("EDGAR filing history is temporarily unavailable — "
                   "showing M&A data only; retry shortly.")
    filings = (summ or {}).get("filings_by_year", {})
    buybacks = (summ or {}).get("buybacks", [])

    shelf_total = sum(c.get("shelf", 0) for c in filings.values())
    off_total = sum(c.get("offerings", 0) for c in filings.values())
    pill_row([
        stat_pill("M&A DEALS", f"{len(deals):,}"),
        stat_pill("SHELF REGISTRATIONS", f"{shelf_total:,}"),
        stat_pill("OFFERING TAKEDOWNS (424B)", f"{off_total:,}"),
        stat_pill("BUYBACK 8-KS (2001+)", f"{len(buybacks):,}"),
    ], margin="2px 0 12px")

    # ── Transaction volume: M&A value bars + all-transaction count line ──
    years = {}
    for d in deals:
        yr = _deal_year(d)
        if yr is None:
            continue
        y = years.setdefault(yr, {"count": 0, "value": 0.0})
        y["count"] += 1
        y["value"] += (d.get("value_usd") or 0) / 1_000_000
    for ystr, c in filings.items():
        y = years.setdefault(int(ystr), {"count": 0, "value": 0.0})
        y["count"] += c.get("shelf", 0) + c.get("offerings", 0)
    for r in buybacks:
        try:
            y = years.setdefault(int(r["date"][:4]), {"count": 0, "value": 0.0})
        except (TypeError, ValueError):
            continue
        y["count"] += 1
    if years:
        xs = list(range(min(years), max(years) + 1))
        vals = [years.get(x, {}).get("value", 0.0) for x in xs]
        cnts = [years.get(x, {}).get("count", 0) for x in xs]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=xs, y=vals, name="M&A value ($M)",
                             marker_color=CATEGORICAL_PALETTE[0]))
        fig.add_trace(go.Scatter(x=xs, y=cnts, name="Transactions (count)",
                                 mode="lines+markers", yaxis="y2",
                                 line=dict(color=CATEGORICAL_PALETTE[2])))
        apply_standard_layout(fig, title="Transaction volume",
                              height=320, yaxis_title="M&A value ($M)")
        fig.update_layout(yaxis2=dict(overlaying="y", side="right",
                                      showgrid=False, rangemode="tozero",
                                      title="Count"),
                          bargap=0.35)
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})
        st.caption("Bars: known M&A deal values by completion/termination "
                   "year (announcement-stated or ratio-computed — deals "
                   "with unsourceable values contribute to the count only). "
                   "Line: all transactions — M&A deals plus shelf/424B "
                   "filings and buyback-program 8-Ks by filing year.")

    left, right = st.columns([3, 2])

    # ── Top transactions by value ──
    with left:
        st.markdown("**Top transactions by value**")
        top = sorted((d for d in deals if d.get("value_usd")),
                     key=lambda d: d["value_usd"], reverse=True)[:10]
        if not top:
            st.caption("No deals with a sourceable value.")
        else:
            body = ""
            for d in top:
                target, buyer, _seller, kind = _deal_parties(d, subject, cert_map)
                _status, completed_cell, ann_cell, val_cell, _a = _deal_cells(d)
                body += (
                    "<tr>"
                    f'<td style="text-align:left;">{ann_cell}</td>'
                    f'<td style="text-align:left;">{completed_cell}</td>'
                    f'<td style="text-align:left;">{target}</td>'
                    f'<td style="text-align:left;">{buyer}</td>'
                    f'<td style="text-align:left;">{kind}</td>'
                    f'<td style="text-align:right;">{val_cell}</td>'
                    "</tr>")
            st.markdown(
                '<div class="ksk-grid"><table><thead><tr>'
                '<th style="text-align:left;">Announced</th>'
                '<th style="text-align:left;">Completed</th>'
                '<th style="text-align:left;">Target</th>'
                '<th style="text-align:left;">Buyer</th>'
                '<th style="text-align:left;">Type</th>'
                '<th style="text-align:right;">Value</th>'
                "</tr></thead><tbody>" + body + "</tbody></table></div>",
                unsafe_allow_html=True)

    # ── Transaction-type pie ──
    with right:
        st.markdown("**Transactions by type**")
        whole = sum(1 for d in deals if d["deal_kind"] == "whole_company"
                    and d["status"] == "completed")
        branch = sum(1 for d in deals if d["deal_kind"] == "branch")
        term = sum(1 for d in deals if d["status"] == "terminated")
        labels_vals = [("Whole-company M&A", whole), ("Branch deals", branch),
                       ("Terminated M&A", term),
                       ("Offerings (424B, ECM/DCM unsplit)", off_total),
                       ("Shelf registrations", shelf_total),
                       ("Buyback 8-Ks (2001+)", len(buybacks))]
        labels_vals = [(lv, v) for lv, v in labels_vals if v > 0]
        if not labels_vals:
            st.caption("Nothing to chart yet.")
        else:
            fig = go.Figure(go.Pie(
                labels=[lv for lv, _ in labels_vals],
                values=[v for _, v in labels_vals],
                marker=dict(colors=CATEGORICAL_PALETTE),
                textinfo="value", sort=False))
            apply_standard_layout(fig, height=320, show_legend=True,
                                  hovermode="closest")
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False})

    # ── Buyback announcement feed ──
    st.markdown("**Buyback announcements**")
    if not buybacks:
        st.caption("No buyback-program 8-Ks found (EDGAR full-text, 2001+).")
    else:
        body = "".join(
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(r["date"])}</td>'
            f'<td style="text-align:left;"><a href="{_h.escape(r["url"])}" '
            f'target="_blank">{_h.escape(r["form"])} filing</a></td>'
            "</tr>"
            for r in buybacks[:15])
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Filed</th>'
            '<th style="text-align:left;">Filing</th>'
            "</tr></thead><tbody>" + body + "</tbody></table></div>",
            unsafe_allow_html=True)
        st.caption(f"{len(buybacks):,} buyback-program 8-Ks since 2001 "
                   "(showing the latest 15) · 8-Ks quoting a repurchase "
                   "program, earnings filings excluded — our own "
                   "classification from filing type and item codes. "
                   "Authorized amounts land with the Detailed Offerings "
                   "build.")


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


def _bank_picker() -> str | None:
    """The per-bank panes' shared picker — ONE widget key, so a bank picked
    on Summary follows to M&A History and back. Defaults to the session's
    current company when set; no selection = no network."""
    from data.bank_universe import get_universe_tickers
    tickers = get_universe_tickers()
    default = st.session_state.get("company_pick")
    idx = tickers.index(default) if default in tickers else None
    return st.selectbox(
        "Bank", options=tickers, index=idx,
        format_func=lambda t: f"{t} — {get_name(t)}" if get_name(t) != t else t,
        placeholder="Pick a bank…", key="txn_bank")


def _load_deals(ticker: str):
    """(deals, cert, cik) for a picked bank, spinner included; deals is []
    when nothing is on record and None when the cert can't resolve."""
    from data.bank_mapping import get_cik, get_fdic_cert
    from data.ma_history import get_ma_history

    cert = get_fdic_cert(ticker)
    cik = get_cik(ticker)
    if not cert:
        st.warning(f"No FDIC cert resolved for {ticker} — deal history "
                   "unavailable.")
        return None, None, None
    with st.spinner("Assembling deal history — first load pulls FDIC "
                    "structure records and EDGAR announcement filings "
                    "(cached 7 days)…"):
        deals = get_ma_history(cert, cik=cik)
    return deals, cert, cik


def _render_ma_history():
    st.caption("Completed whole-company and branch deals from FDIC structure "
               "history; announce dates and deal values from the announcement "
               "8-K (EDGAR full-text, 2001+); terminated deals from the "
               "holdco's termination 8-Ks. Values labeled stated (verbatim "
               "from the press release) or computed (exchange ratio × price "
               "× shares — hover for the formula). n/a = not sourceable, "
               "never estimated.")

    ticker = _bank_picker()
    if not ticker:
        st.info("Pick a bank to load its deal history.")
        return
    deals, cert, cik = _load_deals(ticker)
    if deals is None:
        return
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

    _render_deal_table(deals, subject, cert_map)


def _deal_parties(d: dict, subject: str, cert_map: dict[int, str]):
    """(target, buyer, seller, kind) cells for one deal row, from the
    picked bank's perspective."""
    cp = d.get("counterparty") or {}
    cp_html = _party(cp.get("name"), cp.get("cert"), cert_map)
    me = _h.escape(subject)
    sale = d["direction"] == "sale"
    if d["deal_kind"] == "whole_company":
        if d["status"] == "terminated":
            # Direction is often unknowable for cash deals — show the
            # counterparty on the known side only.
            target, buyer = (me, cp_html) if sale else                             (cp_html, me) if d["direction"] else                             (f"{me} / {cp_html}", "—")
            seller = "—"
        elif sale:
            target, buyer, seller = me, cp_html, "—"
        else:
            target, buyer, seller = cp_html, me, "—"
        kind = "Whole company"
    else:
        n = d.get("branch_count")
        target = f"Branch ({n})" if n else "Branch"
        buyer, seller = (cp_html, me) if sale else (me, cp_html)
        kind = "Branch"
    return target, buyer, seller, kind


def _deal_cells(d: dict) -> tuple[str, str, str, str, str]:
    """(status, completed, announced, value, assets) cells for a deal row."""
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
        val_cell = f'<span title="{_h.escape(note)}">{_fmt_bn(val)}{sup}</span>'
    else:
        val_cell = "—"
    assets = d.get("target_assets")
    if assets is not None:
        asof = d.get("target_assets_repdte") or ""
        assets_cell = (f'<span title="FDIC total assets at {_h.escape(asof)}">'
                       f"{_fmt_bn(assets)}</span>")
    else:
        assets_cell = "—"
    return status, completed_cell, ann_cell, val_cell, assets_cell


def _render_deal_table(deals: list[dict], subject: str,
                       cert_map: dict[int, str]) -> None:
    """The dense §14 deal table — shared by Detailed M&A History and the
    Summary tab's Details toggle."""
    body = ""
    for d in deals:
        target, buyer, seller, kind = _deal_parties(d, subject, cert_map)
        status, completed_cell, ann_cell, val_cell, assets_cell = _deal_cells(d)
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
