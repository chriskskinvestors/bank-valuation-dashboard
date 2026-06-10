"""
Financial Highlights — a multi-period, SNL/Capital-IQ-style summary table that
is the landing page for each bank under Company Analysis.

A period toggle switches columns between Annual (last 5 fiscal years) and
Quarterly (last 8 quarters). Rows are grouped: Balance Sheet, Profitability,
Balance-Sheet Ratios, Asset Quality, Capital Adequacy (FDIC Call Reports) and
Per-Share (SEC filings, HoldCo).
"""
from __future__ import annotations
from datetime import datetime

import streamlit as st
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


def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _usd(v_thousands):
    v = _num(v_thousands)
    if v is None:
        return "—"
    v *= 1000
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:,.2f}B"
    if a >= 1e6:
        return f"${v/1e6:,.1f}M"
    return f"${v:,.0f}"


def _pct(v, dp=2):
    v = _num(v)
    return f"{v:.{dp}f}%" if v is not None else "—"


def _ratio_pct(num, den, dp=2):
    n, d = _num(num), _num(den)
    if n is None or not d:
        return "—"
    return f"{n/d*100:.{dp}f}%"


def _dollars_ps(v, dp=2):
    v = _num(v)
    return f"${v:.{dp}f}" if v is not None else "—"


def _count(v):
    v = _num(v)
    return f"{v:,.0f}" if v is not None else "—"


def _iso(d):
    return d if hasattr(d, "year") else datetime.fromisoformat(str(d)[:10])


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


def _nearest(date_map: dict[str, float], target: datetime, fwd_days=110, back_days=400):
    """Value whose end-date is nearest the target (preferring on/after)."""
    if not date_map:
        return None
    best, best_gap = None, None
    for k, v in date_map.items():
        try:
            d = datetime.fromisoformat(k)
        except ValueError:
            continue
        gap = (d - target).days
        if -back_days <= gap <= fwd_days:
            score = gap if gap >= 0 else (abs(gap) + 1000)  # prefer on/after
            if best_gap is None or score < best_gap:
                best, best_gap = v, score
    return best


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


def _flow_for(d: datetime, q_map: dict, a_map: dict, quarterly: bool):
    """Pick the right flow value for a column end-date. Annual mode → the
    full-year (365d) value. Quarterly mode → the single-quarter (90d) value;
    for Q4 (no 10-Q) derive it as annual − (Q1+Q2+Q3)."""
    key = d.strftime("%Y-%m-%d")
    if not quarterly:
        return a_map.get(key)
    v = q_map.get(key)
    if v is not None:
        return v
    if d.month == 12:  # Q4: annual minus the three reported quarters
        annual = a_map.get(key)
        q1 = q_map.get(f"{d.year}-03-31")
        q2 = q_map.get(f"{d.year}-06-30")
        q3 = q_map.get(f"{d.year}-09-30")
        if annual is not None and None not in (q1, q2, q3):
            return round(annual - (q1 + q2 + q3), 2)
    return None


