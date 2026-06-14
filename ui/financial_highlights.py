"""
Financial Highlights — a multi-period, SNL/Capital-IQ-style summary table that
is the landing page for each bank under Company Analysis.

A period toggle switches columns between Annual (last 5 fiscal years) and
Quarterly (last 8 quarters). Every cell is clickable: it opens a drill-down
popup showing the calculation and the underlying source numbers (FDIC Call
Report fields / SEC XBRL tags), as-of date, and a link to the primary source.

Principle: show our work where we compute a value, and cite the source where
the source reports it directly. FDIC-reported ratios (ROAA, NIM, CET1…) are
labelled "reported by FDIC, field X"; ratios we derive (ROAE, ROATCE,
loans/deposits, TCE/TA, BVPS, TBVPS) show the formula with the actual
numerator and denominator and their source tags.
"""
from __future__ import annotations
import json
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from data.bank_mapping import get_bank_info
from data import fdic_client, sec_client


# ── small helpers ──────────────────────────────────────────────────────────
def _year(repdte):
    if repdte is None:
        return None
    if hasattr(repdte, "year"):
        return int(repdte.year)
    s = str(repdte)
    try:
        return int(s[:4])
    except Exception:
        return None


def _month(repdte):
    if hasattr(repdte, "month"):
        return int(repdte.month)
    s = str(repdte)
    try:
        return int(s.split("-")[1]) if "-" in s else int(s[4:6])
    except Exception:
        return None


# Shared numeric primitives (utils/formatting) — kept under the old local
# names because several modules import them from here.
from utils.formatting import (
    num as _num, thou as _thou, pct as _pct,
    usd_compact_from_thousands as _usd,
)
_count = _thou  # was a same-bodied duplicate of _thou


def _ratio_pct(num, den, dp=2):
    n, d = _num(num), _num(den)
    if n is None or not d:
        return "—"
    return f"{n/d*100:.{dp}f}%"


def _dollars_ps(v, dp=2):
    v = _num(v)
    return f"${v:.{dp}f}" if v is not None else "—"


def _iso(d):
    return d if hasattr(d, "year") else datetime.fromisoformat(str(d)[:10])


def _disp_date(d):
    dd = _iso(d)
    return dd.strftime("%b-%d-%Y")


# ── SEC extraction (keyed by period end-date) ────────────────────────────────
def _sec_map(facts: dict, concept: str, instant: bool, span: str = "both") -> dict[str, float]:
    """{end_date 'YYYY-MM-DD': value} from 10-K/10-Q. instant=balance item;
    else a flow. span filters flow length: 'quarter' (~90d), 'annual' (~365d),
    or 'both'."""
    out: dict[str, float] = {}
    try:
        units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {})
    except Exception:
        return out
    for arr in units.values():
        for e in arr:
            if e.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A"):
                continue
            end, val = e.get("end"), e.get("val")
            if not end or val is None:
                continue
            try:
                d_end = datetime.fromisoformat(end)
            except ValueError:
                continue
            if not instant:
                start = e.get("start")
                if not start:
                    continue
                try:
                    days = (d_end - datetime.fromisoformat(start)).days
                except ValueError:
                    continue
                is_q = 80 <= days <= 100
                is_a = 350 <= days <= 380
                if span == "quarter" and not is_q:
                    continue
                if span == "annual" and not is_a:
                    continue
                if span == "both" and not (is_q or is_a):
                    continue
            out[end] = float(val)  # latest filing for a given end wins
    return out


def _nearest_kv(date_map: dict[str, float], target: datetime, fwd_days=110, back_days=400):
    """(end_date_str, value) whose end-date is nearest the target (prefer on/after)."""
    best_k, best_v, best_gap = None, None, None
    for k, v in date_map.items():
        try:
            d = datetime.fromisoformat(k)
        except ValueError:
            continue
        gap = (d - target).days
        if -back_days <= gap <= fwd_days:
            score = gap if gap >= 0 else (abs(gap) + 1000)
            if best_gap is None or score < best_gap:
                best_k, best_v, best_gap = k, v, score
    return best_k, best_v


def _shares_map(facts: dict) -> dict[str, float]:
    """Cover-page dei shares + GAAP year-end shares, keyed by end-date."""
    out: dict[str, float] = {}
    dei = facts.get("facts", {}).get("dei", {}).get(
        "EntityCommonStockSharesOutstanding", {}).get("units", {})
    for arr in dei.values():
        for e in arr:
            if e.get("end") and e.get("val") is not None:
                out[e["end"]] = float(e["val"])
    for k, v in _sec_map(facts, "CommonStockSharesOutstanding", instant=True).items():
        out.setdefault(k, v)
    return out


