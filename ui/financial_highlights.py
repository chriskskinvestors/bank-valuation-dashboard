"""
Financial Highlights — a multi-year, SNL/Capital-IQ-style summary table that
is the landing page for each bank under Company Analysis.

Columns = the last 5 fiscal years (year-end Call Reports) plus the latest
quarter. Rows are grouped: Balance Sheet, Profitability, Balance-Sheet Ratios,
Asset Quality, Capital Adequacy (all from FDIC Call Reports) and Per-Share
(from SEC filings, HoldCo).
"""
from __future__ import annotations
import streamlit as st
import pandas as pd

from data.bank_mapping import get_bank_info
from data import fdic_client, sec_client


# ── small helpers ──────────────────────────────────────────────────────────
def _year(repdte) -> int | None:
    if repdte is None:
        return None
    if hasattr(repdte, "year"):
        return int(repdte.year)
    s = str(repdte)
    try:
        return int(s[:4]) if "-" in s else int(s[:4])
    except Exception:
        return None


def _month(repdte) -> int | None:
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
    """FDIC $thousands → scaled $ string."""
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


# ── SEC per-share, by fiscal year ───────────────────────────────────────────
def _sec_annual(facts: dict, concept: str, instant: bool) -> dict[int, float]:
    """{fiscal_year: value} from 10-K entries. instant=balance-sheet item
    (point-in-time, year-end); else annual flow (~365-day period)."""
    out: dict[int, float] = {}
    try:
        units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {})
    except Exception:
        return out
    from datetime import datetime
    for entries in units.values():
        for e in entries:
            if e.get("form") not in ("10-K", "10-K/A"):
                continue
            end = e.get("end")
            val = e.get("val")
            if not end or val is None:
                continue
            try:
                d_end = datetime.fromisoformat(end)
            except ValueError:
                continue
            if d_end.month != 12:  # fiscal-year-end only (calendar-year filers)
                continue
            if not instant:
                start = e.get("start")
                if not start:
                    continue
                try:
                    span = (d_end - datetime.fromisoformat(start)).days
                except ValueError:
                    continue
                if not (350 <= span <= 380):
                    continue
            yr = d_end.year
            # latest filing wins for a given year
            out[yr] = float(val)
    return out


def _shares_at_yearend(facts: dict, years: list[int]) -> dict[int, float]:
    """Year-end shares: cover-page dei:EntityCommonStockSharesOutstanding is the
    most reliable recent source (companies stop tagging the GAAP year-end count).
    Take the cover-page figure dated nearest AFTER each fiscal year-end."""
    from datetime import datetime
    pts = []
    units = facts.get("facts", {}).get("dei", {}).get(
        "EntityCommonStockSharesOutstanding", {}).get("units", {})
    for arr in units.values():
        for e in arr:
            if e.get("end") and e.get("val"):
                try:
                    pts.append((datetime.fromisoformat(e["end"]), float(e["val"])))
                except ValueError:
                    pass
    # GAAP year-end count fallback (older filings).
    gaap = _sec_annual(facts, "CommonStockSharesOutstanding", instant=True)
    out: dict[int, float] = {}
    for y in years:
        ye = datetime(y, 12, 31)
        after = [(d, v) for d, v in pts if 0 <= (d - ye).days <= 110]
        if after:
            out[y] = min(after, key=lambda x: (x[0] - ye).days)[1]
        elif y in gaap:
            out[y] = gaap[y]
        else:
            before = [(d, v) for d, v in pts if (ye - d).days >= 0]
            if before:
                out[y] = max(before, key=lambda x: x[0])[1]
    return out


def _per_share_by_year(cik: int | None, years: list[int]) -> dict[int, dict]:
    """{year: {eps, dps, bvps, tbvps, shares}} from SEC (HoldCo)."""
    if not cik:
        return {}
    facts = sec_client.fetch_company_facts(cik)
    if not facts:
        return {}
    eps = _sec_annual(facts, "EarningsPerShareDiluted", instant=False)
    dps = _sec_annual(facts, "CommonStockDividendsPerShareDeclared", instant=False)
    equity = _sec_annual(facts, "StockholdersEquity", instant=True)
    shares = _shares_at_yearend(facts, years)
    goodwill = _sec_annual(facts, "Goodwill", instant=True)
    intang = _sec_annual(facts, "IntangibleAssetsNetExcludingGoodwill", instant=True)
    incl = _sec_annual(facts, "IntangibleAssetsNetIncludingGoodwill", instant=True)

    out: dict[int, dict] = {}
    for yr in set(list(eps) + list(dps) + list(equity) + list(shares) + list(years)):
        sh = shares.get(yr)
        eq = equity.get(yr)
        bvps = (eq / sh) if (eq and sh) else None
        adj = None
        gw = goodwill.get(yr)
        if gw is not None:
            adj = gw + (intang.get(yr) or 0)
        elif incl.get(yr) is not None:
            adj = incl.get(yr)
        tbvps = ((eq - adj) / sh) if (eq and sh and adj is not None) else bvps
        out[yr] = {"eps": eps.get(yr), "dps": dps.get(yr),
                   "bvps": bvps, "tbvps": tbvps, "shares": sh}
    return out


