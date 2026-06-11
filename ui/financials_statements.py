"""
SNL-style statement tables (Income Statement, Balance Sheet, Performance
Analysis, Fair Value, Portfolio, Capital Structure).

Each is a multi-period table (Annual = last 5 FY, Quarterly = last 8 quarters)
built from the FDIC Call Report, reusing the click-to-source popup component from
financial_highlights. Rows are data-driven specs: (label, kind, *fields).
  kind: "dollar" ($000 field) · "pct" (% field) · "diff" (f1 − f2, $000) ·
        "ratio" (f1 ÷ f2 × 100) · "tce" (equity − intangibles, $000)
"""
from __future__ import annotations
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from data.bank_mapping import get_bank_info
from data import fdic_client
from ui.financial_highlights import _build_component


def _num(v):
    try:
        return None if (v is None or pd.isna(v)) else float(v)
    except (TypeError, ValueError):
        return None


def _usd(v_thousands):
    v = _num(v_thousands)
    if v is None:
        return "—"
    d = v * 1000.0
    a = abs(d)
    if a >= 1e9:
        return f"${d/1e9:.2f}B"
    if a >= 1e6:
        return f"${d/1e6:.1f}M"
    return f"${d:,.0f}"


def _thou(v):
    v = _num(v)
    return f"{v:,.0f}" if v is not None else "—"


def _pct(v):
    v = _num(v)
    return f"{v:.2f}%" if v is not None else "—"


def _yr(repdte):
    return int(repdte.year) if hasattr(repdte, "year") else None


def _mo(repdte):
    return int(repdte.month) if hasattr(repdte, "month") else None


def _disp(repdte):
    try:
        return pd.to_datetime(repdte).strftime("%b %d, %Y")
    except Exception:
        return str(repdte)


_DEFAULT_TRENDS = [("roaa", "ROAA"), ("nim", "Net Interest Margin"),
                   ("efficiency_ratio", "Efficiency Ratio"), ("cet1_ratio", "CET1 Ratio")]