def _sec_prov_map(facts: dict, concept: str, instant: bool, span: str = "both",
                  ns: str = "us-gaap") -> dict[str, dict]:
    """{end_date: {accn, form, end}} — the *original* filing (earliest filed)
    that first reported this concept for this period, for source-doc linking."""
    out: dict[str, dict] = {}
    try:
        units = facts.get("facts", {}).get(ns, {}).get(concept, {}).get("units", {})
    except Exception:
        return out
    for arr in units.values():
        for e in arr:
            form = e.get("form")
            if form not in ("10-K", "10-K/A", "10-Q", "10-Q/A"):
                continue
            end, val = e.get("end"), e.get("val")
            if not end or val is None:
                continue
            if not instant:
                start = e.get("start")
                if not start:
                    continue
                try:
                    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
                except ValueError:
                    continue
                is_q, is_a = 80 <= days <= 100, 350 <= days <= 380
                if (span == "quarter" and not is_q) or (span == "annual" and not is_a) \
                        or (span == "both" and not (is_q or is_a)):
                    continue
            filed = e.get("filed", "9999-99-99")
            cur = out.get(end)
            if cur is None or filed < cur["filed"]:
                out[end] = {"accn": e.get("accn"), "form": form, "end": end, "filed": filed}
    return out


def _shares_prov_map(facts: dict) -> dict[str, dict]:
    out = _sec_prov_map(facts, "EntityCommonStockSharesOutstanding", instant=True, ns="dei")
    for k, v in _sec_prov_map(facts, "CommonStockSharesOutstanding", instant=True).items():
        out.setdefault(k, v)
    return out


def _sec_doc(cik, prov):
    """{url, label} pointing at the exact 10-K/10-Q filing index on EDGAR."""
    if not cik or not prov or not prov.get("accn"):
        return None
    accn = prov["accn"]
    nod = accn.replace("-", "")
    end = prov.get("end", "")
    try:
        dd = datetime.fromisoformat(end)
        lbl = f"{dd.month}/{dd.day}/{dd.year}"
    except ValueError:
        lbl = end
    return {"url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nod}/{accn}-index.htm",
            "label": f"{lbl} {prov.get('form', 'filing')}"}


def _fdic_doc(cert, repdte):
    """{url, label} → FFIEC CDR Call Report facsimile for this cert + quarter."""
    dd = _iso(repdte)
    return {"url": (f"https://cdr.ffiec.gov/public/ViewFacsimileDirect.aspx?ds=call"
                    f"&idType=fdiccert&id={cert}&date={dd.strftime('%m%d%Y')}"),
            "label": f"{dd.month}/{dd.day}/{dd.year} Call Report"}


def _flow_for(d: datetime, q_map: dict, a_map: dict, quarterly: bool):
    """Pick the right flow value for a column end-date. Annual mode → full-year
    (365d). Quarterly mode → single-quarter (90d); Q4 derived as annual −
    (Q1+Q2+Q3) since there is no Q4 10-Q. Returns (value, derived_note)."""
    key = d.strftime("%Y-%m-%d")
    if not quarterly:
        return a_map.get(key), None
    v = q_map.get(key)
    if v is not None:
        return v, None
    if d.month == 12:
        annual = a_map.get(key)
        q1 = q_map.get(f"{d.year}-03-31")
        q2 = q_map.get(f"{d.year}-06-30")
        q3 = q_map.get(f"{d.year}-09-30")
        if annual is not None and None not in (q1, q2, q3):
            return round(annual - (q1 + q2 + q3), 2), \
                f"derived: FY {annual:.2f} − 9-mo {q1 + q2 + q3:.2f}"
    return None, None