# ── the table ───────────────────────────────────────────────────────────────
def render_financial_highlights(ticker: str):
    """Render the multi-year financial-highlights table for one bank."""
    info = get_bank_info(ticker)
    name = info.get("name") if info else ticker
    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None

    st.markdown(f"### {name} ({ticker}) — Financial Highlights")
    st.caption("Fiscal-year figures from FDIC Call Reports; per-share from SEC "
               "filings (holding company). $ amounts scaled to B/M.")

    if not cert:
        st.info("No FDIC Call Report data mapped for this bank.")
        return

    with st.spinner("Loading multi-year financials…"):
        hist = fdic_client.get_historical_financials(cert, quarters=28)
    if hist is None or hist.empty:
        st.info("No FDIC history available.")
        return

    hist = hist.copy()
    hist["_y"] = hist["REPDTE"].apply(_year)
    hist["_m"] = hist["REPDTE"].apply(_month)
    ye = hist[hist["_m"] == 12].dropna(subset=["_y"])
    years = sorted({int(y) for y in ye["_y"]})[-5:]
    rec = {int(y): ye[ye["_y"] == y].iloc[0].to_dict() for y in years}

    # Latest quarter as a "current" column if it's newer than the last year-end.
    latest = hist.sort_values("REPDTE").iloc[-1].to_dict()
    show_latest = _month(latest.get("REPDTE")) != 12
    cols = list(years) + (["LTM"] if show_latest else [])
    col_rec = dict(rec)
    if show_latest:
        col_rec["LTM"] = latest

    persh = _per_share_by_year(cik, years)

    def _roatce(r):
        ni, eq, intan = _num(r.get("NETINC")), _num(r.get("EQTOT")), _num(r.get("INTAN")) or 0
        tce = (eq - intan) if eq is not None else None
        return f"{ni/tce*100:.2f}%" if (ni is not None and tce and tce > 0) else "—"

    def _roae(r):
        return _ratio_pct(r.get("NETINC"), r.get("EQTOT"))

    # (section, [(label, fn)])
    sections = [
        ("Balance Sheet", [
            ("Total assets", lambda r: _usd(r.get("ASSET"))),
            ("Net loans", lambda r: _usd(r.get("LNLSNET"))),
            ("Total deposits", lambda r: _usd(r.get("DEP"))),
            ("Total equity", lambda r: _usd(r.get("EQTOT"))),
            ("Securities", lambda r: _usd(r.get("SC"))),
        ]),
        ("Profitability", [
            ("Net income", lambda r: _usd(r.get("NETINC"))),
            ("ROAA", lambda r: _pct(r.get("ROA"))),
            ("ROAE", _roae),
            ("ROATCE", _roatce),
            ("Net interest margin", lambda r: _pct(r.get("NIMY"))),
            ("Efficiency ratio", lambda r: _pct(r.get("EEFFR"))),
        ]),
        ("Balance Sheet Ratios", [
            ("Loans / deposits", lambda r: _ratio_pct(r.get("LNLSNET"), r.get("DEP"))),
            ("Securities / assets", lambda r: _ratio_pct(r.get("SC"), r.get("ASSET"))),
            ("Equity / assets", lambda r: _ratio_pct(r.get("EQTOT"), r.get("ASSET"))),
            ("Tang. common equity / tang. assets",
             lambda r: _ratio_pct((_num(r.get("EQTOT")) or 0) - (_num(r.get("INTAN")) or 0),
                                  (_num(r.get("ASSET")) or 0) - (_num(r.get("INTAN")) or 0))),
        ]),
        ("Asset Quality", [
            ("NPLs / loans", lambda r: _pct(r.get("NCLNLSR"))),
            ("Net charge-offs / loans", lambda r: _pct(r.get("NTLNLSR"))),
            ("Loan-loss reserves / loans", lambda r: _pct(r.get("LNATRESR"))),
        ]),
        ("Capital Adequacy", [
            ("CET1 ratio", lambda r: _pct(r.get("IDT1CER"))),
            ("Total capital ratio", lambda r: _pct(r.get("RBCRWAJ"))),
            ("Leverage ratio", lambda r: _pct(r.get("RBCT1JR"))),
        ]),
        ("Per Share (HoldCo)", [
            ("Diluted EPS", lambda r, y=None: _dollars_ps((persh.get(y) or {}).get("eps"))),
            ("Dividends / share", lambda r, y=None: _dollars_ps((persh.get(y) or {}).get("dps"))),
            ("Book value / share", lambda r, y=None: _dollars_ps((persh.get(y) or {}).get("bvps"))),
            ("Tangible BV / share", lambda r, y=None: _dollars_ps((persh.get(y) or {}).get("tbvps"))),
            ("Shares outstanding", lambda r, y=None: _count((persh.get(y) or {}).get("shares"))),
        ]),
    ]

    # Build HTML table.
    def _hdr(y):
        return f"FY{y}" if isinstance(y, int) else "Latest"
    head = ('<tr><th style="text-align:left; padding:5px 10px;">($ scaled)</th>'
            + "".join(f'<th style="text-align:right; padding:5px 12px; '
                      f'font-weight:600; color:#0f172a;">{_hdr(y)}</th>' for y in cols)
            + "</tr>")
    body = []
    for sec_name, rows in sections:
        body.append(
            f'<tr><td colspan="{len(cols)+1}" style="padding:9px 10px 3px; '
            f'font-weight:700; color:#1e3a8a; font-size:0.82rem; '
            f'text-transform:uppercase; letter-spacing:0.03em;">{sec_name}</td></tr>')
        for label, fn in rows:
            cells = []
            for y in cols:
                r = col_rec.get(y, {})
                try:
                    v = fn(r, y) if fn.__code__.co_argcount == 2 else fn(r)
                except Exception:
                    v = "—"
                cells.append(f'<td style="text-align:right; padding:4px 12px; '
                             f'color:#1d4ed8;">{v}</td>')
            body.append(
                f'<tr><td style="text-align:left; padding:4px 10px; '
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
