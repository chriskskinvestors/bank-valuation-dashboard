"""
Institutional Ownership UI — 13F holdings for a bank.
"""

import streamlit as st
from ui.states import skeleton as _skeleton
import pandas as pd

from data.bank_mapping import get_name
from data.form13f_client import fetch_institutional_holdings, summarize_holdings
from utils.formatting import fmt_dollars
from ui.chrome import table_export, title_bar


def sample_coverage_pct(shares_sum, shares_out) -> float | None:
    """Sampled 13F shares as a % of shares outstanding — how much of the
    company the holders FOUND actually cover (UX-P0-12: JPM's 26-filer sample
    read as the whole institutional base). None when shares outstanding is
    missing or non-positive: n/a, never a guess."""
    if shares_sum is None or not shares_out or shares_out <= 0:
        return None
    return shares_sum / shares_out * 100


def render_ownership(ticker: str):
    """Render 13F institutional holdings panel."""
    name = get_name(ticker)

    title_bar(f"{name} ({ticker})", "Institutional (13F)")
    st.subheader("Institutional Ownership (13F)")
    st.caption(
        "Institutional holders found via SEC EDGAR full-text search of recent "
        "13F-HR filings (last ~90 days, up to 30 filers) — a sample, not the full "
        "holder list. Small banks may have limited 13F coverage."
    )

    with st.spinner("Fetching 13F filings from SEC EDGAR..."):
        holders = fetch_institutional_holdings(ticker, name, max_filers=30)
        summary = summarize_holdings(holders)

    if not holders:
        from ui.states import empty_state
        empty_state('No 13F filings found',
                    'This can happen for smaller banks with limited institutional coverage, or if the SEC full-text search fails for the ticker')
        return

    # ── Headline metrics (click any value for its source) ──
    from ui.source_trace import render_traceable_cards, make_calc
    entity = f"{name} ({ticker})"
    SRC = "SEC 13F-HR filings (EDGAR full-text search)"
    nf = summary["total_filers"]
    tot_val = summary.get("total_value_usd") or 0
    # Shares outstanding: the same SEC-fundamentals accessor Corporate Profile
    # uses (1h memo over cached companyfacts — no new fetch path).
    shares_out = None
    from data.bank_mapping import get_cik
    cik = get_cik(ticker)
    if cik:
        try:
            from data import sec_client
            shares_out = (sec_client.get_latest_fundamentals(cik) or {}).get(
                "shares_outstanding")
        except Exception:
            shares_out = None
    cov = sample_coverage_pct(summary["total_shares"], shares_out)

    def own_card(label, value, definition, terms, op=None):
        return {"label": label, "value": value,
                "calc": make_calc(label, value, entity=entity, source=SRC,
                                  asof="last ~90 days", unit="", ref="aggregated 13F-HR filings",
                                  definition=definition, terms=terms, op=op, reported=(op is None))}

    cards = [
        own_card("13F Filers Found", str(nf),
                 "Number of 13F-HR filers (last ~90 days) found by SEC EDGAR "
                 "full-text search, capped at 30 — whatever the search returned, "
                 "NOT the total institutional base and not necessarily the largest "
                 "holders.",
                 [{"label": "13F-HR filers found", "val": str(nf)}]),
        own_card("Shares Held (filers found)", f"{summary['total_shares']:,.0f}",
                 "Shares held across the 13F filers found via EDGAR full-text "
                 "search — a sample, not the complete institutional base.",
                 [{"label": "Shares (summed across filers)", "val": f"{summary['total_shares']:,.0f}",
                   "sub": f"across {nf} 13F-HR filings found"}]),
        own_card("Value (filers found)", fmt_dollars(tot_val, 2),
                 "Reported market value across the 13F filers found via EDGAR "
                 "full-text search — a sample, not total institutional ownership.",
                 [{"label": "Value (summed across filers)", "val": fmt_dollars(tot_val, 2),
                   "sub": f"across {nf} 13F-HR filings found"}]),
        own_card("Sample Coverage", f"{cov:.2f}%" if cov is not None else "n/a",
                 "Shares held by the sampled 13F filers as a share of shares "
                 "outstanding — how much of the company this sample covers.",
                 [{"label": "Sampled 13F shares", "val": f"{summary['total_shares']:,.0f}",
                   "sub": f"across {nf} 13F-HR filings found"},
                  {"label": "Shares outstanding",
                   "val": f"{shares_out:,.0f}" if shares_out else "n/a",
                   "sub": "latest SEC filing (same figure as Corporate Profile)"}],
                 op="Sampled 13F shares ÷ shares outstanding × 100"),
    ]
    render_traceable_cards(cards, key=f"ownership_{ticker}", columns=4)

    # ── QoQ flow summary (added / trimmed / new vs prior quarter) ──────
    n_added = sum(1 for h in holders if h.get("change_status") == "Added")
    n_trim = sum(1 for h in holders if h.get("change_status") == "Trimmed")
    n_new = sum(1 for h in holders if h.get("change_status") == "New")
    n_unk = sum(1 for h in holders if h.get("change_status") == "Unknown")
    if n_added or n_trim or n_new or n_unk:
        unk = f" · {n_unk} prior-quarter lookup failed" if n_unk else ""
        st.caption(
            f"**Vs prior quarter:** {n_added} added · {n_trim} trimmed · "
            f"{n_new} new positions{unk} · click any **Filing ↗** for the source 13F-HR."
        )

    # ── Holders table — each row links to its 13F-HR; change vs prior Q ──
    def _chg(h):
        status = h.get("change_status")
        pct = h.get("change_pct")
        if status == "New":
            return "New"
        if status == "Unchanged":
            return "— Unch."
        if pct is None:
            return "—"
        return f"{pct:+.0f}%"

    rows = []
    exp_rows = []      # raw numerics for the export (never display strings)
    total_val = summary["total_value_usd"] or 1
    for h in holders:
        pct_of_inst = (h["value_usd"] / total_val * 100) if total_val else 0
        rows.append({
            "Rank": len(rows) + 1,
            "Institution": h["filer_name"],
            "Δ QoQ": _chg(h),
            "Date Filed": h.get("date_filed") or "—",
            "Shares": f"{h['shares']:,.0f}",
            "Value": fmt_dollars(h["value_usd"], 2),
            "% of Inst": f"{pct_of_inst:.1f}%",
            "Filing": h.get("filing_url") or None,
        })
        exp_rows.append({
            "Rank": len(exp_rows) + 1,
            "Institution": h["filer_name"],
            "Filer CIK": h.get("filer_cik"),
            "Date Filed": h.get("date_filed"),
            "Shares": h.get("shares"),
            "Value ($)": h.get("value_usd"),
            "% of Sampled Inst Value (%)": (h["value_usd"] / tot_val * 100
                                            if tot_val and h.get("value_usd") is not None
                                            else None),
            "Δ QoQ Status": h.get("change_status"),
            "Δ QoQ Shares (%)": h.get("change_pct"),
            "Prior Qtr Shares": h.get("prior_shares"),
            "Accession": h.get("accession"),
            "Filing URL": h.get("filing_url"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        height=min(640, 36 + 35 * len(df)),
        column_config={
            "Δ QoQ": st.column_config.TextColumn(
                "Δ QoQ", help="Share change vs the filer's prior 13F-HR quarter",
                width="small"),
            "Filing": st.column_config.LinkColumn(
                "Filing", help="Open the source 13F-HR on SEC EDGAR",
                display_text="SEC ↗", width="small"),
        },
    )
    # Underlying numeric holder records (unformatted shares / value_usd)
    table_export(pd.DataFrame(exp_rows), f"institutional_holders_{ticker}",
                 key=f"exp_institutional_holders_{ticker}",
                 sheet="Institutional holders",
                 formats={"Rank": "int", "Filer CIK": "text", "Date Filed": "date",
                          "Shares": "int", "Value ($)": "usd",
                          "% of Sampled Inst Value (%)": "pct",
                          "Δ QoQ Shares (%)": "pct", "Prior Qtr Shares": "int"},
                 provenance={"Page": "Company Analysis › Ownership › Institutional (13F)",
                             "Ticker": ticker, "Company": name,
                             "Source": SRC,
                             "Coverage": f"{nf} 13F-HR filers found by EDGAR full-text "
                                         "search in the last ~90 days (capped at 30) — "
                                         "a sample, not the full institutional base",
                             "Δ QoQ": "share change vs each filer's prior 13F-HR"},
                 freeze_cols=2)

    st.caption(
        "13F filings are required for institutions managing >$100M, cover equity holdings "
        "only (not derivatives), and are filed 45 days after quarter-end. Δ QoQ compares each "
        "filer's share count to their previous 13F-HR."
    )


def _qoq_moves(hist: dict, q1: str, q0: str) -> list[dict]:
    """Per-holder share change between two stored snapshot quarters (q1 =
    latest, q0 = prior). 'New' = present in q1 only; 'Exited' = present in q0
    only (presence in the stored SAMPLE, not proof of a market exit);
    unchanged positions are omitted. Pure — unit-tested directly."""
    moves = []
    for h, m in hist.items():
        cur_sh = (m.get(q1) or {}).get("shares")
        prev_sh = (m.get(q0) or {}).get("shares")
        if cur_sh is None and prev_sh is None:
            continue
        if prev_sh is None:
            status, delta = "New", cur_sh
        elif cur_sh is None:
            status, delta = "Exited", -prev_sh
        else:
            status, delta = "", cur_sh - prev_sh
        if not delta:
            continue
        pct = (delta / prev_sh * 100) if prev_sh else None
        moves.append({"Institution": h, "Status": status,
                      "Δ Shares": delta, "Δ %": pct})
    return moves


def render_holder_history(ticker: str):
    """Holder × quarter matrix from the stored 13F quarterly snapshots, plus a
    QoQ Top Buyers / Sellers ranking (SNL 'Ownership History' — phase 1: light
    versions built from our-universe positions). History accumulates going
    forward from when quarterly retention shipped; EDGAR backfill of older
    quarters is a planned later task — sparse early history is honest, not a bug."""
    from data.form13f_client import get_holder_history

    name = get_name(ticker)
    title_bar(f"{name} ({ticker})", "Holder History")
    st.subheader("Institutional Holder History (13F)")
    st.caption(
        "Share positions of the largest reporting institutions by calendar "
        "quarter, assembled from stored 13F-HR snapshots (a sample of the "
        "biggest filers found, not the complete institutional base)."
    )

    with st.spinner("Reading stored 13F quarter snapshots..."):
        hist = get_holder_history(ticker, quarters=20)

    if not hist:
        from ui.states import empty_state
        empty_state('No stored 13F quarter snapshots for this bank yet',
                    'History accumulates as the quarterly 13F refresh runs — open the Institutional (13F) tab once to seed the current quarter')
        return

    quarters = sorted({q for m in hist.values() for q in m}, reverse=True)
    shown = quarters[:8]                      # cap the matrix width

    def _latest_value(m: dict) -> float:
        for q in quarters:
            v = (m.get(q) or {}).get("value_usd")
            if v:
                return v
        return 0.0

    holders = sorted(hist, key=lambda h: -_latest_value(hist[h]))

    # ── Matrix: holders (rows) × stored quarters (columns) ──────────────
    # Both metrics come straight from the stored snapshots — value is each
    # filing's own reported position value (SNL's mkt-val column, phase 2).
    view = st.radio("Matrix values", ["Shares", "Reported value"],
                    horizontal=True, key=f"hh_view_{ticker}")
    rows = []
    for h in holders:
        row = {"Institution": h}
        for q in shown:
            cell = hist[h].get(q) or {}
            if view == "Shares":
                v = cell.get("shares")
                row[q] = f"{v:,.0f}" if v is not None else "—"
            else:
                v = cell.get("value_usd")
                row[q] = fmt_dollars(v) if v is not None else "—"
        rows.append(row)
    df = pd.DataFrame(rows)
    from ui.tables import ksk_table
    ksk_table(df, max_height_px=640)
    # Raw holder × quarter records (unformatted shares / value_usd)
    flat = [{"Institution": h, "Quarter": q,
             "Shares": (hist[h][q] or {}).get("shares"),
             "Reported Value ($)": (hist[h][q] or {}).get("value_usd")}
            for h in holders for q in sorted(hist[h], reverse=True)]
    table_export(pd.DataFrame(flat), f"holder_history_{ticker}_{quarters[0]}",
                 key=f"exp_holder_history_{ticker}",
                 sheet="Holder history",
                 formats={"Shares": "int", "Reported Value ($)": "usd"},
                 provenance={"Page": "Company Analysis › Ownership › Holder History",
                             "Ticker": ticker, "Company": name,
                             "Source": "SEC EDGAR Form 13F-HR filings — stored "
                                       "quarterly snapshots of the largest filers found",
                             "Quarters": f"{quarters[-1]} to {quarters[0]} "
                                         f"({len(quarters)} stored)",
                             "Reported Value": "each filing's own quarter-end "
                                               "position value"},
                 freeze_cols=1)

    # ── Top Buyers / Sellers: latest stored quarter vs the prior one ────
    if len(quarters) < 2:
        st.caption(
            f"Only **{quarters[0]}** is stored so far — the Top Buyers / "
            "Sellers ranking appears once two quarters have accumulated."
        )
        return

    q1, q0 = quarters[0], quarters[1]         # latest, prior
    moves = _qoq_moves(hist, q1, q0)

    if not moves:
        st.caption(f"No position changes between {q0} and {q1} among stored holders.")
        return

    def _fmt(rows_):
        return [{"Institution": r["Institution"],
                 "Status": r["Status"] or "—",
                 "Δ Shares": f"{r['Δ Shares']:+,.0f}",
                 "Δ %": (f"{r['Δ %']:+.0f}%" if r["Δ %"] is not None else
                         ("New" if r["Status"] == "New" else "—"))}
                for r in rows_]

    buyers = sorted([m for m in moves if m["Δ Shares"] > 0],
                    key=lambda m: -m["Δ Shares"])[:10]
    sellers = sorted([m for m in moves if m["Δ Shares"] < 0],
                     key=lambda m: m["Δ Shares"])[:10]

    st.markdown(f"**Top Buyers / Sellers — {q1} vs {q0}** (sampled filers)")
    bc, sc = st.columns(2)
    with bc:
        st.markdown("**Buyers**")
        if buyers:
            st.dataframe(pd.DataFrame(_fmt(buyers)), use_container_width=True,
                         hide_index=True, height=36 + 35 * len(buyers))
        else:
            from ui.states import empty_state
            empty_state('No adds among stored holders')
    with sc:
        st.markdown("**Sellers**")
        if sellers:
            st.dataframe(pd.DataFrame(_fmt(sellers)), use_container_width=True,
                         hide_index=True, height=36 + 35 * len(sellers))
        else:
            from ui.states import empty_state
            empty_state('No trims/exits among stored holders')
    st.caption(
        "Δ compares each institution's stored share count between the two most "
        "recent snapshot quarters. 'New'/'Exited' reflect presence in the stored "
        "sample — a holder can drop out of the sample without selling."
    )


def render_crossholdings(ticker: str):
    """Inferred crossholdings (SNL 'Ownership Crossholdings'): for this bank's
    largest stored 13F holders, which OTHER universe banks each institution
    also holds — a pure cross-join of the stored quarterly snapshots."""
    from data.form13f_client import get_crossholdings

    name = get_name(ticker)
    title_bar(f"{name} ({ticker})", "Crossholdings")
    st.subheader("Institutional Crossholdings (inferred)")

    with st.spinner("Cross-joining stored 13F snapshots..."):
        x = get_crossholdings(ticker)

    if not x or not x.get("rows"):
        from ui.states import empty_state
        empty_state('No stored 13F snapshot for this bank yet — open the Institutional (13F) tab once to seed it',
                    "Crossholdings are inferred from stored snapshots, so coverage grows as more banks' 13F tabs are viewed")
        return

    st.caption(
        f"**{x['quarter']}** · cross-joined against **{x['coverage']}** other "
        "universe banks with a stored snapshot for the same quarter. Inferred "
        "from our stored sample of largest filers — an institution can hold "
        "other banks not shown here."
    )
    if x["coverage"] == 0:
        from ui.states import empty_state
        empty_state('No other banks have a stored snapshot for this quarter yet — the cross-join will populate as 13F tabs are viewed')
        return

    # ksk-grid HTML (not st.dataframe): the "Largest other positions" cell
    # carries SEVERAL tickers, each deep-linking to its Company page
    # (universal linking rule) — a canvas-grid LinkColumn can only link a
    # whole single-value cell.
    import html as _h
    body = ""
    for r in x["rows"]:
        others = r["others"]
        tops = ", ".join(
            f'<a href="?bank={_h.escape(o["ticker"], quote=True)}" '
            f'target="_self">{_h.escape(o["ticker"])}</a> '
            f'({_h.escape(fmt_dollars(o["value_usd"], 1))})'
            for o in others[:5] if o.get("value_usd"))
        pos = (fmt_dollars(r["subject_value_usd"], 2)
               if r.get("subject_value_usd") else "—")
        body += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(r["holder"])}</td>'
            f'<td style="text-align:right;">{_h.escape(pos)}</td>'
            f'<td style="text-align:right;">{len(others)}</td>'
            f'<td style="text-align:left;">{tops or "—"}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid" style="max-height:640px;overflow-y:auto;">'
        "<table><thead><tr>"
        '<th style="text-align:left;">Institution</th>'
        '<th style="text-align:right;">Position here</th>'
        '<th style="text-align:right;">Other banks held</th>'
        '<th style="text-align:left;">Largest other positions</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    flat = [{"Institution": r["holder"],
             f"Position in {ticker} ($)": r.get("subject_value_usd"),
             "Other Bank": o["ticker"],
             "Shares": o.get("shares"), "Reported Value ($)": o.get("value_usd")}
            for r in x["rows"] for o in r["others"]]
    if flat:
        table_export(pd.DataFrame(flat), f"crossholdings_{ticker}_{x['quarter']}",
                     key=f"exp_crossholdings_{ticker}",
                     sheet="Crossholdings",
                     formats={f"Position in {ticker} ($)": "usd",
                              "Shares": "int", "Reported Value ($)": "usd"},
                     provenance={"Page": "Company Analysis › Ownership › Crossholdings",
                                 "Ticker": ticker, "Company": name,
                                 "Source": "SEC EDGAR Form 13F-HR filings — stored "
                                           "quarterly snapshots, cross-joined across "
                                           "universe banks",
                                 "Quarter": x["quarter"],
                                 "Coverage": f"{x['coverage']} other universe banks "
                                             "with a stored snapshot for this quarter "
                                             "(inferred from the stored sample — an "
                                             "institution can hold banks not shown)"},
                     freeze_cols=1)