def _per_share_for_ends(cik, ends: list[datetime], quarterly: bool = False) -> dict:
    """{end_date(datetime): {...per-share values + raw inputs for drill-down}}."""
    if not cik:
        return {}
    facts = sec_client.fetch_company_facts(cik)
    if not facts:
        return {}
    eps_q = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="quarter")
    eps_a = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="annual")
    epsb_q = _sec_map(facts, "EarningsPerShareBasic", instant=False, span="quarter")
    epsb_a = _sec_map(facts, "EarningsPerShareBasic", instant=False, span="annual")
    wad_q = _sec_map(facts, "WeightedAverageNumberOfDilutedSharesOutstanding",
                     instant=False, span="quarter")
    wad_a = _sec_map(facts, "WeightedAverageNumberOfDilutedSharesOutstanding",
                     instant=False, span="annual")
    amort_q = _sec_map(facts, "AmortizationOfIntangibleAssets", instant=False, span="quarter")
    amort_a = _sec_map(facts, "AmortizationOfIntangibleAssets", instant=False, span="annual")
    dps_q = _sec_map(facts, "CommonStockDividendsPerShareDeclared", instant=False, span="quarter")
    dps_a = _sec_map(facts, "CommonStockDividendsPerShareDeclared", instant=False, span="annual")
    equity = _sec_map(facts, "StockholdersEquity", instant=True)
    goodwill = _sec_map(facts, "Goodwill", instant=True)
    intang = _sec_map(facts, "IntangibleAssetsNetExcludingGoodwill", instant=True)
    incl = _sec_map(facts, "IntangibleAssetsNetIncludingGoodwill", instant=True)
    shares = _shares_map(facts)
    # provenance (original filing accession/form per period) for source-doc links
    eq_prov = _sec_prov_map(facts, "StockholdersEquity", instant=True)
    sh_prov = _shares_prov_map(facts)
    epsq_prov = _sec_prov_map(facts, "EarningsPerShareDiluted", instant=False, span="quarter")
    epsa_prov = _sec_prov_map(facts, "EarningsPerShareDiluted", instant=False, span="annual")
    dpsq_prov = _sec_prov_map(facts, "CommonStockDividendsPerShareDeclared",
                              instant=False, span="quarter")
    dpsa_prov = _sec_prov_map(facts, "CommonStockDividendsPerShareDeclared",
                              instant=False, span="annual")

    out = {}
    for d in ends:
        key = d.strftime("%Y-%m-%d")
        if key in equity:
            eq, eq_date = equity[key], key
        else:
            eq_date, eq = _nearest_kv(equity, d, 12, 12)
        if key in shares:
            sh, sh_date = shares[key], key
        else:
            sh_date, sh = _nearest_kv(shares, d)
        gw = goodwill.get(key)
        other = intang.get(key)
        if gw is not None:
            adj = gw + (other or 0)
            adj_basis = "goodwill + other intangibles" if other else "goodwill"
        else:
            adj = incl.get(key)
            adj_basis = "intangibles incl. goodwill"
        bvps = (eq / sh) if (eq and sh) else None
        tbvps = ((eq - adj) / sh) if (eq and sh and adj is not None) else bvps
        eps, eps_note = _flow_for(d, eps_q, eps_a, quarterly)
        dps, dps_note = _flow_for(d, dps_q, dps_a, quarterly)
        basic_eps, _ = _flow_for(d, epsb_q, epsb_a, quarterly)
        avg_dil, _ = _flow_for(d, wad_q, wad_a, quarterly)
        amort, _ = _flow_for(d, amort_q, amort_a, quarterly)
        # Diluted EPS before intangible amortization: add back the after-tax
        # intangible amortization per diluted share (21% statutory rate, flagged
        # in the drill-down). n/a unless both amortization and the diluted share
        # count are reported — never a fabricated add-back.
        eps_before_amort = None
        if eps is not None and amort and avg_dil:
            eps_before_amort = round(eps + amort * (1 - 0.21) / avg_dil, 2)
        out[d] = {
            "eps": eps, "eps_note": eps_note, "dps": dps, "dps_note": dps_note,
            "basic_eps": basic_eps, "avg_diluted_shares": avg_dil,
            "eps_before_amort": eps_before_amort, "_amort": amort,
            "bvps": bvps, "tbvps": tbvps, "shares": sh,
            "_eq": eq, "_eq_date": eq_date, "_sh_date": sh_date,
            "_adj": adj, "_gw": gw, "_other": other, "_incl": incl.get(key),
            "_adj_basis": adj_basis,
            "_eq_prov": eq_prov.get(eq_date) if eq_date else None,
            "_sh_prov": sh_prov.get(sh_date) if sh_date else None,
            "_eps_prov": epsq_prov.get(key) or epsa_prov.get(key),
            "_dps_prov": dpsq_prov.get(key) or dpsa_prov.get(key),
        }
    return out


# ── definitions (shown in the drill-down, CapIQ-style) ───────────────────────
_DEFS = {
    "Total assets": "Total assets reported on the Call Report balance sheet.",
    "Net loans": "Loans and leases net of unearned income and the allowance.",
    "Total deposits": "Total interest- and non-interest-bearing deposits.",
    "Total equity": "Total bank equity capital.",
    "Securities": "Total investment securities (HTM + AFS, at carrying value).",
    "Net income (YTD)": "Year-to-date net income after taxes and extraordinary items.",
    "ROAA": "Annualized net income as a percent of average assets.",
    "ROAE": "Annualized net income as a percent of total equity.",
    "ROATCE": "Annualized net income as a percent of tangible common equity "
              "(equity less intangible assets).",
    "Net interest margin": "Net interest income as a percent of average earning assets.",
    "Efficiency ratio": "Non-interest expense divided by the sum of net interest "
                        "income and non-interest income.",
    "Loans / deposits": "Net loans divided by total deposits — a liquidity/funding gauge.",
    "Securities / assets": "Investment securities as a percent of total assets.",
    "Equity / assets": "Total equity as a percent of total assets.",
    "Tang. common equity / tang. assets":
        "Tangible common equity (equity − intangibles) divided by tangible assets "
        "(assets − intangibles).",
    "NPLs / loans": "Non-current loans and leases as a percent of total loans.",
    "Net charge-offs / loans": "Annualized net charge-offs as a percent of total loans.",
    "Loan-loss reserves / loans": "Allowance for credit losses as a percent of total loans.",
    "CET1 ratio": "Common equity tier 1 capital to risk-weighted assets (bank-level).",
    "Total capital ratio": "Total risk-based capital to risk-weighted assets (bank-level).",
    "Leverage ratio": "Tier 1 capital to average total assets (bank-level).",
    "Diluted EPS": "Diluted earnings per common share, as reported by the holding company.",
    "Dividends / share": "Common dividends declared per share.",
    "Book value / share": "Total common equity divided by shares outstanding.",
    "Tangible BV / share": "Tangible common equity (equity − intangibles) divided by "
                           "shares outstanding.",
    "Shares outstanding": "Common shares outstanding (cover-page / year-end).",
}


