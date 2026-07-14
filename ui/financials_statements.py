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


def _expr_terms(expr: str) -> list[tuple[int, str]]:
    """'A+B-C' → [(+1,'A'), (+1,'B'), (-1,'C')] — the field expression used by
    the fratio kind. First term is implicitly positive."""
    out, sign, tok = [], 1, ""
    for ch in expr:
        if ch in "+-":
            if tok:
                out.append((sign, tok))
            sign, tok = (1 if ch == "+" else -1), ""
        else:
            tok += ch
    if tok:
        out.append((sign, tok))
    return out


def _eval_expr(getter, expr: str, skip_none_positive: bool):
    """Evaluate a field expression against `getter` (field → value | None).

    Returns (total, terms, ok). Numerator semantics (skip_none_positive=True):
    a None positive term is skipped like the "sum" kind (absent ≠ $0); ok is
    False only when NO term was present. Denominator semantics (False):
    every POSITIVE term is required (None → not ok — a partial denominator is
    plausible-wrong); a None NEGATIVE term counts as 0, matching the "tce"
    kind's INTAN handling. terms = [(signed_label, value_or_None)] for the
    click-through."""
    total, seen, ok = 0.0, False, True
    terms = []
    for sign, fld in _expr_terms(expr):
        v = _num(getter(fld))
        terms.append((("− " if sign < 0 else "") + fld, v))
        if v is None:
            if sign > 0 and not skip_none_positive:
                ok = False
            continue
        total += sign * v
        seen = True
    if skip_none_positive:
        ok = seen
    return total, terms, ok


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
# Each chart groups SAME-MAGNITUDE series so tighten_yaxis can zoom and the
# trends actually read. The old groups mixed a $20B line with a $0.1B one, which
# pinned the axis to ~0 and flattened every line. Magnitudes hand-checked for
# ABCB (e.g. total_assets 28B / total_loans 22B / total_deposits 23B cluster;
# consumer/auto/card loans are all <$0.2B and belong together, not under a $5B
# residential line).
_BS_TRENDS = [
    ("Assets, Loans & Deposits ($B)", ["total_assets", "total_loans", "total_deposits"]),
    ("Loan Composition ($B)", ["ln_re_total", "ln_re_residential", "ln_ci"]),
    ("CRE Detail ($B)", ["ln_re_nres_oo", "ln_re_nres_noo", "ln_re_multifam", "ln_re_construct"]),
    ("Consumer & Specialty Loans ($B)", ["ln_consumer", "ln_auto", "ln_credit_card", "ln_ag"]),
    ("Securities & Cash ($B)", ["securities", "cash_balances"]),
    ("Deposit Funding ($B)", ["core_deposits", "large_time_dep", "uninsured_deposits"]),
    ("Insured vs Uninsured Deposits ($B)", ["insured_deposits", "uninsured_deposits"]),
    ("Equity ($B) vs Capital Ratios (%)", ["total_equity", "cet1_ratio", "leverage_ratio"]),
]

