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

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from data.bank_mapping import get_bank_info
from data import fdic_client
from ui.financial_highlights import _build_component


# Shared numeric primitives — one implementation in utils/formatting.
from utils.formatting import (
    num as _num, thou as _thou, pct as _pct,
    usd_compact_from_thousands as _usd,
)


def _yr(repdte):
    return int(repdte.year) if hasattr(repdte, "year") else None


def _mo(repdte):
    return int(repdte.month) if hasattr(repdte, "month") else None


def _disp(repdte):
    try:
        return pd.to_datetime(repdte).strftime("%b %d, %Y")
    except Exception:
        return str(repdte)


def _psd(v):
    v = _num(v)
    return f"${v:,.2f}" if v is not None else "—"


def _eff_tax(rec):
    """Effective tax rate from the period; clamped, default 21%."""
    itax, pti = _num(rec.get("ITAX")), _num(rec.get("PTAXNETINC"))
    if itax is not None and pti:
        return min(max(itax / pti, 0.0), 0.40)
    return 0.21


def _core_income(rec):
    """Net income excluding realized securities gains/losses (tax-effected) and
    extraordinary items — a defensible 'core' earnings figure for banks."""
    ni = _num(rec.get("NETINC"))
    if ni is None:
        return None
    igl = _num(rec.get("IGLSEC")) or 0.0
    extra = _num(rec.get("EXTRA")) or 0.0
    t = _eff_tax(rec)
    return ni - igl * (1 - t) - extra


# Trend charts are GROUPED: each entry is (chart_title, [metric_keys]) and the
# keys in a group plot together on one chart (grouped_trend_chart handles the
# axes — a group mixing $ levels and % ratios gets a secondary axis). Keys come
# from config.METRICS_BY_KEY; test_statement_trends pins that every key here
# exists and carries an FDIC field, so a typo can't silently render a blank.
_DEFAULT_TRENDS = [
    ("Returns & Margin (%)", ["roaa", "nim"]),
    ("Efficiency (%)", ["efficiency_ratio"]),
    ("Capital Ratios (%)", ["cet1_ratio", "leverage_ratio", "total_capital_ratio"]),
    ("Asset Quality (%)", ["npl_ratio", "nco_ratio", "reserve_to_loans"]),
]

# Balance Sheet — composition, mix, and funding trends (the table's own story).
_BS_TRENDS = [
    ("Balance Sheet Size ($B)", ["total_assets", "total_loans", "total_deposits", "total_equity"]),
    ("Asset Mix ($B)", ["total_loans", "securities", "cash_balances", "trading_assets"]),
    ("Loan Portfolio ($B)", ["ln_re_total", "ln_ci", "ln_consumer", "ln_ag"]),
    ("CRE Detail ($B)", ["ln_re_nres_oo", "ln_re_nres_noo", "ln_re_multifam", "ln_re_construct"]),
    ("Residential & Consumer ($B)", ["ln_re_residential", "ln_consumer", "ln_auto", "ln_credit_card"]),
    ("Deposit Composition ($B)", ["total_deposits", "core_deposits", "brokered_deposits", "large_time_dep"]),
    ("Insured vs Uninsured Deposits ($B)", ["insured_deposits", "uninsured_deposits"]),
    ("Securities: AFS vs HTM ($B)", ["sec_afs", "sec_htm", "trading_assets"]),
    ("Loans & Deposits ($B) vs L/D (%)", ["total_loans", "total_deposits", "loans_to_deposits"]),
    ("Equity ($B) vs Capital Ratios (%)", ["total_equity", "cet1_ratio", "leverage_ratio"]),
]


# ── FFIEC Schedule RI / RI-E joins (stored call-report detail) ──────────────
_FTE_TAX_RATE = 0.21   # federal statutory rate for the FTE gross-up


def _fte_adjustment(tax_exempt_loan, tax_exempt_sec, rate=_FTE_TAX_RATE):
    """Fully-taxable-equivalent gross-up of tax-exempt income:
    (RIAD4313 + RIAD4507) × t ÷ (1 − t) with t = 0.21 (federal statutory
    rate). $thousands in → $thousands out. None unless at least one
    component is present (n/a, never a guessed $0); a filed $0 in both
    components yields a true $0 adjustment.
    Hand-check (Banner Bank 12/31/2025): (15,532 + 14,865) × 0.21/0.79
    = 30,397 × 0.265823 = 8,080.2 ($000)."""
    parts = [v for v in (tax_exempt_loan, tax_exempt_sec) if v is not None]
    if not parts:
        return None
    return sum(parts) * rate / (1.0 - rate)


# ── FFIEC deposit-cost split (Schedule RI item 2.a YTD interest + Schedule
# RC-K single-quarter average balances; data/ffiec_client.get_deposit_cost_detail
# semantics). The numerators are calendar-YTD and the denominators are
# single-quarter averages, so a raw quotient is wrong for every quarter except
# Q1 — the rate functions below de-cumulate / average first, and refuse
# (None + reason) whenever an input that would keep the number honest is
# missing. Reasons render as n/a in the click-through, never a guess.
_DEP_PRIOR_MISSING = "prior quarter not ingested — cannot de-cumulate YTD"
_DEP_FY_INCOMPLETE = "incomplete quarterly average history"
_DEP_NO_RECONCILE = "components do not reconcile to total interest expense"

# side → (RIAD YTD-interest codes, RCON quarterly-average codes)
_DEP_SPLIT_CODES = {
    "cds": ("RIADHK03 + RIADHK04", "RCONHK16 + RCONHK17"),
    "other_ib": ("RIAD4508 + RIAD0093", "RCON3485 + RCONB563"),
}


def _dep_quarterly_cost_rate(ytd_q, ytd_prior, avg_q, quarter):
    """Discrete-quarter annualized deposit cost (%) from a calendar-YTD
    Schedule RI interest flow and a single-quarter RC-K average balance
    ($000 in): (YTD_q − YTD_{q−1}) ÷ avg_q × 4 × 100. Q1 uses YTD_q
    directly (calendar YTD resets Jan 1). Returns (rate, None) or
    (None, reason) — raw YTD is NEVER divided by one quarter's average
    for Q2–Q4 (that overstates the rate by the elapsed-quarter count).
    Hand-check: YTD Q2 30,000, YTD Q1 14,000, avg_q2 1,520,000 →
    (16,000 ÷ 1,520,000) × 4 × 100 = 4.2105%."""
    if ytd_q is None:
        return None, "interest expense not reported in this filing"
    if avg_q is None or avg_q <= 0:
        return None, "RC-K average balance not reported in this filing"
    if quarter == 1:
        return ytd_q / avg_q * 4.0 * 100.0, None
    if ytd_prior is None:
        return None, _DEP_PRIOR_MISSING
    return (ytd_q - ytd_prior) / avg_q * 4.0 * 100.0, None


def _dep_annual_cost_rate(fy_int, quarterly_avgs):
    """Full-year deposit cost (%): FY interest (the Dec-31 calendar-YTD
    flow) ÷ mean of the year's four single-quarter RC-K averages × 100
    ($000 in). Any missing quarterly average → (None, reason): a
    partial-year mean posing as the FY denominator is plausible-wrong.
    Hand-check (synthetic FY, CD interest 54,368): mean(1,500,000;
    1,520,000; 1,540,000; 1,539,845) = 1,524,961.25 →
    54,368 ÷ 1,524,961.25 × 100 = 3.5652%."""
    if fy_int is None:
        return None, "interest expense not reported in this filing"
    if len(quarterly_avgs) != 4 or any(a is None for a in quarterly_avgs):
        return None, _DEP_FY_INCOMPLETE
    mean = sum(quarterly_avgs) / 4.0
    if mean <= 0:
        return None, "RC-K average balances not positive"
    return fy_int / mean * 100.0, None


def _prior_quarter_end(dt):
    """Calendar quarter-end immediately before dt (itself a quarter-end)."""
    return (pd.Timestamp(dt).normalize() - pd.offsets.QuarterEnd(1))


def _dep_cost_by_date(cert):
    """{normalized report date → stored deposit-cost split dict}
    (data/call_report_store, ffiec_client.get_deposit_cost_detail shape).
    Keyed by DATE rather than column index because the rate math reaches
    beyond the displayed columns: de-cumulating a quarter needs the PRIOR
    quarter's YTD row, and an FY rate needs all four quarterly averages.
    Empty dict when the store is unavailable (local dev) or nothing is
    ingested; missing dates render dead, never imputed."""
    try:
        from data.call_report_store import get_stored_deposit_cost_detail
        # 40 quarters ≥ the Annual view's 5-FY lookback.
        rows = get_stored_deposit_cost_detail(cert, quarters=40)
    except Exception as e:
        print(f"[statements] deposit-cost store unavailable for cert {cert}: "
              f"{type(e).__name__}: {e}")
        return {}
    out = {}
    for d in rows:
        try:
            out[pd.to_datetime(d.get("reporting_period")).normalize()] = d
        except Exception:
            continue
    return out


def _ri_details_by_column(cert, recs_list):
    """{column index → stored detail dict} for Schedule RI and Schedule RI-E
    (data/call_report_store), joined on report date. RI and RI-E are YTD
    within the calendar year — the SAME convention as the FDIC SDI income
    fields this table already shows raw per column — so a date join is the
    whole story: no diffing, no annualizing. Returns empty dicts when the
    store is unavailable (local dev) or nothing is ingested; missing columns
    render dead, never imputed."""
    try:
        from data.call_report_store import (get_stored_ri_detail,
                                            get_stored_rie_detail)

        def _by_date(rows):
            out = {}
            for d in rows:
                try:
                    out[pd.to_datetime(d.get("reporting_period")).normalize()] = d
                except Exception:
                    continue
            return out

        # 40 quarters ≥ the Annual view's 5-FY lookback.
        ri = _by_date(get_stored_ri_detail(cert, quarters=40))
        rie = _by_date(get_stored_rie_detail(cert, quarters=40))
    except Exception as e:
        print(f"[statements] RI/RI-E store unavailable for cert {cert}: "
              f"{type(e).__name__}: {e}")
        return {}, {}
    ri_by_ci, rie_by_ci = {}, {}
    for i, r in enumerate(recs_list):
        try:
            dt = pd.to_datetime(r.get("REPDTE")).normalize()
        except Exception:
            continue
        if dt in ri:
            ri_by_ci[i] = ri[dt]
        if dt in rie:
            rie_by_ci[i] = rie[dt]
    return ri_by_ci, rie_by_ci


# RI-E preprinted itemizations of "all other noninterest expense" — official
# MDRM item names (data/ffiec_client._RI_E_EXPENSE_CODES), shown indented
# under "Other non-interest expense". (key, RIAD code, label)
_RIE_EXPENSE_ROWS = [
    ("data_processing", "C017", "Data processing expenses"),
    ("marketing_professional", "0497", "Marketing and other professional services"),
    ("directors_fees", "4136", "Directors' fees"),
    ("printing_supplies", "C018", "Printing, stationery, and supplies"),
    ("postage", "8403", "Postage"),
    ("legal", "4141", "Legal expense"),
    ("fdic_assessments", "4146", "Federal insurance premium"),
    ("accounting_auditing", "F556", "Accounting and auditing expenses"),
    ("consulting_advisory", "F557", "Consulting and advisory expense"),
    ("atm_interchange", "F558", "ATM and interchange expense"),
    ("telecommunications", "F559", "Telecommunications expense"),
]

_RIE_INDENT = "&nbsp;&nbsp;&nbsp;&nbsp;"


