"""Executive Compensation sub-tab (Ownership section).

Proxy (DEF 14A) Summary Compensation Table for the named executives, sourced
from FMP's governance-executive-compensation endpoint (data/fmp_compensation).
Two views: the latest fiscal year's pay-component breakdown, and a multi-year
total-compensation trend for the current named-executive group. Honest empty
state when a bank has no proxy coverage.

FMP's feed is NOT the filing (UX-P0-07: JPM FY2025 CEO total 43.00M in FMP
vs 40,632,724 in the DEF 14A). A fiscal year's FMP figures are shown only
when an FMP total for that year is within 1% of a PEO Summary Compensation
Table total the issuer tagged in its own proxy XBRL (Pay versus Performance,
data/sec_pvp); otherwise the year renders n/a with the reason.
"""
from __future__ import annotations

import html as _h

import streamlit as st

from data.bank_mapping import get_name
from data.fmp_compensation import get_executive_compensation
from utils.formatting import fmt_dollars
from ui.chrome import title_bar

_COMPONENTS = [
    ("salary", "Salary"),
    ("bonus", "Bonus"),
    ("stock_award", "Stock"),
    ("option_award", "Option"),
    ("incentive", "Incentive"),
    ("other", "Other"),
]


def render_compensation(ticker: str):
    """Overview ▸ Compensation: proxy Summary Compensation Table (FMP) +
    pay-versus-performance (proxy inline XBRL, data/sec_pvp)."""
    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Compensation")
    from data.bank_mapping import get_cik
    from data.sec_pvp import get_pay_versus_performance

    cik = get_cik(ticker)
    pvp = get_pay_versus_performance(cik) if cik else None
    _render_neo_tables(ticker, pvp)
    _render_pay_versus_performance(pvp)


_VERIFY_TOL = 0.01  # owner rule: FMP total within 1% of the proxy's XBRL PEO total


def verified_years(fmp_rows: list[dict], pvp: dict | None) -> dict[int, str]:
    """{fiscal year: "ok" | "mismatch" | "unverifiable"} for every FMP year.

    "ok" — at least one FMP row that year has a total within 1% (relative to
    the XBRL value) of a PEO SCT total the issuer tagged for the same fiscal
    year (pvp["years"][i]["peo_total"] is a list — several PEOs in a
    transition year). "unverifiable" — no PvP PEO total for that year.
    PvP years are keyed by the calendar year of the fiscal-year-end date."""
    peo_by_year: dict[int, list] = {}
    for r in (pvp or {}).get("years") or []:
        try:
            y = int(str(r.get("fy_end"))[:4])
        except ValueError:
            continue
        peo_by_year[y] = [float(v) for v in (r.get("peo_total") or []) if v]
    out = {}
    for y in {r["year"] for r in fmp_rows}:
        peos = peo_by_year.get(y)
        if not peos:
            out[y] = "unverifiable"
            continue
        totals = [r["total"] for r in fmp_rows
                  if r["year"] == y and r.get("total") is not None]
        match = any(abs(t - p) / abs(p) <= _VERIFY_TOL
                    for t in totals for p in peos)
        out[y] = "ok" if match else "mismatch"
    return out


def _unverified_note(y: int, status: str) -> str:
    if status == "mismatch":
        return (f"The compensation feed (FMP) disagrees with the proxy's own "
                f"XBRL for FY{y} — not shown.")
    return f"FY{y} cannot be verified against the proxy's XBRL — not shown."


def _render_neo_tables(ticker: str, pvp: dict | None):
    rows = get_executive_compensation(ticker)
    if not rows:
        from ui.states import empty_state
        empty_state('No named-executive compensation is available for this company from the proxy-statement provider (many smaller banks are not covered)')
        return
    status = verified_years(rows, pvp)

    years = sorted({r["year"] for r in rows}, reverse=True)
    latest = years[0]
    latest_rows = sorted((r for r in rows if r["year"] == latest),
                         key=lambda r: r.get("total") or 0, reverse=True)
    link = next((r.get("link") for r in latest_rows if r.get("link")), None)

    # ── Latest-year Summary Compensation Table ───────────────────────────
    st.markdown(f"#### Summary Compensation — FY{latest}")
    if status[latest] != "ok":
        from ui.states import empty_state
        empty_state(_unverified_note(latest, status[latest]),
                    "The CEO's proxy-reported total is in Pay versus "
                    "Performance below." if status[latest] == "mismatch" else None)
    else:
        _render_summary_table(latest, latest_rows, link)

    # ── Multi-year total-comp trend (current NEO group) ──────────────────
    trend_years = years[:5]
    if len(trend_years) > 1:
        st.markdown("#### Total compensation — 5-year trend")
        by_exec_year = {}
        for r in rows:
            by_exec_year[(r["name_position"], r["year"])] = r.get("total")
        hidden = [y for y in trend_years if status[y] != "ok"]
        if len(hidden) < len(trend_years):
            execs = [r["name_position"] for r in latest_rows]  # current NEOs, by FY total desc
            th = ('<div class="ksk-grid"><table><thead><tr>'
                  '<th style="text-align:left;">Executive</th>'
                  + "".join(f'<th style="text-align:right;">FY{y}</th>' for y in trend_years)
                  + "</tr></thead><tbody>")
            tb = ""
            for ex in execs:
                tb += ("<tr>"
                       f'<td style="text-align:left;">{_h.escape(ex)}</td>'
                       + "".join(
                           '<td style="text-align:right;">'
                           + ("n/a" if y in hidden
                              else fmt_dollars(by_exec_year.get((ex, y))))
                           + "</td>"
                           for y in trend_years)
                       + "</tr>")
            st.markdown(th + tb + "</tbody></table></div>", unsafe_allow_html=True)
        cap = ("Total compensation per the FMP feed (proxy Summary Compensation "
               "Table), shown only for years verified against the CEO total in "
               "the proxy's XBRL; — where an executive was not a named "
               "officer that year.")
        if hidden:
            cap += " Hidden (n/a): " + " ".join(_unverified_note(y, status[y])
                                                for y in hidden)
        st.caption(cap)