# Income Statement — dollar P&L trends (its own story), so it no longer shows the
# same ratio charts as Performance Analysis. NII/fees/expense/income are FDIC
# YTD figures, so the lines step up within each year (Q1→Q4) and reset — the
# YoY level is the trend. Margin (a clean ratio) rides the NII chart's right axis.
_INCOME_TRENDS = [
    ("Net Interest Income & Margin", ["net_interest_income", "nim"]),
    ("Revenue & Expense ($B)", ["net_interest_income", "nonint_income", "nonint_expense"]),
    ("Pre-tax & Net Income ($B)", ["pretax_income", "net_income"]),
    ("Provision for Credit Losses ($B)", ["provision"]),
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


def _decum_flow(rec, field, quarterly, hist_by_date):
    """Span-consistent numerator for the flow ÷ avg-balance rate kinds (audit #26).

    FDIC income flows are YTD-cumulative while the rate kinds' denominator is a
    single-period 2-point average, so the numerator must cover the SAME span:
      • Annual view: rows are 12/31 records — the YTD figure IS the full year
        (factor 1.0; unchanged behavior).
      • Quarterly view: de-cumulate to the single quarter (Q1: the YTD IS the
        quarter; otherwise current YTD − the prior quarter's YTD, looked up in
        the FULL history rather than just the displayed columns) and annualize
        ×4 — matching the single-quarter denominator. The old behavior
        annualized the YTD directly, putting a months-1..N average flow over a
        latest-quarter balance — tens of bps off in rising-rate regimes.

    Returns (value, factor); (None, None) when the field is absent or the
    prior-quarter row is missing (cannot de-cumulate — "—" beats a mixed-span
    number, cardinal rule).
    """
    cur = _num(rec.get(field))
    if cur is None:
        return None, None
    dt = pd.Timestamp(rec.get("REPDTE")).normalize()
    if not quarterly:
        return cur, 12.0 / dt.month
    if dt.month == 3:
        return cur, 4.0
    prev = hist_by_date.get(_prior_quarter_end(dt))
    pv = _num(prev.get(field)) if prev is not None else None
    if pv is None:
        return None, None
    return cur - pv, 4.0


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
                     with_fte: bool = False, side_by_side: bool = False,
                     header: bool = True):
    info = get_bank_info(ticker)
    name = info.get("name") if info else ticker
    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None

    # header=False when the caller's page chrome already names the bank + tab
    # (Asset Quality Detail renders inside credit_dynamics' title_bar page).
    if header:
        st.markdown(f"### {name} ({ticker}) — {title}")
    period = st.radio("Period", ["Annual", "Quarterly"], horizontal=True,
                      key=f"{key_prefix}_period_{ticker}", label_visibility="collapsed")
    st.caption("From the FDIC Call Report. Click any number for its source field "
               "and, where computed, the formula and inputs.")
    if not cert:
        st.info("No FDIC Call Report data mapped for this bank.")
        return
    with st.spinner("Loading…"):
        hist = fdic_client.get_historical_financials(cert, quarters=44)
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

    # Span-consistent flow numerators for the rate kinds (audit #26): quarterly
    # columns de-cumulate YTD flows to the single quarter (×4) so numerator and
    # single-quarter avg denominator cover the same months. Date-keyed over the
    # FULL history so the first displayed column can still reach its prior
    # quarter (same trick as _dep_cost_by_date).
    _quarterly_view = (period == "Quarterly")
    _hist_by_date = {pd.Timestamp(r["REPDTE"]).normalize(): r
                     for r in hist.to_dict("records")}

    def _flow(ci, field):
        return _decum_flow(recs_list[ci], field, _quarterly_view, _hist_by_date)

    def _core_flow(ci):
        """De-cumulated core income + factor (quarterly spans matched, #26 —
        see _core_income for the definition). Absent IGLSEC/EXTRA legitimately
        mean zero; present but un-decumulatable → (None, None). The effective
        tax rate is the current YTD ratio — a rate, not a flow, so no span mix."""
        rec = recs_list[ci]
        ni, fq = _flow(ci, "NETINC")
        if ni is None:
            return None, None
        parts = {}
        for fld in ("IGLSEC", "EXTRA"):
            if _num(rec.get(fld)) is None:
                parts[fld] = 0.0
                continue
            qv, _ = _flow(ci, fld)
            if qv is None:
                return None, None
            parts[fld] = qv
        t = _eff_tax(rec)
        return ni - parts["IGLSEC"] * (1 - t) - parts["EXTRA"], fq

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
            # Denominator is the 2-point AVERAGE ((begin+end)/2 via _avg), like
            # every other avg-denominated kind (netopex, costfunds, core_roae):
            # period-END equity understated the return for a bank that raised
            # equity mid-period while the popup claimed an average.
            ni, fq = _flow(ci, "NETINC"); eq = _avg(ci, "EQTOT")
            intan = _avg(ci, "INTAN") or 0
            tce = (eq - intan) if eq is not None else None
            v = f"{ni*fq/tce*100:.2f}%" if (ni is not None and tce and tce > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(ni*fq)) + " ($000)" if ni is not None else "—"},
                            {"label": "Avg tangible common equity (EQTOT − INTAN)",
                             "val": _thou(round(tce)) + " ($000)" if tce is not None else "—"}],
                           "Net income ÷ avg tangible common equity × 100", False)
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
            nf, df_ = args; n, fq = _flow(ci, nf); d = _avg(ci, df_)
            v = f"{n*fq/d*100:.2f}%" if (n is not None and d) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": nf + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(n*fq)) + " ($000)" if n is not None else "—"},
                            {"label": "Avg " + df_, "val": _thou(round(d)) + " ($000)" if d else "—"}],
                           f"{nf} ÷ avg {df_} × 100", False)
        if kind == "yield2":      # (A − B) annualized ÷ avg balance × 100
            af_, bf_, df_ = args
            a, fq = _flow(ci, af_); b, _fb = _flow(ci, bf_); d = _avg(ci, df_)
            v = f"{(a-b)*fq/d*100:.2f}%" if (a is not None and b is not None and d) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": f"{af_} − {bf_}" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round((a-b)*fq)) + " ($000)" if (a is not None and b is not None) else "—"},
                            {"label": "Avg " + df_, "val": _thou(round(d)) + " ($000)" if d else "—"}],
                           f"({af_} − {bf_}) ÷ avg {df_} × 100", False)
        if kind == "roate":       # Return on avg tangible (total) equity
            # 2-point average denominator — see roatce.
            ni, fq = _flow(ci, "NETINC"); eq = _avg(ci, "EQTOT")
            intan = _avg(ci, "INTAN") or 0
            te = (eq - intan) if eq is not None else None
            v = f"{ni*fq/te*100:.2f}%" if (ni is not None and te and te > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(ni*fq)) + " ($000)" if ni is not None else "—"},
                            {"label": "Avg tangible equity (EQTOT − INTAN)",
                             "val": _thou(round(te)) + " ($000)" if te is not None else "—"}],
                           "Net income ÷ tangible equity × 100", False)
        if kind == "roace":       # Return on avg COMMON equity
            # 2-point average denominator — see roatce. The preferred guard
            # below keys off the CURRENT period's EQPP (outstanding now).
            ni, fq = _flow(ci, "NETINC"); eq = _avg(ci, "EQTOT")
            pfd = _num(rec.get("EQPP")) or 0
            ce = (eq - (_avg(ci, "EQPP") or 0)) if eq is not None else None
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
            v = f"{ni*fq/ce*100:.2f}%" if (ni is not None and ce and ce > 0) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net income" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(ni*fq)) + " ($000)" if ni is not None else "—"},
                            {"label": "Avg common equity (EQTOT − EQPP)",
                             "val": _thou(round(ce)) + " ($000)" if ce is not None else "—"}],
                           "Net income ÷ common equity × 100 (no preferred outstanding)", False)
        if kind == "netopex":     # Net operating expense ÷ avg assets
            nonx, fq = _flow(ci, "NONIX"); noni, _fb = _flow(ci, "NONII")
            a = _avg(ci, "ASSET")
            net = (nonx - noni) if (nonx is not None and noni is not None) else None
            v = f"{net*fq/a*100:.2f}%" if (net is not None and a) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Net op. expense (NONIX − NONII)" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(net*fq)) + " ($000)" if net is not None else "—"},
                            {"label": "Avg assets (ASSET)",
                             "val": _thou(round(a)) + " ($000)" if a else "—"}],
                           "(NONIX − NONII) ÷ avg assets × 100", False)
        if kind == "costfunds":   # Cost of funds: int exp ÷ avg total funding
            ie, fq = _flow(ci, "EINTEXP")
            # SUBND is the sub-debt BALANCE (ESUBND is its interest expense —
            # never mix an expense into a balance denominator).
            fund = _avgsum(ci, ["DEP", "FREPP", "OTHBFHLB", "SUBND"])
            v = f"{ie*fq/fund*100:.2f}%" if (ie is not None and fund) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Total interest expense (EINTEXP)" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(ie*fq)) + " ($000)" if ie is not None else "—"},
                            {"label": "Avg funding (deposits + borrowings)",
                             "val": _thou(round(fund)) + " ($000)" if fund else "—"}],
                           "Total interest expense ÷ avg (deposits + borrowings) × 100", False)
        if kind == "costdebt":    # Cost of borrowings/debt
            ie, fq = _flow(ci, "EINTEXP"); ed, _fb = _flow(ci, "EDEP")
            borint = (ie - ed) if (ie is not None and ed is not None) else None
            # SUBND is the sub-debt BALANCE (not ESUBND, its interest expense).
            bor = _avgsum(ci, ["FREPP", "OTHBFHLB", "SUBND"])
            rate = (borint * fq / bor * 100) if (borint is not None and bor and bor >= 1000) else None
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
                           [{"label": "Borrowings interest (EINTEXP − EDEP)" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(borint*fq)) + " ($000)" if borint is not None else "—"},
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
            # Annualized growth (%) of a $000 balance vs the prior column.
            # Annual view: prior column is the prior FY, so YoY is already an
            # annual rate. Quarterly view: prior column is the prior QUARTER —
            # the raw QoQ change must be compounded ((1+QoQ)^4 − 1) to match
            # the "Annualized Growth Rates" header (was shown raw, ~4× off).
            # First column has no prior period → n/a (not 0%).
            quarterly = (period == "Quarterly")
            fl = args[0]; cur = _num(rec.get(fl))
            op = (f"(({fl}_t ÷ {fl}_t−1)^4 − 1) × 100 — "
                  "QoQ compounded to an annual rate" if quarterly
                  else f"({fl}_t ÷ {fl}_t−1 − 1) × 100")
            if ci == 0:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   [{"label": label,
                                     "val": "n/a — no prior period in view"}],
                                   op, False)
            prev = _num(recs_list[ci - 1].get(fl))
            terms = [{"label": f"{fl} (current)", "val": _term000(cur)},
                     {"label": f"{fl} (prior quarter)" if quarterly
                      else f"{fl} (prior year)", "val": _term000(prev)}]
            if cur is None or not prev:
                return "n/a", calc(label, "n/a", asof, "Computed from Call Report",
                                   terms, op, False)
            ratio = cur / prev
            if ratio <= 0:
                # A non-positive balance ratio is a data problem, and it has no
                # real compounded rate — n/a over a fabricated number.
                return "n/a", calc(label, "n/a — non-positive balance ratio",
                                   asof, "Computed from Call Report", terms, op, False)
            g = ((ratio ** 4 - 1.0) if quarterly else (ratio - 1.0)) * 100.0
            return f"{g:.2f}%", calc(label, f"{g:.2f}%", asof,
                                     "Computed from Call Report", terms, op, False)
        if kind == "flow":
            # Income-statement flow shown as a PERIOD dollar amount (not a
            # rate): Annual columns are 12/31 rows, so the calendar-YTD figure
            # IS the full year. Quarterly columns show the SINGLE quarter —
            # the filed quarterly field (args[1], e.g. NTLNLSQ) when the FDIC
            # files one, else de-cumulated YTD (audit #26 machinery); a column
            # whose prior quarter is missing renders dead, never a mixed span.
            ytd_fl, q_fl = args
            if not _quarterly_view:
                raw = _num(rec.get(ytd_fl))
                return _usd(raw), calc(label, _usd(raw), asof,
                                       f"FDIC field {ytd_fl} (calendar-YTD; FY at 12/31)",
                                       [{"label": label, "val": _term000(raw)}],
                                       None, True)
            if q_fl is not None:
                raw = _num(rec.get(q_fl))
                if raw is not None:
                    return _usd(raw), calc(label, _usd(raw), asof,
                                           f"FDIC field {q_fl} (single quarter, as filed)",
                                           [{"label": label, "val": _term000(raw)}],
                                           None, True)
            qv, _fq = _flow(ci, ytd_fl)
            if qv is None:
                return "—", calc(label, "—", asof, "Computed from Call Report",
                                 [{"label": label,
                                   "val": "n/a — prior quarter not available to "
                                          "de-cumulate the YTD flow"}],
                                 f"{ytd_fl}_YTD(q) − {ytd_fl}_YTD(q−1)", False)
            return _usd(qv), calc(label, _usd(qv), asof, "Computed from Call Report",
                                  [{"label": f"{ytd_fl} (YTD, current)",
                                    "val": _term000(_num(rec.get(ytd_fl)))},
                                   {"label": "Single quarter (de-cumulated)",
                                    "val": _term000(qv)}],
                                  f"{ytd_fl}_YTD(q) − {ytd_fl}_YTD(q−1)", False)
        if kind == "fratio":
            # Field-expression ratio (%): args = (numerator_expr, denominator
            # _expr), e.g. ("NALNLS+RSLNLTOT+ORE", "ASSET") or the Texas-ratio
            # denominator "EQTOT-INTAN+LNATRES". Semantics in _eval_expr.
            num_expr, den_expr = args
            nv, nterms, nok = _eval_expr(rec.get, num_expr, True)
            dv, dterms, dok = _eval_expr(rec.get, den_expr, False)
            terms = ([{"label": lb, "val": _term000(v)} for lb, v in nterms]
                     + [{"label": f"÷ {lb.replace('− ', '− ')}", "val": _term000(v)}
                        for lb, v in dterms])
            op = f"({num_expr}) ÷ ({den_expr}) × 100"
            if not nok or not dok or dv <= 0:
                return "—", calc(label, "—", asof, "Computed from Call Report",
                                 terms, op, False)
            v = f"{nv / dv * 100:.2f}%"
            return v, calc(label, v, asof, "Computed from Call Report", terms, op, False)
        if kind == "flowratio":
            # Ratio of two FLOWS over the same span (e.g. Provision ÷ NCO):
            # Annual = FY ÷ FY; Quarterly = single quarter ÷ single quarter
            # (filed quarterly field preferred, else de-cumulated — same rules
            # as the "flow" kind; a mixed-span quotient never renders).
            (n_ytd, n_q), (d_ytd, d_q) = args

            def _one(ytd_fl, q_fl):
                if not _quarterly_view:
                    return _num(rec.get(ytd_fl))
                if q_fl is not None:
                    raw = _num(rec.get(q_fl))
                    if raw is not None:
                        return raw
                qv, _f = _flow(ci, ytd_fl)
                return qv

            nv, dv = _one(n_ytd, n_q), _one(d_ytd, d_q)
            span = "FY" if not _quarterly_view else "single quarter"
            terms = [{"label": f"{n_ytd} ({span})", "val": _term000(nv)},
                     {"label": f"÷ {d_ytd} ({span})", "val": _term000(dv)}]
            op = f"{n_ytd} ÷ {d_ytd} × 100 (same-span flows)"
            if nv is None or dv is None:
                return "—", calc(label, "—", asof, "Computed from Call Report",
                                 terms, op, False)
            if dv <= 0 or nv < 0:
                # SNL/CapIQ convention: a negative provision (reserve release)
                # or non-positive NCO base has no meaningful coverage multiple
                # — NM, not a giant negative percentage. The $ rows above
                # still show the release itself.
                return "NM", calc(label, "NM — not meaningful (negative flow "
                                  "in the period)", asof,
                                  "Computed from Call Report", terms, op, False)
            v = f"{nv / dv * 100:.2f}%"
            return v, calc(label, v, asof, "Computed from Call Report", terms, op, False)
        if kind == "crit":
            # Criticized/classified loan grades from the company's OWN
            # 10-K/10-Q dimensional XBRL (FinancingReceivableCreditQuality
            # Indicator) — the one block on this tab that is NOT Call Report
            # data. args = (by_period map from credit_quality_history, row key).
            # XBRL facts are RAW DOLLARS; this table is $000 → ÷ 1,000 at this
            # boundary. Columns with no filing at that period end render dead.
            crit_map, ckey = args
            dt_key = pd.Timestamp(rec.get("REPDTE")).strftime("%Y-%m-%d")
            entry = crit_map.get(dt_key)
            if not entry:
                return "—", None
            src = entry.get("source") or {}
            ref = (f"{src.get('form', '10-K')} filed {src.get('filed', '?')} — "
                   f"dimensional XBRL ({entry.get('concept', '')})")
            link = src.get("url")
            graded_total = sum(v for v in (entry.get("total_by_grade") or {}).values()
                               if isinstance(v, (int, float)))
            if ckey == "coverage":
                loans = _num(rec.get("LNLSGR"))
                terms = [{"label": "Graded loans (all tagged grades, $000)",
                          "val": _thou(round(graded_total / 1000.0)) + " ($000)"},
                         {"label": "÷ Gross loans (LNLSGR)", "val": _term000(loans)}]
                if not loans or graded_total <= 0:
                    return "—", calc(label, "—", asof, ref, terms,
                                     "graded ÷ LNLSGR × 100", False,
                                     source="SEC filing XBRL", link=link)
                v = f"{graded_total / 1000.0 / loans * 100:.1f}%"
                return v, calc(label, v, asof, ref, terms,
                               "graded ÷ LNLSGR × 100", False,
                               source="SEC filing XBRL", link=link)
            if ckey in ("classified", "criticized"):
                raw = entry.get(ckey)
                op = ("substandard + doubtful + loss" if ckey == "classified"
                      else "classified + special mention")
            else:
                raw = (entry.get("total_by_grade") or {}).get(ckey)
                op = None
            if raw is None:
                return "n/a", calc(label, "n/a", asof, ref,
                                   [{"label": label,
                                     "val": "n/a — grade not tagged in this filing"}],
                                   op, False, source="SEC filing XBRL", link=link)
            v = _usd(raw / 1000.0)
            return v, calc(label, v, asof, ref,
                           [{"label": f"{label} (XBRL, $ ÷ 1,000)",
                             "val": _thou(round(raw / 1000.0)) + " ($000)"}],
                           op, op is None, source="SEC filing XBRL", link=link)
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
            core, fq = _core_flow(ci); a = _avg(ci, "ASSET")
            v = f"{core*fq/a*100:.2f}%" if (core is not None and a) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Core income" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(core*fq)) + " ($000)" if core is not None else "—"},
                            {"label": "Avg assets", "val": _thou(round(a)) + " ($000)" if a else "—"}],
                           "Core income ÷ avg assets × 100", False)
        if kind == "core_roae":
            core, fq = _core_flow(ci); e = _avg(ci, "EQTOT")
            v = f"{core*fq/e*100:.2f}%" if (core is not None and e) else "—"
            return v, calc(label, v, asof, "Computed from Call Report",
                           [{"label": "Core income" + (" (annualized)" if (fq or 1) != 1 else ""),
                             "val": _thou(round(core*fq)) + " ($000)" if core is not None else "—"},
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
    st.markdown("##### Trends")
    # One timeframe selector drives every chart below. Data is quarterly, so the
    # windows are year-based; 5Y (= the prior fixed tail(20)) is the default.
    tf = st.segmented_control(
        "Timeframe", ["1Y", "3Y", "5Y", "10Y", "ALL"], default="5Y",
        key=f"{key_prefix}_trtf_{ticker}", label_visibility="collapsed") or "5Y"
    _nq = {"1Y": 4, "3Y": 12, "5Y": 20, "10Y": 40, "ALL": None}[tf]
    _hh = hist.sort_values("REPDTE")
    h = _hh if _nq is None else _hh.tail(_nq)
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
        # ITRADE = trading revenue (RI income line); NOT TRADE, which is the
        # balance-sheet trading-account ASSET used on the Balance Sheet tab.
        ("Trading account income", "dollar", "ITRADE"),
        ("Trust / fiduciary revenue", "dollar", "IFIDUC"),
        ("Service charges on deposits", "dollar", "ISERCHG"),
        ("Insurance revenue", "dollar", "IINSOTH"),
        ("Investment banking fees", "dollar", "IINVFEE"),
        ("Other non-interest income", "noniother",
         "ITRADE", "IFIDUC", "ISERCHG", "IINSOTH", "IINVFEE"),
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

# ── Asset Quality Detail (docs/SNL-BUILD-PLAN.md tab 4) ─────────────────────
# Every field live-verified against TCBK (cert 21943) and BANR (cert 28489)
# 12/31/2025, cross-checked to the owner's CapIQ TCBK screenshot where the
# bases align exactly: ORE 6,245 / LNATRES 125,762 / NTLNLS 9,922
# (= DRLNLS 11,051 − CRLNLS 1,129) / P9LNLS 82; reported ratios NTLNLSR 0.14,
# LNATRESR 1.77. Level rows differing from CapIQ do so by ENTITY (bank
# subsidiary Call Report vs holdco 10-K) — e.g. nonaccrual 64,137 vs 62,449.
#
# Conventions (SNL): Nonperforming Loans = nonaccrual (NALNLS) + restructured
# ACCRUING (RSLNLTOT, the RC-C M.1 in-compliance memo — restructured loans
# that are past due / nonaccrual live in the P3RS*/P9RS*/NARS* fields and are
# already inside P3/P9/NALNLS, so the sum never double-counts; verified
# NARSLNLT ⊄ RSLNLTOT for both banks). The FDIC's own "noncurrent" aggregate
# (NCLNLS = nonaccrual + 90+ PD) is shown as its Reported: row, same pattern
# as CapIQ's "Reported:" lines. NAASSETR was rejected for the nonaccrual/assets
# row: its dictionary title is an agricultural-loan ratio even though the
# value coincides — the transparent fratio shows its own inputs instead.
_ASSET_QUALITY = [
    ("Asset Quality ($000)", [
        ("Nonaccrual Loans", "dollar", "NALNLS"),
        ("Restructured Loans (accruing)", "dollar", "RSLNLTOT"),
        ("» Nonperforming Loans", "sum", "NALNLS", "RSLNLTOT"),
        ("Real Estate Owned & Repossessed, Net", "dollar", "ORE"),
        ("» Nonperforming Assets", "sum", "NALNLS", "RSLNLTOT", "ORE"),
        ("90+ Days Past Due, Still Accruing", "dollar", "P9LNLS"),
        ("» NPAs & 90+ Day Delinquent", "sum", "NALNLS", "RSLNLTOT", "ORE", "P9LNLS"),
        ("Reported: Noncurrent Loans (nonaccrual + 90+ PD)", "dollar", "NCLNLS"),
        ("30–89 Days Past Due, Still Accruing", "dollar", "P3LNLS"),
    ]),
    ("Loan Loss Reserve & Charge-Offs ($000)", [
        ("Loan Loss Reserve", "dollar", "LNATRES"),
        ("Provision for Credit Losses", "flow", "ELNATR", None),
        ("Gross Charge-Offs", "flow", "DRLNLS", "DRLNLSQ"),
        ("Recoveries", "flow", "CRLNLS", "CRLNLSQ"),
        ("» Net Charge-Offs", "flow", "NTLNLS", "NTLNLSQ"),
    ]),
    ("Asset Quality Ratios (%)", [
        ("NPAs / Assets", "fratio", "NALNLS+RSLNLTOT+ORE", "ASSET"),
        ("Reported: Nonperforming Assets / Assets", "pct", "NPERFV"),
        ("Nonaccrual Loans / Assets", "fratio", "NALNLS", "ASSET"),
        ("NPAs & 90+ PD / Assets", "fratio", "NALNLS+RSLNLTOT+ORE+P9LNLS", "ASSET"),
        ("Nonaccrual Loans / Loans", "fratio", "NALNLS", "LNLSGR"),
        ("NPLs / Loans", "fratio", "NALNLS+RSLNLTOT", "LNLSGR"),
        ("Reported: Noncurrent Loans / Loans", "pct", "NCLNLSR"),
        ("30–89 Days Past Due / Loans", "fratio", "P3LNLS", "LNLSGR"),
        ("90+ Days Past Due / Loans", "fratio", "P9LNLS", "LNLSGR"),
        ("NPAs / (Loans + REO)", "fratio", "NALNLS+RSLNLTOT+ORE", "LNLSGR+ORE"),
        ("NPAs & 90+ PD / (Equity + LLR)", "fratio",
         "NALNLS+RSLNLTOT+ORE+P9LNLS", "EQTOT+LNATRES"),
        # Texas ratio. EQTOT − INTAN is tangible TOTAL equity (bank subs almost
        # never carry preferred; a common-only figure would need EQPP netting).
        ("NPAs & 90+ PD / (Tangible Equity + LLR)", "fratio",
         "NALNLS+RSLNLTOT+ORE+P9LNLS", "EQTOT-INTAN+LNATRES"),
        ("Reserves / NPLs", "fratio", "LNATRES", "NALNLS+RSLNLTOT"),
        ("Reserves / NPAs & 90+ PD", "fratio",
         "LNATRES", "NALNLS+RSLNLTOT+ORE+P9LNLS"),
        ("Reserves / Loans", "pct", "LNATRESR"),
        ("Loan Loss Provision / NCO", "flowratio",
         ("ELNATR", None), ("NTLNLS", "NTLNLSQ")),
        ("NCOs / Avg Loans (reported, annualized)", "pct", "NTLNLSR"),
    ]),
]

# Criticized & Classified rows (grade key in the credit_quality_history
# breakdown → row label). "coverage" is computed: graded ÷ LNLSGR — the
# XBRL footnote often grades only the commercial book (plan §4 decision:
# XBRL-only, label the partial coverage, never present it as whole-portfolio).
_CRIT_ROWS = [
    ("Pass", "pass"),
    ("Special Mention", "special_mention"),
    ("Substandard", "substandard"),
    ("Doubtful", "doubtful"),
    ("Loss", "loss"),
    ("» Classified (Substandard + Doubtful + Loss)", "classified"),
    ("» Criticized (Classified + Special Mention)", "criticized"),
    ("Graded Loans / Gross Loans (coverage)", "coverage"),
]


# ── Capital Adequacy (docs/SNL-BUILD-PLAN.md tab 3) ─────────────────────────
# Live-verified TCBK/BANR 12/31/2025: RBCT1J/RWAJ reproduces IDT1CER exactly
# (RBCT1J = CET1 $), RBCT1/RWAJ = RBC1RWAJ, RBCT1 + RBCT2 = RBC to the dollar.
# Leverage stays RBCT1JR — the field config.py's leverage_ratio metric already
# uses platform-wide (RBC1AAJ differs a few bps; one convention everywhere).
# T1/T2 component walks are the RC-R section further down the page; LCR/HQLA
# are large-bank-only (SNL shows NA too) — covered by the holdco caption.
_CAPITAL_ADEQUACY = [
    ("Regulatory Capital ($000)", [
        ("Common Equity Tier 1 (CET1) Capital", "dollar", "RBCT1J"),
        ("Additional Tier 1 Capital", "diff", "RBCT1", "RBCT1J"),
        ("» Tier 1 Capital", "dollar", "RBCT1"),
        ("Tier 2 Capital", "dollar", "RBCT2"),
        ("» Total Risk-Based Capital", "dollar", "RBC"),
        ("Risk-Weighted Assets", "dollar", "RWAJ"),
    ]),
    ("Equity Capital ($000)", [
        ("Total Equity Capital", "dollar", "EQTOT"),
        ("Total Intangibles (incl. goodwill)", "dollar", "INTAN"),
        ("» Tangible Equity", "tce"),
    ]),
    ("Capital Ratios (%)", [
        ("CET1 Ratio", "pct", "IDT1CER"),
        ("Tier 1 Ratio", "pct", "RBC1RWAJ"),
        ("Total Capital Ratio", "pct", "RBCRWAJ"),
        ("Tier 1 Leverage Ratio", "pct", "RBCT1JR"),
        ("RWA / Total Assets", "ratio", "RWAJ", "ASSET"),
        ("Equity / Assets", "ratio", "EQTOT", "ASSET"),
        ("Tangible Equity / Tangible Assets", "fratio", "EQTOT-INTAN", "ASSET-INTAN"),
    ]),
    ("Annualized Growth Rates (%)", [
        ("CET1 Capital Growth", "growth", "RBCT1J"),
        ("Risk-Weighted Asset Growth", "growth", "RWAJ"),
        ("Equity Growth", "growth", "EQTOT"),
    ]),
]


# ── Asset Quality by Loan Type (docs/SNL-BUILD-PLAN.md tab 5 — beats SNL) ───
# The FULL per-category delinquency dollar matrix from SDI, probed 2026-07-13:
# leaf-category sums reconcile to the filed totals TO THE DOLLAR for
# TCBK + BANR in all three stages. "of which" rows (HELOC ⊂ 1-4 fam;
# OO/NOO ⊂ CRE) are excluded from the leaf set. Agricultural P3AG/P9AG/NAAG
# exist in the FDIC dictionary but the financials endpoint drops them —
# the residual row (filed total − leaves) carries ag + anything unrequested,
# and renders n/a if ever negative (residual kind).
_BYLT_LEAVES = ["RECONS", "RERES", "REMULT", "RENRES", "REAG",
                "CI", "CRCD", "AUTO", "CONOTH", "LS", "OTHLN"]
_BYLT_ROWS = [
    ("Construction & Land", "RECONS"),
    ("1–4 Family Residential", "RERES"),
    ("of which: HELOC (1–4 fam lines)", "RELOC"),
    ("Multifamily", "REMULT"),
    ("CRE Nonfarm Nonresidential", "RENRES"),
    ("of which: Owner-Occupied", "RENROW"),
    ("of which: Non-Owner-Occupied", "RENROT"),
    ("Farmland", "REAG"),
    ("Commercial & Industrial", "CI"),
    ("Credit Cards", "CRCD"),
    ("Auto", "AUTO"),
    ("Other Consumer", "CONOTH"),
    ("Leases", "LS"),
    ("All Other Loans", "OTHLN"),
]


def _bylt_section(title, prefix, total_field):
    rows = [(lb, "dollar", prefix + sfx) for lb, sfx in _BYLT_ROWS]
    rows.append(("Agricultural & other (residual)", "residual", total_field,
                 *[prefix + s for s in _BYLT_LEAVES]))
    rows.append(("» Total", "dollar", total_field))
    return (title, rows)


_AQ_BY_LOAN_TYPE = [
    _bylt_section("Nonaccrual by Loan Type ($000)", "NA", "NALNLS"),
    _bylt_section("90+ Days Past Due, Accruing, by Loan Type ($000)", "P9", "P9LNLS"),
    _bylt_section("30–89 Days Past Due by Loan Type ($000)", "P3", "P3LNLS"),
    ("Noncurrent Ratio by Loan Type (%, reported)", [
        ("Construction & Land", "pct", "NCRECONR"),
        ("1–4 Family Residential", "pct", "NCRERESR"),
        ("HELOC (1–4 fam lines)", "pct", "NCRELOCR"),
        ("Multifamily", "pct", "NCREMULR"),
        ("CRE Nonfarm Nonresidential", "pct", "NCRENRER"),
        ("Commercial & Industrial", "pct", "IDNCCIR"),
        ("Consumer", "pct", "IDNCCONR"),
        ("» Total Loans & Leases", "pct", "NCLNLSR"),
    ]),
]

# ── Deposit/Loan Composition (docs/SNL-BUILD-PLAN.md tab 6) ─────────────────
# Both trees reconcile to the dollar (probed TCBK/BANR 12/31/2025):
# LNLSGR = LNRE + LNCI + LNCON + LNAG + LS + LNOTHER + LNMUNI + LNDEP
# (BANR's 397M gap was LNMUNI); DEP = TRN + NTR; NTR = NTRSMMDA (MMDA) +
# NTRSOTH (other savings) + NTRTIME. HELOC ⊂ 1-4 fam; OO/NOO ⊂ CRE.
_DEPOSIT_LOAN_COMP = [
    ("Loan Composition ($000)", [
        ("Construction & Land", "dollar", "LNRECONS"),
        ("1–4 Family Residential", "dollar", "LNRERES"),
        ("of which: HELOC (1–4 fam lines)", "dollar", "LNRELOC"),
        ("Multifamily", "dollar", "LNREMULT"),
        ("CRE Nonfarm Nonresidential", "dollar", "LNRENRES"),
        ("of which: Owner-Occupied", "dollar", "LNRENROW"),
        ("of which: Non-Owner-Occupied", "dollar", "LNRENROT"),
        ("Farmland", "dollar", "LNREAG"),
        ("» Total Real Estate Loans", "dollar", "LNRE"),
        ("Commercial & Industrial", "dollar", "LNCI"),
        ("Consumer", "dollar", "LNCON"),
        ("of which: Auto", "dollar", "LNAUTO"),
        ("of which: Credit Cards", "dollar", "LNCRCD"),
        ("of which: Other Consumer", "dollar", "LNCONOTH"),
        ("Agricultural Production", "dollar", "LNAG"),
        ("Municipal & States", "dollar", "LNMUNI"),
        ("Loans to Depository Institutions", "dollar", "LNDEP"),
        ("Leases", "dollar", "LS"),
        ("Other Loans", "dollar", "LNOTHER"),
        ("» Gross Loans & Leases", "dollar", "LNLSGR"),
    ]),
    ("Loan Mix (% of gross loans)", [
        ("Real Estate / Gross Loans", "fratio", "LNRE", "LNLSGR"),
        ("CRE (nonfarm nonres) / Gross Loans", "fratio", "LNRENRES", "LNLSGR"),
        ("Construction / Gross Loans", "fratio", "LNRECONS", "LNLSGR"),
        ("1–4 Family / Gross Loans", "fratio", "LNRERES", "LNLSGR"),
        ("C&I / Gross Loans", "fratio", "LNCI", "LNLSGR"),
        ("Consumer / Gross Loans", "fratio", "LNCON", "LNLSGR"),
    ]),
    ("Deposit Composition ($000)", [
        ("Transaction Accounts", "dollar", "TRN"),
        ("of which: Demand Deposits", "dollar", "DDT"),
        ("Savings & MMDA", "dollar", "NTRSMMDA"),
        ("Other Savings", "dollar", "NTRSOTH"),
        ("Time Deposits", "dollar", "NTRTIME"),
        ("» Total Nontransaction", "dollar", "NTR"),
        ("» Total Deposits", "dollar", "DEP"),
        ("Non-Interest-Bearing (domestic)", "dollar", "DEPNIDOM"),
        ("Interest-Bearing (domestic)", "dollar", "DEPIDOM"),
        ("Core Deposits", "dollar", "COREDEP"),
        ("Brokered Deposits", "dollar", "BRO"),
        ("Est. Insured Deposits", "dollar", "DEPINS"),
        ("Est. Uninsured Deposits", "dollar", "DEPUNINS"),
        ("Accounts > $250K", "dollar", "DEPLGAMT"),
        ("Accounts ≤ $250K", "dollar", "DEPSMAMT"),
    ]),
    ("Deposit Mix & Funding (%)", [
        ("Core Deposits / Deposits", "fratio", "COREDEP", "DEP"),
        ("Brokered / Deposits", "fratio", "BRO", "DEP"),
        ("Est. Uninsured / Deposits", "fratio", "DEPUNINS", "DEP"),
        ("Non-Interest-Bearing / Deposits", "fratio", "DEPNIDOM", "DEP"),
        ("Time Deposits / Deposits", "fratio", "NTRTIME", "DEP"),
        ("Net Loans / Deposits", "ratio", "LNLSNET", "DEP"),
    ]),
    ("Annualized Growth Rates (%)", [
        ("Gross Loan Growth", "growth", "LNLSGR"),
        ("CRE Growth", "growth", "LNRENRES"),
        ("Deposit Growth", "growth", "DEP"),
        ("Core Deposit Growth", "growth", "COREDEP"),
    ]),
]

# Deposit Trends keeps its beta/cost charts; its left table becomes the
# deposit side of the composition (with the toggle) + growth.
_DEPOSIT_TRENDS_TABLE = [_DEPOSIT_LOAN_COMP[2], _DEPOSIT_LOAN_COMP[3],
                         _DEPOSIT_LOAN_COMP[4]]

# Trend groups for the Deposit/Loan Composition page (metric keys pinned by
# test_statement_trends to exist in config.METRICS_BY_KEY).
_DLC_TRENDS = [
    ("Deposit Funding Mix (%)", ["nonint_dep_pct", "core_dep_pct",
                                 "uninsured_pct", "brokered_pct"]),
    ("Deposits ($B)", ["total_deposits", "core_deposits", "uninsured_deposits"]),
    ("Loan Composition ($B)", ["ln_re_total", "ln_re_residential", "ln_ci"]),
    ("CRE Detail ($B)", ["ln_re_nres_oo", "ln_re_nres_noo",
                         "ln_re_multifam", "ln_re_construct"]),
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


def _cr_title(ticker: str, page: str) -> None:
    """SNL title bar for a Company-Reported sub-tab (replaces st.subheader)."""
    from ui.chrome import title_bar
    info = get_bank_info(ticker)
    name = info.get("name") if info else ticker
    title_bar(f"{name} ({ticker})", page)


def _cr_component(col_labels: list, rows: list, *, entity: str = "", src: str | None = None,
                  height_extra: int = 0) -> None:
    """Render a Company-Reported table through the SAME _build_component the
    Templated statements use (sec bands, navy values, zebra). cells={} => not
    clickable (CR has no per-cell calc). rows: [{"label", "values":[str|None,...],
    "kind":"data"|"header"}]; values pre-formatted; "(" => red .neg; None/"" =>
    grey blank."""
    import html as _h
    from streamlit.components import v1 as _stc
    ncol = len(col_labels)
    body, ri = [], 0
    for r in rows:
        if r.get("kind") == "header":
            body.append(f'<tr><td class="sec" colspan="{ncol + 1}">{_h.escape(str(r["label"]))}</td></tr>')
            continue
        tds = [f'<td class="lbl">{_h.escape(str(r["label"]))}</td>']
        for v in r["values"]:
            s = "" if v is None or v == "" else _h.escape(str(v))
            if s == "":
                tds.append('<td class="val dead"></td>')
            else:
                neg = " neg" if s.strip().startswith("(") else ""
                tds.append(f'<td class="val{neg}">{s}</td>')
        zebra = ' class="zebra"' if ri % 2 == 1 else ""
        body.append(f'<tr{zebra}>{"".join(tds)}</tr>')
        ri += 1
    head = ('<th class="lblh">(figures in USD)</th>'
            + "".join(f'<th class="colh">{_h.escape(str(lb))}</th>' for lb in col_labels))
    height = 96 + 23 * (ri + 4) + height_extra
    html = _build_component(head, "".join(body), {}, entity, None, src)
    # _build_component is width:100% — right for a multi-year statement that fills
    # the page, but a snapshot with only 1-2 value columns would stretch those
    # columns edge-to-edge and leave a huge dead gap. Constrain narrow tables to a
    # left column instead of spanning the full width.
    if len(col_labels) <= 2:
        with st.columns([2, 3])[0]:
            _stc.html(html, height=height, scrolling=False)
    else:
        _stc.html(html, height=height, scrolling=False)


def _cr_usd(raw):
    """Company-Reported raw dollars -> Templated $-compact ($673.0M / $1.16B)."""
    return _usd(raw / 1000.0) if raw is not None else ""   # _usd takes $thousands


def _render_company_statement(ticker: str, stype: str):
    """Company-Reported statement (stype = "income" | "balance"), stitched from
    the bank's own SEC filings. An Annual/Quarterly toggle switches between the
    multi-year 10-K stitch (default) and the discrete-single-quarter 10-Q stitch
    (12 quarters). Faithful to the company's own line items; blank where a line
    wasn't reported that period."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        st.info("No SEC filer mapping for this bank.")
        return
    view = st.radio("Period", ["Annual", "Quarterly"], horizontal=True,
                    key=f"cr_period_{stype}_{ticker}", label_visibility="collapsed")
    if view == "Quarterly":
        _render_company_statement_quarterly(ticker, stype, cik, info)
        return
    _render_company_statement_annual(ticker, stype, cik, info)


def _render_company_statement_annual(ticker, stype, cik, info):
    """Multi-year Company-Reported statement, stitched from the bank's recent
    10-Ks. Faithful to the company's own line items; blank where a line wasn't
    reported that year. The income per-share / weighted-share trailer is omitted
    pending per-share unit handling."""
    import re
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
               f"Dollar lines \\$-compact;{_persh_note} "
               f"blank = not separately reported that year.")
    periods = stmt["periods"][::-1]            # oldest → newest (matches Templated)
    cols = [f"FY{_yr(p)}" for p in periods]

    def _m(label, v):
        if v is None:
            return ""
        if _eps.search(label):
            return f"${v:,.2f}"                 # EPS as $/share (component escapes)
        if _shares.search(label):
            return f"{v / 1e6:,.1f}M"           # share counts in millions
        # Dollar lines: raw dollars -> Templated $-compact; negatives in parens.
        return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)

    rows = []
    for r in stmt["rows"]:
        if r["header"]:
            rows.append({"label": r["label"], "values": [], "kind": "header"})
        else:
            vals = r["values"][::-1]
            rows.append({"label": r["label"],
                         "values": [_m(r["label"], v) for v in vals],
                         "kind": "data"})

    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(cols, rows, entity=entity, src=src)
    with _rt:
        _cr_statement_trends(stmt, ticker, f"cr{stype}",
                             _CR_INCOME_TRENDS if stype == "income" else _CR_BALANCE_TRENDS)


def _render_company_statement_quarterly(ticker, stype, cik, info):
    """Discrete-quarter Company-Reported statement (12 quarters), stitched from
    the bank's own 10-Qs (and 10-Ks for the year-end column). Income/cash-flow
    columns are TRUE single quarters: Q1–Q3 are each 10-Q's "three months ended"
    figure; Q4 = annual 10-K minus the nine-month 10-Q (audit invariant A21 — a
    quarter is never a YTD cumulative). Balance-sheet columns are point-in-time
    quarter-end snapshots. A quarter that can't be cleanly derived is left blank,
    never guessed. All figures are as-reported and company-sourced (never FDIC)."""
    import re
    try:
        from data.sec_statements import as_reported_statement_multiquarter
        res = as_reported_statement_multiquarter(cik, stype, n_quarters=12)
    except Exception:
        res = None
    if not res:
        st.caption("Company-reported quarterly statement not available from this "
                   "filer's 10-Qs — n/a.")
        return
    stmt, filings, latest = res["statement"], res["filings"], res["meta"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(latest['cik'])}/"
           f"{latest['accession']}/{latest['doc']}")

    _eps = re.compile(r"per share|per common share", re.I)
    _shares = re.compile(r"\(in shares\)|in shares", re.I)
    _has_persh = any(_eps.search(r["label"]) or _shares.search(r["label"])
                     for r in stmt["rows"] if not r["header"])
    _persh_note = " EPS in \\$/share, shares in millions;" if _has_persh else ""
    _q4 = ("" if stype == "balance"
           else " Discrete quarters — Q4 = annual 10-K minus the nine-month 10-Q.")
    st.caption(f"Source: stitched from the bank's 10-Qs — latest [{latest['date']}]"
               f"({src}); {len(stmt['periods'])} quarters from {len(filings)} filings."
               f"{_q4} As-reported, company-sourced (never FDIC). Dollar lines "
               f"\\$-compact;{_persh_note} blank = not cleanly derivable.")
    periods = stmt["periods"][::-1]            # oldest → newest (matches Annual)
    cols = list(periods)                       # already compact "Q3'25" labels

    def _m(label, v):
        if v is None:
            return ""
        if _eps.search(label):
            return f"${v:,.2f}"
        if _shares.search(label):
            return f"{v / 1e6:,.1f}M"
        return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)

    rows = []
    for r in stmt["rows"]:
        if r["header"]:
            rows.append({"label": r["label"], "values": [], "kind": "header"})
        else:
            vals = r["values"][::-1]
            rows.append({"label": r["label"],
                         "values": [_m(r["label"], v) for v in vals],
                         "kind": "data"})

    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(cols, rows, entity=entity, src=src)
    with _rt:
        _cr_statement_trends(stmt, ticker, f"crq{stype}",
                             _CR_INCOME_TRENDS if stype == "income" else _CR_BALANCE_TRENDS)


# Company-Reported statement trend charts. The scraped statement is ANNUAL (5 FY),
# so these are clean year-over-year lines (no FDIC YTD sawtooth). Each series is
# matched to a stmt row by its as-reported label; groups hold like-magnitude lines
# so tighten_yaxis zooms and the trends read (same principle as the FDIC trends).
# (title, divisor, axis-unit, [(legend, [accepted as-reported labels])]).
_CR_INCOME_TRENDS = [
    ("Net Interest Income & Net Income ($M)", 1e6, "$M", [
        ("Net interest income", ["net interest income"]),
        ("Net income", ["net income"]),
    ]),
    ("Revenue & Expense ($M)", 1e6, "$M", [
        ("Total interest income", ["total interest income"]),
        ("Noninterest income", ["total noninterest income"]),
        ("Noninterest expense", ["total noninterest expense"]),
    ]),
    ("Provision for Credit Losses ($M)", 1e6, "$M", [
        ("Provision", ["provision for credit losses"]),
    ]),
]
_CR_BALANCE_TRENDS = [
    ("Assets, Loans & Deposits ($B)", 1e9, "$B", [
        ("Total assets", ["total assets"]),
        ("Net loans", ["loans, net", "total loans, net", "net loans"]),
        ("Total deposits", ["total deposits"]),
    ]),
    ("Shareholders' Equity ($B)", 1e9, "$B", [
        ("Total equity", ["total shareholders' equity",
                          "total stockholders' equity", "total equity"]),
    ]),
]


def _cr_statement_trends(stmt, ticker, key_prefix, groups):
    """Annual trend charts from a scraped Company-Reported statement (clean YoY
    lines). Matches each series to a stmt data row by normalized as-reported label
    (curly apostrophes folded); scales by the group divisor. Series with no match
    are skipped; a chart with no matched series is skipped; nothing renders if no
    chart has data."""
    import re
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)

    def _norm(s):
        return re.sub(r"\s+", " ", (s or "")).strip().lower().replace(
            "’", "'").replace("‘", "'")

    by_label = {_norm(r["label"]): r["values"][::-1]
                for r in stmt["rows"] if not r["header"]}

    def _xlab(p):
        # Annual periods carry a 4-digit year -> "FY2025". Quarterly periods are
        # already compact ("Q3'25") -> shown verbatim.
        m = re.search(r"\b\d{4}\b", p or "")
        return f"FY{m.group()}" if m else (p or "")
    xs = [_xlab(p) for p in stmt["periods"][::-1]]

    charts = []
    for title, div, unit, series in groups:
        traces = []
        for lab, alts in series:
            vals = next((by_label[_norm(a)] for a in alts if _norm(a) in by_label), None)
            if vals is None:
                continue
            ys = [(v / div if v is not None else None) for v in vals]
            if any(y is not None for y in ys):
                traces.append((lab, ys))
        if traces:
            charts.append((title, unit, traces))
    if not charts:
        return

    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, unit, traces)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                allv = []
                for i, (lab, ys) in enumerate(traces):
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, name=lab, mode="lines+markers", connectgaps=True,
                        line=dict(color=CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)],
                                  width=2), marker=dict(size=5)))
                    allv += [y for y in ys if y is not None]
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title=unit, show_legend=len(traces) > 1)
                tighten_yaxis(fig, values=allv or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_crtr_{ticker}_{r + j}")


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


def _comp_norm_label(label) -> str:
    """Normalised key for matching the SAME category across periods: lowercase,
    trimmed, curly apostrophes folded to straight, internal whitespace collapsed.
    Used only for row alignment — the DISPLAYED label is the filer's own text."""
    s = str(label).replace("’", "'").replace("‘", "'")
    return " ".join(s.lower().split())


def _render_company_composition(ticker, kind):
    """As-reported loan/deposit composition (kind = "loan" | "deposit") from the
    bank's OWN 10-K inline XBRL — each line the filer's own category label, the set
    reconciled to the disclosed total (data/sec_composition). Multi-year: one $
    column per fiscal year the filing reconciles (oldest→newest, up to 5 FY), the
    row set the UNION of categories across those years matched by normalised label,
    blank where a category wasn't reported that year (never carried forward). n/a
    when the bank doesn't disclose a clean, reconciling composition."""
    import re
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
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
           f"{meta['accession']}/{meta['doc']}")

    # comp is {period: {"total", "rows": [(label, value)]}} newest-first; take the
    # most recent 5 FY and render oldest→newest (matching the multi-year statements).
    periods = list(comp)[:5][::-1]            # oldest → newest

    def _yr(p):
        m = re.search(r"\d{4}", p or "")
        return m.group() if m else (p or "")

    cols = [f"FY{_yr(p)}" for p in periods]

    # Union the category labels across the in-view periods, matched by normalised
    # label; keep the first-seen display text. A category is a row even if only one
    # period reports it (blank elsewhere — filers rename/add/drop lines year to
    # year; never guess or carry a value forward).
    order, display, per_val = [], {}, {}       # norm-key order, key->label, key->{period:value}
    for p in periods:
        for label, v in comp[p]["rows"]:
            key = _comp_norm_label(label)
            if key not in per_val:
                order.append(key)
                display[key] = str(label)
                per_val[key] = {}
            per_val[key][p] = v

    # Order rows by the latest period's value (largest first), trailing any category
    # absent from the latest period; keeps the table reading like the latest year.
    latest = periods[-1]
    order.sort(key=lambda k: (-(per_val[k].get(latest) or -1), display[k].lower()))

    rows = []
    for key in order:
        vals = [_cr_usd(per_val[key].get(p)) if per_val[key].get(p) is not None else None
                for p in periods]
        if all(v is None for v in vals):
            continue                         # category None for every in-view period
        rows.append({"label": display[key], "values": vals, "kind": "data"})
    rows.append({"label": "Total",
                 "values": [_cr_usd(comp[p]["total"]) for p in periods], "kind": "data"})

    latest_total = comp[latest]["total"]
    st.caption(f"Source: company 10-K [{meta['date']}]({src}) — as reported, "
               f"{len(periods)} fiscal year{'s' if len(periods) != 1 else ''} "
               f"(latest {latest}). Each line is the company's own category; the "
               f"lines reconcile to the disclosed total each year "
               f"(latest \\${latest_total / 1e6:,.0f}M). Blank = not separately "
               f"reported that year.")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _cr_component(cols, rows, entity=entity, src=src)


# @st.fragment on the three statement pages: the Annual/Quarterly toggle +
# trend-timeframe selectors inside render_statement used to rerun the WHOLE
# Company page to redraw one statement. Scoping each page to a fragment reruns
# only that statement. Fragmenting the dispatched wrappers (not the shared
# render_statement helper) keeps the boundary at the page level and matches the
# other Company-module fragments. Fragments render fully on a full rerun.
@st.fragment
def render_income_statement(ticker):
    render_statement(ticker, "is", "Income Statement", _INCOME, with_ri=True,
                     trends=_INCOME_TRENDS, side_by_side=True)


@st.fragment
def render_balance_sheet(ticker):
    render_statement(ticker, "bs", "Balance Sheet", _BALANCE, trends=_BS_TRENDS,
                     side_by_side=True)


@st.fragment
def render_performance_analysis(ticker):
    render_statement(ticker, "perf", "Performance Analysis", _PERFORMANCE,
                     with_persh=True, with_dep_cost=True, with_fte=True,
                     side_by_side=True)


def render_aq_by_loan_type(ticker):
    """Asset Quality by Loan Type statement table (the full per-category
    delinquency matrix SNL shows as NA). Left column of credit_dynamics'
    by-loan-type view — segment NPL chart stays on the right."""
    render_statement(ticker, "aqlt", "Asset Quality by Loan Type",
                     _AQ_BY_LOAN_TYPE, trends=[], header=False)


@st.fragment
def render_deposit_loan_composition(ticker):
    """Deposit/Loan Composition page: full mix table (both trees reconcile
    to filed totals on the face) with trends on the right."""
    render_statement(ticker, "dlc", "Deposit/Loan Composition",
                     _DEPOSIT_LOAN_COMP, trends=_DLC_TRENDS,
                     side_by_side=True, header=False)


def render_deposit_trends_table(ticker):
    """Deposit-side composition table for the Deposit Trends page's left
    column (its cost/beta charts stay on the right)."""
    render_statement(ticker, "deptr", "Deposit Trends",
                     _DEPOSIT_TRENDS_TABLE, trends=[], header=False)


def render_capital_adequacy(ticker):
    """Capital Adequacy statement table (bank-level regulatory capital).
    Rendered by capital_dynamics inside its left column — same integration
    as render_asset_quality (charts right, page chrome owns the header)."""
    render_statement(ticker, "capadq", "Capital Adequacy", _CAPITAL_ADEQUACY,
                     trends=[], header=False)


def render_asset_quality(ticker):
    """Asset Quality Detail statement table (SNL/CapIQ depth). Rendered by
    credit_dynamics inside its own left column (charts stay on the right), so
    no side_by_side/trends/header here — the table IS this pane.

    The Criticized & Classified section joins the company's OWN 10-K/10-Q
    dimensional-XBRL grades onto the FDIC columns by period end. The history
    mode must match the Annual/Quarterly radio, which lives INSIDE
    render_statement — its widget state is read here (absent on first render
    = the radio's Annual default). SEC-side failure degrades to dead cells;
    the FDIC table must never be hostage to EDGAR."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    quarterly = st.session_state.get(f"aq_period_{ticker}") == "Quarterly"

    crit_map = {}
    if cik:
        try:
            from data.xbrl_dimensional import credit_quality_history
            crit_map = credit_quality_history(int(cik), quarterly=quarterly) or {}
        except Exception as e:
            print(f"[statements] criticized history unavailable for {ticker}: "
                  f"{type(e).__name__}: {e}")

    spec = list(_ASSET_QUALITY)
    if crit_map:
        # Partial-coverage label is REQUIRED (plan §4 owner decision): filers
        # often grade only the commercial book in XBRL — say what's graded,
        # never imply whole-portfolio.
        spec.append((
            "Criticized & Classified — Graded Classes Only ($000, company 10-K/10-Q XBRL)",
            [(lb, "crit", crit_map, ck) for lb, ck in _CRIT_ROWS],
        ))
    render_statement(ticker, "aq", "Asset Quality Detail", spec,
                     trends=[], header=False)


# Fair-value hierarchy sub-tab: per-side section band + the rows shown within it.
# fmt: "usd" = $-compact dollars; "pct" = % (fraction → %). The netting / grand
# rows are added per-side only for years that disclose a reconciling netting item
# (filer's tagged grand ≠ level sum); blank elsewhere.
_FV_LEVEL_ROWS = [
    ("Level 1 (quoted prices)", "l1", "usd"),
    ("Level 2 (observable inputs)", "l2", "usd"),
    ("Level 3 (unobservable inputs)", "l3", "usd"),
    ("Total (sum of levels)", "total", "usd"),
]
_CR_FV_SIDES = [("assets", "Assets at fair value"),
                ("liabilities", "Liabilities at fair value")]


def _render_fair_value_hierarchy(ticker):
    """Multi-year (up to 5 FY) recurring ASC 820 fair-value hierarchy (Level 1/2/3)
    for the HOLDING COMPANY, stitched FY-end-only from its own recent 10-Ks
    (data.sec_filing_scraper.fair_value_multiyear_for). Level 3 is the mark-to-
    model share. Each year is independently reconcile-gated by the extractor (the
    level sum is the side's total — never a component-summed guess; disclosure-table
    conflations are rejected). Where a year's tagged grand total differs from the
    level sum (dealer counterparty/collateral netting) that difference is shown as
    an explicit reconciling line for that year; a row n/a for every year is dropped;
    a year that doesn't disclose a side is blank. Company-reported, never FDIC."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import fair_value_multiyear_for
        res = fair_value_multiyear_for(cik, n_years=5)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Fair Value Hierarchy — recurring (ASC 820)")
    if not res or not res.get("fair_value"):
        st.caption("Fair-value hierarchy rollup not tagged in this filer's 10-Ks — "
                   "n/a. (Per-instrument component extraction is planned.)")
        return
    meta, fv = res["meta"], res["fair_value"]
    periods = sorted(fv, reverse=True)[::-1]            # oldest → newest (FY-ends)
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
           f"{meta['accession']}/{meta['doc']}")
    cols = [f"FY{p[:4]}" for p in periods]

    def _cell(period, side, key, fmt):
        """Formatted cell, or None (blank) when the year doesn't disclose it."""
        d = (fv.get(period) or {}).get(side)
        if not d:
            return None
        v = d.get(key)
        if v is None:
            return None
        if fmt == "pct":
            return f"{v * 100:.1f}%"
        return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)

    needs_netting = any(
        (fv.get(p) or {}).get(side) and not (fv[p][side]).get("_reconciles")
        for p in periods for side, _ in _CR_FV_SIDES)

    rows = []
    for side, band in _CR_FV_SIDES:
        metrics = list(_FV_LEVEL_ROWS)
        if needs_netting:
            metrics = metrics + [
                ("Counterparty/collateral netting", "netting", "usd"),
                ("Total per filing", "grand", "usd")]
        metrics = metrics + [("Level 3 % of total", "l3_pct", "pct")]
        side_rows = []
        for label, key, fmt in metrics:
            vals = [_cell(p, side, key, fmt) for p in periods]
            if any(v is not None for v in vals):        # drop rows n/a every year
                side_rows.append({"label": label, "values": vals, "kind": "data"})
        if side_rows:
            rows.append({"label": band, "values": [], "kind": "header"})
            rows.extend(side_rows)

    if not rows:
        st.caption("Fair-value hierarchy rollup not tagged in this filer's 10-Ks — "
                   "n/a. (Per-instrument component extraction is planned.)")
        return

    st.caption(
        f"Source: company 10-K filings — latest [{meta['date']}]({src}); "
        f"{len(periods)} fiscal year-end{'s' if len(periods) != 1 else ''} stitched "
        f"from {len(res['filings'])} filings. Level 3 = mark-to-model (unobservable "
        "inputs); the total is the sum of tagged levels. Company-reported, never FDIC.")

    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(cols, rows, entity=entity, src=src)
        if needs_netting:
            st.caption("Level totals are gross; for a year whose filer-tagged grand "
                       "total nets counterparty/collateral arrangements, the netting "
                       "is shown as a reconciling line (blank where it doesn't apply).")
    with _rt:
        _cr_fair_value_trends(periods, fv, ticker, "crfv")


