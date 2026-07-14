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
  Detailed Offerings — per-bank registered offerings + private
      placements (data/offerings): 424B covers classified per document
      (ECM / DCM / merger prospectus / preliminary / selling-holder
      resale), S-1/S-3 shelf registrations, and 8-K Item 3.02 private
      placements (3.02+2.01 skipped — acquisition consideration). Gross
      amounts strict-extracted from covers; layout owner-confirmed
      2026-07-13. The Summary pie reuses this classification to split
      its Offerings bucket.
  Private Equity Transactions — per-bank stake filings + private
      placements (data/stake_filings + the Offerings PP rows). SC 13D
      (activist/control intent) shown prominently; the SC 13G pile
      (passive schedules — index managers file on every bank) collapsed
      behind an expander; the 13D/13G split is the FORM'S OWN
      distinction, not a classification of ours. Public PE coverage is
      thin by nature — an honest sparse table, per the plan.
  Comparable Deal Analysis — UNIVERSE-wide computed deal comps
      (data/deal_comps): announced values ÷ target financials at
      announcement — P/TBV paid (SEC holdco basis for the ratio's priced
      entity, FDIC bank-sub otherwise, always labeled), price/assets,
      core deposit premium; median-by-year chart, size buckets, scatter,
      selected-bank overlay. Reads the snapshot compiled nightly by
      jobs/refresh_deal_comps — never builds on the render thread.
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
                     "Detailed Offerings", "Private Equity Transactions",
                     "Comparable Deal Analysis", "Insider Activity"],
                    key="transactions")
    if sel == "Transactions Summary":
        _render_summary()
    elif sel == "Detailed M&A History":
        _render_ma_history()
    elif sel == "Detailed Offerings":
        _render_offerings()
    elif sel == "Private Equity Transactions":
        _render_pe()
    elif sel == "Comparable Deal Analysis":
        _render_comps()
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
               "(S-1/S-3 family) and 424B offering takedowns (the pie "
               "splits ECM/DCM/private placements via the Detailed "
               "Offerings classification), and buyback-program 8-Ks "
               "(EDGAR full-text 2001+, earnings filings excluded) — type "
               "classification is our own from filing types and item codes.")

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
        # Offerings split by the Detailed Offerings classification when it
        # loads (merger prospectuses excluded there — they ARE the M&A
        # slices); honest unsplit-count fallback if that fetch failed.
        from data.offerings import get_offerings
        orows = get_offerings(cik) if cik else None
        if orows is not None:
            off_slices = [
                ("ECM offerings",
                 sum(1 for r in orows if r["kind"] == "ECM")),
                ("DCM offerings",
                 sum(1 for r in orows if r["kind"] == "DCM")),
                ("Private placements",
                 sum(1 for r in orows if r["kind"] == "Private placement")),
            ]
        else:
            off_slices = [("Offerings (424B, ECM/DCM unsplit)", off_total)]
        labels_vals = [("Whole-company M&A", whole), ("Branch deals", branch),
                       ("Terminated M&A", term), *off_slices,
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


# ── Detailed Offerings ────────────────────────────────────────────────────

_RAISE_KINDS = ("ECM", "DCM", "Private placement")


def _render_offerings():
    from data.bank_mapping import get_cik
    from data.offerings import get_offerings

    st.caption("Registered offerings and private placements from the "
               "holdco's EDGAR history: 424B prospectus covers classified "
               "per document (ECM equity / DCM debt / merger prospectuses / "
               "preliminary / selling-holder resales), S-1/S-3 shelf "
               "registrations, and 8-K Item 3.02 private placements. Gross "
               "amounts are strict-extracted from the cover — n/a when not "
               "stated, never estimated. Classification is our own from "
               "filing type and cover text.")

    ticker = _bank_picker()
    if not ticker:
        st.info("Pick a bank to load its offerings history.")
        return
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK resolved for {ticker} — offerings "
                   "unavailable.")
        return
    with st.spinner("Reading the EDGAR filing history and prospectus "
                    "covers (cached 7 days)…"):
        rows = get_offerings(cik)
    if rows is None:
        st.warning("EDGAR is temporarily unavailable — retry shortly.")
        return
    if not rows:
        st.info(f"No registered offerings on record for {get_name(ticker)}.")
        return

    raised = sum(r["gross_usd"] or 0 for r in rows
                 if r["kind"] in _RAISE_KINDS)
    n_ecm = sum(1 for r in rows if r["kind"] == "ECM")
    n_dcm = sum(1 for r in rows if r["kind"] == "DCM")
    n_pp = sum(1 for r in rows if r["kind"] == "Private placement")
    n_shelf = sum(1 for r in rows if r["kind"] == "Shelf registration")
    pill_row([
        stat_pill("KNOWN GROSS RAISED", _fmt_bn(raised) if raised else "—"),
        stat_pill("ECM", f"{n_ecm:,}"),
        stat_pill("DCM", f"{n_dcm:,}"),
        stat_pill("PRIVATE PLACEMENTS", f"{n_pp:,}"),
        stat_pill("SHELF REGISTRATIONS", f"{n_shelf:,}"),
    ], margin="2px 0 12px")

    body = ""
    for r in rows:
        form = _h.escape(r["form"])
        form_cell = (f'<a href="{_h.escape(r["url"])}" target="_blank">{form}</a>'
                     if r.get("url") else form)
        px = r.get("price_per_share")
        body += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(r["date"])}</td>'
            f'<td style="text-align:left;">{form_cell}</td>'
            f'<td style="text-align:left;">{_h.escape(r["kind"])}</td>'
            f'<td style="text-align:left;">{_h.escape(r["security"] or "—")}</td>'
            f'<td style="text-align:right;">{_fmt_bn(r["gross_usd"])}</td>'
            f'<td style="text-align:right;">{f"${px:,.2f}" if px else "—"}</td>'
            "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Filed</th>'
        '<th style="text-align:left;">Form</th>'
        '<th style="text-align:left;">Kind</th>'
        '<th style="text-align:left;">Security</th>'
        '<th style="text-align:right;">Gross</th>'
        '<th style="text-align:right;">Price/share</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True)
    st.caption(f"{len(rows):,} filings · newest first · merger prospectuses "
               "(deal documents — see Detailed M&A History), preliminary "
               "supplements and selling-holder resales are shown but "
               "excluded from raise totals. Pre-2001 text-only filings can "
               "be Unclassified (no fetchable cover). ECM vs DCM split "
               "feeds the Summary pie.")


