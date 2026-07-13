"""
Institutional Ownership UI — 13F holdings for a bank.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name
from data.form13f_client import fetch_institutional_holdings, summarize_holdings
from utils.formatting import fmt_dollars
from ui.chrome import table_export, title_bar


def render_ownership(ticker: str):
    """Render 13F institutional holdings panel."""
    name = get_name(ticker)

    title_bar(f"{name} ({ticker})", "Institutional (13F)")
    st.subheader("Institutional Ownership (13F)")
    st.caption(
        "Top institutional holders from most recent 13F-HR filings (last ~90 days). "
        "Small banks may have limited 13F coverage."
    )

    with st.spinner("Fetching 13F filings from SEC EDGAR..."):
        holders = fetch_institutional_holdings(ticker, name, max_filers=30)
        summary = summarize_holdings(holders)

    if not holders:
        st.info(
            "No 13F filings found. This can happen for smaller banks with limited "
            "institutional coverage, or if the SEC full-text search fails for the ticker."
        )
        return

    # ── Headline metrics (click any value for its source) ──
    from ui.source_trace import render_traceable_cards, make_calc
    entity = f"{name} ({ticker})"
    SRC = "SEC 13F-HR filings (EDGAR full-text search)"
    nf = summary["total_filers"]
    tot_val = summary.get("total_value_usd") or 0
    top5_val = sum(h["value_usd"] for h in holders[:5] if h.get("value_usd"))

    def own_card(label, value, definition, terms, op=None):
        return {"label": label, "value": value,
                "calc": make_calc(label, value, entity=entity, source=SRC,
                                  asof="last ~90 days", unit="", ref="aggregated 13F-HR filings",
                                  definition=definition, terms=terms, op=op, reported=(op is None))}

    cards = [
        own_card("Institutional Filers", str(nf),
                 "Number of institutions (>$100M AUM) reporting a position in their latest "
                 "13F-HR filing over the last ~90 days.",
                 [{"label": "13F-HR filers", "val": str(nf)}]),
        own_card("Shares Held (top filers)", f"{summary['total_shares']:,.0f}",
                 "Shares held across the largest reporting institutions found via "
                 "EDGAR full-text search — a sample of the biggest filers, not the "
                 "complete institutional base.",
                 [{"label": "Shares (summed across filers)", "val": f"{summary['total_shares']:,.0f}",
                   "sub": f"across {nf} 13F-HR filings (largest found)"}]),
        own_card("Value (top filers)", fmt_dollars(tot_val, 2),
                 "Reported market value across the largest reporting institutions "
                 "found via EDGAR full-text search — a sample of the biggest filers, "
                 "not total institutional ownership.",
                 [{"label": "Value (summed across filers)", "val": fmt_dollars(tot_val, 2),
                   "sub": f"across {nf} 13F-HR filings (largest found)"}]),
        own_card("Top 5 Concentration", f"{summary['top_5_concentration']:.0f}%",
                 "Share of the sampled institutional dollar value held by the five "
                 "largest holders — a concentration/crowding gauge.",
                 [{"label": "Top-5 holders' value", "val": fmt_dollars(top5_val, 2)},
                  {"label": "Sampled institutional value", "val": fmt_dollars(tot_val, 2)}],
                 op="Top-5 value ÷ sampled institutional value × 100"),
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
    table_export(pd.DataFrame(holders), f"institutional_holders_{ticker}",
                 key=f"exp_institutional_holders_{ticker}")

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
        st.info(
            "No stored 13F quarter snapshots for this bank yet. History "
            "accumulates as the quarterly 13F refresh runs — open the "
            "Institutional (13F) tab once to seed the current quarter."
        )
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

    # ── Matrix: holders (rows) × stored quarters (columns), shares ──────
    rows = []
    for h in holders:
        row = {"Institution": h}
        for q in shown:
            cell = hist[h].get(q)
            sh = (cell or {}).get("shares")
            row[q] = f"{sh:,.0f}" if sh is not None else "—"
        rows.append(row)
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 height=min(640, 36 + 35 * len(df)))
    # Raw holder × quarter records (unformatted shares / value_usd)
    flat = [{"institution": h, "quarter": q, **(hist[h][q] or {})}
            for h in holders for q in sorted(hist[h], reverse=True)]
    table_export(pd.DataFrame(flat), f"holder_history_{ticker}",
                 key=f"exp_holder_history_{ticker}")

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
            st.caption("No adds among stored holders.")
    with sc:
        st.markdown("**Sellers**")
        if sellers:
            st.dataframe(pd.DataFrame(_fmt(sellers)), use_container_width=True,
                         hide_index=True, height=36 + 35 * len(sellers))
        else:
            st.caption("No trims/exits among stored holders.")
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
        st.info(
            "No stored 13F snapshot for this bank yet — open the "
            "Institutional (13F) tab once to seed it. Crossholdings are "
            "inferred from stored snapshots, so coverage grows as more banks' "
            "13F tabs are viewed."
        )
        return

    st.caption(
        f"**{x['quarter']}** · cross-joined against **{x['coverage']}** other "
        "universe banks with a stored snapshot for the same quarter. Inferred "
        "from our stored sample of largest filers — an institution can hold "
        "other banks not shown here."
    )
    if x["coverage"] == 0:
        st.info("No other banks have a stored snapshot for this quarter yet — "
                "the cross-join will populate as 13F tabs are viewed.")
        return

    rows = []
    for r in x["rows"]:
        others = r["others"]
        tops = ", ".join(
            f"{o['ticker']} ({fmt_dollars(o['value_usd'], 1)})"
            for o in others[:5] if o.get("value_usd"))
        rows.append({
            "Institution": r["holder"],
            "Position here": fmt_dollars(r["subject_value_usd"], 2)
                              if r.get("subject_value_usd") else "—",
            "Other banks held": len(others),
            "Largest other positions": tops or "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 height=min(640, 36 + 35 * len(rows)))
    flat = [{"holder": r["holder"], "ticker": o["ticker"],
             "shares": o.get("shares"), "value_usd": o.get("value_usd")}
            for r in x["rows"] for o in r["others"]]
    if flat:
        table_export(pd.DataFrame(flat), f"crossholdings_{ticker}",
                     key=f"exp_crossholdings_{ticker}")


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

    with st.spinner("Loading 13F holders..."):
        holders = fetch_institutional_holdings(ticker, name, max_filers=30)
    if not holders:
        st.info("No 13F holders found for this bank (limited institutional "
                "coverage is normal for small banks).")
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

    table_export(pd.DataFrame(rows), f"ownership_detailed_{ticker}",
                 key=f"exp_owndet_{ticker}")