# ── Ownership Detailed (SNL plan §13, phase 1) ─────────────────────────

def _detailed_rows(holders: list[dict], prior_shares: dict[str, float] | None,
                   shares_out: float | None, price: float | None) -> list[dict]:
    """Factual per-holder rows for the Detailed table. Derived cells render
    ONLY when their inputs are sound: %CSO needs a positive share count,
    market value a positive price, QoQ deltas a stored prior-quarter
    snapshot (prior_shares None = no prior snapshot at all → deltas n/a;
    holder absent from the prior snapshot → New position).

    Phase 1 is deliberately facts-only — SNL's style/turnover/orientation
    columns need each holder's full 13F book (phase 2)."""
    rows = []
    for h in holders:
        shares = h.get("shares")
        d_shares = d_pct = None
        is_new = False
        if prior_shares is not None and shares is not None:
            prev = prior_shares.get(h.get("filer_name"))
            if prev is None:
                is_new = True
            else:
                d_shares = shares - prev
                d_pct = (d_shares / prev * 100.0) if prev > 0 else None
        pct_cso = (shares / shares_out * 100.0
                   if shares is not None and isinstance(shares_out, (int, float))
                   and shares_out > 0 else None)
        mkt_value = (shares * price
                     if shares is not None and isinstance(price, (int, float))
                     and price > 0 else None)
        rows.append({
            "holder": h.get("filer_name"),
            "filer_cik": h.get("filer_cik"),
            "accession": h.get("accession"),
            "shares": shares,
            "d_shares": d_shares,
            "d_pct": d_pct,
            "is_new": is_new,
            "pct_cso": pct_cso,
            "mkt_value": mkt_value,
            "reported_value": h.get("value_usd"),
            "filed": h.get("date_filed"),
        })
    rows.sort(key=lambda r: r["shares"] or 0, reverse=True)
    return rows