# ── Private Equity Transactions ───────────────────────────────────────────

def _stake_table(rows: list[dict]) -> str:
    body = ""
    for r in rows:
        nm = r.get("holder_name")
        holder = _h.escape(nm) if nm else "—"
        form = _h.escape(r["form"])
        form_cell = (f'<a href="{_h.escape(r["url"])}" target="_blank">'
                     f"{form}</a>") if r.get("url") else form
        body += ("<tr>"
                 f'<td style="text-align:left;">{_h.escape(r["date"])}</td>'
                 f'<td style="text-align:left;">{holder}</td>'
                 f'<td style="text-align:left;">{form_cell}</td>'
                 "</tr>")
    return ('<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Filed</th>'
            '<th style="text-align:left;">Holder</th>'
            '<th style="text-align:left;">Form</th>'
            "</tr></thead><tbody>" + body + "</tbody></table></div>")


def _render_pe():
    from data.bank_mapping import get_cik
    from data.offerings import get_offerings
    from data.stake_filings import get_stake_filings

    st.caption("Large-stake filings on this bank (SEC Schedules 13D/13G — "
               "13D signals activist/control intent, 13G is the passive "
               "schedule; that split is the form's own, not ours) plus "
               "private placements from the Offerings leg. Public "
               "private-equity coverage is inherently thin — 13D/G only "
               "captures stakes above 5% — so this is an honest sparse "
               "view, never an estimate.")

    ticker = _bank_picker()
    if not ticker:
        st.info("Pick a bank to load its stake filings.")
        return
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK resolved for {ticker}.")
        return
    with st.spinner("Reading stake filings and private placements "
                    "(cached 7 days)…"):
        rows = get_stake_filings(cik, get_name(ticker) or ticker)
        offers = get_offerings(cik)
    if rows is None:
        st.warning("EDGAR is temporarily unavailable — retry shortly.")
        return

    d13 = [r for r in rows if r["form"].startswith("SC 13D")]
    g13 = [r for r in rows if r["form"].startswith("SC 13G")]
    pps = [r for r in (offers or []) if r["kind"] == "Private placement"]
    pp_gross = sum(r["gross_usd"] or 0 for r in pps)
    pill_row([
        stat_pill("13D FILINGS", f"{len(d13):,}"),
        stat_pill("13G FILINGS", f"{len(g13):,}"),
        stat_pill("PRIVATE PLACEMENTS", f"{len(pps):,}"),
        stat_pill("KNOWN PP GROSS", _fmt_bn(pp_gross) if pp_gross else "—"),
    ], margin="2px 0 12px")

    st.markdown("**Schedule 13D filings (activist / control intent)**")
    if d13:
        st.markdown(_stake_table(d13), unsafe_allow_html=True)
    else:
        st.caption("No 13D has ever been filed on this bank — no holder "
                   "has declared activist or control intent above 5%.")

    st.markdown("**Private placements (8-K Item 3.02)**")
    if pps:
        body = ""
        for r in pps:
            form = _h.escape(r["form"])
            form_cell = (f'<a href="{_h.escape(r["url"])}" target="_blank">'
                         f"{form}</a>") if r.get("url") else form
            body += ("<tr>"
                     f'<td style="text-align:left;">{_h.escape(r["date"])}</td>'
                     f'<td style="text-align:left;">{form_cell}</td>'
                     f'<td style="text-align:right;">{_fmt_bn(r["gross_usd"])}</td>'
                     "</tr>")
        st.markdown('<div class="ksk-grid"><table><thead><tr>'
                    '<th style="text-align:left;">Filed</th>'
                    '<th style="text-align:left;">Filing</th>'
                    '<th style="text-align:right;">Gross</th>'
                    "</tr></thead><tbody>" + body + "</tbody></table></div>",
                    unsafe_allow_html=True)
        st.caption("Unregistered sales (incl. 2008-era TARP CPP preferred "
                   "issues to Treasury); gross shown only where stated in "
                   "the filing.")
    elif offers is None:
        st.caption("Private placements unavailable (EDGAR fetch failed) — "
                   "retry shortly.")
    else:
        st.caption("No Item 3.02 private placements on record.")

    with st.expander(f"Schedule 13G filings (passive) — {len(g13):,}"):
        if g13:
            st.markdown(_stake_table(g13), unsafe_allow_html=True)
        else:
            st.caption("None on record.")
    st.caption("Sources: issuer EDGAR submissions (complete filing list) + "
               "EDGAR full-text search (holder names, 2001+ — older "
               "filings show a working link with holder —).")