def render_statement(ticker: str, key_prefix: str, title: str, spec: list,
                     trends: list | None = None):
    info = get_bank_info(ticker)
    name = info.get("name") if info else ticker
    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None

    st.markdown(f"### {name} ({ticker}) — {title}")
    period = st.radio("Period", ["Annual", "Quarterly"], horizontal=True,
                      key=f"{key_prefix}_period_{ticker}", label_visibility="collapsed")
    st.caption("From the FDIC Call Report. Click any number for its source field "
               "and, where computed, the formula and inputs.")
    if not cert:
        st.info("No FDIC Call Report data mapped for this bank.")
        return
    with st.spinner("Loading…"):
        hist = fdic_client.get_historical_financials(cert, quarters=36)
    if hist is None or hist.empty:
        st.info("No FDIC history available.")
        return

    hist = hist.copy()
    hist["REPDTE"] = pd.to_datetime(hist["REPDTE"])
    hist = hist.sort_values("REPDTE")
    if period == "Annual":
        ye = hist[hist["REPDTE"].dt.month == 12]
        recs_list = list(ye.tail(5).to_dict("records"))
        labels = [f"FY{int(r['REPDTE'].year)}" for r in recs_list]
    else:
        recs_list = list(hist.tail(8).to_dict("records"))
        labels = [f"Q{(r['REPDTE'].month-1)//3+1} '{str(r['REPDTE'].year)[2:]}" for r in recs_list]
    if not recs_list:
        st.info("No periods available.")
        return

    fdic_link = f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"
    entity = f"{name} ({ticker})"

    def calc(metric, v, asof, ref, terms, op, reported):
        return {"metric": metric, "entity": entity, "source": "FDIC Call Report",
                "asof": asof, "unit": "", "ref": ref, "definition": "",
                "terms": terms, "op": op, "reported": reported, "link": fdic_link}

    def cell(rec, kind, args, label):
        asof = _disp(rec.get("REPDTE"))
        if kind == "dollar":
            f = args[0]; raw = _num(rec.get(f))
            return _usd(raw), calc(label, _usd(raw), asof, f"FDIC field {f}",
                                   [{"label": label, "val": _thou(raw) + " ($000)"}], None, True)
        if kind == "pct":
            f = args[0]; raw = _num(rec.get(f))
            return _pct(raw), calc(label, _pct(raw), asof, f"FDIC field {f}",
                                   [{"label": label + " (as reported)", "val": _pct(raw)}], None, True)
        if kind == "diff":
            f1, f2 = args; a, b = _num(rec.get(f1)), _num(rec.get(f2))
            v = _usd(a - b) if (a is not None and b is not None) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": f1, "val": _thou(a) + " ($000)"},
                            {"label": f2, "val": _thou(b) + " ($000)"}], f"{f1} − {f2}", False)
        if kind == "ratio":
            f1, f2 = args; a, b = _num(rec.get(f1)), _num(rec.get(f2))
            v = f"{a/b*100:.2f}%" if (a is not None and b) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": f1, "val": _thou(a) + " ($000)"},
                            {"label": f2, "val": _thou(b) + " ($000)"}], f"{f1} ÷ {f2} × 100", False)
        if kind == "tce":
            eq = _num(rec.get("EQTOT")); intan = _num(rec.get("INTANGW")) or 0
            v = _usd(eq - intan) if eq is not None else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Total equity", "val": _thou(eq) + " ($000)"},
                            {"label": "Intangibles", "val": _thou(intan) + " ($000)"}],
                           "Total equity − intangibles", False)
        return "—", None

    cells, rows_html, ri = {}, [], 0
    ncol = len(recs_list)
    for sec_name, rows in spec:
        rows_html.append(f'<tr><td class="sec" colspan="{ncol+1}">{sec_name}</td></tr>')
        for row in rows:
            label, kind, args = row[0], row[1], row[2:]
            tds = [f'<td class="lbl">{label}</td>']
            for ci, rec in enumerate(recs_list):
                try:
                    v, c = cell(rec, kind, args, label)
                except Exception:
                    v, c = "—", None
                cid = f"{ri}_{ci}"
                if c:
                    cells[cid] = c
                    tds.append(f'<td class="val" data-cid="{cid}">{v}</td>')
                else:
                    tds.append(f'<td class="val dead">{v}</td>')
            zebra = ' class="zebra"' if ri % 2 == 1 else ""
            rows_html.append(f'<tr{zebra}>{"".join(tds)}</tr>')
            ri += 1

    head = ('<th class="lblh">($ in thousands unless noted)</th>'
            + "".join(f'<th class="colh">{lb}</th>' for lb in labels))
    height = 150 + 29 * (ri + len(spec) + 1)
    sec_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"
                if cik else fdic_link)
    html = _build_component(head, "".join(rows_html), cells, entity, fdic_link, sec_link)

    # Table on the left; fill the empty space to its right with trend charts
    # built from the same FDIC history.
    tr = _DEFAULT_TRENDS if trends is None else trends
    _tbl_col, _chart_col = st.columns([2, 1])
    with _tbl_col:
        components.html(html, height=height, scrolling=False)
    with _chart_col:
        _render_statement_trends(hist, ticker, key_prefix, tr)
    st.caption(f"Latest: FDIC Call Report {_disp(recs_list[-1].get('REPDTE'))} · live each load.")


def _render_statement_trends(hist, ticker, key_prefix, trends):
    if not trends:
        return
    try:
        from ui.charts import metrics_trend_chart
    except Exception:
        return
    h = hist.sort_values("REPDTE").tail(20)
    st.markdown('<div style="font-size:0.7rem; text-transform:uppercase; '
                'letter-spacing:0.04em; color:#64748b; font-weight:700; '
                'margin:2px 0 2px;">Trends</div>', unsafe_allow_html=True)
    for key, label in trends:
        try:
            st.plotly_chart(metrics_trend_chart(h, [key], label),
                            use_container_width=True,
                            key=f"{key_prefix}_tr_{ticker}_{key}")
        except Exception:
            pass