def _cr_fair_value_trends(periods, fv, ticker, key_prefix):
    """Right-hand trend charts for multi-year Company-Reported Fair Value: Level 3
    as % of total, one single-line chart per side (Assets, Liabilities). Same style
    as _cr_securities_trends; a side n/a for every year is skipped; ≥3 points to
    render (else table only)."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = [f"FY{p[:4]}" for p in periods]               # already oldest → newest
    charts = []
    for title, side in (("Level 3 % of assets at fair value", "assets"),
                        ("Level 3 % of liabilities at fair value", "liabilities")):
        ys = []
        for p in periods:
            d = (fv.get(p) or {}).get(side)
            l3p = d.get("l3_pct") if d else None
            ys.append(l3p * 100 if l3p is not None else None)
        if sum(1 for y in ys if y is not None) >= 3:
            charts.append((title, ys))
    if not charts:
        return
    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title="%", show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_fvtr_{ticker}_{r + j}")


# Securities (AFS/HTM) sub-tab: (portfolio data-key, section band label,
# [(row label, dict key, fmt)]). fmt: "usd" = $-compact dollars; "pct" = signed
# % (fraction → %). Gross gain/loss are blanked per-year where that period's
# bridge doesn't reconcile (gross split untagged) — the ac/fv/net trio is always
# shown when present.
_SEC_ROWS = [
    ("Amortized cost", "amortized_cost", "usd"),
    ("Gross unrealized gain", "unrealized_gain", "usd"),
    ("Gross unrealized loss", "unrealized_loss", "usd"),
    ("Fair value", "fair_value", "usd"),
    ("Net unrealized gain / (loss)", "net_unrealized", "usd"),
    ("Net unrealized, % of amortized cost", "underwater_pct", "pct"),
]
_CR_SECURITIES_SECTIONS = [
    ("afs", "Available-for-sale", _SEC_ROWS),
    ("htm", "Held-to-maturity", _SEC_ROWS),
]


def _render_securities_portfolio(ticker):
    """Multi-year (up to 5 FY) Company-Reported AFS / HTM debt-securities
    amortized-cost → fair-value bridge, stitched FY-end-only from the HOLDING
    COMPANY's own recent 10-Ks (data.sec_filing_scraper.securities_multiyear_for).
    The HTM unrealized loss never touches the balance sheet or AOCI — this is the
    'underwater bonds' picture across years. Each period/portfolio is reconcile-
    gated by the extractor (the amortized-cost candidate had to bridge fair value);
    the gross gain/loss split is blanked for a year that doesn't tag a tying split
    (never a guess). A row n/a for every year is dropped. Company-reported,
    never FDIC."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import securities_multiyear_for
        res = securities_multiyear_for(cik, n_years=5)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Investment Securities — AFS / HTM (Company Reported)")
    if not res or not res.get("securities"):
        st.caption("AFS/HTM amortized-cost → fair-value bridge not tagged in this "
                   "filer's 10-Ks — n/a.")
        return
    meta, sec = res["meta"], res["securities"]
    periods = sorted(sec, reverse=True)[::-1]          # oldest → newest (FY-ends)
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
           f"{meta['accession']}/{meta['doc']}")
    cols = [f"FY{p[:4]}" for p in periods]

    def _cell(period, port, key, fmt):
        """Formatted cell, or None (blank) when the year doesn't disclose it."""
        d = (sec.get(period) or {}).get(port)
        if not d:
            return None
        v = d.get(key)
        if v is None:
            return None
        if fmt == "pct":
            return f"{v * 100:+.1f}%"
        return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)

    any_gross_gated = False
    rows = []
    for port, band, metrics in _CR_SECURITIES_SECTIONS:
        sec_rows = []
        for label, key, fmt in metrics:
            vals = [_cell(p, port, key, fmt) for p in periods]
            if any(v is not None for v in vals):       # drop rows n/a every year
                sec_rows.append({"label": label, "values": vals, "kind": "data"})
        if sec_rows:
            rows.append({"label": band, "values": [], "kind": "header"})
            rows.extend(sec_rows)
        # Flag if any in-view period tagged this portfolio but not a tying split.
        for p in periods:
            d = (sec.get(p) or {}).get(port)
            if d and not d.get("_reconciles"):
                any_gross_gated = True

    if not rows:
        st.caption("AFS/HTM amortized-cost → fair-value bridge not tagged in this "
                   "filer's 10-Ks — n/a.")
        return

    st.caption(
        f"Source: company 10-K filings — latest [{meta['date']}]({src}); "
        f"{len(periods)} fiscal year-ends stitched from {len(res['filings'])} filings. "
        "Net unrealized = fair value − amortized cost; HTM losses are NOT reflected "
        "on the balance sheet or in AOCI. Company-reported, never FDIC.")

    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(cols, rows, entity=entity, src=src)
        if any_gross_gated:
            st.caption("Gross gain/loss split shown only for a year that tags a split "
                       "tying the amortized-cost → fair-value bridge; otherwise blank "
                       "(the net is the directly tagged fair value − amortized cost).")
    with _rt:
        _cr_securities_trends(periods, sec, ticker, "crsec")