def _spec_with_rie_rows(spec, rie_by_ci):
    """Insert the RI-E sub-block: the preprinted itemized expense lines plus
    the bank's labeled expense write-ins under "Other non-interest expense",
    and its labeled income write-ins under "Other non-interest income".
    A bank that itemized nothing in the displayed window gets no sub-block
    (a wall of n/a is noise, not honesty); a bank with SOME itemized lines
    shows every preprinted line — n/a marks below-threshold lines, with the
    reason in the click-through."""
    if not rie_by_ci:
        return spec
    import html as _html
    details = list(rie_by_ci.values())

    def _writein_rows(list_key):
        seen, rows = set(), []
        for det in details:
            for w in det.get(list_key) or []:
                lb = str(w.get("label") or "")
                if not lb or lb in seen:
                    continue
                seen.add(lb)
                # Filed free text goes into the table HTML — escape it.
                rows.append((f"{_RIE_INDENT}Write-in: {_html.escape(lb)}",
                             "rie_wi", list_key, lb))
        return rows

    expense_rows = [(f"{_RIE_INDENT}{lbl}", "rie", key, code, lbl)
                    for key, code, lbl in _RIE_EXPENSE_ROWS]
    expense_rows += _writein_rows("expense_writeins")
    income_rows = _writein_rows("income_writeins")

    out = []
    for sec_name, rows in spec:
        new_rows = []
        for row in rows:
            new_rows.append(row)
            if row[0] == "Other non-interest expense":
                new_rows.extend(expense_rows)
            elif row[0] == "Other non-interest income" and income_rows:
                new_rows.extend(income_rows)
        out.append((sec_name, new_rows))
    return out