# ── the table ───────────────────────────────────────────────────────────────
def render_financial_highlights(ticker: str):
    info = get_bank_info(ticker)
    name = info.get("name") if info else ticker
    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None

    st.markdown(f"### {name} ({ticker}) — Financial Highlights")
    period = st.radio("Period", ["Annual", "Quarterly"], horizontal=True,
                      key=f"fh_period_{ticker}", label_visibility="collapsed")
    st.caption("Fiscal figures from FDIC Call Reports; per-share from SEC filings "
               "(holding company). Click any number to see the calculation and its sources.")

    if not cert:
        st.info("No FDIC Call Report data mapped for this bank.")
        return
    with st.spinner("Loading financials…"):
        hist = fdic_client.get_historical_financials(cert, quarters=36)
    if hist is None or hist.empty:
        st.info("No FDIC history available.")
        return

    hist = hist.copy()
    hist["_y"] = hist["REPDTE"].apply(_year)
    hist["_m"] = hist["REPDTE"].apply(_month)
    hist = hist.sort_values("REPDTE")

    if period == "Annual":
        ye = hist[hist["_m"] == 12].dropna(subset=["_y"])
        years = sorted({int(y) for y in ye["_y"]})[-5:]
        keys = years
        labels = {y: f"FY{y}" for y in years}
        recs = {y: ye[ye["_y"] == y].iloc[0].to_dict() for y in years}
        ends = {y: datetime(int(y), 12, 31) for y in years}
    else:
        q = hist.tail(8)
        keys, labels, recs, ends = [], {}, {}, {}
        for _, r in q.iterrows():
            d = _iso(r["REPDTE"])
            k = d.strftime("%Y-%m-%d")
            keys.append(k)
            labels[k] = f"Q{(d.month-1)//3+1} '{str(d.year)[2:]}"
            recs[k] = r.to_dict()
            ends[k] = d

    persh = _per_share_for_ends(cik, list(ends.values()), quarterly=(period == "Quarterly"))
    col_ps = {k: persh.get(ends[k], {}) for k in keys}
    asof = {k: _disp_date(recs[k].get("REPDTE")) for k in keys}

    fdic_link = f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"
    sec_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type=10-K") if cik else fdic_link
    entity = f"{name} ({ticker})"

    def _annual_factor(r):
        m = _month(r.get("REPDTE")) or 12
        return 12.0 / m if m else 1.0

    # payload builders ── each returns {"v": display, "calc": {...}}
    def P(v, metric, source, asof_s, unit, ref, terms, op, reported, link):
        return {"v": v, "calc": {
            "metric": metric, "entity": entity, "source": source, "asof": asof_s,
            "unit": unit, "ref": ref, "definition": _DEFS.get(metric, ""),
            "terms": terms, "op": op, "reported": reported, "link": link}}

    def fdic_dollar(metric, field):
        def b(k):
            raw = _num(recs[k].get(field))
            return P(_usd(raw), metric, "FDIC Call Report", asof[k], "$ in thousands",
                     f"FDIC field {field}",
                     [{"label": metric, "val": _thou(raw) + " ($000)"}],
                     None, True, fdic_link)
        return b

    def fdic_pct(metric, field):
        def b(k):
            raw = _num(recs[k].get(field))
            return P(_pct(raw), metric, "FDIC Call Report", asof[k], "%",
                     f"FDIC field {field}",
                     [{"label": metric + " (as reported)", "val": _pct(raw)}],
                     None, True, fdic_link)
        return b

    def fdic_ratio(metric, nf, df_, nlbl, dlbl):
        def b(k):
            n, d = _num(recs[k].get(nf)), _num(recs[k].get(df_))
            return P(_ratio_pct(n, d), metric, "FDIC Call Report", asof[k], "%",
                     "Computed from Call Report",
                     [{"label": nlbl, "val": _thou(n) + " ($000)"},
                      {"label": dlbl, "val": _thou(d) + " ($000)"}],
                     f"{nlbl} ÷ {dlbl} × 100", False, fdic_link)
        return b

    def roae(k):
        r = recs[k]; ni, eq = _num(r.get("NETINC")), _num(r.get("EQTOT"))
        f = _annual_factor(r)
        v = f"{ni*f/eq*100:.2f}%" if (ni is not None and eq) else "—"
        ni_a = round(ni * f) if ni is not None else None
        terms = [{"label": "Net income" + (" (annualized)" if f != 1 else ""),
                  "val": _thou(ni_a) + " ($000)",
                  "sub": (f"YTD {_thou(ni)} × 12/{int(round(12/f))}" if f != 1 else None)},
                 {"label": "Total equity", "val": _thou(eq) + " ($000)"}]
        return P(v, "ROAE", "FDIC Call Report", asof[k], "%", "Computed from Call Report",
                 terms, "Net income ÷ Total equity × 100", False, fdic_link)

    def roatce(k):
        r = recs[k]; ni = _num(r.get("NETINC")); eq = _num(r.get("EQTOT"))
        intan = _num(r.get("INTAN")) or 0
        f = _annual_factor(r); tce = (eq - intan) if eq is not None else None
        v = f"{ni*f/tce*100:.2f}%" if (ni is not None and tce and tce > 0) else "—"
        ni_a = round(ni * f) if ni is not None else None
        terms = [{"label": "Net income" + (" (annualized)" if f != 1 else ""),
                  "val": _thou(ni_a) + " ($000)",
                  "sub": (f"YTD {_thou(ni)} × 12/{int(round(12/f))}" if f != 1 else None)},
                 {"label": "Tangible common equity", "val": _thou(tce) + " ($000)",
                  "sub": f"Equity {_thou(eq)} − Intangibles {_thou(intan)}"}]
        return P(v, "ROATCE", "FDIC Call Report", asof[k], "%", "Computed from Call Report",
                 terms, "Net income ÷ Tangible common equity × 100", False, fdic_link)

    def sec_eps(k):
        ps = col_ps.get(k, {}); v = ps.get("eps")
        doc = _sec_doc(cik, ps.get("_eps_prov"))
        terms = [{"label": "Diluted EPS (reported)", "val": _dollars_ps(v),
                  "sub": ps.get("eps_note"), "doc": doc}]
        return P(_dollars_ps(v), "Diluted EPS", "SEC filing (10-K/10-Q)",
                 _disp_date(ends[k]), "$ / share", "XBRL EarningsPerShareDiluted",
                 terms, None, ps.get("eps_note") is None, (doc or {}).get("url") or sec_link)

    def sec_dps(k):
        ps = col_ps.get(k, {}); v = ps.get("dps")
        doc = _sec_doc(cik, ps.get("_dps_prov"))
        terms = [{"label": "Dividends declared / share", "val": _dollars_ps(v),
                  "sub": ps.get("dps_note"), "doc": doc}]
        return P(_dollars_ps(v), "Dividends / share", "SEC filing (10-K/10-Q)",
                 _disp_date(ends[k]), "$ / share",
                 "XBRL CommonStockDividendsPerShareDeclared",
                 terms, None, ps.get("dps_note") is None, (doc or {}).get("url") or sec_link)

    def sec_bvps(k):
        ps = col_ps.get(k, {}); eq = ps.get("_eq"); sh = ps.get("shares")
        eq_doc = _sec_doc(cik, ps.get("_eq_prov")); sh_doc = _sec_doc(cik, ps.get("_sh_prov"))
        terms = [{"label": "Total common equity", "val": _thou((eq or 0) / 1000) + " ($000)",
                  "sub": f"as of {ps.get('_eq_date') or '—'} · XBRL StockholdersEquity",
                  "doc": eq_doc},
                 {"label": "Shares outstanding", "val": _count(sh),
                  "sub": f"as of {ps.get('_sh_date') or '—'}", "doc": sh_doc}]
        return P(_dollars_ps(ps.get("bvps")), "Book value / share",
                 "SEC filing (10-K/10-Q)", _disp_date(ends[k]), "$ / share",
                 "Computed: equity ÷ shares", terms,
                 "Total common equity ÷ shares outstanding", False,
                 (eq_doc or {}).get("url") or sec_link)

    def sec_tbvps(k):
        ps = col_ps.get(k, {}); eq = ps.get("_eq"); sh = ps.get("shares")
        adj = ps.get("_adj"); tce = (eq - adj) if (eq is not None and adj is not None) else None
        basis = ps.get("_adj_basis", "intangibles")
        eq_doc = _sec_doc(cik, ps.get("_eq_prov")); sh_doc = _sec_doc(cik, ps.get("_sh_prov"))
        terms = [{"label": "Tangible common equity", "val": _thou((tce or 0) / 1000) + " ($000)",
                  "sub": (f"Equity {_thou((eq or 0)/1000)} − {basis} "
                          f"{_thou((adj or 0)/1000)} ($000)"), "doc": eq_doc},
                 {"label": "Shares outstanding", "val": _count(sh),
                  "sub": f"as of {ps.get('_sh_date') or '—'}", "doc": sh_doc}]
        return P(_dollars_ps(ps.get("tbvps")), "Tangible BV / share",
                 "SEC filing (10-K/10-Q)", _disp_date(ends[k]), "$ / share",
                 "Computed: (equity − intangibles) ÷ shares", terms,
                 "Tangible common equity ÷ shares outstanding", False,
                 (eq_doc or {}).get("url") or sec_link)

    def sec_shares(k):
        ps = col_ps.get(k, {}); sh = ps.get("shares")
        doc = _sec_doc(cik, ps.get("_sh_prov"))
        terms = [{"label": "Common shares outstanding", "val": _count(sh),
                  "sub": f"as of {ps.get('_sh_date') or '—'} · cover-page / year-end",
                  "doc": doc}]
        return P(_count(sh), "Shares outstanding", "SEC filing (10-K/10-Q)",
                 _disp_date(ends[k]), "shares", "XBRL EntityCommonStockSharesOutstanding",
                 terms, None, True, (doc or {}).get("url") or sec_link)

    sections = [
        ("Balance Sheet", [
            ("Total assets", fdic_dollar("Total assets", "ASSET")),
            ("Net loans", fdic_dollar("Net loans", "LNLSNET")),
            ("Total deposits", fdic_dollar("Total deposits", "DEP")),
            ("Total equity", fdic_dollar("Total equity", "EQTOT")),
            ("Securities", fdic_dollar("Securities", "SC")),
        ]),
        ("Profitability", [
            ("Net income (YTD)", fdic_dollar("Net income (YTD)", "NETINC")),
            ("ROAA", fdic_pct("ROAA", "ROA")),
            ("ROAE", roae),
            ("ROATCE", roatce),
            ("Net interest margin", fdic_pct("Net interest margin", "NIMY")),
            ("Efficiency ratio", fdic_pct("Efficiency ratio", "EEFFR")),
        ]),
        ("Balance Sheet Ratios", [
            ("Loans / deposits", fdic_ratio("Loans / deposits", "LNLSNET", "DEP",
                                            "Net loans", "Total deposits")),
            ("Securities / assets", fdic_ratio("Securities / assets", "SC", "ASSET",
                                               "Securities", "Total assets")),
            ("Equity / assets", fdic_ratio("Equity / assets", "EQTOT", "ASSET",
                                           "Total equity", "Total assets")),
            ("Tang. common equity / tang. assets", _tce_ta_builder(recs, asof, fdic_link, P)),
        ]),
        ("Asset Quality", [
            ("NPLs / loans", fdic_pct("NPLs / loans", "NCLNLSR")),
            ("Net charge-offs / loans", fdic_pct("Net charge-offs / loans", "NTLNLSR")),
            ("Loan-loss reserves / loans", fdic_pct("Loan-loss reserves / loans", "LNATRESR")),
        ]),
        ("Capital Adequacy (bank-level)", [
            ("CET1 ratio", fdic_pct("CET1 ratio", "IDT1CER")),
            ("Total capital ratio", fdic_pct("Total capital ratio", "RBCRWAJ")),
            ("Leverage ratio", fdic_pct("Leverage ratio", "RBCT1JR")),
        ]),
        ("Per Share (HoldCo)", [
            ("Diluted EPS", sec_eps),
            ("Dividends / share", sec_dps),
            ("Book value / share", sec_bvps),
            ("Tangible BV / share", sec_tbvps),
            ("Shares outstanding", sec_shares),
        ]),
    ]

    # Build cells + HTML rows
    cells: dict[str, dict] = {}
    rows_html = []
    ri = 0
    cell_errors: list[str] = []
    for sec_name, rows in sections:
        rows_html.append(
            f'<tr><td class="sec" colspan="{len(keys)+1}">{sec_name}</td></tr>')
        for label, fn in rows:
            tds = [f'<td class="lbl">{label}</td>']
            for ci, k in enumerate(keys):
                try:
                    payload = fn(k)
                except Exception as e:
                    # A computation bug must not be indistinguishable from
                    # "not reported" — collect and log once per render.
                    cell_errors.append(f"{label}[{k}]: {type(e).__name__}: {e}")
                    payload = {"v": "—", "calc": None}
                cid = f"{ri}_{ci}"
                if payload.get("calc"):
                    calc = payload["calc"]
                    # FDIC terms all trace to the same quarterly Call Report.
                    if calc.get("source", "").startswith("FDIC"):
                        cr_doc = _fdic_doc(cert, recs[k].get("REPDTE"))
                        for t in calc.get("terms", []):
                            t.setdefault("doc", cr_doc)
                    cells[cid] = calc
                    tds.append(f'<td class="val" data-cid="{cid}">{payload["v"]}</td>')
                else:
                    tds.append(f'<td class="val dead">{payload.get("v", "—")}</td>')
            zebra = ' class="zebra"' if ri % 2 == 1 else ""
            rows_html.append(f'<tr{zebra}>{"".join(tds)}</tr>')
            ri += 1

    if cell_errors:
        print(f"[financial_highlights] {ticker}: {len(cell_errors)} cell(s) "
              f"failed to compute — {'; '.join(cell_errors[:5])}")

    head = ('<th class="lblh">($ in thousands unless noted)</th>'
            + "".join(f'<th class="colh">{labels[k]}</th>' for k in keys))

    n_rows = ri + len(sections) + 1
    height = 96 + 23 * n_rows
    html = _build_component(head, "".join(rows_html), cells, entity, fdic_link, sec_link)

    # Table left, trend charts right (2×2 grid) — balanced columns so the table
    # fills its side and the charts fill theirs, with no dead gap between.
    _tbl_col, _chart_col = st.columns([1, 1])
    with _tbl_col:
        components.html(html, height=height, scrolling=False)
    with _chart_col:
        _render_fh_trends(hist, ticker)

    # Freshness / sourcing line — visible on every load so it's clear the data
    # is current and where it comes from. FDIC is fetched live each render;
    # SEC facts are cached but invalidated within ~30 min of a new 10-K/10-Q
    # (poll-events job), with a 24h TTL backstop.
    fresh = f"Latest data: FDIC Call Report {asof[keys[-1]]}"
    try:
        from data import cache
        age = cache.get_age(f"sec_facts:{int(cik)}") if cik else None
        if age is not None:
            fresh += f" · SEC facts refreshed {age/3600:.0f}h ago"
    except Exception:
        pass
    st.caption(fresh + ". Auto-updates on new filings — FDIC live each load; "
               "SEC within ~30 min of a 10-K/10-Q posting (24h max).")