def _cr_securities_trends(periods, sec, ticker, key_prefix):
    """Right-hand trend charts for multi-year Company-Reported Securities: net
    unrealized as % of amortized cost (the underwater %), one single-line chart per
    portfolio (AFS, HTM). Same plotting style as _cr_credit_trends; a portfolio
    n/a for every year is skipped; ≥3 points to render."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = [f"FY{p[:4]}" for p in periods]               # already oldest → newest
    charts = []
    for title, port in (("AFS net unrealized, % of amortized cost", "afs"),
                        ("HTM net unrealized, % of amortized cost", "htm")):
        ys = []
        for p in periods:
            d = (sec.get(p) or {}).get(port)
            uw = d.get("underwater_pct") if d else None
            ys.append(uw * 100 if uw is not None else None)
        if sum(1 for y in ys if y is not None) >= 3:
            charts.append((title, ys))
    if not charts:
        return
    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title="%", show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_sectr_{ticker}_{r + j}")


def render_fair_value(ticker):
    render_statement(ticker, "fv", "Fair Value Analysis", _FAIR_VALUE)
    _render_fair_value_hierarchy(ticker)
    st.caption("AFS/HTM unrealized gain/loss (AOCI) detail from FFIEC Schedule RC-B, "
               "and the ASC 825 fair-value-of-financial-instruments table (loans, "
               "deposits, debt), are next on the roadmap.")


def _render_credit_quality(ticker):
    """Multi-year (up to 5 FY) Company-Reported Credit Quality / Allowance, mirroring
    the Company-Reported Performance tab: section bands (Allowance & Loans, Asset-
    quality ratios, Coverage) with the table on the LEFT and % trend charts (ACL/
    loans, nonaccrual/loans, net charge-offs/loans) on the RIGHT. Every figure is
    derived from the bank's OWN multi-year statements + 10-K asset-quality table and
    allowance rollforward — never FDIC. ACL coverage of nonaccrual = acl ÷ (nonaccrual
    $), where nonaccrual $ = npl_loans × net_loans (both company-reported). A metric
    whose inputs aren't cleanly disclosed for a year renders blank (never a guess);
    a row n/a for every year is dropped."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    st.markdown("---")
    st.subheader("Credit Quality / Allowance — Company Reported")
    try:
        years, dicts, src = _cr_highlights_by_year(ticker)
    except Exception:
        years = dicts = src = None
    if not years or not dicts:
        st.caption("Company-reported allowance / asset-quality figures not available "
                   "from this filer's 10-Ks — n/a.")
        return
    periods = years[::-1]                               # oldest → newest
    order = list(range(len(years)))[::-1]               # column order, oldest-first

    # ACL coverage of nonaccrual (x): acl ÷ nonaccrual$, where nonaccrual$ =
    # npl_loans × net_loans. Computed per year; None when any input is missing.
    def _cov(d):
        acl, npl, nl = d.get("acl"), d.get("npl_loans"), d.get("net_loans")
        if acl is None or npl is None or nl in (None, 0):
            return None
        denom = npl * nl
        return acl / denom if denom else None

    for d in dicts:
        d["_acl_cov_na"] = _cov(d)

    def _fmt(v, kind):
        if v is None:
            return None
        if kind == "usd":
            return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)
        if kind == "x":
            return f"{v:.2f}x"
        return f"{v * 100:.2f}%"                        # pct2 (fraction → %)

    rows = []
    for sec_name, metrics in _CR_CREDIT_SECTIONS:
        sec_rows = []
        for label, key, kind in metrics:
            vals = [_fmt(dicts[i].get(key), kind) for i in order]
            # Drop a row that is n/a for every year; keep if any year has data.
            if any(v is not None for v in vals):
                sec_rows.append({"label": label, "values": vals, "kind": "data"})
        if sec_rows:
            rows.append({"label": sec_name, "values": [], "kind": "header"})
            rows.extend(sec_rows)

    if not rows:
        st.caption("Company-reported allowance / asset-quality figures not available "
                   "from this filer's 10-Ks — n/a.")
        return

    st.caption(
        f"Source: company 10-K filings ([latest]({src})); {len(periods)} fiscal "
        "years from the bank's own asset-quality table and allowance rollforward — "
        "allowance for credit losses, gross loans (loans net of unearned income), "
        "provision, and the ACL/loan, nonaccrual/loan and net charge-off/loan ratios. "
        "Ratios are against net loans, as the company reports them; coverage = ACL ÷ "
        "nonaccrual loans. Company-reported throughout — never FDIC. Blank where a "
        "filing doesn't disclose a clean figure.")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(periods, rows, entity=entity, src=src)
    with _rt:
        _cr_credit_trends(years, dicts, ticker, "crcq")