def render_statement(ticker: str, key_prefix: str, title: str, spec: list,
                     trends: list | None = None, with_persh: bool = False,
                     with_ri: bool = False, with_dep_cost: bool = False,
                     with_fte: bool = False, side_by_side: bool = False):
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

    # FFIEC Schedule RI / RI-E stored detail (Income Statement only) — joined
    # by report date; the RI-E itemized-expense sub-block is inserted only
    # when the bank actually itemized something in the displayed window.
    ri_by_ci, rie_by_ci = {}, {}
    if with_ri or with_fte:
        # with_fte loads RI tax-exempt income for the FTE-NIM line WITHOUT the
        # RI-E expense-row insertion (which only belongs on the Income tab).
        ri_by_ci, rie_by_ci = _ri_details_by_column(cert, recs_list)
    if with_ri:
        spec = _spec_with_rie_rows(spec, rie_by_ci)

    # FFIEC deposit-cost split (Performance Analysis only) — keyed by report
    # date, not column index: the rate math needs the prior quarter's row
    # (de-cumulation) and all four quarterly averages (FY mean).
    dep_by_date = _dep_cost_by_date(cert) if with_dep_cost else {}

    # Per-share data (SEC holding-company filings) — only loaded when a spec
    # needs it (Performance Analysis), keyed by column index.
    ps_by_ci = {}
    if with_persh and cik:
        try:
            from ui.financial_highlights import _per_share_for_ends
            ends = [pd.to_datetime(r["REPDTE"]).to_pydatetime() for r in recs_list]
            persh = _per_share_for_ends(cik, ends, quarterly=(period == "Quarterly"))
            ps_by_ci = {i: (persh.get(ends[i], {}) or {}) for i in range(len(recs_list))}
        except Exception:
            ps_by_ci = {}

    fdic_link = f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"
    sec_filing_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                       f"&CIK={cik}&type=10-K") if cik else fdic_link
    entity = f"{name} ({ticker})"

    def calc(metric, v, asof, ref, terms, op, reported,
             source="FDIC Call Report", link=None):
        return {"metric": metric, "entity": entity, "source": source,
                "asof": asof, "unit": "", "ref": ref, "definition": "",
                "terms": terms, "op": op, "reported": reported,
                "link": link or fdic_link}

    def _af(rec):
        """Annualization factor — YTD interim figures are scaled to an annual
        rate; Q4 / fiscal-year records pass through unchanged."""
        m = _mo(rec.get("REPDTE")) or 12
        return 12.0 / m if m else 1.0

    def _avg(ci, field):
        """Average balance over the current and prior period (begin+end)/2;
        falls back to the period-end value for the first column."""
        cur = _num(recs_list[ci].get(field))
        if ci > 0:
            prev = _num(recs_list[ci - 1].get(field))
            if cur is not None and prev is not None:
                return (cur + prev) / 2.0
        return cur

    def _avgsum(ci, fields):
        """Average of a SUM of balances (e.g. funding = deposits + borrowings).
        Linear, so avg(A+B)=avg(A)+avg(B); a None component is treated as 0.
        Returns None only if no component is present at all."""
        tot, seen = 0.0, False
        for fl in fields:
            a = _avg(ci, fl)
            if a is not None:
                tot += a
                seen = True
        return tot if seen else None

    def _revenue(rec):
        ii, ie, noni = _num(rec.get("INTINC")), _num(rec.get("EINTEXP")), _num(rec.get("NONII"))
        return (ii - ie + noni) if None not in (ii, ie, noni) else None

    def _ri_doc_link(rec):
        """FFIEC CDR facsimile for this cert + quarter (RI / RI-E rows)."""
        try:
            from ui.financial_highlights import _fdic_doc
            return _fdic_doc(cert, rec.get("REPDTE"))["url"]
        except Exception:
            return fdic_link

    def _term000(v):
        """$000 term value; absent stays honest — never rendered as $0."""
        return _thou(v) + " ($000)" if v is not None else "n/a — not reported in this filing"

    def cell(ci, kind, args, label):
        rec = recs_list[ci]
        asof = _disp(rec.get("REPDTE"))
        f = _af(rec)
        if kind == "dollar":
            fl = args[0]; raw = _num(rec.get(fl))
            return _usd(raw), calc(label, _usd(raw), asof, f"FDIC field {fl}",
                                   [{"label": label, "val": _thou(raw) + " ($000)"}], None, True)
        if kind == "pct":
            fl = args[0]; raw = _num(rec.get(fl))
            return _pct(raw), calc(label, _pct(raw), asof, f"FDIC field {fl}",
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
        if kind == "noniother":
            # Residual "other" noninterest income = NONII − the itemized rows
            # shown above it (SNL semantics: their "other" is a remainder, not
            # the narrow IOTHII field). args = the itemized FDIC fields; the
            # residual shrinks honestly as more lines get itemized (RI rows).
            total = _num(rec.get("NONII"))
            parts = [(fl, _num(rec.get(fl)) or 0) for fl in args]
            v = _usd(total - sum(p for _, p in parts)) if total is not None else "—"
            terms = [{"label": "Total non-interest income (NONII)",
                      "val": _thou(total) + " ($000)"}]
            terms += [{"label": f"− {fl}", "val": _thou(p) + " ($000)"} for fl, p in parts]
            return v, calc(label, v, asof, "Computed from Call Report", terms,
                           "NONII − itemized non-interest income lines", False)
        if kind == "ppnr":
            # Pre-provision net revenue = NII + noninterest income − noninterest
            # expense: operating earnings power before credit costs (SNL line).
            ii, ie = _num(rec.get("INTINC")), _num(rec.get("EINTEXP"))
            noni, nonx = _num(rec.get("NONII")), _num(rec.get("NONIX"))
            ok = None not in (ii, ie, noni, nonx)
            v = _usd(ii - ie + noni - nonx) if ok else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net interest income (INTINC − EINTEXP)",
                             "val": _thou(ii - ie) + " ($000)" if None not in (ii, ie) else "—"},
                            {"label": "Noninterest income (NONII)", "val": _thou(noni) + " ($000)"},
                            {"label": "Noninterest expense (NONIX)", "val": _thou(nonx) + " ($000)"}],
                           "NII + noninterest income − noninterest expense", False)
        if kind == "etr":
            # Effective tax rate = income tax ÷ pre-tax income × 100
            tax, ptx = _num(rec.get("ITAX")), _num(rec.get("PTAXNETINC"))
            v = f"{tax/ptx*100:.2f}%" if (tax is not None and ptx) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Income tax (ITAX)", "val": _thou(tax) + " ($000)"},
                            {"label": "Pre-tax net income (PTAXNETINC)", "val": _thou(ptx) + " ($000)"}],
                           "ITAX ÷ PTAXNETINC × 100", False)
        if kind == "tce":
            # TCE = equity − TOTAL intangibles (INTAN, incl. goodwill) — the
            # standard convention, and the same field the roatce kind uses.
            # (Previously this used INTANGW = goodwill only while roatce used
            # INTAN, so "TCE" and "ROATCE" on the same page disagreed.)
            eq = _num(rec.get("EQTOT")); intan = _num(rec.get("INTAN")) or 0
            v = _usd(eq - intan) if eq is not None else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Total equity", "val": _thou(eq) + " ($000)"},
                            {"label": "Intangibles (incl. goodwill)", "val": _thou(intan) + " ($000)"}],
                           "Total equity − total intangibles", False)
        if kind == "roatce":
            ni = _num(rec.get("NETINC")); eq = _num(rec.get("EQTOT"))
            intan = _num(rec.get("INTAN")) or 0
            tce = (eq - intan) if eq is not None else None
            v = f"{ni*f/tce*100:.2f}%" if (ni is not None and tce and tce > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(ni*f)) + " ($000)" if ni is not None else "—"},
                            {"label": "Tangible common equity", "val": _thou(tce) + " ($000)"}],
                           "Net income ÷ tangible common equity × 100", False)
        if kind == "marginrev":   # flow ÷ total revenue × 100 (both YTD, no annualizing)
            fl = args[0]; n = _num(rec.get(fl)); rev = _revenue(rec)
            v = f"{n/rev*100:.2f}%" if (n is not None and rev) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": fl, "val": _thou(n) + " ($000)"},
                            {"label": "Total revenue (NII + non-int income)",
                             "val": _thou(round(rev)) + " ($000)" if rev else "—"}],
                           f"{fl} ÷ total revenue × 100", False)
        if kind == "pctdiff":     # A% − B%
            a, b = _num(rec.get(args[0])), _num(rec.get(args[1]))
            v = f"{a-b:.2f}%" if (a is not None and b is not None) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": args[0], "val": _pct(a)},
                            {"label": args[1], "val": _pct(b)}], f"{args[0]} − {args[1]}", False)
        if kind == "yield":       # flow (annualized) ÷ avg balance × 100
            nf, df_ = args; n = _num(rec.get(nf)); d = _avg(ci, df_)
            v = f"{n*f/d*100:.2f}%" if (n is not None and d) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": nf + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(n*f)) + " ($000)" if n is not None else "—"},
                            {"label": "Avg " + df_, "val": _thou(round(d)) + " ($000)" if d else "—"}],
                           f"{nf} ÷ avg {df_} × 100", False)
        if kind == "yield2":      # (A − B) annualized ÷ avg balance × 100
            af_, bf_, df_ = args
            a, b, d = _num(rec.get(af_)), _num(rec.get(bf_)), _avg(ci, df_)
            v = f"{(a-b)*f/d*100:.2f}%" if (a is not None and b is not None and d) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": f"{af_} − {bf_}" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round((a-b)*f)) + " ($000)" if (a is not None and b is not None) else "—"},
                            {"label": "Avg " + df_, "val": _thou(round(d)) + " ($000)" if d else "—"}],
                           f"({af_} − {bf_}) ÷ avg {df_} × 100", False)
        if kind == "roate":       # Return on avg tangible (total) equity
            ni = _num(rec.get("NETINC")); eq = _num(rec.get("EQTOT"))
            intan = _num(rec.get("INTAN")) or 0
            te = (eq - intan) if eq is not None else None
            v = f"{ni*f/te*100:.2f}%" if (ni is not None and te and te > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(ni*f)) + " ($000)" if ni is not None else "—"},
                            {"label": "Avg tangible equity (EQTOT − INTAN)",
                             "val": _thou(round(te)) + " ($000)" if te is not None else "—"}],
                           "Net income ÷ tangible equity × 100", False)
        if kind == "roace":       # Return on avg COMMON equity
            ni = _num(rec.get("NETINC")); eq = _num(rec.get("EQTOT"))
            pfd = _num(rec.get("EQPP")) or 0
            ce = (eq - pfd) if eq is not None else None
            if pfd > 0:
                # NI-to-common needs preferred dividends (FFIEC Schedule RI-A),
                # which the FDIC SDI feed does not carry. Dividing total NI by
                # common equity would OVERSTATE the return by the preferred
                # dividend, so render n/a + flag rather than a wrong number.
                return "n/a", calc(label, "n/a", asof,
                                   "n/a — preferred dividends not in FDIC SDI feed",
                                   [{"label": "Preferred equity (EQPP)",
                                     "val": _thou(pfd) + " ($000)"},
                                    {"label": label,
                                     "val": "n/a — needs RI-A preferred dividends (later phase)"}],
                                   "(Net income − preferred dividends) ÷ avg common equity × 100", False)
            v = f"{ni*f/ce*100:.2f}%" if (ni is not None and ce and ce > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(ni*f)) + " ($000)" if ni is not None else "—"},
                            {"label": "Avg common equity (EQTOT − EQPP)",
                             "val": _thou(round(ce)) + " ($000)" if ce is not None else "—"}],
                           "Net income ÷ common equity × 100 (no preferred outstanding)", False)
        if kind == "netopex":     # Net operating expense ÷ avg assets
            nonx, noni = _num(rec.get("NONIX")), _num(rec.get("NONII"))
            a = _avg(ci, "ASSET")
            net = (nonx - noni) if (nonx is not None and noni is not None) else None
            v = f"{net*f/a*100:.2f}%" if (net is not None and a) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net op. expense (NONIX − NONII)" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(net*f)) + " ($000)" if net is not None else "—"},
                            {"label": "Avg assets (ASSET)",
                             "val": _thou(round(a)) + " ($000)" if a else "—"}],
                           "(NONIX − NONII) ÷ avg assets × 100", False)
        if kind == "costfunds":   # Cost of funds: int exp ÷ avg total funding
            ie = _num(rec.get("EINTEXP"))
            # SUBND is the sub-debt BALANCE (ESUBND is its interest expense —
            # never mix an expense into a balance denominator).
            fund = _avgsum(ci, ["DEP", "FREPP", "OTHBFHLB", "SUBND"])
            v = f"{ie*f/fund*100:.2f}%" if (ie is not None and fund) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Total interest expense (EINTEXP)" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(ie*f)) + " ($000)" if ie is not None else "—"},
                            {"label": "Avg funding (deposits + borrowings)",
                             "val": _thou(round(fund)) + " ($000)" if fund else "—"}],
                           "Total interest expense ÷ avg (deposits + borrowings) × 100", False)
        if kind == "costdebt":    # Cost of borrowings/debt
            ie, ed = _num(rec.get("EINTEXP")), _num(rec.get("EDEP"))
            borint = (ie - ed) if (ie is not None and ed is not None) else None
            # SUBND is the sub-debt BALANCE (not ESUBND, its interest expense).
            bor = _avgsum(ci, ["FREPP", "OTHBFHLB", "SUBND"])
            rate = (borint * f / bor * 100) if (borint is not None and bor and bor >= 1000) else None
            # Borrowings swing intra-year while we only see the period-end
            # balance, so a real interest figure over a shrunk year-end balance
            # can overstate the rate. n/a outside a plausible band (0–8%) rather
            # than print a double-digit "cost of debt" that isn't real.
            if rate is None or rate <= 0 or rate > 8:
                why = ("negligible borrowings" if (not bor or bor < 1000)
                       else "period-end borrowings make the rate unreliable")
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   [{"label": "Avg borrowings (FREPP + OTHBFHLB + SUBND)",
                                     "val": _thou(round(bor)) + " ($000)" if bor else "n/a"},
                                    {"label": label, "val": "n/a — " + why}],
                                   "(EINTEXP − EDEP) ÷ avg borrowings × 100", False)
            v = f"{rate:.2f}%"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Borrowings interest (EINTEXP − EDEP)" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(borint*f)) + " ($000)" if borint is not None else "—"},
                            {"label": "Avg borrowings (FREPP + OTHBFHLB + SUBND)",
                             "val": _thou(round(bor)) + " ($000)"}],
                           "(EINTEXP − EDEP) ÷ avg borrowings × 100", False)
        # ── FFIEC Schedule RI: FTE NII derivation (computed) ─────────────
        if kind in ("fte_adj", "nii_fte"):
            det = ri_by_ci.get(ci)
            if det is None:
                return "—", None   # RI detail not ingested for this period
            tel = _num(det.get("tax_exempt_loan_income"))
            tes = _num(det.get("tax_exempt_sec_income"))
            fte = _fte_adjustment(tel, tes)
            doc_link = _ri_doc_link(rec)
            te_terms = [
                {"label": "Tax-exempt income on loans (RIAD4313)",
                 "val": _term000(tel)},
                {"label": "Tax-exempt income on securities (RIAD4507)",
                 "val": _term000(tes)},
            ]
            ref = "computed — statutory 21% federal rate"
            if kind == "fte_adj":
                op = ("(RIAD4313 + RIAD4507) × t ÷ (1 − t), t = 0.21 — "
                      "computed — statutory 21% federal rate")
                if fte is None:
                    return "n/a", calc(label, "n/a", asof, ref, te_terms + [
                        {"label": "FTE adjustment",
                         "val": "n/a — tax-exempt income not reported"}],
                        op, False, source="FFIEC Call Report — Schedule RI",
                        link=doc_link)
                v = _usd(fte)
                return v, calc(label, v, asof, ref, te_terms + [
                    {"label": "FTE adjustment = sum × 0.21 ÷ 0.79",
                     "val": _thou(round(fte)) + " ($000)"}],
                    op, False, source="FFIEC Call Report — Schedule RI",
                    link=doc_link)
            # nii_fte
            ii, ie = _num(rec.get("INTINC")), _num(rec.get("EINTEXP"))
            nii = ii - ie if None not in (ii, ie) else None
            op = ("NII + (RIAD4313 + RIAD4507) × 0.21 ÷ (1 − 0.21) — "
                  "computed — statutory 21% federal rate")
            terms = [{"label": "Net interest income (INTINC − EINTEXP)",
                      "val": _term000(nii)}] + te_terms + [
                     {"label": "FTE adjustment = tax-exempt sum × 0.21 ÷ 0.79",
                      "val": (_thou(round(fte)) + " ($000)") if fte is not None
                             else "n/a — tax-exempt income not reported"}]
            if fte is None or nii is None:
                return "n/a", calc(label, "n/a", asof, ref, terms, op, False,
                                   source="FFIEC Call Report — Schedule RI",
                                   link=doc_link)
            v = _usd(nii + fte)
            return v, calc(label, v, asof, ref, terms, op, False,
                           source="FFIEC Call Report — Schedule RI",
                           link=doc_link)
        if kind == "ftenim":
            # FTE net interest margin = reported NIM + the tax-equivalent gross-up
            # of muni interest, expressed in bps of avg earning assets. n/a when
            # Schedule RI tax-exempt income isn't ingested for this column — a
            # bank without that detail shows reported NIM only, never a guess.
            nim = _num(rec.get("NIMY")); ea = _avg(ci, "ERNAST")
            det = ri_by_ci.get(ci)
            op = "reported NIM + FTE adjustment ÷ avg earning assets × 100"
            if det is None or nim is None or not ea:
                return "n/a", calc(label, "n/a", asof,
                                   "n/a — Schedule RI tax-exempt income not ingested",
                                   [{"label": label,
                                     "val": "n/a — needs FFIEC RI tax-exempt income"}],
                                   op, False)
            tel = _num(det.get("tax_exempt_loan_income"))
            tes = _num(det.get("tax_exempt_sec_income"))
            fte = _fte_adjustment(tel, tes)
            doc_link = _ri_doc_link(rec)
            if fte is None:
                return "n/a", calc(label, "n/a", asof,
                                   "computed — statutory 21% federal rate",
                                   [{"label": "Reported NIM (NIMY)", "val": _pct(nim)},
                                    {"label": "FTE adjustment",
                                     "val": "n/a — tax-exempt income not reported"}],
                                   op, False, source="FFIEC Call Report — Schedule RI",
                                   link=doc_link)
            v = f"{nim + fte * f / ea * 100:.2f}%"
            return v, calc(label, v, asof, "computed — statutory 21% federal rate",
                           [{"label": "Reported NIM (NIMY)", "val": _pct(nim)},
                            {"label": "FTE adjustment" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(fte * f)) + " ($000)"},
                            {"label": "Avg earning assets (ERNAST)",
                             "val": _thou(round(ea)) + " ($000)"}],
                           op, False, source="FFIEC Call Report — Schedule RI",
                           link=doc_link)
        # ── FFIEC Schedule RI-E: itemized other noninterest expense ──────
        if kind == "rie":
            key, code, clean = args
            det = rie_by_ci.get(ci)
            if det is None:
                return "—", None   # RI-E not ingested for this period
            doc_link = _ri_doc_link(rec)
            ref = f"Schedule RI-E (MDRM RIAD{code})"
            raw = _num(det.get(key))
            if raw is None:
                # None = below the itemization threshold — NOT a $0.
                return "n/a", calc(clean, "n/a", asof, ref, [
                    {"label": clean,
                     "val": "n/a — below the RI-E itemization threshold "
                            "(not itemized in this filing; a filed $0 "
                            "would show $0)"}],
                    None, True, source="FFIEC Call Report — Schedule RI-E",
                    link=doc_link)
            v = _usd(raw)
            return v, calc(clean, v, asof, ref, [
                {"label": clean, "val": _thou(raw) + " ($000)"}],
                None, True, source="FFIEC Call Report — Schedule RI-E",
                link=doc_link)
        if kind == "rie_wi":
            list_key, wlabel = args
            det = rie_by_ci.get(ci)
            if det is None:
                return "—", None   # RI-E not ingested for this period
            clean = f"Write-in: {wlabel}"
            codes = ("RIAD4461/4462/4463" if list_key == "income_writeins"
                     else "RIAD4464/4467/4468")
            doc_link = _ri_doc_link(rec)
            match = next((w for w in (det.get(list_key) or [])
                          if str(w.get("label") or "") == wlabel), None)
            raw = _num(match.get("value")) if match else None
            if raw is None:
                return "n/a", calc(clean, "n/a", asof,
                                   f"Schedule RI-E write-in ({codes})", [
                    {"label": clean,
                     "val": "n/a — not filed as a write-in this period "
                            "(only amounts above the itemization "
                            "threshold are listed)"}],
                    None, True, source="FFIEC Call Report — Schedule RI-E",
                    link=doc_link)
            v = _usd(raw)
            return v, calc(clean, v, asof,
                           f"Schedule RI-E write-in ({codes}) — bank's own "
                           "filed label", [
                {"label": clean, "val": _thou(raw) + " ($000)"}],
                None, True, source="FFIEC Call Report — Schedule RI-E",
                link=doc_link)
        # ── FFIEC deposit-cost split: CD vs other interest-bearing ───────
        if kind in ("dep_rate", "dep_flow", "dep_avg"):
            side = args[0]                       # "cds" | "other_ib"
            riad, rcon = _DEP_SPLIT_CODES[side]
            int_key, avg_key = f"int_{side}", f"avg_{side}"
            dt = pd.to_datetime(rec.get("REPDTE")).normalize()
            det = dep_by_date.get(dt)
            if det is None:
                return "—", None   # split not ingested for this period
            doc_link = _ri_doc_link(rec)
            src = "FFIEC Call Report — Schedule RI 2.a / RC-K"
            ref = f"Schedule RI item 2.a ({riad}) / Schedule RC-K ({rcon})"
            if det.get("reconciles") is not True:
                # False = the components don't sum to RIAD4073 net of
                # 4180/4185/4200 (the split is missing a piece); None =
                # RIAD4073 absent (identity unverifiable). Either way no
                # number from this row may be displayed as the split.
                return "n/a", calc(label, "n/a", asof, ref, [
                    {"label": label, "val": f"n/a — {_DEP_NO_RECONCILE}"}],
                    None, True, source=src, link=doc_link)
            annual = (period == "Annual")
            q_ends = [pd.Timestamp(dt.year, m, d) for m, d in
                      ((3, 31), (6, 30), (9, 30), (12, 31))]
            if kind == "dep_flow":
                raw = _num(det.get(int_key))
                ref_f = f"Schedule RI item 2.a ({riad}) — calendar-YTD"
                if raw is None:
                    return "n/a", calc(label, "n/a", asof, ref_f, [
                        {"label": label,
                         "val": "n/a — not reported in this filing"}],
                        None, True, source=src, link=doc_link)
                v = _usd(raw)
                return v, calc(label, v, asof, ref_f, [
                    {"label": label + " (calendar-YTD, as filed)",
                     "val": _thou(raw) + " ($000)"}],
                    None, True, source=src, link=doc_link)
            if kind == "dep_avg":
                if not annual:
                    raw = _num(det.get(avg_key))
                    ref_a = f"Schedule RC-K ({rcon}) — single-quarter average"
                    if raw is None:
                        return "n/a", calc(label, "n/a", asof, ref_a, [
                            {"label": label,
                             "val": "n/a — not reported in this filing"}],
                            None, True, source=src, link=doc_link)
                    v = _usd(raw)
                    return v, calc(label, v, asof, ref_a, [
                        {"label": label + " (single-quarter average, as filed)",
                         "val": _thou(raw) + " ($000)"}],
                        None, True, source=src, link=doc_link)
                # Annual column: RC-K averages are single-quarter, so the FY
                # figure is the mean of the year's four quarterly averages —
                # computed; n/a unless all four quarters are ingested. (The
                # reconciles gate above applies to the RI interest split;
                # RC-K balance averages join it here only as FY-mean inputs.)
                avgs = [_num((dep_by_date.get(qe) or {}).get(avg_key))
                        for qe in q_ends]
                terms = [{"label": f"Q{i + 1} average ({rcon})",
                          "val": _term000(a)} for i, a in enumerate(avgs)]
                op = f"mean of the four quarterly RC-K averages ({rcon}) — computed"
                if any(a is None for a in avgs):
                    return "n/a", calc(label, "n/a", asof, ref, terms + [
                        {"label": label, "val": f"n/a — {_DEP_FY_INCOMPLETE}"}],
                        op, False, source=src, link=doc_link)
                mean = sum(avgs) / 4.0
                v = _usd(mean)
                return v, calc(label, v, asof, ref, terms + [
                    {"label": label + " (mean of quarterly averages)",
                     "val": _thou(round(mean)) + " ($000)"}],
                    op, False, source=src, link=doc_link)
            # dep_rate — annualized cost (%), de-cumulated; computed.
            ytd_q = _num(det.get(int_key))
            if annual:
                avgs = [_num((dep_by_date.get(qe) or {}).get(avg_key))
                        for qe in q_ends]
                rate, reason = _dep_annual_cost_rate(ytd_q, avgs)
                op = (f"FY interest (calendar-YTD at Dec 31, {riad}) ÷ mean of "
                      f"the four quarterly RC-K averages ({rcon}) × 100 — computed")
                terms = [{"label": f"FY interest ({riad}, calendar-YTD)",
                          "val": _term000(ytd_q)}]
                terms += [{"label": f"Q{i + 1} average balance ({rcon})",
                           "val": _term000(a)} for i, a in enumerate(avgs)]
                if rate is None:
                    return "n/a", calc(label, "n/a", asof, ref, terms + [
                        {"label": label, "val": f"n/a — {reason}"}],
                        op, False, source=src, link=doc_link)
                terms.append({"label": "Mean of quarterly averages",
                              "val": _thou(round(sum(avgs) / 4.0)) + " ($000)"})
                v = f"{rate:.2f}%"
                return v, calc(label, v, asof, ref, terms, op, False,
                               source=src, link=doc_link)
            q = (dt.month - 1) // 3 + 1
            avg_q = _num(det.get(avg_key))
            ytd_prior, prior_note = None, None
            if q != 1:
                pdet = dep_by_date.get(_prior_quarter_end(dt))
                if pdet is None:
                    pass   # canonical _DEP_PRIOR_MISSING from the rate fn
                elif pdet.get("reconciles") is not True:
                    prior_note = ("prior quarter components do not reconcile "
                                  "— cannot de-cumulate YTD")
                else:
                    ytd_prior = _num(pdet.get(int_key))
                    if ytd_prior is None:
                        prior_note = ("prior quarter interest not reported "
                                      "— cannot de-cumulate YTD")
            rate, reason = _dep_quarterly_cost_rate(ytd_q, ytd_prior, avg_q, q)
            if reason == _DEP_PRIOR_MISSING and prior_note:
                reason = prior_note   # ingested-but-unusable beats "not ingested"
            op = (f"(YTD_q − YTD_q−1) ÷ avg_q × 4 × 100, annualized; Q1 uses "
                  f"YTD directly (calendar YTD resets) — interest {riad} "
                  f"(calendar-YTD), average balance {rcon} (single-quarter) "
                  f"— computed")
            terms = [
                {"label": f"Interest, calendar-YTD this quarter ({riad})",
                 "val": _term000(ytd_q)},
                {"label": f"Interest, calendar-YTD prior quarter ({riad})",
                 "val": ("— (Q1: calendar YTD resets)" if q == 1 else
                         _thou(ytd_prior) + " ($000)" if ytd_prior is not None
                         else f"n/a — {prior_note or _DEP_PRIOR_MISSING}")},
                {"label": f"Average balance this quarter ({rcon})",
                 "val": _term000(avg_q)},
            ]
            if rate is None:
                return "n/a", calc(label, "n/a", asof, ref, terms + [
                    {"label": label, "val": f"n/a — {reason}"}],
                    op, False, source=src, link=doc_link)
            v = f"{rate:.2f}%"
            return v, calc(label, v, asof, ref, terms, op, False,
                           source=src, link=doc_link)
        # ── Balance-sheet computed kinds ─────────────────────────────────
        if kind == "na":
            # Honest gap: a line SNL shows but the FDIC SDI feed cannot
            # source. args[0] is the reason (e.g. "not separable in FDIC";
            # "FFIEC RC-F — later phase"). Never a guessed $0.
            reason = args[0] if args else "not available from this source"
            return "n/a", calc(label, "n/a", asof, "n/a — " + reason,
                               [{"label": label, "val": "n/a — " + reason}],
                               None, True)
        if kind == "sum":
            # Computed subtotal = sum of the named FDIC fields that are
            # present. Absent fields are skipped (a None component is "n/a",
            # not $0); if NONE are present the subtotal is n/a, never $0.
            present = [(fl, _num(rec.get(fl))) for fl in args]
            vals = [(fl, v) for fl, v in present if v is not None]
            if not vals:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   [{"label": fl, "val": "n/a — not reported"}
                                    for fl in args],
                                   " + ".join(args), False)
            total = sum(v for _, v in vals)
            terms = [{"label": fl, "val": _thou(v) + " ($000)"} for fl, v in vals]
            return _usd(total), calc(label, _usd(total), asof,
                                     "Computed from Call Report", terms,
                                     " + ".join(args), False)
        if kind == "htm":
            # Held-to-maturity securities: the filed SCHA field when present,
            # else the residual SC − SCAF − TRADE. Banner: SCHA 943,973 vs
            # residual 944,198 (0.02% apart) — SCHA is the filed value, used.
            scha = _num(rec.get("SCHA"))
            if scha is not None:
                return _usd(scha), calc(label, _usd(scha), asof,
                                        "FDIC field SCHA",
                                        [{"label": "Held-to-maturity securities (SCHA)",
                                          "val": _thou(scha) + " ($000)"}], None, True)
            sc, scaf = _num(rec.get("SC")), _num(rec.get("SCAF"))
            tr = _num(rec.get("TRADE")) or 0
            if sc is None or scaf is None:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   [{"label": "SCHA / SC / SCAF",
                                     "val": "n/a — not reported"}],
                                   "SC − SCAF − TRADE", False)
            v = sc - scaf - tr
            return _usd(v), calc(label, _usd(v), asof, "Computed from Call Report",
                                 [{"label": "Total securities (SC)", "val": _thou(sc) + " ($000)"},
                                  {"label": "− AFS securities (SCAF)", "val": _thou(scaf) + " ($000)"},
                                  {"label": "− Trading (TRADE)", "val": _thou(tr) + " ($000)"}],
                                 "SC − SCAF − TRADE", False)
        if kind == "otherint":
            # Other intangibles = INTAN − INTANGW − INTANMSR. A negative
            # residual is a data problem → n/a + flag, never a negative plug.
            intan, gw = _num(rec.get("INTAN")), _num(rec.get("INTANGW"))
            msr = _num(rec.get("INTANMSR")) or 0
            terms = [{"label": "Total intangibles (INTAN)", "val": _term000(intan)},
                     {"label": "− Goodwill (INTANGW)", "val": _term000(gw)},
                     {"label": "− Mortgage servicing intangible (INTANMSR)",
                      "val": _thou(msr) + " ($000)"}]
            if intan is None or gw is None:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   terms, "INTAN − INTANGW − INTANMSR", False)
            v = intan - gw - msr
            if v < 0:
                return "n/a", calc(label, "n/a — negative residual (data problem)",
                                   asof, "Computed from Call Report",
                                   terms + [{"label": "Residual",
                                             "val": f"n/a — negative ({_thou(v)}) — "
                                                    "components exceed total"}],
                                   "INTAN − INTANGW − INTANMSR", False)
            return _usd(v), calc(label, _usd(v), asof, "Computed from Call Report",
                                 terms, "INTAN − INTANGW − INTANMSR", False)
        if kind == "residual":
            # Residual plug = total − the named itemized lines (Other Assets =
            # ASSET − itemized; Other Liabilities = LIAB − DEP − OTHBFHLB −
            # SUBND). A None component is treated as 0 in the subtraction (it
            # is shown n/a elsewhere); a NEGATIVE residual is a data problem →
            # n/a + flag, never a silent negative plug.
            total_fl, sub_fls = args[0], args[1:]
            total = _num(rec.get(total_fl))
            parts = [(fl, _num(rec.get(fl)) or 0) for fl in sub_fls]
            terms = [{"label": total_fl, "val": _term000(total)}]
            terms += [{"label": f"− {fl}", "val": _thou(p) + " ($000)"} for fl, p in parts]
            op = f"{total_fl} − " + " − ".join(sub_fls)
            if total is None:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   terms, op, False)
            v = total - sum(p for _, p in parts)
            if v < 0:
                return "n/a", calc(label, "n/a — negative residual (data problem)",
                                   asof, "Computed from Call Report",
                                   terms + [{"label": "Residual",
                                             "val": f"n/a — negative ({_thou(v)}) — "
                                                    "itemized lines exceed total"}],
                                   op, False)
            return _usd(v), calc(label, _usd(v), asof, "Computed from Call Report",
                                 terms, op, False)
        if kind == "growth":
            # Annualized YoY growth (%) of a $000 field from the prior column.
            # First column has no prior year → n/a (not 0%).
            fl = args[0]; cur = _num(rec.get(fl))
            if ci == 0:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   [{"label": label,
                                     "val": "n/a — no prior period in view"}],
                                   f"({fl}_t ÷ {fl}_t−1 − 1) × 100", False)
            prev = _num(recs_list[ci - 1].get(fl))
            terms = [{"label": f"{fl} (current)", "val": _term000(cur)},
                     {"label": f"{fl} (prior period)", "val": _term000(prev)}]
            op = f"({fl}_t ÷ {fl}_t−1 − 1) × 100"
            if cur is None or not prev:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   terms, op, False)
            g = (cur / prev - 1.0) * 100.0
            return f"{g:.2f}%", calc(label, f"{g:.2f}%", asof,
                                     "Computed from Call Report", terms, op, False)
        # ── Per-share (SEC holding-company filings) ──────────────────────
        ps = ps_by_ci.get(ci, {})
        if kind == "ps":
            key = args[0]; v = _psd(ps.get(key))
            return v, calc(label, v, asof, "SEC filing (holding company)",
                           [{"label": label, "val": v}], None, True,
                           source="SEC filing", link=sec_filing_link)
        if kind == "shares":
            key = args[0] if args else "shares"
            sh = _num(ps.get(key))
            v = f"{sh:,.0f}" if sh is not None else "—"
            return v, calc(label, v, asof, "SEC filing (holding company)",
                           [{"label": label, "val": v}], None, True,
                           source="SEC filing", link=sec_filing_link)
        if kind == "payout":
            dps, eps = _num(ps.get("dps")), _num(ps.get("eps"))
            v = f"{dps/eps*100:.2f}%" if (dps is not None and eps) else "—"
            return v, calc(label, v, asof, "Computed from SEC per-share",
                           [{"label": "Dividends / share", "val": _psd(dps)},
                            {"label": "Diluted EPS", "val": _psd(eps)}],
                           "DPS ÷ EPS × 100", False,
                           source="SEC filing", link=sec_filing_link)
        # ── Normalized 'Core' (ex realized securities gains/losses) ──────
        if kind == "core_income":
            core = _core_income(rec)
            v = _usd(core)
            igl = _num(rec.get("IGLSEC")) or 0.0
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income", "val": _thou(_num(rec.get("NETINC"))) + " ($000)"},
                            {"label": "Less: securities gains (after-tax)",
                             "val": _thou(round(igl*(1-_eff_tax(rec)))) + " ($000)"}],
                           "Net income − after-tax securities gains/losses", False)
        if kind == "core_roaa":
            core = _core_income(rec); a = _avg(ci, "ASSET")
            v = f"{core*f/a*100:.2f}%" if (core is not None and a) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Core income" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(core*f)) + " ($000)" if core is not None else "—"},
                            {"label": "Avg assets", "val": _thou(round(a)) + " ($000)" if a else "—"}],
                           "Core income ÷ avg assets × 100", False)
        if kind == "core_roae":
            core = _core_income(rec); e = _avg(ci, "EQTOT")
            v = f"{core*f/e*100:.2f}%" if (core is not None and e) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Core income" + (" (annualized)" if f != 1 else ""),
                             "val": _thou(round(core*f)) + " ($000)" if core is not None else "—"},
                            {"label": "Avg equity", "val": _thou(round(e)) + " ($000)" if e else "—"}],
                           "Core income ÷ avg equity × 100", False)
        if kind == "core_eps":
            core = _core_income(rec); sh = _num(ps.get("shares"))
            v = f"${core*1000/sh:,.2f}" if (core is not None and sh) else "—"
            return v, calc(label, v, asof, "Computed (FDIC core income ÷ SEC shares)",
                           [{"label": "Core income", "val": _usd(core)},
                            {"label": "Avg diluted shares", "val": f"{sh:,.0f}" if sh else "—"}],
                           "Core income ÷ avg diluted shares", False,
                           source="FDIC + SEC", link=sec_filing_link)
        if kind == "nonrecur":
            igl = _num(rec.get("IGLSEC")) or 0.0; extra = _num(rec.get("EXTRA")) or 0.0
            pti = _num(rec.get("PTAXNETINC"))
            v = f"{(igl+extra)/pti*100:.2f}%" if pti else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Securities gains + extraordinary",
                             "val": _thou(round(igl+extra)) + " ($000)"},
                            {"label": "Pre-tax net income", "val": _thou(pti) + " ($000)"}],
                           "(Securities gains + extraordinary) ÷ pre-tax income × 100", False)
        return "—", None

    cells, rows_html, ri = {}, [], 0
    cell_errors: list[str] = []
    ncol = len(recs_list)
    for sec_name, rows in spec:
        rows_html.append(f'<tr><td class="sec" colspan="{ncol+1}">{sec_name}</td></tr>')
        for row in rows:
            label, kind, args = row[0], row[1], row[2:]
            tds = [f'<td class="lbl">{label}</td>']
            for ci, rec in enumerate(recs_list):
                try:
                    v, c = cell(ci, kind, args, label)
                except Exception as e:
                    # A computation bug must not be indistinguishable from
                    # "not reported" — collect and log once per render.
                    cell_errors.append(f"{label}[{ci}]: {type(e).__name__}: {e}")
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

    if cell_errors:
        print(f"[statements] {ticker} {title}: {len(cell_errors)} cell(s) "
              f"failed to compute — {'; '.join(cell_errors[:5])}")

    head = ('<th class="lblh">(figures in USD)</th>'
            + "".join(f'<th class="colh">{lb}</th>' for lb in labels))
    height = 96 + 23 * (ri + len(spec) + 1)
    sec_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"
                if cik else fdic_link)
    html = _build_component(head, "".join(rows_html), cells, entity, fdic_link, sec_link)

    tr = _DEFAULT_TRENDS if trends is None else trends
    cap = f"Latest: FDIC Call Report {_disp(recs_list[-1].get('REPDTE'))} · live each load."
    if side_by_side:
        # Page pattern (user 2026-06-25): click-to-source table on the left,
        # trend charts tiled two-per-row (2×2) on the right — like Financial
        # Highlights, an even 50/50 split.
        _lt, _rt = st.columns([1, 1])
        with _lt:
            components.html(html, height=height, scrolling=False)
            st.caption(cap)
        with _rt:
            _render_statement_trends(hist, ticker, key_prefix, tr)
    else:
        # The statement IS the page: table full-width on top (its many columns
        # need the room), then a dense grid of grouped trend charts beneath it
        # (user 2026-06-14: more charts, multi-metric, real axes).
        components.html(html, height=height, scrolling=False)
        st.caption(cap)
        _render_statement_trends(hist, ticker, key_prefix, tr)