# ── Comparable Deal Analysis ──────────────────────────────────────────────

_SIZE_BUCKETS = [("<$500M", 0, 5e8), ("$500M–$1B", 5e8, 1e9),
                 ("$1–5B", 1e9, 5e9), ("$5B+", 5e9, float("inf"))]


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def _render_comps():
    import plotly.graph_objects as go
    from data.deal_comps import get_comps_snapshot
    from utils.chart_style import CATEGORICAL_PALETTE, apply_standard_layout

    st.caption("Computed deal comps across every bank's M&A history: "
               "announced deal value ÷ target financials at announcement. "
               "P/TBV basis is SEC holdco tangible common equity for the "
               "deal's priced entity when it resolves, FDIC bank-subsidiary "
               "tangible equity otherwise — hover any multiple for basis "
               "and as-of date. Core deposit premium = (value − TBV) ÷ core "
               "deposits, bank-sub basis only. n/a = not sourceable, never "
               "estimated. Announced-but-pending deals are not yet included "
               "(no completion or termination filing to anchor on).")

    snap = get_comps_snapshot()
    if not snap:
        st.info("The universe comps snapshot has not been compiled yet — "
                "the refresh-deal-comps job builds it (nightly, or run it "
                "manually after deploy). Per-bank deals are already "
                "available on Detailed M&A History.")
        return

    deals = snap["deals"]
    pill_row([
        stat_pill("DEALS", f"{snap['deals_total']:,}"),
        stat_pill("PRICED (P/TBV)", f"{snap['deals_priced']:,}"),
        stat_pill("BANKS COVERED", f"{snap['banks_covered']:,}"),
        stat_pill("SNAPSHOT", _h.escape(str(snap.get("built_at", ""))[:10])),
    ], margin="2px 0 12px")

    # ── Filters ──────────────────────────────────────────────────────────
    fa, fb, fc = st.columns([1, 1, 2])
    with fa:
        since = st.selectbox("Announced since", ["All years", "2005", "2010",
                                                 "2015", "2020"],
                             index=0, key="comps_since")
    with fb:
        status = st.selectbox("Status", ["All", "Completed", "Terminated"],
                              index=0, key="comps_status")
    with fc:
        overlay = st.selectbox(
            "Highlight a bank's own deals", options=[None] + sorted(
                {d["buyer_ticker"] for d in deals if d.get("buyer_ticker")}),
            index=0, format_func=lambda t: t or "—",
            key="comps_overlay")

    def _date(d):
        return (d.get("announce_date") or d.get("completion_date")
                or d.get("termination_date") or "")

    shown = deals
    if since != "All years":
        shown = [d for d in shown if _date(d) >= since]
    if status != "All":
        shown = [d for d in shown if d["status"] == status.lower()]
    priced_shown = [d for d in shown if d.get("p_tbv")]

    # ── Median P/TBV by announce year ────────────────────────────────────
    by_year = {}
    for d in priced_shown:
        yr = _date(d)[:4]
        if yr:
            by_year.setdefault(int(yr), []).append(d["p_tbv"])
    if by_year:
        xs = list(range(min(by_year), max(by_year) + 1))
        meds = [_median(by_year.get(x, [])) for x in xs]
        cnts = [len(by_year.get(x, [])) for x in xs]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=xs, y=cnts, name="Priced deals",
                             marker_color=CATEGORICAL_PALETTE[5],
                             opacity=0.45, yaxis="y2"))
        fig.add_trace(go.Scatter(x=xs, y=meds, name="Median P/TBV paid",
                                 mode="lines+markers", connectgaps=True,
                                 line=dict(color=CATEGORICAL_PALETTE[0])))
        apply_standard_layout(fig, title="Median P/TBV paid by announce year",
                              height=320, yaxis_title="P/TBV (x)")
        fig.update_layout(yaxis2=dict(overlaying="y", side="right",
                                      showgrid=False, rangemode="tozero",
                                      title="Deals"))
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})

    left, right = st.columns([2, 3])

    # ── Size-bucket summary ──────────────────────────────────────────────
    with left:
        st.markdown("**Pricing by target size**")
        body = ""
        for label, lo, hi in _SIZE_BUCKETS:
            rows_b = [d for d in priced_shown
                      if lo <= (d.get("comp_assets") or
                                d.get("target_assets") or 0) < hi]
            med_p = _median([d["p_tbv"] for d in rows_b])
            med_pa = _median([d.get("price_assets") for d in rows_b])
            med_cdp = _median([d.get("core_dep_premium") for d in rows_b])
            body += (
                "<tr>"
                f'<td style="text-align:left;">{label}</td>'
                f'<td style="text-align:right;">{len(rows_b)}</td>'
                f'<td style="text-align:right;">{f"{med_p:.2f}x" if med_p else "—"}</td>'
                f'<td style="text-align:right;">{f"{med_pa*100:.1f}%" if med_pa else "—"}</td>'
                f'<td style="text-align:right;">{f"{med_cdp*100:.1f}%" if med_cdp else "—"}</td>'
                "</tr>")
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Target assets</th>'
            '<th style="text-align:right;">Deals</th>'
            '<th style="text-align:right;">Med P/TBV</th>'
            '<th style="text-align:right;">Med P/Assets</th>'
            '<th style="text-align:right;">Med core dep prem</th>'
            "</tr></thead><tbody>" + body + "</tbody></table></div>",
            unsafe_allow_html=True)

    # ── P/TBV vs size scatter ────────────────────────────────────────────
    with right:
        st.markdown("**P/TBV vs target size**")
        base = [d for d in priced_shown
                if (d.get("comp_assets") or d.get("target_assets"))]
        if base:
            def _pt(d):
                return ((d.get("comp_assets") or d.get("target_assets")) / 1e6,
                        d["p_tbv"],
                        f"{d.get('buyer_ticker') or ''} → "
                        f"{d.get('target_name') or ''}<br>"
                        f"{_date(d)} · {_fmt_bn(d.get('value_usd'))} · "
                        f"{d['p_tbv']:.2f}x ({d.get('tbv_basis')})")
            others = [d for d in base if d.get("buyer_ticker") != overlay]
            mine = [d for d in base if overlay and d.get("buyer_ticker") == overlay]
            fig = go.Figure()
            pts = [_pt(d) for d in others]
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts],
                mode="markers", name="Universe deals",
                marker=dict(color=CATEGORICAL_PALETTE[0], size=7,
                            opacity=0.55),
                hovertext=[p[2] for p in pts], hoverinfo="text"))
            if mine:
                pts = [_pt(d) for d in mine]
                fig.add_trace(go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts],
                    mode="markers", name=f"{overlay} deals",
                    marker=dict(color=CATEGORICAL_PALETTE[3], size=11,
                                symbol="diamond"),
                    hovertext=[p[2] for p in pts], hoverinfo="text"))
            apply_standard_layout(fig, height=340,
                                  yaxis_title="P/TBV paid (x)",
                                  xaxis_title="Target assets ($M, log)",
                                  hovermode="closest")
            fig.update_xaxes(type="log")
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False})

    # ── Deal table ───────────────────────────────────────────────────────
    st.markdown("**Deal comps**")
    cert_map = _cert_ticker_map()
    body = ""
    for d in shown[:250]:
        buyer = d.get("buyer_ticker") or ""
        buyer_cell = (f'<a href="?s=Company&bank={_h.escape(buyer)}" '
                      f'target="_self">{_h.escape(buyer)}</a>') if buyer else "—"
        tgt = _party(d.get("target_name"), d.get("target_cert"), cert_map)
        ann = d.get("announce_date")
        if ann and d.get("announce_url"):
            ann_cell = (f'<a href="{_h.escape(d["announce_url"])}" '
                        f'target="_blank">{_h.escape(ann)}</a>')
        else:
            ann_cell = _h.escape(ann) if ann else "—"
        status_cell = ("Completed" if d["status"] == "completed" else
                       '<span style="color:var(--danger,#dc2626);'
                       'font-weight:600;">Terminated</span>')
        p = d.get("p_tbv")
        if p:
            note = (f"{d.get('tbv_basis')} TBV {_fmt_bn(d.get('tbv_usd'))} "
                    f"as of {d.get('tbv_asof')}")
            if d.get("value_note"):
                # Names the priced entity — vital on flipped MOEs where the
                # bank-level target isn't the holdco the value priced.
                note += f" · {d['value_note']}"
            ptbv_cell = f'<span title="{_h.escape(note)}">{p:.2f}x</span>'
        elif d.get("flagged"):
            ptbv_cell = f'<span title="{_h.escape(d["flagged"])}">n/a†</span>'
        else:
            ptbv_cell = "—"
        pa = d.get("price_assets")
        cdp = d.get("core_dep_premium")
        assets = d.get("comp_assets") or d.get("target_assets")
        body += (
            "<tr>"
            f'<td style="text-align:left;">{ann_cell}</td>'
            f'<td style="text-align:left;">{buyer_cell}</td>'
            f'<td style="text-align:left;">{tgt}</td>'
            f'<td style="text-align:left;">{status_cell}</td>'
            f'<td style="text-align:right;">{_fmt_bn(d.get("value_usd"))}</td>'
            f'<td style="text-align:right;">{_fmt_bn(assets)}</td>'
            f'<td style="text-align:right;">{ptbv_cell}</td>'
            f'<td style="text-align:right;">{f"{pa*100:.1f}%" if pa else "—"}</td>'
            f'<td style="text-align:right;">{f"{cdp*100:.1f}%" if cdp else "—"}</td>'
            "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Announced</th>'
        '<th style="text-align:left;">Buyer</th>'
        '<th style="text-align:left;">Target</th>'
        '<th style="text-align:left;">Status</th>'
        '<th style="text-align:right;">Value</th>'
        '<th style="text-align:right;">Target assets</th>'
        '<th style="text-align:right;">P/TBV</th>'
        '<th style="text-align:right;">P/Assets</th>'
        '<th style="text-align:right;">Core dep prem</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True)
    shown_n = min(len(shown), 250)
    st.caption(f"{shown_n:,} of {len(shown):,} deals shown (newest first"
               f"{', capped at 250' if len(shown) > 250 else ''}) · deals "
               "without a sourceable value price the count only · "
               "† = multiple outside the 0.2x–8x sanity band (basis "
               "mismatch guard) · sources: FDIC structure history + "
               "financials, EDGAR announcement 8-Ks, SEC companyfacts.")

    try:
        import pandas as pd
        from ui.chrome import table_export
        table_export(pd.DataFrame(shown), "deal_comps", key="comps_export")
    except Exception:
        pass


# ── Detailed M&A History ──────────────────────────────────────────────────

def _fmt_bn(raw_dollars) -> str:
    """$ figure in $B/$M from raw dollars; em-dash for n/a."""
    if raw_dollars is None:
        return "—"
    if abs(raw_dollars) >= 1_000_000_000:
        return f"${raw_dollars / 1_000_000_000:,.2f}B"
    return f"${raw_dollars / 1_000_000:,.1f}M"


# Shared cert->ticker map (data.bank_universe) — promoted from here when the
# deposit-share tables became its second consumer.
from data.bank_universe import cert_ticker_map as _cert_ticker_map


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
        # Universal linking rule: the ticker deep-links to the Company page
        # (this was the one Transactions table with a bare ticker cell).
        tk_cell = (f'<a href="?s=Company&bank={_h.escape(tk)}" target="_self">'
                   f'{_h.escape(tk)}</a>') if tk else "—"
        body += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(str(r.get("date") or ""))}</td>'
            f'<td style="text-align:left;font-weight:600;">{tk_cell}</td>'
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