def _cr_hl_norm(s):
    """Normalize an as-reported statement label for matching (same convention as
    _cr_statement_trends: collapse whitespace, lowercase, fold curly apostrophes)."""
    import re
    return re.sub(r"\s+", " ", (s or "")).strip().lower().replace(
        "’", "'").replace("‘", "'")


def _cr_hl_series(stmt):
    """{normalized label -> [values newest-first]} for a multi-year as-reported
    statement's DATA rows (headers skipped). Raw dollars, as the statement carries
    them (units already de-scaled by the parser)."""
    return {_cr_hl_norm(r["label"]): r["values"]
            for r in stmt["rows"] if not r["header"]}


def _cr_highlights_by_year(ticker):
    """Per-fiscal-year Company-Reported highlights, NEWEST-FIRST, derived from the
    bank's own multi-year income + balance statements (data.sec_statements) and its
    holdco regulatory-capital table (data.sec_filing_scraper). Returns
    (years, dicts, src) — years = ["FY2025",…] newest-first; dicts a parallel list
    of {metric: value-or-None}; src = the latest 10-K link. Returns (None, …) when
    the statements aren't available.

    Levels are raw dollars; ratios are FRACTIONS (0.015 = 1.5%). ROAA/ROAE/ROATCE
    use AVERAGE balances ((beginning+ending)/2) where a prior year is in view — the
    same convention the Company-Reported Performance tab and financial_highlights_for
    use, so the latest year ties to that snapshot — falling back to the period-end
    balance for the OLDEST year (no prior in view). A metric whose inputs are
    missing for a year is None (blank), never a guess."""
    import re
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return None, None, None
    try:
        from data.sec_statements import as_reported_statement_multiyear
        inc = as_reported_statement_multiyear(cik, "income", 5)
        bal = as_reported_statement_multiyear(cik, "balance", 5)
    except Exception:
        inc = bal = None
    if not inc or not bal:
        return None, None, None

    inc_s, bal_s = inc["statement"], bal["statement"]
    # Drive the year set off the BALANCE periods (totals every metric anchors to),
    # newest-first. Join the income statement by fiscal-year integer.
    def _yr(p):
        m = re.search(r"\d{4}", p or "")
        return int(m.group()) if m else None

    bal_years = [_yr(p) for p in bal_s["periods"]]
    inc_years = [_yr(p) for p in inc_s["periods"]]
    bin_ = _cr_hl_series(bal_s)
    iinc = _cr_hl_series(inc_s)

    def _b(label_alts, year):
        """Balance value for a year by label (first matching alt)."""
        if year not in bal_years:
            return None
        ci = bal_years.index(year)
        for a in label_alts:
            vals = bin_.get(_cr_hl_norm(a))
            if vals and ci < len(vals) and vals[ci] is not None:
                return vals[ci]
        return None

    def _i(label_alts, year):
        if year not in inc_years:
            return None
        ci = inc_years.index(year)
        for a in label_alts:
            vals = iinc.get(_cr_hl_norm(a))
            if vals and ci < len(vals) and vals[ci] is not None:
                return vals[ci]
        return None

    # Holdco regulatory capital, keyed by fiscal-year int (period_end "YYYY-MM-DD").
    cap_by_year = {}
    try:
        from data.sec_filing_scraper import holdco_capital_for
        from data.bank_mapping import get_fdic_cert
        cert = None
        try:
            cert = get_fdic_cert(ticker)
        except Exception:
            cert = info.get("fdic_cert") if info else None
        cap_res = holdco_capital_for(cik, cert)
        if cap_res:
            for period, d in cap_res["capital"].items():
                y = _yr(period)
                if y is not None:
                    cap_by_year[y] = d
    except Exception:
        cap_by_year = {}

    # NIM / NPLs / net-charge-offs scraped from the bank's OWN 10-Ks (not in the
    # primary income/balance statements; data.sec_filing_scraper parses the MD&A
    # average-balance table + the allowance rollforward). Keyed by fiscal year,
    # values are fractions or None — company-reported, never FDIC.
    aq_by_year = {}
    try:
        from data.sec_filing_scraper import company_asset_quality_nim
        _aq = company_asset_quality_nim(cik)
        if _aq:
            aq_by_year = _aq.get("by_year", {}) or {}
    except Exception:
        aq_by_year = {}

    _EQUITY = ["total shareholders' equity", "total stockholders' equity"]
    dicts = []
    for k, year in enumerate(bal_years):
        prior = bal_years[k + 1] if k + 1 < len(bal_years) else None

        def _avg(label_alts):
            """(beginning+ending)/2 over this year and the prior IN-VIEW year;
            period-end alone for the oldest column (no prior)."""
            cur = _b(label_alts, year)
            if cur is None:
                return None
            if prior is not None:
                pv = _b(label_alts, prior)
                if pv is not None:
                    return (cur + pv) / 2.0
            return cur

        ta = _b(["total assets"], year)
        nl = _b(["loans, net", "total loans, net", "net loans"], year)
        # Gross loans = the pre-allowance carrying line ("Loans, net of unearned
        # income"). Distinct key from "Loans, net" (exact-match _b), so no collision.
        loans_gross = _b(["loans, net of unearned income",
                          "loans, net of unearned fees and costs",
                          "total loans"], year)
        dep = _b(["total deposits"], year)
        eq = _b(_EQUITY, year)
        afs = _b(["debt securities available-for-sale, at fair value, "
                  "net of allowance for credit losses"], year)
        htm = _b(["debt securities held-to-maturity, at amortized cost, "
                  "net of allowance for credit losses"], year)
        # AFS / HTM labels embed year-varying allowance amounts — match on a prefix.
        if afs is None or htm is None:
            for nlab, vals in bin_.items():
                if year in bal_years and bal_years.index(year) < len(vals):
                    vv = vals[bal_years.index(year)]
                    if afs is None and nlab.startswith("debt securities available-for-sale"):
                        afs = vv
                    elif htm is None and nlab.startswith("debt securities held-to-maturity"):
                        htm = vv
        securities = None
        if afs is not None or htm is not None:
            securities = (afs or 0.0) + (htm or 0.0)
        gw = _b(["goodwill"], year)
        intang = _b(["other intangible assets, net"], year)
        acl = _b(["allowance for credit losses"], year)
        acl = abs(acl) if acl is not None else None   # filed as a contra (negative)

        # "net income (loss)" covers filers (KEY) whose headline line is labelled
        # 'NET INCOME (LOSS)'; the longer 'attributable to …' lines never match
        # these exact keys, so the consolidated total is taken, not a subtotal.
        ni = _i(["net income", "net income (loss)", "net income (loss) available "
                 "to common shareholders"], year)
        nii = _i(["net interest income"], year)
        nonii = _i(["total noninterest income"], year)
        nonix = _i(["total noninterest expense"], year)
        prov = _i(["provision for credit losses", "provision for loan losses",
                   "(reversal of) provision for credit losses"], year)
        eps_dil = _i(["diluted earnings per common share (in dollars per share)",
                      "diluted earnings per share (in dollars per share)",
                      "diluted earnings per common share",
                      "diluted earnings per share",
                      "earnings per common share - diluted (in dollars per share)"],
                     year)
        # PPNR = NII + noninterest income − noninterest expense (all three present).
        ppnr = (nii + nonii - nonix
                if (nii is not None and nonii is not None and nonix is not None)
                else None)

        # Tangible common equity (period-end and average).
        def _tce(at_year):
            e = _b(_EQUITY, at_year)
            if e is None:
                return None
            g = _b(["goodwill"], at_year) or 0.0
            it = _b(["other intangible assets, net"], at_year) or 0.0
            return e - g - it

        tce_cur = _tce(year)
        tce_avg = tce_cur
        if tce_cur is not None and prior is not None:
            tce_pv = _tce(prior)
            if tce_pv is not None:
                tce_avg = (tce_cur + tce_pv) / 2.0

        avg_assets = _avg(["total assets"])
        avg_equity = _avg(_EQUITY)

        def _ratio(num, den):
            return (num / den) if (num is not None and den not in (None, 0)) else None

        cap = cap_by_year.get(year, {})
        d = {
            "total_assets": ta, "net_loans": nl, "total_deposits": dep,
            "total_equity": eq, "securities": securities,
            "nii": nii, "noninterest_income": nonii, "noninterest_expense": nonix,
            "provision": prov, "ppnr": ppnr, "eps_diluted": eps_dil,
            "net_income": ni,
            "roaa": _ratio(ni, avg_assets),
            "roae": _ratio(ni, avg_equity),
            "roatce": _ratio(ni, tce_avg if (tce_avg and tce_avg > 0) else None),
            "nim": aq_by_year.get(year, {}).get("nim"),     # scraped from the 10-K MD&A
            "efficiency": _ratio(nonix, (nii + nonii)
                                 if (nii is not None and nonii is not None) else None),
            "loans_deposits": _ratio(nl, dep),
            "securities_assets": _ratio(securities, ta),
            "equity_assets": _ratio(eq, ta),
            "tce_ta": _ratio(tce_cur,
                             (ta - (gw or 0.0) - (intang or 0.0)) if ta is not None else None),
            "npl_loans": aq_by_year.get(year, {}).get("npl_loans"),   # scraped 10-K asset quality
            "nco_loans": aq_by_year.get(year, {}).get("nco_loans"),   # scraped allowance rollforward
            "reserves_loans": _ratio(acl, nl),
            "acl": acl,                          # abs allowance dollars (contra de-signed)
            "loans_gross": loans_gross,          # "Loans, net of unearned income" (pre-allowance)
            "cet1": cap.get("cet1_ratio"),
            "total_capital": cap.get("total_ratio"),
            "leverage": cap.get("lev_ratio"),
        }
        dicts.append(d)

    years = [f"FY{y}" if y else "" for y in bal_years]
    latest = inc["meta"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(latest['cik'])}/"
           f"{latest['accession']}/{latest['doc']}")
    return years, dicts, src