def _render_statement_trends(hist, ticker, key_prefix, trends):
    """Grouped trend charts, two per row. Works full-width (beneath the table)
    or nested in the right column of the table-left/charts-right layout (one
    level of column nesting, same as Financial Highlights). `trends` is a list
    of (chart_title, [metric_keys])."""
    if not trends:
        return
    try:
        from ui.charts import grouped_trend_chart
    except Exception:
        return
    h = hist.sort_values("REPDTE").tail(20)
    st.markdown("##### Trends")
    for r in range(0, len(trends), 2):
        cols = st.columns(2)
        for j, (col, (title, keys)) in enumerate(zip(cols, trends[r:r + 2])):
            with col:
                try:
                    st.plotly_chart(grouped_trend_chart(h, keys, title),
                                    use_container_width=True,
                                    key=f"{key_prefix}_tr_{ticker}_{r + j}")
                except Exception:
                    pass


# ── Statement specs ─────────────────────────────────────────────────────────
# SNL-depth layout (docs/SNL-BUILD-PLAN.md tab 1). All values are the bank
# subsidiary's Call Report — holdco SEC totals can differ slightly; the
# click-through provenance names the entity. FFIEC Schedule RI / RI-E rows
# (FTE NII, itemized expense detail, write-ins) join from the stored
# call-report detail (data/call_report_store) by report date — columns
# without ingested detail render dead, never imputed. Remaining RI rows
# (gain on sale of loans, BOLI) are still to come.
_INCOME = [
    ("Interest Income & Expense", [
        ("Interest & fees on loans", "dollar", "ILNDOM"),
        ("Income on investment securities", "dollar", "ISC"),
        ("Total interest income", "dollar", "INTINC"),
        ("Interest on deposits", "dollar", "EDEP"),
        ("Total interest expense", "dollar", "EINTEXP"),
        ("Net interest income", "diff", "INTINC", "EINTEXP"),
        # FTE derivation from Schedule RI tax-exempt income (RIAD4313/4507),
        # computed at the 21% federal statutory rate; n/a when tax-exempt
        # income isn't reported.
        ("FTE adjustment", "fte_adj"),
        ("Net interest income (FTE)", "nii_fte"),
    ]),
    ("Provision for Credit Losses", [
        ("Provision for credit losses", "dollar", "ELNATR"),
    ]),
    ("Non-Interest Income", [
        ("Trading account income", "dollar", "TRADE"),
        ("Trust / fiduciary revenue", "dollar", "IFIDUC"),
        ("Service charges on deposits", "dollar", "ISERCHG"),
        ("Insurance revenue", "dollar", "IINSOTH"),
        ("Investment banking fees", "dollar", "IINVFEE"),
        ("Other non-interest income", "noniother",
         "TRADE", "IFIDUC", "ISERCHG", "IINSOTH", "IINVFEE"),
        ("Total non-interest income", "dollar", "NONII"),
        ("Realized gain (loss) on securities", "dollar", "IGLSEC"),
    ]),
    ("Non-Interest Expense", [
        ("Compensation & benefits", "dollar", "ESAL"),
        ("Occupancy & equipment", "dollar", "EPREMAGG"),
        ("Amortization of intangibles", "dollar", "EAMINTAN"),
        ("Other non-interest expense", "dollar", "EOTHNINT"),
        ("Total non-interest expense", "dollar", "NONIX"),
    ]),
    ("Earnings", [
        ("Pre-provision net revenue", "ppnr"),
        ("Pre-tax net income", "dollar", "PTAXNETINC"),
        ("Provision for taxes", "dollar", "ITAX"),
        ("Effective tax rate", "etr"),
        ("Minority interest", "dollar", "NETIMIN"),
        ("Extraordinary items", "dollar", "EXTRA"),
        ("Net income", "dollar", "NETINC"),
    ]),
]

