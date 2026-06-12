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


_DEFAULT_TRENDS = [("roaa", "ROAA"), ("nim", "Net Interest Margin"),
                   ("efficiency_ratio", "Efficiency Ratio"), ("cet1_ratio", "CET1 Ratio")]


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
                     with_ri: bool = False, with_dep_cost: bool = False):
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
    if with_ri:
        ri_by_ci, rie_by_ci = _ri_details_by_column(cert, recs_list)
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
        # ── Per-share (SEC holding-company filings) ──────────────────────
        ps = ps_by_ci.get(ci, {})
        if kind == "ps":
            key = args[0]; v = _psd(ps.get(key))
            return v, calc(label, v, asof, "SEC filing (holding company)",
                           [{"label": label, "val": v}], None, True,
                           source="SEC filing", link=sec_filing_link)
        if kind == "shares":
            sh = _num(ps.get("shares"))
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

    head = ('<th class="lblh">($ in thousands unless noted)</th>'
            + "".join(f'<th class="colh">{lb}</th>' for lb in labels))
    height = 96 + 23 * (ri + len(spec) + 1)
    sec_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"
                if cik else fdic_link)
    html = _build_component(head, "".join(rows_html), cells, entity, fdic_link, sec_link)

    # Wide table left, slimmer trend column right (DESIGN-SYSTEM.md: the
    # statement IS the page; charts support it).
    tr = _DEFAULT_TRENDS if trends is None else trends
    _tbl_col, _chart_col = st.columns([3, 2])
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
    # 2×2 grid of compact charts, filling the column densely.
    for r in range(0, len(trends), 2):
        cols = st.columns(2)
        for col, (key, label) in zip(cols, trends[r:r + 2]):
            with col:
                try:
                    st.plotly_chart(metrics_trend_chart(h, [key], label),
                                    use_container_width=True,
                                    key=f"{key_prefix}_tr_{ticker}_{key}")
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
        # INTAN = total intangibles incl. goodwill (INTANGW is goodwill only —
        # it was previously shown here under the "Intangible assets" label).
        ("Intangible assets (incl. goodwill)", "dollar", "INTAN"),
        ("Goodwill", "dollar", "INTANGW"),
    ]),
]

_PERFORMANCE = [
    ("Profitability Ratios (%)", [
        ("Return on avg assets (ROAA)", "pct", "ROA"),
        ("Return on avg equity (ROAE)", "pct", "ROE"),
        ("Return on avg tangible common equity (ROATCE)", "roatce"),
        ("Profit margin", "marginrev", "NETINC"),
    ]),
    ("Margin & Spread (%)", [
        ("Net interest margin", "pct", "NIMY"),
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
    ]),
    ("Yield / Cost Detail (%)", [
        ("Yield: total loans", "yield", "ILNDOM", "LNLSGR"),
        ("Yield: investment securities", "yield", "ISC", "SC"),
        ("Yield: interest-earning assets", "pct", "INTINCY"),
        ("Cost: interest-bearing deposits", "yield", "EDEP", "DEPIDOM"),
        ("Cost: total deposits", "yield", "EDEP", "DEP"),
        ("Cost: funding (earning-asset basis)", "pct", "INTEXPY"),
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
        ("Diluted EPS", "ps", "eps"),
        ("Book value / share", "ps", "bvps"),
        ("Tangible book value / share", "ps", "tbvps"),
        ("Dividends declared / share", "ps", "dps"),
        ("Dividend payout ratio", "payout"),
        ("Avg diluted shares (actual)", "shares"),
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
    render_statement(ticker, "is", "Income Statement", _INCOME, with_ri=True)


def render_balance_sheet(ticker):
    render_statement(ticker, "bs", "Balance Sheet", _BALANCE)


def render_performance_analysis(ticker):
    render_statement(ticker, "perf", "Performance Analysis", _PERFORMANCE,
                     with_persh=True, with_dep_cost=True)


def render_fair_value(ticker):
    render_statement(ticker, "fv", "Fair Value Analysis", _FAIR_VALUE)
    st.caption("Detailed AFS/HTM fair-value and unrealized gain/loss (AOCI) breakdown "
               "from FFIEC Schedule RC-B is on the roadmap.")


def render_portfolio(ticker):
    render_statement(ticker, "port", "Portfolio Analysis", _PORTFOLIO)


def render_capital_structure(ticker):
    render_statement(ticker, "capstruct", "Capital Structure Details", _CAPITAL_STRUCTURE)