# Financial-Highlights sections: (section name, [(row label, metric key, fmt)]).
# fmt: "usd" = $-compact dollars; "pct2" = x.xx%. A None value renders blank.
_CR_HL_SECTIONS = [
    ("Balance Sheet", [
        ("Total assets", "total_assets", "usd"),
        ("Net loans", "net_loans", "usd"),
        ("Total deposits", "total_deposits", "usd"),
        ("Total equity", "total_equity", "usd"),
        ("Securities", "securities", "usd"),
    ]),
    ("Profitability", [
        ("Net income", "net_income", "usd"),
        ("ROAA", "roaa", "pct2"),
        ("ROAE", "roae", "pct2"),
        ("ROATCE", "roatce", "pct2"),
        ("Net interest margin", "nim", "pct2"),
        ("Efficiency ratio", "efficiency", "pct2"),
    ]),
    ("Balance Sheet Ratios", [
        ("Loans/deposits", "loans_deposits", "pct2"),
        ("Securities/assets", "securities_assets", "pct2"),
        ("Equity/assets", "equity_assets", "pct2"),
        ("Tang. common equity/tang. assets", "tce_ta", "pct2"),
    ]),
    ("Asset Quality", [
        ("NPLs/loans", "npl_loans", "pct2"),
        ("Net charge-offs/loans", "nco_loans", "pct2"),
        ("Loan-loss reserves/loans", "reserves_loans", "pct2"),
    ]),
    ("Capital Adequacy", [
        ("CET1", "cet1", "pct2"),
        ("Total capital", "total_capital", "pct2"),
        ("Leverage", "leverage", "pct2"),
    ]),
]

# Highlights trend charts: (title, metric key). One % series per chart; CET1 is
# dropped at render time when n/a for every year (capital not disclosed).
_CR_HL_TRENDS = [
    ("ROAA (%)", "roaa"),
    ("ROAE (%)", "roae"),
    ("Efficiency ratio (%)", "efficiency"),
    ("ROATCE (%)", "roatce"),
    ("CET1 (%)", "cet1"),
]


# Performance-Analysis sections: (section name, [(row label, metric key, fmt)]).
# Drives the multi-year Company-Reported Performance table. All keys come straight
# from _cr_highlights_by_year (income-statement levels + ratios on average balances).
_CR_PERF_SECTIONS = [
    ("Earnings", [
        ("Net interest income", "nii", "usd"),
        ("Noninterest income", "noninterest_income", "usd"),
        ("Noninterest expense", "noninterest_expense", "usd"),
        ("Pre-provision net revenue (PPNR)", "ppnr", "usd"),
        ("Provision for credit losses", "provision", "usd"),
        ("Net income", "net_income", "usd"),
    ]),
    ("Profitability", [
        ("ROAA", "roaa", "pct2"),
        ("ROAE", "roae", "pct2"),
        ("ROATCE", "roatce", "pct2"),
        ("Net interest margin", "nim", "pct2"),
        ("Efficiency ratio", "efficiency", "pct2"),
    ]),
    ("Per share", [
        ("Diluted EPS", "eps_diluted", "eps"),
    ]),
]

# Performance trend charts (right side): one % series each, ≥3 points to render,
# cap 4. NIM leads, then ROAA/ROAE/Efficiency.
_CR_PERF_TRENDS = [
    ("Net interest margin (%)", "nim"),
    ("ROAA (%)", "roaa"),
    ("ROAE (%)", "roae"),
    ("Efficiency ratio (%)", "efficiency"),
]