def _render_summary_table(latest: int, latest_rows: list[dict], link):
    head = ('<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Executive</th>'
            + "".join(f'<th style="text-align:right;">{lbl}</th>'
                      for _, lbl in _COMPONENTS)
            + '<th style="text-align:right;">Total</th>'
            "</tr></thead><tbody>")
    body = ""
    for r in latest_rows:
        cells = "".join(
            f'<td style="text-align:right;">{fmt_dollars(r.get(k))}</td>'
            for k, _ in _COMPONENTS)
        body += ("<tr>"
                 f'<td style="text-align:left;">{_h.escape(r["name_position"])}</td>'
                 f"{cells}"
                 f'<td style="text-align:right;font-weight:600;">{fmt_dollars(r.get("total"))}</td>'
                 "</tr>")
    st.markdown(head + body + "</tbody></table></div>", unsafe_allow_html=True)
    src = f" · [DEF 14A]({link})" if link else ""
    st.caption(f"Named executive officers, FY{latest}, from the FMP compensation "
               f"feed (proxy Summary Compensation Table) — verified against the "
               f"CEO total in the proxy's own XBRL (Pay versus Performance) "
               f"within 1%. Source: FMP / SEC{src}.")


# ── Pay versus Performance (Item 402(v), proxy inline XBRL) ──────────────

def _pvp_money(v) -> str:
    return fmt_dollars(v) if v is not None else "n/a"


def _pvp_peo_cell(vals: list) -> str:
    """One value per PEO that year — a transition year discloses several and
    the XBRL loses the officer names, so all are shown, oldest tag order."""
    if not vals:
        return "n/a"
    return " / ".join(fmt_dollars(v) for v in vals)


def _pvp_csm_cell(v) -> str:
    """Company-selected measure value: dimensionless issuer's-choice number
    (a ratio, %, or $ figure) — render compactly without asserting a unit."""
    if v is None:
        return "n/a"
    if isinstance(v, float) and abs(v) < 1000:
        return f"{v:,.4g}"
    return fmt_dollars(v)


def _render_pay_versus_performance(pvp: dict | None):
    if not pvp:
        st.caption("Pay-versus-performance: no tagged Item 402(v) disclosure "
                   "in this company's proxy XBRL.")
        return

    st.markdown("#### Pay versus Performance")
    head = ('<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Fiscal Year</th>'
            '<th style="text-align:right;">PEO SCT Total</th>'
            '<th style="text-align:right;">PEO Actually Paid</th>'
            '<th style="text-align:right;">Avg NEO Total</th>'
            '<th style="text-align:right;">Avg NEO Actually Paid</th>'
            '<th style="text-align:right;">TSR ($100)</th>'
            '<th style="text-align:right;">Peer TSR ($100)</th>'
            '<th style="text-align:right;">Net Income</th>'
            '<th style="text-align:right;">Co-Selected Measure</th>'
            "</tr></thead><tbody>")
    body = ""
    for r in pvp["years"]:
        body += ("<tr>"
                 f'<td style="text-align:left;">{_h.escape(str(r["fy_end"])[:4])}</td>'
                 f'<td style="text-align:right;">{_pvp_peo_cell(r["peo_total"])}</td>'
                 f'<td style="text-align:right;">{_pvp_peo_cell(r["peo_paid"])}</td>'
                 f'<td style="text-align:right;">{_pvp_money(r["non_peo_avg_total"])}</td>'
                 f'<td style="text-align:right;">{_pvp_money(r["non_peo_avg_paid"])}</td>'
                 f'<td style="text-align:right;">{_pvp_money(r["tsr"])}</td>'
                 f'<td style="text-align:right;">{_pvp_money(r["peer_tsr"])}</td>'
                 f'<td style="text-align:right;">{_pvp_money(r["net_income"])}</td>'
                 f'<td style="text-align:right;">{_pvp_csm_cell(r["co_selected"])}</td>'
                 "</tr>")
    st.markdown(head + body + "</tbody></table></div>", unsafe_allow_html=True)

    notes = ["As disclosed under Item 402(v) (proxy inline XBRL, ecd taxonomy); "
             "values are the issuer's own tagged facts. TSR columns are the "
             "year-end value of a fixed \\$100 investment; the co-selected "
             "measure is the issuer's chosen metric (name not machine-readable)."]
    if pvp.get("multi_peo"):
        notes.append("Years showing multiple PEO values had a CEO transition; "
                     "the XBRL does not attribute values to named officers.")
    if pvp.get("source_url"):
        notes.append(f"Source: [DEF 14A filed {pvp.get('filed')}]({pvp['source_url']}).")
    st.caption(" ".join(notes))