# SNL "Balance Sheet" layout (docs/SNL-BUILD-PLAN.md tab 2), every field
# value-verified live against Banner Bank (BANR, cert 28489) 03/31/2026.
# Subtotals: "sum" kinds are computed sums of their displayed components;
# subtotal lines that map to a single filed FDIC total (Total Net Loans =
# LNLSNET, Total Intangible Assets = INTAN, Total Assets/Liabilities/Equity)
# use "dollar" so the click-through opens straight to the filed field.
# Lines the FDIC SDI feed cannot source render n/a (kind "na") with the
# reason — FFIEC RC-B/RC-F/RC-M and the As-Reported HFS split are later
# phases. Live-verification deltas from the build spec:
#   · SCEQ (other/equity securities) is NOT in the SDI feed → n/a.
#   · HTM uses the filed SCHA (943,973) — the SC−SCAF−TRADE residual
#     (944,198) is 0.02% apart; SCHA is the reported value.
#   · "Loan Servicing Rights" = MSA (47,460), not INTANMSR (11,321) — MSA
#     matches the SNL magnitude.
#   · No AOCI field maps reliably in the SDI feed (EQUPTOT is undistributed
#     net income / equity-cap component, NOT AOCI) → AOCI line n/a.
_BALANCE = [
    ("Assets ($000)", [
        ("Cash and Due from Banks", "diff", "CHBAL", "CHBALI"),
        ("Fed Funds Sold & Resell (combined)", "dollar", "FREPO"),
        ("Deposits at Financial Institutions", "dollar", "CHBALI"),
        ("Other Cash & Cash Equivalents", "na", "not separable in the FDIC SDI feed"),
        ("» Cash and Cash Equivalents", "sum", "CHBAL", "FREPO"),
        ("Trading Account Securities", "dollar", "TRADE"),
        ("Available for Sale Securities", "dollar", "SCAF"),
        ("Held to Maturity Securities", "htm"),
        ("Other Securities", "na", "equity/other securities (SCEQ) not in the FDIC SDI feed"),
        # Subtotal = the DISPLAYED component fields (TRADE + SCAF + SCHA for
        # securities), NOT SC: SC (2,979,219) exceeds SCAF + SCHA (2,978,994)
        # by 225 ($000) for Banner because HTM here is the filed SCHA, and a
        # subtotal must equal the sum of its shown components to the dollar.
        ("» Total Cash & Securities", "sum", "CHBAL", "FREPO", "TRADE", "SCAF", "SCHA"),
        ("Gross Loans Held for Investment", "dollar", "LNLSGR"),
        ("Loan Loss Reserve", "diff", "LNLSGR", "LNLSNET"),
        ("Loans Held for Sale", "na", "FFIEC RC item 5369 — later phase"),
        ("» Total Net Loans", "dollar", "LNLSNET"),
        ("Real Estate Owned", "dollar", "ORE"),
        ("Goodwill", "dollar", "INTANGW"),
        ("Core Deposit Intangibles", "na", "FFIEC RC-M — later phase"),
        # Mortgage-servicing intangible (INTANMSR) is the piece of INTAN that
        # "Other Intangibles" nets out, so showing it as its own line lets the
        # breakdown reconcile on the face of the table: Goodwill + CDI + MSR
        # intangible + Other = » Total Intangible Assets (INTAN). Distinct from
        # "Loan Servicing Rights" (MSA) below — a different, broader field.
        ("Mortgage Servicing Intangible", "dollar", "INTANMSR"),
        ("Other Intangibles", "otherint"),
        ("» Total Intangible Assets", "dollar", "INTAN"),
        ("Loan Servicing Rights", "dollar", "MSA"),
        ("Credit Card Rights", "na", "not reported in the FDIC SDI feed"),
        ("Other Loan Servicing Rights", "na", "not reported in the FDIC SDI feed"),
        ("Fixed Assets", "dollar", "BKPREM"),
        ("Interest Receivable", "na", "FFIEC RC-F — later phase"),
        ("Prepaid Expense", "na", "not reported in the FDIC SDI feed"),
        ("Bank-owned Life Insurance", "na", "FFIEC RC-F — later phase"),
        # Residual plug = ASSET − the DISPLAYED itemized asset lines so that
        # (residual + every shown line) reconciles to ASSET to the dollar:
        # Cash+Due (CHBAL−CHBALI) + Deposits-at-FIs (CHBALI) collapse to CHBAL;
        # securities use the shown TRADE + SCAF + SCHA (not SC, which is 225
        # higher); plus net loans, REO, total intangibles, MSA, fixed assets.
        ("Other Assets", "residual", "ASSET",
         "CHBAL", "FREPO", "TRADE", "SCAF", "SCHA", "LNLSNET", "ORE",
         "INTAN", "MSA", "BKPREM"),
        ("» Total Assets", "dollar", "ASSET"),
    ]),
    ("Liabilities ($000)", [
        ("Total Deposits", "dollar", "DEP"),
        ("FHLB & Other Borrowings (combined)", "dollar", "OTHBFHLB"),
        ("Senior Debt", "na", "FFIEC — later phase"),
        ("Total Subordinated Debt", "dollar", "SUBND"),
        ("» Total Debt", "sum", "OTHBFHLB", "SUBND"),
        ("Total Other Liabilities", "residual", "LIAB", "DEP", "OTHBFHLB", "SUBND"),
        ("» Total Liabilities", "dollar", "LIAB"),
    ]),
    ("Equity ($000)", [
        ("Total Preferred Equity", "dollar", "EQPP"),
        # NCI is not separable in the FDIC SDI feed; folded into common with
        # the note below (the explicit NCI line renders n/a).
        ("Common Equity (incl. NCI)", "diff", "EQTOT", "EQPP"),
        ("Noncontrolling Interests", "na", "not separable in the FDIC SDI feed (folded into common equity)"),
        ("Tot Acc Other Comprehensive Inc (AOCI)", "na", "no AOCI field maps reliably in the FDIC SDI feed (EQUPTOT is not AOCI)"),
        ("» Total Equity", "dollar", "EQTOT"),
    ]),
    ("Balance Sheet Analysis (%)", [
        ("Gross Loans HFI / Total Assets", "ratio", "LNLSGR", "ASSET"),
        ("Loans / Deposits", "ratio", "LNLSGR", "DEP"),
        ("Loan Loss Reserves / Gross Loans", "pct", "LNATRESR"),
        ("FTE Employees (actual)", "na", "headcount not reported in the FDIC SDI feed"),
    ]),
    ("Annualized Growth Rates (%)", [
        ("Asset Growth", "growth", "ASSET"),
        ("Gross Loans HFI Growth", "growth", "LNLSGR"),
        ("Deposit Growth", "growth", "DEP"),
    ]),
    # Average Balances (FFIEC Schedule RC-K) is deferred to a later phase
    # (see docs/SNL-BUILD-PLAN.md) and intentionally NOT shown on this tab.
]