def render_ownership_detailed(ticker: str, metrics: dict):
    import html as _h
    from data.form13f_client import get_holder_history
    from data.fmp_client import get_quote
    from data.sec_pvp import _filing_url

    name = get_name(ticker)
    title_bar(f"{name} ({ticker})", "Ownership Detailed")

    with _skeleton():
        holders = fetch_institutional_holdings(ticker, name, max_filers=30)
    if not holders:
        from ui.states import empty_state
        empty_state('No 13F holders found for this bank (limited institutional coverage is normal for small banks)')
        return

    # Prior-quarter shares for QoQ deltas — needs ≥2 stored snapshots.
    hist = get_holder_history(ticker, quarters=2)
    quarters = sorted({q for by_q in hist.values() for q in by_q}, reverse=True)
    prior_shares = None
    prior_q = None
    if len(quarters) >= 2:
        prior_q = quarters[1]
        prior_shares = {h: (by_q.get(prior_q) or {}).get("shares")
                        for h, by_q in hist.items()
                        if (by_q.get(prior_q) or {}).get("shares") is not None}

    shares_out = (metrics or {}).get("shares_outstanding")
    price = (get_quote(ticker) or {}).get("price")
    rows = _detailed_rows(holders, prior_shares, shares_out, price)

    def _n(v, fmt="{:,.0f}"):
        return fmt.format(v) if v is not None else "n/a"

    def _delta_cell(r):
        if r["is_new"]:
            return '<span style="color:#059669;font-weight:600;">New</span>'
        if r["d_shares"] is None:
            return "n/a"
        color = "#059669" if r["d_shares"] >= 0 else "#dc2626"
        pct = f' ({r["d_pct"]:+.0f}%)' if r["d_pct"] is not None else ""
        return (f'<span style="color:{color};">{r["d_shares"]:+,.0f}{pct}</span>')

    body = ""
    for r in rows:
        url = _filing_url(int(r["filer_cik"]), r["accession"]) \
            if r.get("filer_cik") and r.get("accession") else None
        holder = _h.escape(r["holder"] or "—")
        if url:
            holder = f'<a href="{url}" target="_blank">{holder}</a>'
        body += ("<tr>"
                 f'<td style="text-align:left;">{holder}</td>'
                 f'<td style="text-align:right;">{_n(r["shares"])}</td>'
                 f'<td style="text-align:right;">{_delta_cell(r)}</td>'
                 f'<td style="text-align:right;">{_n(r["pct_cso"], "{:.2f}%")}</td>'
                 f'<td style="text-align:right;">{fmt_dollars(r["mkt_value"]) if r["mkt_value"] is not None else "n/a"}</td>'
                 f'<td style="text-align:right;">{fmt_dollars(r["reported_value"]) if r["reported_value"] is not None else "n/a"}</td>'
                 f'<td style="text-align:left;">{_h.escape(r["filed"] or "n/a")}</td>'
                 "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Holder</th>'
        '<th style="text-align:right;">Shares</th>'
        '<th style="text-align:right;">&Delta; Shares (QoQ)</th>'
        '<th style="text-align:right;">% CSO</th>'
        '<th style="text-align:right;">Mkt Value</th>'
        '<th style="text-align:right;">Reported (13F)</th>'
        '<th style="text-align:left;">Filed</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)

    notes = ["Top 13F filers from EDGAR full-text search — a coverage sample, "
             "not every institutional owner. Mkt Value = shares × current "
             "price; Reported = the filing's own quarter-end value."]
    if prior_q:
        notes.append(f"QoQ deltas vs the stored {prior_q} snapshot.")
    else:
        notes.append("QoQ deltas need two stored quarterly snapshots — "
                     "history accumulates from the quarterly 13F warm job.")
    notes.append("Style / turnover / orientation columns need each holder's "
                 "full 13F book (phase 2).")
    st.caption(" ".join(notes))

    exp = pd.DataFrame(rows).rename(columns={
        "holder": "Holder", "filer_cik": "Filer CIK", "accession": "Accession",
        "shares": "Shares", "d_shares": "Δ Shares (QoQ)", "d_pct": "Δ Shares (QoQ) (%)",
        "is_new": "New Position", "pct_cso": "% CSO (%)",
        "mkt_value": "Mkt Value ($)", "reported_value": "Reported Value ($)",
        "filed": "Date Filed"})
    table_export(exp, f"ownership_detailed_{ticker}",
                 key=f"exp_owndet_{ticker}",
                 sheet="Ownership detailed",
                 formats={"Filer CIK": "text", "Shares": "int",
                          "Δ Shares (QoQ)": "int", "Δ Shares (QoQ) (%)": "pct",
                          "% CSO (%)": "pct", "Mkt Value ($)": "usd",
                          "Reported Value ($)": "usd", "Date Filed": "date"},
                 provenance={"Page": "Company Analysis › Ownership › Ownership Detailed",
                             "Ticker": ticker, "Company": name,
                             "Source": "SEC EDGAR Form 13F-HR filings (largest filers "
                                       "found via full-text search — a coverage sample)",
                             "Prior quarter (QoQ)": prior_q or "n/a — needs two stored "
                                                               "quarterly snapshots",
                             "Shares outstanding (% CSO)": shares_out,
                             "Price ($) (Mkt Value)": price,
                             "Mkt Value": "shares × current price; Reported Value = the "
                                          "filing's own quarter-end value"},
                 freeze_cols=1)