# ── Statement specs ─────────────────────────────────────────────────────────
_INCOME = [
    ("Interest Income", [
        ("Interest & fees on loans", "dollar", "ILNDOM"),
        ("Income on investment securities", "dollar", "ISC"),
        ("Total interest income", "dollar", "INTINC"),
    ]),
    ("Interest Expense", [
        ("Interest on deposits", "dollar", "EDEP"),
        ("Total interest expense", "dollar", "EINTEXP"),
    ]),
    ("Net Interest Income", [
        ("Net interest income", "diff", "INTINC", "EINTEXP"),
        ("Provision for credit losses", "dollar", "ELNATR"),
    ]),
    ("Non-Interest Income", [
        ("Total non-interest income", "dollar", "NONII"),
    ]),
    ("Non-Interest Expense", [
        ("Salaries & employee benefits", "dollar", "ESAL"),
        ("Premises & equipment", "dollar", "EPREMAGG"),
        ("Amortization of intangibles", "dollar", "EAMINTAN"),
        ("Other non-interest expense", "dollar", "EOTHNINT"),
        ("Total non-interest expense", "dollar", "NONIX"),
    ]),
    ("Pre-Tax & Net Income", [
        ("Pre-tax net income", "dollar", "PTAXNETINC"),
        ("Income tax", "dollar", "ITAX"),
        ("Net income", "dollar", "NETINC"),
    ]),
]

_BALANCE = [
    ("Assets", [
        ("Cash & balances due", "dollar", "CHBAL"),
        ("Investment securities", "dollar", "SC"),
        ("Gross loans & leases", "dollar", "LNLSGR"),
        ("Net loans & leases", "dollar", "LNLSNET"),
        ("Total assets", "dollar", "ASSET"),
    ]),
    ("Liabilities", [
        ("Total deposits", "dollar", "DEP"),
        ("Non-interest-bearing deposits", "dollar", "DEPNIDOM"),
        ("Total liabilities", "dollar", "LIAB"),
    ]),
    ("Equity", [
        ("Total equity capital", "dollar", "EQTOT"),
        ("Tangible common equity", "tce"),
        ("Intangible assets", "dollar", "INTANGW"),
    ]),
]

_PERFORMANCE = [
    ("Returns", [
        ("Return on avg assets (ROAA)", "pct", "ROA"),
        ("Return on avg equity (ROAE)", "pct", "ROE"),
    ]),
    ("Margin & Efficiency", [
        ("Net interest margin", "pct", "NIMY"),
        ("Efficiency ratio", "pct", "EEFFR"),
    ]),
    ("Asset Quality", [
        ("Non-current loans / loans", "pct", "NCLNLSR"),
        ("Net charge-offs / loans", "pct", "NTLNLSR"),
        ("Loan-loss reserves / loans", "pct", "LNATRESR"),
    ]),
]

_FAIR_VALUE = [
    ("Investment Securities", [
        ("Total investment securities", "dollar", "SC"),
        ("Securities / total assets", "ratio", "SC", "ASSET"),
    ]),
]

_PORTFOLIO = [
    ("Loan Portfolio", [
        ("Gross loans & leases", "dollar", "LNLSGR"),
        ("Net loans & leases", "dollar", "LNLSNET"),
        ("Loans / total assets", "ratio", "LNLSNET", "ASSET"),
        ("Loan-loss reserves / loans", "pct", "LNATRESR"),
    ]),
    ("Securities Portfolio", [
        ("Total securities", "dollar", "SC"),
        ("Securities / total assets", "ratio", "SC", "ASSET"),
    ]),
]

_CAPITAL_STRUCTURE = [
    ("Capital", [
        ("Total equity capital", "dollar", "EQTOT"),
        ("Tangible common equity", "tce"),
        ("Equity / assets", "ratio", "EQTOT", "ASSET"),
    ]),
    ("Regulatory Capital Ratios", [
        ("CET1 ratio", "pct", "IDT1CER"),
        ("Total risk-based capital ratio", "pct", "RBCRWAJ"),
        ("Tier 1 leverage ratio", "pct", "RBCT1JR"),
    ]),
]


def render_income_statement(ticker):
    render_statement(ticker, "is", "Income Statement", _INCOME)


def render_balance_sheet(ticker):
    render_statement(ticker, "bs", "Balance Sheet", _BALANCE)


def render_performance_analysis(ticker):
    render_statement(ticker, "perf", "Performance Analysis", _PERFORMANCE)


def render_fair_value(ticker):
    render_statement(ticker, "fv", "Fair Value Analysis", _FAIR_VALUE)
    st.caption("Detailed AFS/HTM fair-value and unrealized gain/loss (AOCI) breakdown "
               "from FFIEC Schedule RC-B is on the roadmap.")


def render_portfolio(ticker):
    render_statement(ticker, "port", "Portfolio Analysis", _PORTFOLIO)


def render_capital_structure(ticker):
    render_statement(ticker, "capstruct", "Capital Structure Details", _CAPITAL_STRUCTURE)