_PERFORMANCE = [
    ("Profitability Ratios (%)", [
        ("Return on avg assets (ROAA)", "pct", "ROA"),
        ("Return on avg equity (ROAE)", "pct", "ROE"),
        ("Return on avg common equity (ROACE)", "roace"),
        ("Return on avg tangible common equity (ROATCE)", "roatce"),
        ("Return on avg tangible equity (ROATE)", "roate"),
        ("Profit margin", "marginrev", "NETINC"),
    ]),
    ("Margin & Spread (%)", [
        ("Net interest margin (FTE)", "ftenim"),
        ("Net interest margin (reported)", "pct", "NIMY"),
        ("Yield on earning assets", "pct", "INTINCY"),
        ("Cost of funding earning assets", "pct", "INTEXPY"),
        ("Net interest spread", "pctdiff", "INTINCY", "INTEXPY"),
        ("Net interest income / avg assets", "yield2", "INTINC", "EINTEXP", "ASSET"),
    ]),
    ("Efficiency (%)", [
        ("Efficiency ratio", "pct", "EEFFR"),
        ("Overhead ratio (non-int exp / revenue)", "marginrev", "NONIX"),
        ("Non-interest income / operating revenue", "marginrev", "NONII"),
        ("Non-interest income / avg assets", "pct", "NONIIAY"),
        ("Non-interest expense / avg assets", "pct", "NONIXAY"),
        ("Net operating expense / avg assets", "netopex"),
    ]),
    ("Yield / Cost Detail (%)", [
        ("Yield: total loans", "yield", "ILNDOM", "LNLSGR"),
        ("Yield: investment securities", "yield", "ISC", "SC"),
        ("Yield: other earning assets", "na",
         "not separable from the FDIC feed — interest income isn't broken out "
         "for non-loan, non-securities earning assets"),
        ("Yield: interest-earning assets", "pct", "INTINCY"),
        ("Cost: interest-bearing deposits", "yield", "EDEP", "DEPIDOM"),
        ("Cost: total deposits", "yield", "EDEP", "DEP"),
        ("Cost: borrowings / debt", "costdebt"),
        ("Cost: funding (earning-asset basis)", "pct", "INTEXPY"),
        ("Cost of funds (all funding)", "costfunds"),
    ]),
    # FFIEC Schedule RI 2.a / RC-K stored split (data/call_report_store) —
    # the SNL 'Int Cost: CDs' vs 'Int Cost: Other Deposits' rows the FDIC
    # feed can't provide. Rates are de-cumulated from calendar-YTD interest
    # (computed); columns without an ingested row render dead, and a row
    # whose components don't reconcile to total interest expense renders n/a.
    ("Deposit Cost Detail — bank subsidiary (call report)", [
        ("Cost of CDs (%)", "dep_rate", "cds"),
        ("Cost of other interest-bearing deposits (%)", "dep_rate", "other_ib"),
        ("CD interest expense (calendar-YTD)", "dep_flow", "cds"),
        ("Other interest-bearing deposit interest (calendar-YTD)", "dep_flow", "other_ib"),
        ("Avg CD balances", "dep_avg", "cds"),
        ("Avg other interest-bearing deposit balances", "dep_avg", "other_ib"),
    ]),
    ("Asset Quality (%)", [
        ("Non-current loans / loans", "pct", "NCLNLSR"),
        ("Net charge-offs / loans", "pct", "NTLNLSR"),
        ("Loan-loss reserves / loans", "pct", "LNATRESR"),
    ]),
    ("Core Earnings — normalized (ex realized securities gains/losses)", [
        ("Core income", "core_income"),
        ("Core EPS", "core_eps"),
        ("Core ROAA", "core_roaa"),
        ("Core ROAE", "core_roae"),
        ("Net nonrecurring income / pre-tax income", "nonrecur"),
    ]),
    ("Share & Per-Share Info (HoldCo, SEC)", [
        ("Basic EPS", "ps", "basic_eps"),
        ("Diluted EPS", "ps", "eps"),
        ("Diluted EPS before amortization", "ps", "eps_before_amort"),
        ("Book value / share", "ps", "bvps"),
        ("Tangible book value / share", "ps", "tbvps"),
        ("Dividends declared / share", "ps", "dps"),
        ("Dividend payout ratio", "payout"),
        # "shares" (dei cover-page / year-end) is the period-end common share
        # count; "avg_diluted_shares" is the weighted-average diluted count from
        # the EPS denominator — two different SNL lines, two different numbers.
        ("Avg diluted shares (actual)", "shares", "avg_diluted_shares"),
        ("Common shares outstanding (actual)", "shares", "shares"),
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


def _render_as_reported_statement(ticker: str, stype: str):
    """The company's own primary statement, parsed faithfully from its latest
    10-K's SEC-rendered R-file (data.sec_statements) — labels/order/lines are the
    company's; values are reproduced, never re-templated."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        st.info("No SEC filer mapping for this bank — As-Reported view unavailable.")
        return
    try:
        from data.sec_statements import as_reported_statements_for
        res = as_reported_statements_for(cik)
    except Exception:
        res = None
    stmts = (res or {}).get("statements", {})
    if stype not in stmts:
        st.caption("As-Reported statement not available from this filer's latest 10-K — n/a.")
        return
    meta, stmt = res["meta"], stmts[stype]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
           f"{meta['accession']}/{meta['doc']}")
    basis = f" · {stmt['basis']}" if stmt["basis"] else ""
    st.caption(f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — as the "
               f"company reports it{basis}. Values in millions of dollars.")
    periods = stmt["periods"] or [""]

    def _m(v):
        if v is None:
            return ""
        x = v / 1e6
        return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"

    def _esc(s):
        # Labels can embed $ amounts (would render as LaTeX) and pipes (would
        # break the markdown table) — escape both.
        return s.replace("|", r"\|").replace("$", r"\$")

    hdr = "| | " + " | ".join(periods) + " |"
    sep = "|---|" + "---|" * len(periods)
    rows = [hdr, sep]
    for r in stmt["rows"]:
        lab = _esc(r["label"])
        if r["header"]:
            rows.append(f"| **{lab}** |" + " |" * len(periods))
        else:
            vals = (r["values"] + [None] * len(periods))[:len(periods)]
            rows.append(f"| {lab} | " + " | ".join(_m(v) for v in vals) + " |")
    st.markdown("\n".join(rows))


def _render_company_statement(ticker: str, stype: str):
    """Multi-year Company-Reported statement (stype = "income" | "balance"),
    stitched from the bank's recent 10-Ks. Faithful to the company's own line
    items; blank where a line wasn't reported that year. The income per-share /
    weighted-share trailer is omitted pending per-share unit handling."""
    import re
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        st.info("No SEC filer mapping for this bank.")
        return
    try:
        from data.sec_statements import as_reported_statement_multiyear
        res = as_reported_statement_multiyear(cik, stype, n_years=5)
    except Exception:
        res = None
    if not res:
        st.caption("Company-reported statement not available from this filer's 10-Ks — n/a.")
        return
    stmt, filings, latest = res["statement"], res["filings"], res["meta"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(latest['cik'])}/"
           f"{latest['accession']}/{latest['doc']}")

    def _yr(p):
        m = re.search(r"\d{4}", p or "")
        return m.group() if m else (p or "")

    _eps = re.compile(r"per share|per common share", re.I)
    _shares = re.compile(r"\(in shares\)|in shares", re.I)
    _has_persh = any(_eps.search(r["label"]) or _shares.search(r["label"])
                     for r in stmt["rows"] if not r["header"])
    _persh_note = " EPS in \\$/share, shares in millions;" if _has_persh else ""
    st.caption(f"Source: company 10-K filings — latest [{latest['date']}]({src}); "
               f"{len(stmt['periods'])} fiscal years stitched from {len(filings)} filings. "
               f"Dollar lines in \\$ millions;{_persh_note} "
               f"blank = not separately reported that year.")
    periods = stmt["periods"][::-1]            # oldest → newest (matches Templated)
    cols = [f"FY{_yr(p)}" for p in periods]

    def _m(label, v):
        if v is None:
            return ""
        if _eps.search(label):
            return f"\\${v:,.2f}"
        if _shares.search(label):
            return f"{v / 1e6:,.1f}M"
        x = v / 1e6
        return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"

    def _esc(s):
        return s.replace("|", r"\|").replace("$", r"\$")

    out = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for r in stmt["rows"]:
        lab = _esc(r["label"])
        if r["header"]:
            out.append(f"| **{lab}** |" + " |" * len(cols))
        else:
            vals = r["values"][::-1]
            out.append(f"| {lab} | " + " | ".join(_m(r["label"], v) for v in vals) + " |")
    st.markdown("\n".join(out))


def _compositions_cached(cik):
    """compositions_for(cik) cached by the latest 10-K accession — the ~7 MB
    fetch+parse runs once per filing and serves BOTH the loan and deposit tabs."""
    if not cik:
        return None
    from data import cache
    from data.sec_filing_scraper import latest_filing
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    ckey = f"compositions:v1:{meta['accession']}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached or None
    from data.sec_composition import compositions_for
    res = compositions_for(cik)
    try:
        cache.put(ckey, res or {})       # cache n/a too, so it isn't re-fetched
    except Exception:
        pass
    return res or None


def _render_company_composition(ticker, kind):
    """As-reported loan/deposit composition (kind = "loan" | "deposit") from the
    bank's OWN 10-K inline XBRL — each line the filer's own category label, the set
    reconciled to the disclosed total (data/sec_composition). n/a when the bank
    doesn't disclose a clean, reconciling composition (never a forced number)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        st.info("No SEC filer mapping for this bank.")
        return
    try:
        res = _compositions_cached(cik)
    except Exception:
        res = None
    comp = (res or {}).get(kind)
    if not comp:
        st.caption(f"Company-reported {kind} composition is not disclosed in a "
                   f"clean, reconciling table in this filer's latest 10-K — n/a.")
        return
    meta = res["meta"]
    period, d = next(iter(comp.items()))
    total = d["total"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
           f"{meta['accession']}/{meta['doc']}")
    st.caption(f"Source: company 10-K [{meta['date']}]({src}) — as reported at "
               f"{period}. Each line is the company's own category; the lines "
               f"reconcile to the disclosed total of \\${total / 1e6:,.0f}M.")
    out = ["| Category | $ millions | % of total |", "|---|--:|--:|"]
    for row in d["rows"]:
        label, v = row[0], row[1]
        lab = str(label).replace("|", r"\|").replace("$", r"\$")
        pct = (v / total * 100) if total else 0.0
        out.append(f"| {lab} | {v / 1e6:,.0f} | {pct:.1f}% |")
    out.append(f"| **Total** | **{total / 1e6:,.0f}** | **100.0%** |")
    st.markdown("\n".join(out))


def render_income_statement(ticker):
    render_statement(ticker, "is", "Income Statement", _INCOME, with_ri=True,
                     side_by_side=True)


def render_balance_sheet(ticker):
    render_statement(ticker, "bs", "Balance Sheet", _BALANCE, trends=_BS_TRENDS,
                     side_by_side=True)


def render_performance_analysis(ticker):
    render_statement(ticker, "perf", "Performance Analysis", _PERFORMANCE,
                     with_persh=True, with_dep_cost=True, with_fte=True,
                     side_by_side=True)


def _render_fair_value_hierarchy(ticker):
    """Recurring ASC 820 fair-value hierarchy (Level 1/2/3) for the HOLDING
    COMPANY, scraped from its own latest 10-K/10-Q inline XBRL. Level 3 is the
    mark-to-model share. Renders only what reconciles: where the filer's tagged
    grand total differs from the level sum (dealer derivative/collateral netting)
    the difference is shown as an explicit reconciling line; filers that don't tag
    a hierarchy rollup render n/a, never a component-summed guess."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import fair_value_for
        res = fair_value_for(cik)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Fair Value Hierarchy — recurring (ASC 820)")
    if not res or not res.get("fair_value"):
        st.caption("Fair-value hierarchy rollup not tagged in this filer's latest "
                   "filing — n/a. (Per-instrument component extraction is planned.)")
        return
    meta, fv = res["meta"], res["fair_value"]
    period = max(fv)
    sides = fv[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    y, mo = period[:4], period[5:7]
    plab = f"FY{y}" if mo == "12" else f"Q{(int(mo) - 1) // 3 + 1} '{y[2:]}"
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — assets & "
        f"liabilities measured at fair value on a recurring basis, {plab}. Level 3 "
        f"= mark-to-model (unobservable inputs). Updates as soon as the company files.")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    a, lia = sides.get("assets"), sides.get("liabilities")
    needs_netting = any(s and not s["_reconciles"] for s in (a, lia))

    def _col(s, key, kind):
        if not s or s.get(key) is None:
            return "n/a"
        v = s[key]
        return f"{v * 100:.1f}%" if kind == "pct" else _b(v)

    rows = [
        ("Level 1 (quoted prices)", "l1", "usd"),
        ("Level 2 (observable inputs)", "l2", "usd"),
        ("Level 3 (unobservable inputs)", "l3", "usd"),
        ("Total (sum of levels)", "total", "usd"),
    ]
    if needs_netting:
        rows += [("Counterparty/collateral netting", "netting", "usd"),
                 ("Total per filing", "grand", "usd")]
    rows.append(("Level 3 % of total", "l3_pct", "pct"))

    hdr = "| ($) | Assets | Liabilities |"
    sep = "|---|---|---|"
    body = [f"| {lab} | {_col(a, k, kind)} | {_col(lia, k, kind)} |"
            for lab, k, kind in rows]
    st.markdown("\n".join([hdr, sep] + body))
    if needs_netting:
        st.caption("Level totals are gross; the filer's grand total nets "
                   "counterparty/collateral arrangements (shown as a reconciling line).")


def _render_securities_portfolio(ticker):
    """As-reported AFS / HTM debt-securities amortized-cost → fair-value bridge,
    scraped from the HOLDING COMPANY's own latest 10-Q/10-K inline XBRL. The HTM
    unrealized loss never touches the balance sheet or AOCI — this is the
    'underwater bonds' picture. n/a when the filer doesn't tag a reconciling
    amortized-cost + fair-value pair (never a guessed total)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import securities_for
        res = securities_for(cik)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Investment Securities — AFS / HTM (Company Reported)")
    if not res or not res.get("securities"):
        st.caption("AFS/HTM amortized-cost → fair-value bridge not tagged in this "
                   "filer's latest filing — n/a.")
        return
    meta, sec = res["meta"], res["securities"]
    period = max(sec)
    pdat = sec[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    y, mo = period[:4], period[5:7]
    plab = f"FY{y}" if mo == "12" else f"Q{(int(mo) - 1) // 3 + 1} '{y[2:]}"
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — debt securities "
        f"at {plab}. Net unrealized = fair value − amortized cost; HTM losses are "
        f"NOT reflected on the balance sheet or in AOCI. Updates as the company files.")

    afs, htm = pdat.get("afs"), pdat.get("htm")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    def _pct(v):
        return "n/a" if v is None else f"{v * 100:+.1f}%"

    def _col(s, key, fmt):
        if not s or s.get(key) is None:
            return "n/a"
        return fmt(s[key])

    rows = [
        ("Amortized cost", "amortized_cost", _b),
        ("Gross unrealized gain", "unrealized_gain", _b),
        ("Gross unrealized loss", "unrealized_loss", _b),
        ("Fair value", "fair_value", _b),
        ("Net unrealized gain / (loss)", "net_unrealized", _b),
        ("Net unrealized, % of amortized cost", "underwater_pct", _pct),
    ]
    hdr = "| Debt securities | Available-for-sale | Held-to-maturity |"
    sep = "|---|---|---|"
    body = [f"| {lab} | {_col(afs, k, fmt)} | {_col(htm, k, fmt)} |"
            for lab, k, fmt in rows]
    st.markdown("\n".join([hdr, sep] + body))
    if (afs and not afs.get("_reconciles")) or (htm and not htm.get("_reconciles")):
        st.caption("Gross gain/loss split shown only where it ties the amortized-cost "
                   "→ fair-value bridge; otherwise only the (directly tagged) net is shown.")


def render_fair_value(ticker):
    render_statement(ticker, "fv", "Fair Value Analysis", _FAIR_VALUE)
    _render_fair_value_hierarchy(ticker)
    st.caption("AFS/HTM unrealized gain/loss (AOCI) detail from FFIEC Schedule RC-B, "
               "and the ASC 825 fair-value-of-financial-instruments table (loans, "
               "deposits, debt), are next on the roadmap.")


def _render_credit_quality(ticker):
    """As-reported CECL allowance & asset-quality summary, scraped from the HOLDING
    COMPANY's own latest 10-Q/10-K inline XBRL: allowance for credit losses, the
    ACL coverage ratio, nonaccrual loans, net charge-offs and provision. n/a when
    the filer doesn't tag a reconciling allowance + gross-loans pair (never a
    guessed ratio)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import credit_quality_for
        res = credit_quality_for(cik)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Credit Quality / Allowance — Company Reported")
    if not res or not res.get("credit_quality"):
        st.caption("Allowance / loan asset-quality figures not tagged as a reconciling "
                   "pair in this filer's latest filing — n/a.")
        return
    meta, cq = res["meta"], res["credit_quality"]
    period = max(cq)
    d = cq[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    y, mo = period[:4], period[5:7]
    plab = f"FY{y}" if mo == "12" else f"Q{(int(mo) - 1) // 3 + 1} '{y[2:]}"
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — allowance for "
        f"credit losses (CECL) and asset quality at {plab}. Ratios are against "
        f"period-end gross loans. Updates as the company files.")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    def _pct(v):
        return "n/a" if v is None else f"{v * 100:.2f}%"

    def _x(v):
        return "n/a" if v is None else f"{v * 100:.0f}%"

    rows = [
        ("Total loans (gross)", _b(d["loans_gross"])),
        ("Allowance for credit losses (ACL)", _b(d["acl"])),
        ("ACL / total loans", _pct(d["acl_to_loans"])),
        ("Nonaccrual loans", _b(d["nonaccrual"])),
        ("Nonaccrual / total loans", _pct(d["nonaccrual_to_loans"])),
        ("ACL coverage of nonaccruals", _x(d["acl_coverage_nonaccrual"])),
        ("Net charge-offs (period)", _b(d["nco"])),
        ("Net charge-offs / loans", _pct(d["nco_to_loans"])),
        ("Provision for credit losses (period)", _b(d["provision"])),
    ]
    hdr = "| Credit quality | Value |"
    sep = "|---|---|"
    body = [f"| {lab} | {val} |" for lab, val in rows]
    st.markdown("\n".join([hdr, sep] + body))


def _render_financial_highlights(ticker):
    """One-page Company-Reported snapshot from the latest 10-K: balance-sheet
    totals, profitability headline, CET1 and asset-quality — each value sourced
    from the same reconcile-gated extractors behind the detailed tabs."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    anchor = None
    try:
        from data.sec_filing_scraper import financial_highlights_for, _fdic_cet1
        from data.bank_mapping import get_fdic_cert
        try:
            anchor = _fdic_cet1(get_fdic_cert(ticker))
        except Exception:
            anchor = None
        res = financial_highlights_for(cik, anchor_cet1=anchor)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Financial Highlights — Company Reported")
    if not res or not res.get("highlights"):
        st.caption("Headline figures not tagged in this filer's latest 10-K — n/a.")
        return
    meta, h = res["meta"], res["highlights"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    fy = h.get("fy")
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — balance sheet at "
        f"{h['period']}" + (f", income for FY{fy[:4]}" if fy else "") +
        ". Each figure comes from the detailed Company-Reported tab of the same name.")

    def _hb(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.1f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    def _hpct(v):
        return "n/a" if v is None else f"{v * 100:.1f}%"

    def _heps(v):
        return "n/a" if v is None else f"${v:,.2f}"

    rows = [
        ("Total assets", _hb(h["assets"])), ("Total loans (gross)", _hb(h["loans"])),
        ("Total deposits", _hb(h["deposits"])), ("Total equity", _hb(h["equity"])),
        ("Net income (FY)", _hb(h["net_income"])), ("Total revenue (FY)", _hb(h["revenue"])),
        ("Diluted EPS (FY)", _heps(h["eps_diluted"])),
        ("Return on average assets", _hpct(h["roa"])),
        ("Return on average equity", _hpct(h["roe"])),
        ("Efficiency ratio", _hpct(h["efficiency"])),
        ("CET1 ratio", _hpct(h["cet1"])),
        ("ACL / total loans", _hpct(h["acl_to_loans"])),
        ("Nonaccrual / total loans", _hpct(h["nonaccrual_to_loans"])),
    ]
    hdr = "| Financial highlights | Value |"
    sep = "|---|---|"
    body = [f"| {lab} | {val} |" for lab, val in rows]
    st.markdown("\n".join([hdr, sep] + body))


def _render_performance(ticker):
    """As-reported full-year profitability, scraped from the HOLDING COMPANY's own
    latest 10-K inline XBRL: revenue, NII, PPNR, net income, EPS, and the
    efficiency ratio / ROA / ROE. Every figure is a directly tagged income-statement
    line or a transparent combination; ROA/ROE use average balances. n/a when the
    core income lines aren't tagged for the latest fiscal year."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import performance_for
        res = performance_for(cik)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Performance Analysis — Company Reported")

    # Freshest-source layer: when the bank's latest earnings release reports a
    # quarter the 10-Q/10-K hasn't filed yet, surface its (preliminary) diluted
    # EPS — ~2-3 weeks ahead of the periodic filing. 0-mismatch audited; n/a
    # unless the release is genuinely fresher (data.ir_provider.fresh_diluted_eps).
    try:
        from data.ir_provider import fresh_diluted_eps
        _fresh = fresh_diluted_eps(cik)
    except Exception:
        _fresh = None
    if _fresh:
        _q = _fresh["quarter"]
        _ql = f"Q{(int(_q[5:7]) - 1) // 3 + 1} {_q[:4]}"
        st.info(
            f"**Latest quarter (preliminary):** diluted EPS **\\${_fresh['eps']:.2f}** "
            f"for {_ql} — from the [earnings release filed {_fresh['filed_date']}]"
            f"({_fresh['url']}), ahead of the next 10-Q. Unaudited; the filed figure "
            f"supersedes it once published.")

    if not res or not res.get("performance"):
        st.caption("Full-year income-statement lines not tagged in this filer's "
                   "latest 10-K — n/a.")
        return
    meta, perf = res["meta"], res["performance"]
    period = max(perf)
    d = perf[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    note = (" Average balances for ROA/ROE are the mean of beginning and ending "
            "balance-sheet amounts." if d.get("_avg_computed") else "")
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — full year "
        f"FY{period[:4]}. Efficiency = noninterest expense ÷ revenue; PPNR = revenue "
        f"− noninterest expense.{note} Updates as the company files.")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    def _pct(v):
        return "n/a" if v is None else f"{v * 100:.1f}%"

    def _eps(v):
        return "n/a" if v is None else f"${v:,.2f}"

    rows = [
        ("Total revenue", _b(d["revenue"])),
        ("Net interest income", _b(d["nii"])),
        ("Noninterest income", _b(d["noninterest_income"])),
        ("Noninterest expense", _b(d["noninterest_expense"])),
        ("Pre-provision net revenue (PPNR)", _b(d["ppnr"])),
        ("Provision for credit losses", _b(d["provision"])),
        ("Net income", _b(d["net_income"])),
        ("Diluted EPS", _eps(d["eps_diluted"])),
        ("Efficiency ratio", _pct(d["efficiency"])),
        ("Return on average assets (ROA)", _pct(d["roa"])),
        ("Return on average equity (ROE)", _pct(d["roe"])),
    ]
    hdr = f"| Performance (FY{period[:4]}) | Value |"
    sep = "|---|---|"
    body = [f"| {lab} | {val} |" for lab, val in rows]
    st.markdown("\n".join([hdr, sep] + body))


def _render_segments(ticker):
    """As-reported business-segment net income (with revenue/assets), scraped from
    the HOLDING COMPANY's own latest 10-K. Each segment's figures are directly
    tagged; a 'Corporate / other & reconciling items' residual (consolidated − Σ
    reportable) ties them to the consolidated total, exactly as the segment
    footnote presents it. n/a when the filer tags fewer than two reportable
    segments."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import segments_for
        res = segments_for(cik)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Segment Reporting — Company Reported")
    if not res or not res.get("segments"):
        st.caption("Reportable business segments not tagged in this filer's latest "
                   "10-K — n/a. (Single-segment banks report no segment breakdown.)")
        return
    m, seg_data = res["meta"], res["segments"]
    period = max(seg_data)
    d = seg_data[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{m['accession']}/{m['doc']}")
    st.caption(
        f"Source: SEC [{m['form']} filed {m['date']}]({src}) — reportable segments, "
        f"FY{period[:4]}. Segment net income is the {d['ni_measure']} measure; the "
        f"residual reconciles reportable segments to the consolidated total.")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M"

    rows = [(s["label"], _b(s["net_income"]), _b(s["revenue"]), _b(s["assets"]))
            for s in d["segments"]]
    rows.append(("Corporate / other & reconciling items", _b(d["reconciling_residual"]), "", ""))
    rows.append(("**Consolidated net income**", f"**{_b(d['consolidated_net_income'])}**", "", ""))
    hdr = f"| Segment (FY{period[:4]}) | Net income | Revenue | Assets |"
    sep = "|---|---|---|---|"
    body = [f"| {a} | {b} | {c} | {e} |" for a, b, c, e in rows]
    st.markdown("\n".join([hdr, sep] + body))


def _render_rate_risk(ticker):
    """Embedded interest-rate risk: the AFS + HTM unrealized loss already on the
    securities book, against equity and CET1 capital (the post-2023 'underwater
    securities erode capital' story), composed from the same reconcile-gated
    securities/capital extractors. Forward NII/EVE rate-shock sensitivity lives in
    the bank's Item 7A and isn't standardized XBRL, so it is linked, not scraped."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    anchor = None
    try:
        from data.sec_filing_scraper import rate_risk_for, _fdic_cet1
        from data.bank_mapping import get_fdic_cert
        try:
            anchor = _fdic_cet1(get_fdic_cert(ticker))
        except Exception:
            anchor = None
        res = rate_risk_for(cik, anchor_cet1=anchor)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Interest Rate Risk — embedded securities marks (Company Reported)")
    if not res or not res.get("rate_risk"):
        st.caption("AFS/HTM unrealized marks not tagged in this filer's latest filing "
                   "— n/a. Forward rate-shock (NII/EVE) sensitivity is disclosed in "
                   "Item 7A of the 10-K.")
        return
    meta, rr = res["meta"], res["rate_risk"]
    period = max(rr)
    d = rr[period]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — unrealized "
        f"gain/(loss) on AFS + HTM debt securities at {period}, vs equity and CET1 "
        f"capital. This is the rate risk ALREADY on the books; forward NII/EVE "
        f"rate-shock sensitivity is narrative in Item 7A (not standardized XBRL).")

    def _b(v):
        if v is None:
            return "n/a"
        return f"${v / 1e9:+,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:+,.0f}M"

    def _pct(v):
        return "n/a" if v is None else f"{v * 100:+.1f}%"

    rows = [
        ("AFS unrealized gain / (loss)", _b(d["afs_unrealized"])),
        ("HTM unrealized gain / (loss)", _b(d["htm_unrealized"])),
        ("Total unrealized gain / (loss)", _b(d["total_unrealized"])),
        ("Total equity", _b(d["equity"])),
        ("Unrealized as % of equity", _pct(d["unrealized_to_equity"])),
        ("Unrealized as % of CET1 capital", _pct(d["unrealized_to_cet1"])),
    ]
    hdr = "| Embedded rate risk | Value |"
    sep = "|---|---|"
    body = [f"| {lab} | {val} |" for lab, val in rows]
    st.markdown("\n".join([hdr, sep] + body))


def render_portfolio(ticker):
    render_statement(ticker, "port", "Portfolio Analysis", _PORTFOLIO,
                     side_by_side=True)


def render_capital_structure(ticker):
    render_statement(ticker, "capstruct", "Capital Structure Details",
                     _CAPITAL_STRUCTURE, side_by_side=True)