def _cr_perf_trends(years, dicts, ticker, key_prefix):
    """Right-hand trend charts for multi-year Company-Reported Performance: one
    single-line % chart per metric (NIM, ROAA, ROAE, efficiency). Same plotting
    style as _cr_highlights_trends; a metric n/a for every year is skipped; ≥3
    points to render, capped at 4 charts."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = years[::-1]                                    # oldest → newest
    charts = []
    for title, key in _CR_PERF_TRENDS:
        ys = [(d.get(key) * 100 if d.get(key) is not None else None)
              for d in dicts][::-1]
        if sum(1 for y in ys if y is not None) >= 3:
            charts.append((title, ys))
        if len(charts) >= 4:
            break
    if not charts:
        return
    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title="%", show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_perftr_{ticker}_{r + j}")


# Credit-Quality sections: (section name, [(row label, metric key, fmt)]).
# Drives the multi-year Company-Reported Credit Quality table. Keys come from
# _cr_highlights_by_year (acl/loans_gross/provision dollars; reserves_loans/npl_loans/
# nco_loans fractions; _acl_cov_na = ACL coverage of nonaccrual, computed in render).
_CR_CREDIT_SECTIONS = [
    ("Allowance & Loans", [
        ("Allowance for credit losses", "acl", "usd"),
        ("Gross loans (net of unearned income)", "loans_gross", "usd"),
        ("Provision for credit losses", "provision", "usd"),
    ]),
    ("Asset-quality ratios", [
        ("ACL / loans", "reserves_loans", "pct2"),
        ("Nonaccrual / loans", "npl_loans", "pct2"),
        ("Net charge-offs / loans", "nco_loans", "pct2"),
    ]),
    ("Coverage", [
        ("ACL coverage of nonaccrual", "_acl_cov_na", "x"),
    ]),
]

# Credit-Quality trend charts (right side): one % series each, ≥3 points to render,
# cap 4. ACL/loans, nonaccrual/loans, net charge-offs/loans.
_CR_CREDIT_TRENDS = [
    ("ACL / loans (%)", "reserves_loans"),
    ("Nonaccrual / loans (%)", "npl_loans"),
    ("Net charge-offs / loans (%)", "nco_loans"),
]


def _cr_credit_trends(years, dicts, ticker, key_prefix):
    """Right-hand trend charts for multi-year Company-Reported Credit Quality: one
    single-line % chart per asset-quality ratio (ACL/loans, nonaccrual/loans, net
    charge-offs/loans). Same plotting style as _cr_perf_trends; a metric n/a for
    every year is skipped; ≥3 points to render, capped at 4 charts."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = years[::-1]                                    # oldest → newest
    charts = []
    for title, key in _CR_CREDIT_TRENDS:
        ys = [(d.get(key) * 100 if d.get(key) is not None else None)
              for d in dicts][::-1]
        if sum(1 for y in ys if y is not None) >= 3:
            charts.append((title, ys))
        if len(charts) >= 4:
            break
    if not charts:
        return
    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title="%", show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_cqtr_{ticker}_{r + j}")


def _cr_capital_trends(ticker, key_prefix):
    """Company-Reported-only trend charts for Regulatory Capital — holding company.

    Re-reads the SAME scraped holdco capital dict that _render_holdco_capital's
    table uses (data.sec_filing_scraper.holdco_capital_for → {period: {cet1_ratio,
    t1_ratio, total_ratio, lev_ratio, rwa, ...}}; ratios are fractions, rwa is raw
    dollars). Renders a 2×2 of CET1 (%), Tier 1 — or Total when Tier 1 is sparse —
    (%), Leverage (%) and RWA ($B), oldest→newest. Company-scraped only, NEVER
    FDIC. ≥3 disclosed points to draw a line, else the chart is skipped; no
    periods / a single period / no chart with ≥3 points → renders nothing, no
    crash. Same plotting style as _cr_perf_trends. Called ONLY from the
    Company-Reported wrapper (_cr_reg_capital) — never the Templated page, which
    shares _render_holdco_capital's table but not these charts."""
    from data.bank_mapping import get_cik, get_fdic_cert
    cik = get_cik(ticker)
    if not cik:
        return
    try:
        from data.sec_filing_scraper import holdco_capital_for
        res = holdco_capital_for(cik, get_fdic_cert(ticker))
    except Exception:
        res = None
    if not res or not res.get("capital"):
        return
    cap = res["capital"]
    periods = sorted(cap)[:6][-5:]                      # oldest → newest, last 5
    if len(periods) < 3:
        return

    def _plab(p):
        y, m = p[:4], p[5:7]
        return f"FY{y}" if m == "12" else f"Q{(int(m) - 1) // 3 + 1} '{y[2:]}"

    def _ser(key):
        return [(cap[p].get(key) * 100 if cap[p].get(key) is not None else None)
                for p in periods]

    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = [_plab(p) for p in periods]

    # 2nd chart: prefer Tier 1; fall back to Total capital when Tier 1 is too sparse.
    t1 = _ser("t1_ratio")
    tot = _ser("total_ratio")
    second = (("Tier 1 capital ratio (%)", t1, "%")
              if sum(1 for y in t1 if y is not None) >= 3
              else ("Total capital ratio (%)", tot, "%"))
    rwa = [(cap[p].get("rwa") / 1e9 if cap[p].get("rwa") is not None else None)
           for p in periods]
    candidates = [
        ("CET1 ratio (%)", _ser("cet1_ratio"), "%"),
        second,
        ("Tier 1 leverage ratio (%)", _ser("lev_ratio"), "%"),
        ("Risk-weighted assets ($B)", rwa, "$B"),
    ]
    charts = [(t, ys, u) for t, ys, u in candidates
              if sum(1 for y in ys if y is not None) >= 3]
    if not charts:
        return

    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys, unit)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title=unit, show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_captr_{ticker}_{r + j}")


def _cr_highlights_trends(years, dicts, ticker, key_prefix):
    """Right-hand trend charts for the multi-year Financial Highlights: one
    single-line % chart per metric (ROAA, ROAE, efficiency, CET1). xs are the FY
    labels oldest→newest; a chart whose metric is n/a for every year is skipped.
    Reuses the _cr_statement_trends plotting style (apply_standard_layout +
    tighten_yaxis, CHART_HEIGHT_COMPACT)."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    xs = years[::-1]                                    # oldest → newest
    charts = []
    for title, key in _CR_HL_TRENDS:
        ys = [(d.get(key) * 100 if d.get(key) is not None else None)
              for d in dicts][::-1]
        # need >=3 points to read as a trend — skips e.g. a 2-year capital series
        # (its 2-dot line looked broken). Cap at 4 charts (2x2 grid).
        if sum(1 for y in ys if y is not None) >= 3:
            charts.append((title, ys))
        if len(charts) >= 4:
            break
    if not charts:
        return
    st.markdown("##### Trends")
    for r in range(0, len(charts), 2):
        cols = st.columns(2)
        for j, (col, (title, ys)) in enumerate(zip(cols, charts[r:r + 2])):
            with col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", connectgaps=True,
                    line=dict(color=CATEGORICAL_PALETTE[0], width=2),
                    marker=dict(size=5)))
                apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                                      yaxis_title="%", show_legend=False)
                tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"{key_prefix}_hltr_{ticker}_{r + j}")


# Latest-quarter preliminary figures shown in the banner, in display order.
# (label, key, kind) — kind: "usd" raw dollars; "eps" $/share; "pct" as-printed %.
_PRELIM_FIGS = [
    ("Total assets", "total_assets", "usd"),
    ("Total deposits", "total_deposits", "usd"),
    ("Net income", "net_income", "usd"),
    ("Net interest income", "net_interest_income", "usd"),
    ("Diluted EPS", "diluted_eps", "eps"),
    ("Net interest margin", "nim", "pct"),
    ("Return on avg assets", "roaa", "pct"),
    ("Return on avg equity", "roae", "pct"),
]


def _render_preliminary_quarter(ticker, cik):
    """Latest-quarter headline figures from the bank's most-recent earnings 8-K
    (EX-99.1), rendered as a clearly-labeled AS-RELEASED / PRELIMINARY banner —
    visually and textually distinct from the audited multi-year columns below, and
    NEVER merged into them.

    These come from the free-form press release (NOT XBRL) ~4 weeks before the
    10-Q, so each figure is gated (data.sec_earnings_8k): exact label match, dollar
    scale anchored to the prior 10-Q, segment/non-GAAP/out-of-band values rejected
    to n/a. A figure the gate rejected is simply absent here. Renders nothing when
    there's no earnings 8-K / no EX-99.1 / nothing survived the gate."""
    if not cik:
        return
    try:
        from data.sec_earnings_8k import latest_earnings_8k_figures
        res = latest_earnings_8k_figures(cik)
    except Exception:
        res = None
    if not res or not res.get("figures"):
        return
    figs = res["figures"]
    shown = [(lbl, figs.get(key), kind) for lbl, key, kind in _PRELIM_FIGS
             if figs.get(key) is not None]
    if not shown:
        return

    def _fmt(v, kind):
        if kind == "usd":
            # This banner goes through st.markdown, where an unescaped "$" pairs
            # with the next one and Streamlit renders the span between as LaTeX
            # (mangling "$28.11B · Total deposits $22.64B"). Escape it like the EPS
            # branch already does. The HTML-iframe table (_cr_component) is exempt.
            return _cr_usd(v).replace("$", "\\$")
        if kind == "eps":
            return f"\\${v:,.2f}"
        return f"{v:.2f}%"                              # as-printed percent

    acc = res.get("accession", "")
    acc_nodash = acc.replace("-", "")
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/"
           f"{res.get('doc', '')}") if acc and res.get("doc") else ""
    filed = res.get("filed", "")

    st.markdown(
        "**Latest quarter — preliminary (from earnings release, not yet in the "
        "10-Q)**")
    cells = " · ".join(f"**{lbl}** {_fmt(v, kind)}" for lbl, v, kind in shown)
    st.markdown(f":orange[{cells}]")
    link = f"[8-K EX-99.1]({src})" if src else "8-K EX-99.1"
    st.caption(
        f"As-released figures from the company's earnings {link} filed {filed} — "
        "preliminary / as-reported, NOT audited and NOT yet in a 10-K/10-Q. Shown "
        "only where the press-release figure cleanly matched and passed a sanity "
        "check against the prior 10-Q; other headline figures are omitted rather "
        "than guessed. Superseded by the audited 10-Q when filed. Company-reported, "
        "never FDIC.")
    st.markdown("---")


def _render_financial_highlights(ticker):
    """Multi-year (up to 5 FY) Company-Reported Financial Highlights, mirroring the
    Templated Financial Highlights: section bands (Balance Sheet, Profitability,
    Balance Sheet Ratios, Asset Quality, Capital Adequacy) with the table on the
    LEFT and % trend charts on the RIGHT. Every number is derived from the bank's
    OWN multi-year statements + holdco capital table; a metric whose inputs aren't
    cleanly derivable for a year renders blank (never a guess)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        years, dicts, src = _cr_highlights_by_year(ticker)
    except Exception:
        years = dicts = src = None
    st.markdown("---")
    st.subheader("Financial Highlights — Company Reported")
    # Timeliness layer: the latest quarter's as-released figures from the earnings
    # 8-K (EX-99.1), labeled preliminary and visually separate — never merged into
    # the audited FY columns below, never overwriting an audited figure.
    _render_preliminary_quarter(ticker, cik)
    if not years or not dicts:
        st.caption("Company-reported statements not available from this filer's "
                   "10-Ks — n/a.")
        return
    periods = years[::-1]                               # oldest → newest (Templated)
    order = list(range(len(years)))[::-1]               # column order, oldest-first

    def _fmt(v, kind):
        if v is None:
            return None
        if kind == "usd":
            return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)
        return f"{v * 100:.2f}%"                        # pct2 (fraction → %)

    rows = []
    for sec_name, metrics in _CR_HL_SECTIONS:
        rows.append({"label": sec_name, "values": [], "kind": "header"})
        for label, key, kind in metrics:
            vals = [_fmt(dicts[i].get(key), kind) for i in order]
            rows.append({"label": label, "values": vals, "kind": "data"})

    st.caption(
        f"Source: company 10-K filings ([latest]({src})); {len(periods)} fiscal "
        "years stitched from the bank's own income, balance sheet, asset-quality "
        "and regulatory-capital disclosures. Dollar lines \\$-compact; ratios on "
        "average balances where a prior year is in view. NIM, NPL and charge-off "
        "ratios are scraped from the 10-K (MD&A average-balance table + allowance "
        "rollforward); blank where a filing doesn't disclose a clean figure.")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(periods, rows, entity=entity, src=src)
    with _rt:
        _cr_highlights_trends(years, dicts, ticker, "crhl")


def _render_performance(ticker):
    """Multi-year (up to 5 FY) Company-Reported Performance Analysis, mirroring the
    Company-Reported Financial Highlights: section bands (Earnings, Profitability,
    Per share) with the table on the LEFT and % trend charts (NIM, ROAA, ROAE,
    efficiency) on the RIGHT. Every figure is derived from the bank's OWN multi-year
    income statement (levels, EPS) plus ratios on average balances and NIM scraped
    from the 10-K MD&A — never FDIC. A metric whose inputs aren't cleanly disclosed
    for a year renders blank (never a guess); a row n/a for every year is dropped."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
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

    try:
        years, dicts, src = _cr_highlights_by_year(ticker)
    except Exception:
        years = dicts = src = None
    if not years or not dicts:
        st.caption("Company-reported income-statement lines not available from this "
                   "filer's 10-Ks — n/a.")
        return
    periods = years[::-1]                               # oldest → newest
    order = list(range(len(years)))[::-1]               # column order, oldest-first

    def _fmt(v, kind):
        if v is None:
            return None
        if kind == "usd":
            return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)
        if kind == "eps":
            return f"$({abs(v):,.2f})" if v < 0 else f"${v:,.2f}"
        return f"{v * 100:.2f}%"                        # pct2 (fraction → %)

    rows = []
    for sec_name, metrics in _CR_PERF_SECTIONS:
        sec_rows = []
        for label, key, kind in metrics:
            vals = [_fmt(dicts[i].get(key), kind) for i in order]
            # Drop a row that is n/a for every year; keep if any year has data.
            if any(v is not None for v in vals):
                sec_rows.append({"label": label, "values": vals, "kind": "data"})
        if sec_rows:
            rows.append({"label": sec_name, "values": [], "kind": "header"})
            rows.extend(sec_rows)

    st.caption(
        f"Source: company 10-K filings ([latest]({src})); {len(periods)} fiscal "
        "years from the bank's own income statement (NII, noninterest income/expense, "
        "provision, net income, diluted EPS) with ROAA/ROAE/ROATCE and efficiency on "
        "average balances; PPNR = NII + noninterest income − noninterest expense. NIM "
        "is the company's net interest margin scraped from the 10-K MD&A average-"
        "balance table — never FDIC. Blank where a filing doesn't disclose a clean "
        "figure.")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_component(periods, rows, entity=entity, src=src)
    with _rt:
        _cr_perf_trends(years, dicts, ticker, "crperf")