def _render_fh_trends(hist, ticker: str):
    """Compact profitability / capital trend charts shown beside the highlights
    table so the otherwise-empty right column carries the visual story."""
    try:
        from ui.charts import metrics_trend_chart
    except Exception:
        return
    h = hist.sort_values("REPDTE").tail(20)
    items = [("roaa", "ROAA"), ("nim", "Net Interest Margin"),
             ("efficiency_ratio", "Efficiency Ratio"), ("cet1_ratio", "CET1 Ratio")]
    # 2×2 grid of compact charts so the column fills densely (not 4 tall stacks).
    for r in range(0, len(items), 2):
        cols = st.columns(2)
        for col, (key, label) in zip(cols, items[r:r + 2]):
            with col:
                try:
                    st.plotly_chart(metrics_trend_chart(h, [key], label),
                                    use_container_width=True, key=f"fh_tr_{ticker}_{key}")
                except Exception:
                    pass


def _tce_ta_builder(recs, asof, fdic_link, P):
    def b(k):
        eq = _num(recs[k].get("EQTOT")) or 0
        intan = _num(recs[k].get("INTAN")) or 0
        asset = _num(recs[k].get("ASSET")) or 0
        tce, ta = eq - intan, asset - intan
        v = f"{tce/ta*100:.2f}%" if ta else "—"
        terms = [{"label": "Tangible common equity", "val": _thou(tce) + " ($000)",
                  "sub": f"Equity {_thou(eq)} − Intangibles {_thou(intan)}"},
                 {"label": "Tangible assets", "val": _thou(ta) + " ($000)",
                  "sub": f"Assets {_thou(asset)} − Intangibles {_thou(intan)}"}]
        return P(v, "Tang. common equity / tang. assets", "FDIC Call Report", asof[k],
                 "%", "Computed from Call Report", terms,
                 "Tangible common equity ÷ tangible assets × 100", False, fdic_link)
    return b