def _per_share_for_ends(cik, ends: list[datetime], quarterly: bool = False) -> dict:
    """{end_date(datetime): {eps, dps, bvps, tbvps, shares}}. For annual columns
    pass year-end dates; for quarterly pass quarter-end dates."""
    if not cik:
        return {}
    facts = sec_client.fetch_company_facts(cik)
    if not facts:
        return {}
    eps_q = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="quarter")
    eps_a = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="annual")
    dps_q = _sec_map(facts, "CommonStockDividendsPerShareDeclared", instant=False, span="quarter")
    dps_a = _sec_map(facts, "CommonStockDividendsPerShareDeclared", instant=False, span="annual")
    equity = _sec_map(facts, "StockholdersEquity", instant=True)
    goodwill = _sec_map(facts, "Goodwill", instant=True)
    intang = _sec_map(facts, "IntangibleAssetsNetExcludingGoodwill", instant=True)
    incl = _sec_map(facts, "IntangibleAssetsNetIncludingGoodwill", instant=True)
    shares = _shares_map(facts)

    out = {}
    for d in ends:
        key = d.strftime("%Y-%m-%d")
        eq = equity.get(key) or _nearest(equity, d, fwd_days=10, back_days=10)
        sh = shares.get(key) or _nearest(shares, d)
        gw = goodwill.get(key)
        adj = (gw + (intang.get(key) or 0)) if gw is not None else incl.get(key)
        bvps = (eq / sh) if (eq and sh) else None
        tbvps = ((eq - adj) / sh) if (eq and sh and adj is not None) else bvps
        out[d] = {
            "eps": _flow_for(d, eps_q, eps_a, quarterly),
            "dps": _flow_for(d, dps_q, dps_a, quarterly),
            "bvps": bvps, "tbvps": tbvps, "shares": sh,
        }
    return out


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
               "(holding company). $ amounts scaled to B/M.")

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

    # Build columns: key → (label, fdic record, period-end datetime).
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

    def _annual_factor(r):
        # FDIC net income is year-to-date; annualize partial-year quarters so
        # return ratios are comparable to FDIC's own annualized ROA.
        m = _month(r.get("REPDTE")) or 12
        return 12.0 / m if m else 1.0

    def _roatce(r):
        ni, eq, intan = _num(r.get("NETINC")), _num(r.get("EQTOT")), _num(r.get("INTAN")) or 0
        tce = (eq - intan) if eq is not None else None
        if ni is None or not tce or tce <= 0:
            return "—"
        return f"{ni * _annual_factor(r) / tce * 100:.2f}%"

    def _roae(r):
        ni, eq = _num(r.get("NETINC")), _num(r.get("EQTOT"))
        if ni is None or not eq:
            return "—"
        return f"{ni * _annual_factor(r) / eq * 100:.2f}%"

    sections = [
        ("Balance Sheet", [
            ("Total assets", lambda r, k: _usd(r.get("ASSET"))),
            ("Net loans", lambda r, k: _usd(r.get("LNLSNET"))),
            ("Total deposits", lambda r, k: _usd(r.get("DEP"))),
            ("Total equity", lambda r, k: _usd(r.get("EQTOT"))),
            ("Securities", lambda r, k: _usd(r.get("SC"))),
        ]),
        ("Profitability", [
            ("Net income (YTD)", lambda r, k: _usd(r.get("NETINC"))),
            ("ROAA", lambda r, k: _pct(r.get("ROA"))),
            ("ROAE", lambda r, k: _roae(r)),
            ("ROATCE", lambda r, k: _roatce(r)),
            ("Net interest margin", lambda r, k: _pct(r.get("NIMY"))),
            ("Efficiency ratio", lambda r, k: _pct(r.get("EEFFR"))),
        ]),
        ("Balance Sheet Ratios", [
            ("Loans / deposits", lambda r, k: _ratio_pct(r.get("LNLSNET"), r.get("DEP"))),
            ("Securities / assets", lambda r, k: _ratio_pct(r.get("SC"), r.get("ASSET"))),
            ("Equity / assets", lambda r, k: _ratio_pct(r.get("EQTOT"), r.get("ASSET"))),
            ("Tang. common equity / tang. assets",
             lambda r, k: _ratio_pct((_num(r.get("EQTOT")) or 0) - (_num(r.get("INTAN")) or 0),
                                     (_num(r.get("ASSET")) or 0) - (_num(r.get("INTAN")) or 0))),
        ]),
        ("Asset Quality", [
            ("NPLs / loans", lambda r, k: _pct(r.get("NCLNLSR"))),
            ("Net charge-offs / loans", lambda r, k: _pct(r.get("NTLNLSR"))),
            ("Loan-loss reserves / loans", lambda r, k: _pct(r.get("LNATRESR"))),
        ]),
        ("Capital Adequacy (bank-level)", [
            ("CET1 ratio", lambda r, k: _pct(r.get("IDT1CER"))),
            ("Total capital ratio", lambda r, k: _pct(r.get("RBCRWAJ"))),
            ("Leverage ratio", lambda r, k: _pct(r.get("RBCT1JR"))),
        ]),
        ("Per Share (HoldCo)", [
            ("Diluted EPS", lambda r, k: _dollars_ps(col_ps.get(k, {}).get("eps"))),
            ("Dividends / share", lambda r, k: _dollars_ps(col_ps.get(k, {}).get("dps"))),
            ("Book value / share", lambda r, k: _dollars_ps(col_ps.get(k, {}).get("bvps"))),
            ("Tangible BV / share", lambda r, k: _dollars_ps(col_ps.get(k, {}).get("tbvps"))),
            ("Shares outstanding", lambda r, k: _count(col_ps.get(k, {}).get("shares"))),
        ]),
    ]

    head = ('<tr><th style="text-align:left; padding:5px 10px;">($ scaled)</th>'
            + "".join(f'<th style="text-align:right; padding:5px 12px; font-weight:600; '
                      f'color:#0f172a;">{labels[k]}</th>' for k in keys) + "</tr>")
    body = []
    for sec_name, rows in sections:
        body.append(
            f'<tr><td colspan="{len(keys)+1}" style="padding:9px 10px 3px; font-weight:700; '
            f'color:#1e3a8a; font-size:0.8rem; text-transform:uppercase; '
            f'letter-spacing:0.03em;">{sec_name}</td></tr>')
        for label, fn in rows:
            cells = []
            for k in keys:
                try:
                    v = fn(recs.get(k, {}), k)
                except Exception:
                    v = "—"
                cells.append(f'<td style="text-align:right; padding:4px 12px; color:#1d4ed8;">{v}</td>')
            body.append(f'<tr><td style="text-align:left; padding:4px 10px; '
                        f'color:#475569;">{label}</td>' + "".join(cells) + "</tr>")

    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; '
        'font-size:0.85rem; border:1px solid rgba(148,163,184,0.18);">'
        f'<thead style="border-bottom:1px solid rgba(148,163,184,0.3);">{head}</thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    cik_link = (f' · <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
                f'&CIK={cik}&type=10-K" target="_blank">SEC filings</a>') if cik else ""
    st.markdown(
        '<div style="margin-top:8px; font-size:0.78rem; color:#64748b;">'
        f'Source: <a href="https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}" '
        f'target="_blank">FDIC BankFind</a>{cik_link}</div>',
        unsafe_allow_html=True,
    )