def _render_segments(ticker):
    """As-reported business-segment net income (with revenue/assets), scraped from
    the HOLDING COMPANY's recent 10-Ks and stitched across up to 5 fiscal years.
    Each segment's figures are directly tagged; a 'Corporate / other & reconciling
    items' residual (consolidated − Σ reportable) ties net income to the
    consolidated total, exactly as the segment footnote presents it. Rendered as
    section bands per metric (Net income / Revenue / Total assets), one row per
    segment with FY columns oldest→newest. Segments are unioned across years by
    their as-reported label (filers rename/restructure → blanks, never a carry-
    forward); a period whose breakdown doesn't reconcile is gated out at the data
    layer. n/a when the filer tags fewer than two reportable segments (single-
    segment banks report no segment breakdown)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    try:
        from data.sec_filing_scraper import segments_multiyear_for
        res = segments_multiyear_for(cik, n_years=5)
    except Exception:
        res = None
    st.markdown("---")
    st.subheader("Segment Reporting — Company Reported")
    if not res or not res.get("segments"):
        st.caption("Reportable business segments not tagged in this filer's recent "
                   "10-Ks — n/a. (Single-segment banks report no segment breakdown.)")
        return
    m, seg_data, filings = res["meta"], res["segments"], res["filings"]
    periods = sorted(seg_data)                       # oldest → newest
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{m['accession']}/{m['doc']}")
    cols = [f"FY{p[:4]}" for p in periods]
    # A filer either tags per-segment NET INCOME (ni_measure set) or, for the ASC
    # 280 disaggregated-expense filers that don't, a DISCLOSED measure (pretax
    # income / revenue / NII) surfaced AS-IS — never relabelled net income.
    ni_periods = [p for p in periods if seg_data[p].get("ni_measure")]
    disclosed_periods = [p for p in periods if not seg_data[p].get("ni_measure")]
    if ni_periods:
        measures = sorted({seg_data[p]["ni_measure"] for p in ni_periods})
        primary_title = "Net income ($)"
        primary_key = "net_income"
        primary_consol_label = "Consolidated net income"
        measure_note = (f"Segment net income is the {' / '.join(measures)} measure; "
                        f"the residual reconciles reportable segments to the "
                        f"consolidated total.")
    else:
        disc_labels = sorted({seg_data[p].get("disclosed_label") or "disclosed measure"
                              for p in disclosed_periods})
        primary_title = disc_labels[0] if len(disc_labels) == 1 else "Disclosed measure ($)"
        primary_key = "disclosed"
        primary_consol_label = "Consolidated"
        measure_note = (f"This filer does not tag per-segment net income; the table "
                        f"shows the disclosed per-segment {' / '.join(l.split(' (')[0].lower() for l in disc_labels)} "
                        f"(not net income), with a residual reconciling reportable "
                        f"segments to the consolidated total.")
    st.caption(
        f"Source: company 10-K filings — latest [10-K filed {m['date']}]({src}); "
        f"{len(periods)} fiscal year(s) stitched from {len(filings)} filing(s). "
        f"{measure_note} Blank = segment not separately reported that year.")

    def _b(v):
        return "n/a" if v is None else _cr_usd(v)

    # Union of segment labels in as-reported order, newest year first (so the
    # current structure leads); a renamed/dropped segment simply blanks in the
    # years it is absent — never carried forward.
    seg_order: list = []
    for p in periods[::-1]:
        for s in seg_data[p]["segments"]:
            if s["label"] not in seg_order:
                seg_order.append(s["label"])

    def _band(title, key, *, residual=False, consolidated=False):
        """One metric section: a header row, one row per segment across the FY
        columns, and (net income only) the reconciling residual + consolidated
        rows. A segment row that is n/a in every year is dropped."""
        rows = [{"label": title, "values": [], "kind": "header"}]
        body = []
        for lbl in seg_order:
            vals = []
            for p in periods:
                seg = next((s for s in seg_data[p]["segments"] if s["label"] == lbl), None)
                vals.append(_b(seg[key]) if seg is not None and seg.get(key) is not None else "")
            if any(v not in ("", "n/a") for v in vals):
                body.append({"label": lbl, "values": vals, "kind": "data"})
        if not body and not (residual or consolidated):
            return []                                # metric untagged across all years
        rows += body
        # Residual / consolidated come from whichever measure this period carries
        # (NI for NI-tagged filers, the disclosed measure otherwise) so the rows
        # always tie the SAME column they reconcile.
        def _resid(p):
            d = seg_data[p]
            return d["reconciling_residual"] if d.get("ni_measure") else d.get("disclosed_residual")

        def _consol(p):
            d = seg_data[p]
            return d["consolidated_net_income"] if d.get("ni_measure") else d.get("disclosed_consolidated")
        if residual:
            rows.append({"label": "Corporate / other & reconciling items",
                         "values": [_b(_resid(p)) for p in periods],
                         "kind": "data"})
        if consolidated:
            rows.append({"label": primary_consol_label,
                         "values": [_b(_consol(p)) for p in periods],
                         "kind": "data"})
        return rows

    grid_rows = []
    grid_rows += _band(primary_title, primary_key, residual=True, consolidated=True)
    grid_rows += _band("Revenue ($)", "revenue")
    grid_rows += _band("Total assets ($)", "assets")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    _cr_component(cols, grid_rows, entity=entity, src=src)


def _render_rate_risk(ticker):
    """Multi-year (up to 5 FY) embedded interest-rate risk: the AFS + HTM unrealized
    gain/(loss) already on the securities book, against equity and CET1 capital (the
    post-2023 'underwater securities erode capital' story). Composed by year from the
    same shipped, reconcile-gated multi-year functions the Securities and Highlights
    tabs use — the per-FY AFS/HTM net unrealized marks from securities_multiyear_for,
    total equity from _cr_highlights_by_year. CET1 capital ($) is only tagged in the
    bank's freshest filing (holdco_capital_for), so the loss/CET1 row populates only
    the year(s) that filing carries cleanly; every other year blanks (never a guess).
    Forward NII/EVE rate-shock sensitivity lives in the bank's Item 7A and isn't
    standardized XBRL, so it is linked, not scraped. Company-reported, never FDIC
    (the only FDIC touch is the unchanged CET1 anchor inside holdco_capital_for, for
    parity with the prior single-period tab)."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return
    st.markdown("---")
    st.subheader("Interest Rate Risk — embedded securities marks (Company Reported)")

    # Per-FY AFS/HTM net unrealized marks (same source the Securities tab shows).
    try:
        from data.sec_filing_scraper import securities_multiyear_for
        sec_res = securities_multiyear_for(cik, n_years=5)
    except Exception:
        sec_res = None
    # Per-FY total equity (and the latest-10-K link) from the shipped highlights.
    try:
        years, hdicts, hl_src = _cr_highlights_by_year(ticker)
    except Exception:
        years = hdicts = hl_src = None
    if not sec_res or not sec_res.get("securities"):
        st.caption("AFS/HTM unrealized marks not tagged in this filer's 10-Ks — n/a. "
                   "Forward rate-shock (NII/EVE) sensitivity is disclosed in Item 7A "
                   "of the 10-K.")
        return

    import re

    def _yr(s):
        m = re.search(r"\d{4}", s or "")
        return int(m.group()) if m else None

    sec = sec_res["securities"]                          # {fy_period: {...}}
    sec_by_year = {_yr(p): v for p, v in sec.items() if _yr(p) is not None}
    equity_by_year = {}
    if years and hdicts:
        for y, d in zip(years, hdicts):
            yi = _yr(y)
            if yi is not None:
                equity_by_year[yi] = d.get("total_equity")

    # CET1 capital ($) per FY — only the bank's freshest filing tags the capital
    # table, so this typically covers the latest 1-2 FY-ends. Same FDIC CET1 anchor
    # the prior single-period tab used (parity); no NEW FDIC for the marks.
    cet1_by_year = {}
    try:
        from data.sec_filing_scraper import holdco_capital_for
        from data.bank_mapping import get_fdic_cert
        try:
            cert = get_fdic_cert(ticker)
        except Exception:
            cert = info.get("fdic_cert") if info else None
        cap_res = holdco_capital_for(cik, cert)
        if cap_res:
            for period, d in cap_res["capital"].items():
                yi = _yr(period)
                if yi is not None:
                    cet1_by_year[yi] = d.get("cet1_cap")
    except Exception:
        cet1_by_year = {}

    # Year set drives off the securities portfolio years, oldest → newest.
    yrs = sorted(sec_by_year)
    cols = [f"FY{y}" for y in yrs]

    def _marks(y):
        """(afs_net, htm_net, total) for a year, or (None, None, None) when neither
        portfolio is tagged. Total is None only when BOTH marks are absent."""
        d = sec_by_year.get(y) or {}
        afs = (d.get("afs") or {}).get("net_unrealized")
        htm = (d.get("htm") or {}).get("net_unrealized")
        if afs is None and htm is None:
            return None, None, None
        return afs, htm, (afs or 0.0) + (htm or 0.0)

    def _usd_cell(v):
        if v is None:
            return None
        return f"({_cr_usd(abs(v))})" if v < 0 else _cr_usd(v)

    def _pct_cell(v):
        return None if v is None else f"{v * 100:.2f}%"

    afs_row, htm_row, tot_row, eq_row, loss_eq_row, loss_cet1_row = ([] for _ in range(6))
    eq_pct_series = []                                   # for the trend chart
    for y in yrs:
        afs, htm, tot = _marks(y)
        eq = equity_by_year.get(y)
        cet1 = cet1_by_year.get(y)
        # Per-period gate: loss/equity needs the total mark AND a clean equity;
        # loss/CET1 needs the total mark AND a clean CET1 capital. Each blanks
        # independently when its inputs aren't cleanly disclosed for the year.
        loss_eq = (tot / eq) if (tot is not None and eq not in (None, 0)) else None
        loss_cet1 = (tot / cet1) if (tot is not None and cet1 not in (None, 0)) else None
        afs_row.append(_usd_cell(afs))
        htm_row.append(_usd_cell(htm))
        tot_row.append(_usd_cell(tot))
        eq_row.append(_usd_cell(eq))
        loss_eq_row.append(_pct_cell(loss_eq))
        loss_cet1_row.append(_pct_cell(loss_cet1))
        eq_pct_series.append(loss_eq * 100 if loss_eq is not None else None)

    candidate_rows = [
        ("AFS unrealized gain / (loss)", afs_row),
        ("HTM unrealized gain / (loss)", htm_row),
        ("Total unrealized gain / (loss)", tot_row),
        ("Total equity", eq_row),
        ("Unrealized as % of equity", loss_eq_row),
        ("Unrealized as % of CET1 capital", loss_cet1_row),
    ]
    rows = [{"label": lab, "values": vals, "kind": "data"}
            for lab, vals in candidate_rows
            if any(v is not None for v in vals)]    # drop all-n/a rows
    if not rows:
        st.caption("AFS/HTM unrealized marks not tagged in this filer's 10-Ks — n/a. "
                   "Forward rate-shock (NII/EVE) sensitivity is disclosed in Item 7A "
                   "of the 10-K.")
        return

    meta = sec_res["meta"]
    src = hl_src or (f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
                     f"{meta['accession']}/{meta['doc']}")
    st.caption(
        f"Source: company 10-K filings — latest [{meta['date']}]({src}); "
        f"{len(yrs)} fiscal year-end(s) stitched from {len(sec_res['filings'])} "
        "filing(s). Unrealized gain/(loss) on AFS + HTM debt securities vs equity "
        "and CET1 capital — the rate risk ALREADY on the books. CET1 capital is "
        "tagged only in the bank's freshest filing, so that % populates only the "
        "year(s) it cleanly discloses. Forward NII/EVE rate-shock sensitivity is "
        "narrative in Item 7A (not standardized XBRL). Company-reported, never FDIC.")
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    # Chart the loss/equity % only when a clean multi-year series exists (≥3 points).
    if sum(1 for v in eq_pct_series if v is not None) >= 3:
        _lt, _rt = st.columns([1, 1], vertical_alignment="top")
        with _lt:
            _cr_component(cols, rows, entity=entity, src=src)
        with _rt:
            _rate_risk_trend(cols, eq_pct_series, ticker)
    else:
        _cr_component(cols, rows, entity=entity, src=src)


def _rate_risk_trend(xs, ys, ticker):
    """Right-hand trend chart for multi-year Rate Risk: embedded unrealized
    gain/(loss) as % of equity, oldest → newest. Same compact style as the other
    Company-Reported trend charts; gaps are blanked, not interpolated."""
    import plotly.graph_objects as go
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    st.markdown("##### Trends")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines+markers", connectgaps=True,
        line=dict(color=CATEGORICAL_PALETTE[0], width=2), marker=dict(size=5)))
    apply_standard_layout(fig, title="Unrealized gain/(loss), % of equity",
                          height=CHART_HEIGHT_COMPACT, yaxis_title="%",
                          show_legend=False)
    tighten_yaxis(fig, values=[y for y in ys if y is not None] or None)
    st.plotly_chart(fig, use_container_width=True, key=f"crrr_trend_{ticker}")


def render_portfolio(ticker):
    render_statement(ticker, "port", "Portfolio Analysis", _PORTFOLIO,
                     side_by_side=True)


def render_capital_structure(ticker):
    render_statement(ticker, "capstruct", "Capital Structure Details",
                     _CAPITAL_STRUCTURE, side_by_side=True)