def _build_component(head_html, body_html, cells, entity, fdic_link, sec_link):
    data = json.dumps(cells)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:#1e293b; background:transparent; }}
/* width:auto + fixed column widths so the table sizes to its content (no
   stretching that throws a big empty gap between labels and values). */
/* width:100% so the table fills its column instead of hugging the left and
   leaving a dead gap next to the charts. The label column is fixed; the period
   columns share the rest evenly. */
/* Full-grid terminal table (DESIGN-SYSTEM.md: SNL spreadsheet look,
   terminal density, navy links, negatives as red parens). */
table {{ width:100%; border-collapse:collapse; font-size:11px; }}
thead th {{ border:0.5px solid #d6dae3; background:#f4f6f9; padding:2px 7px; }}
.lblh {{ text-align:left; color:#64748b; font-weight:600; width:34%; }}
.colh {{ text-align:right; font-weight:600; color:#334155; }}
td.sec {{ padding:4px 7px 2px; font-weight:700; color:#1e3a8a; font-size:9px;
  text-transform:uppercase; letter-spacing:0.06em; background:#f4f6f9;
  border:0.5px solid #d6dae3; }}
td.lbl {{ text-align:left; padding:2px 7px; color:#475569;
  border:0.5px solid #e2e6ec; }}
td.val {{ text-align:right; padding:2px 7px; color:#1e40af; cursor:pointer;
  position:relative; border:0.5px solid #e2e6ec;
  font-variant-numeric:tabular-nums; }}
td.val.dead {{ color:#94a3b8; cursor:default; }}
td.val.neg {{ color:#b91c1c; }}
tr.zebra td.lbl, tr.zebra td.val {{ background:#fafbfc; }}
tbody tr:hover td.lbl, tbody tr:hover td.val {{ background:rgba(30,64,175,0.07); }}
tbody tr:hover td.lbl {{ color:#0f172a; font-weight:600; }}
td.val:hover {{ background:rgba(30,64,175,0.14) !important; text-decoration:underline; }}
.foot {{ margin-top:7px; font-size:11px; color:#64748b; }}
.foot a {{ color:#1e40af; text-decoration:none; }}
#ov {{ position:fixed; inset:0; background:rgba(15,23,42,0.35); display:none;
  align-items:flex-start; justify-content:center; padding-top:40px; z-index:50; }}
#card {{ width:min(440px,92%); background:#fff; border:1px solid rgba(148,163,184,0.3);
  border-radius:10px; box-shadow:0 12px 40px rgba(15,23,42,0.25); overflow:hidden; }}
#card .hd {{ padding:13px 16px; border-bottom:1px solid rgba(148,163,184,0.2); }}
#card .ttl {{ display:flex; justify-content:space-between; align-items:center;
  font-size:15px; font-weight:700; color:#0f172a; }}
#card .ttl button {{ border:none; background:none; font-size:18px; color:#94a3b8;
  cursor:pointer; line-height:1; }}
#card .ent {{ font-size:12px; color:#475569; margin-top:3px; }}
#card .meta {{ font-size:11.5px; color:#64748b; margin-top:2px; }}
#card .def {{ font-size:12px; color:#475569; padding:10px 16px; line-height:1.5;
  border-bottom:1px solid rgba(148,163,184,0.15); }}
#card .def b {{ color:#1e40af; font-weight:600; letter-spacing:0.03em; font-size:10.5px; }}
#card .calc {{ padding:12px 16px; }}
.bigrow {{ display:flex; justify-content:space-between; align-items:baseline;
  font-weight:700; color:#0f172a; font-size:15px; margin-bottom:8px; }}
.term {{ display:flex; justify-content:space-between; padding:4px 0 4px 14px;
  font-size:12.5px; color:#334155; border-top:1px dashed rgba(148,163,184,0.25); }}
.term .tv {{ color:#1e40af; font-variant-numeric:tabular-nums; }}
.sub {{ font-size:11px; color:#94a3b8; padding:0 0 2px 14px; }}
.doc {{ font-size:11px; color:#94a3b8; padding:0 0 5px 14px; }}
.doc a {{ color:#1e40af; text-decoration:none; }}
.doc a:hover {{ text-decoration:underline; }}
.op {{ font-size:12px; color:#64748b; text-align:center; padding:8px 0 2px;
  font-style:italic; }}
.rep {{ font-size:11.5px; color:#475569; padding:8px 0 2px; }}
.src {{ display:inline-block; margin-top:10px; font-size:12px; color:#1e40af;
  text-decoration:none; }}
</style></head><body>
<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>
<div class="foot">Source:
  <a href="{fdic_link}" target="_blank">FDIC BankFind</a> ·
  <a href="{sec_link}" target="_blank">SEC EDGAR</a> — click any value for its calculation.</div>
<div id="ov"><div id="card"></div></div>
<script>
const CELLS = {data};
const ov = document.getElementById("ov"), card = document.getElementById("card");
function esc(s){{ return (s==null?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;"); }}
function open(c){{
  let terms = (c.terms||[]).map(t =>
    `<div class="term"><span>${{esc(t.label)}}</span><span class="tv">${{esc(t.val)}}</span></div>`
    + (t.sub ? `<div class="sub">${{esc(t.sub)}}</div>` : "")
    + (t.doc ? `<div class="doc"><i>Source document available</i> — `
        + `<a href="${{esc(t.doc.url)}}" target="_blank">view ${{esc(t.doc.label)}} →</a></div>` : "")
    ).join("");
  let opline = c.op ? `<div class="op">${{esc(c.op)}}</div>` : "";
  let rep = c.reported ? `<div class="rep">Reported directly by ${{esc(c.source)}}.</div>` : "";
  card.innerHTML =
    `<div class="hd"><div class="ttl"><span>${{esc(c.metric)}}</span>`
    + `<button onclick="hide()" aria-label="Close">×</button></div>`
    + `<div class="ent">${{esc(c.entity)}}</div>`
    + `<div class="meta">${{esc(c.source)}} &nbsp;|&nbsp; ${{esc(c.asof)}} &nbsp;|&nbsp; ${{esc(c.unit)}} &nbsp;|&nbsp; ${{esc(c.ref)}}</div></div>`
    + (c.definition ? `<div class="def"><b>DEFINITION</b> &nbsp; ${{esc(c.definition)}}</div>` : "")
    + `<div class="calc"><div class="bigrow"><span>${{esc(c.metric)}}</span></div>`
    + terms + opline + rep
    + `<a class="src" href="${{esc(c.link)}}" target="_blank">View source →</a></div>`;
  ov.style.display = "flex";
}}
function hide(){{ ov.style.display = "none"; }}
ov.addEventListener("click", e => {{ if(e.target===ov) hide(); }});
document.addEventListener("keydown", e => {{ if(e.key==="Escape") hide(); }});
document.querySelectorAll("td.val").forEach(td => {{
  const t = td.textContent.trim();
  if (/^-[\\d$.,]/.test(t)) {{ td.textContent = "(" + t.slice(1) + ")"; td.classList.add("neg"); }}
  else if (t.startsWith("(")) td.classList.add("neg");
}});
document.querySelectorAll("td.val[data-cid]").forEach(td =>
  td.addEventListener("click", () => {{ const c = CELLS[td.dataset.cid]; if(c) open(c); }}));
</script></body></html>"""
