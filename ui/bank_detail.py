"""
Bank detail page — deep dive on a single bank.
"""

import html as _html

import pandas as pd
import streamlit as st

from config import METRICS, METRICS_BY_KEY, METRIC_CATEGORIES
from data.bank_mapping import get_name, get_bank_info, get_ir_url
from data import fdic_client, sec_client
from data.ibkr_client import get_ibkr_client
from analysis.peer_comparison import build_radar_data, get_peer_group_by_asset_size
from utils.formatting import format_value
from ui.charts import (
    price_chart, metrics_trend_chart, peer_radar_chart, balance_sheet_chart,
    asset_composition_chart, loan_mix_chart, funding_mix_chart,
    growth_trend_chart, loans_deposits_chart,
)

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# Shared null-safe float (utils/formatting), kept under the local name.
from utils.formatting import num as _num


def _usd_b(v):
    """Dollars → $X.XB / $XXX.XM."""
    v = _num(v)
    if v is None:
        return None
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _usd_b_thou(v):
    """FDIC $thousands → $X.XB / $XXX.XM."""
    v = _num(v)
    return _usd_b(v * 1000) if v is not None else None


def _fy_end(mmdd):
    """SEC fiscalYearEnd 'MMDD' → 'Dec 31'."""
    if not mmdd or len(str(mmdd)) != 4:
        return None
    try:
        mm, dd = int(str(mmdd)[:2]), int(str(mmdd)[2:])
        return f"{_MONTHS[mm]} {dd}"
    except (ValueError, IndexError):
        return None


def _phone(p):
    digits = "".join(ch for ch in str(p or "") if ch.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return p or None


def _kv_table(title, pairs):
    """Compact label/value reference table; rows with empty values are dropped."""
    rows = [(l, v) for l, v in pairs if v not in (None, "", "—")]
    if not rows:
        return ""
    body = "".join(
        f'<div class="lg-row"><span class="lg-label">{l}</span>'
        f'<span class="lg-val">{v}</span></div>'
        for l, v in rows)
    # ksk-ledger (DESIGN-SYSTEM.md): boxless hairline rows, tokens only.
    return f'<div class="ksk-ledger"><div class="lg-title">{title}</div>{body}</div>' 


def _render_valuation_performance_tables(row, fdic_rec=None):
    """Valuation + Performance as two side-by-side reference tables, matching the
    Market Data / Company Profile format above (consistent, dense).

    Performance ratios read from a live FDIC record (passed in) rather than the
    batch metrics row — the batch build can silently drop FDIC fields on a
    transient API failure, which left this column blank."""
    fdic_rec = fdic_rec or {}

    def disp(key):
        m = METRICS_BY_KEY.get(key, {})
        v = row.get(key)
        return (format_value(v, m.get("format", "number"), m.get("decimals", 2))
                if v is not None and not pd.isna(v) else None)

    def _fd_pct(field):
        v = _num(fdic_rec.get(field))
        return f"{v:.2f}%" if v is not None else None

    chg = _num(row.get("change_pct"))
    chg_html = None
    if chg is not None:
        c = "var(--success)" if chg >= 0 else "var(--danger)"
        chg_html = f'<span style="color:{c};">{chg:+.2f}%</span>'

    valuation = [
        ("Last Price", disp("price")),
        ("Change", chg_html),
        ("Market Cap", disp("market_cap")),
        ("P/E (LTM)", disp("pe_ratio")),
        ("EPS (TTM)", disp("eps")),
        ("P/TBV", disp("ptbv_ratio")),
        ("TBV / Share", disp("tbvps")),
        ("Dividend Yield", disp("dividend_yield")),
    ]

    # ROATCE: prefer the engine's blended figure; fall back to an annualized
    # figure computed straight from the live FDIC record.
    roatce_v = disp("roatce_blended")
    if roatce_v is None and fdic_rec:
        ni = _num(fdic_rec.get("NETINC")); eq = _num(fdic_rec.get("EQTOT"))
        intan = _num(fdic_rec.get("INTAN")) or 0
        mo = 12
        try:
            mo = pd.to_datetime(fdic_rec.get("REPDTE")).month or 12
        except Exception:
            pass
        tce = (eq - intan) if eq is not None else None
        if ni is not None and tce and tce > 0:
            roatce_v = f"{ni * (12.0 / mo) / tce * 100:.2f}%"

    performance = [
        ("ROATCE", roatce_v),
        ("ROAA", _fd_pct("ROA")),
        ("Net Interest Margin", _fd_pct("NIMY")),
        ("Efficiency Ratio", _fd_pct("EEFFR")),
        ("CET1 Ratio", _fd_pct("IDT1CER")),
        ("NPL Ratio", _fd_pct("NCLNLSR")),
    ]
    return _kv_table("Valuation", valuation), _kv_table("Performance", performance)


def _fmt_repdte(v):
    try:
        return pd.to_datetime(v).strftime("%b %Y")
    except Exception:
        return str(v)


def _render_financial_highlights_table(ticker, info):
    """SNL-style Financial Highlights — key FDIC figures for the latest quarter vs
    a year ago, side by side."""
    cert = info.get("fdic_cert") if info else None
    if not cert:
        return
    try:
        df = fdic_client.get_historical_financials(cert, quarters=8)
    except Exception:
        return
    if df is None or df.empty or "REPDTE" not in df.columns:
        return
    df = df.sort_values("REPDTE")
    latest = df.iloc[-1]
    prior = df.iloc[-5] if len(df) >= 5 else None

    def num(rec, f):
        if rec is None:
            return None
        return _num(rec.get(f))

    def bil(rec, f):
        v = num(rec, f)
        return f"${v/1e6:.2f}B" if v is not None else "—"

    def pct(rec, f):
        v = num(rec, f)
        return f"{v:.2f}%" if v is not None else "—"

    def tce_ta(rec):
        eq, intan, asset = num(rec, "EQTOT"), (num(rec, "INTAN") or 0), num(rec, "ASSET")
        if eq is None or asset is None or (asset - intan) == 0:
            return "—"
        return f"{(eq - intan) / (asset - intan) * 100:.2f}%"

    rows = [
        ("Total Assets", bil(prior, "ASSET"), bil(latest, "ASSET")),
        ("Total Deposits", bil(prior, "DEP"), bil(latest, "DEP")),
        ("Net Loans", bil(prior, "LNLSNET"), bil(latest, "LNLSNET")),
        ("Total Equity", bil(prior, "EQTOT"), bil(latest, "EQTOT")),
        ("TCE / Tangible Assets", tce_ta(prior), tce_ta(latest)),
        ("LTM ROAA", pct(prior, "ROA"), pct(latest, "ROA")),
        ("LTM ROAE", pct(prior, "ROE"), pct(latest, "ROE")),
        ("Net Interest Margin", pct(prior, "NIMY"), pct(latest, "NIMY")),
        ("Efficiency Ratio", pct(prior, "EEFFR"), pct(latest, "EEFFR")),
        ("CET1 Ratio", pct(prior, "IDT1CER"), pct(latest, "IDT1CER")),
        ("NPL Ratio", pct(prior, "NCLNLSR"), pct(latest, "NCLNLSR")),
        ("NCO Ratio", pct(prior, "NTLNLSR"), pct(latest, "NTLNLSR")),
        ("Reserves / Loans", pct(prior, "LNATRESR"), pct(latest, "LNATRESR")),
    ]
    p_lbl = _fmt_repdte(prior["REPDTE"]) if prior is not None else "Prior"
    l_lbl = _fmt_repdte(latest["REPDTE"])
    body = "".join(
        f'<tr style="border-bottom:1px solid rgba(148,163,184,0.10);">'
        f'<td style="padding:3px 2px;color:#334155;font-size:0.82rem;">{lbl}</td>'
        f'<td style="padding:3px 8px;text-align:right;color:var(--text-secondary);font-size:0.82rem;">{pv}</td>'
        f'<td style="padding:3px 2px;text-align:right;font-weight:600;color:var(--text-primary);'
        f'font-size:0.82rem;">{lv}</td></tr>'
        for lbl, pv, lv in rows)
    st.markdown(
        '<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.04em;'
        'color:var(--brand-hover);font-weight:700;margin:0 0 3px;">Financial Highlights</div>'
        '<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr><th></th>'
        f'<th style="text-align:right;padding:2px 8px;color:var(--text-muted);font-size:0.72rem;font-weight:600;">{p_lbl}</th>'
        f'<th style="text-align:right;padding:2px 2px;color:var(--brand-hover);font-size:0.72rem;font-weight:700;">{l_lbl}</th>'
        f'</tr></thead><tbody>{body}</tbody></table>',
        unsafe_allow_html=True)


def _render_latest_activity(ticker, info):
    """SNL-style Latest Activity — recent first-party news + recent filings."""
    from data.events.wire_base import is_safe_news_url, is_routine_noise
    evs = []
    try:
        from data.events import get_recent_events
        evs = [e for e in get_recent_events(ticker, limit=12)
               if is_safe_news_url(e.get("url")) and not is_routine_noise(e.get("headline"))][:6]
    except Exception:
        evs = []

    docs = []
    cik = info.get("cik") if info else None
    if cik:
        try:
            fi = sec_client.get_filing_info(cik) or {}
            docs = (fi.get("recent_filings") or [])[:8]
        except Exception:
            docs = []

    c_news, c_docs = st.columns(2)
    with c_news:
        st.markdown('<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.04em;'
                    'color:var(--brand-hover);font-weight:700;margin:0 0 3px;">Latest News</div>',
                    unsafe_allow_html=True)
        if evs:
            rows = []
            for e in evs:
                h = _html.escape((e.get("headline") or "")[:90])
                url = e.get("url")
                link = (f'<a href="{_html.escape(str(url))}" target="_blank" '
                        f'style="color:var(--text-primary);text-decoration:none;">{h}</a>') if url else h
                rows.append(f'<div style="padding:3px 0;border-bottom:1px solid rgba(148,163,184,0.10);'
                            f'font-size:0.82rem;line-height:1.3;">{link}</div>')
            st.markdown("".join(rows), unsafe_allow_html=True)
        else:
            st.caption("No recent company news.")
    with c_docs:
        st.markdown('<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.04em;'
                    'color:var(--brand-hover);font-weight:700;margin:0 0 3px;">Recent Filings</div>',
                    unsafe_allow_html=True)
        if docs:
            rows = []
            for f in docs:
                acc = (f.get("accession") or "").replace("-", "")
                url = f.get("url") or (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}" if acc else "")
                label = f"{f.get('form','')} — {f.get('date','')}"
                link = (f'<a href="{_html.escape(str(url))}" target="_blank" '
                        f'style="color:var(--text-primary);text-decoration:none;">{_html.escape(label)}</a>') if url else _html.escape(label)
                rows.append(f'<div style="padding:3px 0;border-bottom:1px solid rgba(148,163,184,0.10);'
                            f'font-size:0.82rem;">{link}</div>')
            st.markdown("".join(rows), unsafe_allow_html=True)
        else:
            st.caption("No recent filings.")


def _render_snapshot(ticker, info, name, row, fdic_rec=None):
    """Capital-IQ-style snapshot: identity line, quick links, and a two-column
    Market Data / Company Profile block built from the data we already pull."""
    cik = info.get("cik") if info else None
    cert = info.get("fdic_cert") if info else None

    filing = {}
    if cik:
        try:
            filing = sec_client.get_filing_info(cik) or {}
        except Exception:
            filing = {}
    quote = {}
    try:
        from data.fmp_client import get_quote
        quote = get_quote(ticker) or {}
    except Exception:
        quote = {}
    if fdic_rec is None:
        fdic_rec = {}
        if cert:
            try:
                fdic_rec = fdic_client.get_latest_financials(cert) or {}
            except Exception:
                fdic_rec = {}
    fund = {}
    if cik:
        try:
            fund = sec_client.get_latest_fundamentals(cik) or {}
        except Exception:
            fund = {}

    # 52-week range + average volume from a 1-year history (cached).
    wk_hi = wk_lo = avg_vol = None
    try:
        from data.fmp_client import get_history
        h1y = get_history(ticker, "1Y")
        if h1y is not None and not h1y.empty:
            if "high" in h1y and h1y["high"].notna().any():
                wk_hi = float(h1y["high"].max())
            if "low" in h1y and h1y["low"].notna().any():
                wk_lo = float(h1y["low"].min())
            if "volume" in h1y and h1y["volume"].notna().any():
                avg_vol = float(h1y["volume"].tail(63).mean())  # ~3 trading months
    except Exception:
        pass

    # ── Identity sub-line ──────────────────────────────────────────────
    exch = (filing.get("exchanges") or [None])[0]
    ident_bits = []
    if exch:
        ident_bits.append(f"{exch}: {ticker}")
    if filing.get("sic_description"):
        ident_bits.append(filing["sic_description"].title())
    if filing.get("hq_city") and filing.get("hq_state"):
        ident_bits.append(f"HQ: {filing['hq_city'].title()}, {filing['hq_state']}")
    # ── Identifier row for the SNL title bar (links, no emojis) ─────────
    def _lnk(label, url):
        return f'<a href="{url}" target="_blank">{label}</a>'

    id_links = []
    tenk = next((f for f in filing.get("recent_filings", []) if f["form"].startswith("10-K")), None)
    tenq = next((f for f in filing.get("recent_filings", []) if f["form"].startswith("10-Q")), None)
    if tenk and tenk.get("url"):
        id_links.append(_lnk("10-K", tenk["url"]))
    if tenq and tenq.get("url"):
        id_links.append(_lnk("10-Q", tenq["url"]))
    if cik:
        id_links.append(_lnk(f"CIK {cik}", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                                           f"&CIK={cik}&type=&dateb=&owner=include&count=40"))
    if cert:
        id_links.append(_lnk(f"FDIC {cert}", f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"))
        id_links.append(_lnk("FFIEC", "https://cdr.ffiec.gov/public/ManageFacsimiles.aspx"))
    ir = get_ir_url(ticker)
    if ir:
        id_links.append(_lnk("IR", ir))
    ids_html = " · ".join(ident_bits + id_links)

    # ── Market Data + Company Profile (two columns) ────────────────────
    price = _num(quote.get("price")) if quote.get("price") is not None else _num(row.get("price"))
    prev = _num(quote.get("close"))
    chg = _num(quote.get("change")); chg_pct = _num(quote.get("change_pct"))
    if chg_pct is None:
        chg_pct = _num(row.get("change_pct"))
    o = _num(quote.get("open")); hi = _num(quote.get("high")); lo = _num(quote.get("low"))
    vol = _num(quote.get("volume")) or _num(row.get("volume"))
    shares = _num(fund.get("shares_outstanding"))
    dy = _num(row.get("dividend_yield"))
    mcap = _num(row.get("market_cap"))

    chg_html = None
    if chg is not None and chg_pct is not None:
        c = "var(--success)" if chg >= 0 else "var(--danger)"
        chg_html = f'<span style="color:{c};">{chg:+.2f} ({chg_pct:+.2f}%)</span>'
    elif chg_pct is not None:
        c = "var(--success)" if chg_pct >= 0 else "var(--danger)"
        chg_html = f'<span style="color:{c};">{chg_pct:+.2f}%</span>'

    market = [
        ("Last Price", f"${price:,.2f}" if price is not None else None),
        ("Change", chg_html),
        ("Previous Close", f"${prev:,.2f}" if prev is not None else None),
        ("Open", f"${o:,.2f}" if o is not None else None),
        ("Day Range", f"${lo:,.2f} – ${hi:,.2f}" if (lo is not None and hi is not None) else None),
        ("52-Week Range", f"${wk_lo:,.2f} – ${wk_hi:,.2f}" if (wk_lo and wk_hi) else None),
        ("Volume", f"{vol:,.0f}" if vol is not None else None),
        ("Avg Volume (3M)", f"{avg_vol:,.0f}" if avg_vol else None),
        ("Market Cap", _usd_b(mcap)),
        ("Shares Outstanding", f"{shares:,.0f}" if shares else None),
        ("Dividend Yield", f"{dy:.2f}%" if dy is not None else None),
    ]

    web = filing.get("website") or ""
    if web and not web.startswith("http"):
        web = "https://" + web
    web_html = (f'<a href="{web}" target="_blank" style="color:var(--brand-hover);">'
                f'{filing["website"]}</a>') if web else None
    hq = None
    if filing.get("hq_city") and filing.get("hq_state"):
        hq = f"{filing['hq_city'].title()}, {filing['hq_state']} {filing.get('hq_zip','')}".strip()

    company = [
        ("Industry", (filing.get("sic_description") or "").title() or None),
        ("Exchange", exch),
        ("State of Incorp.", filing.get("state_of_incorp")),
        ("Fiscal Year End", _fy_end(filing.get("fiscal_year_end"))),
        ("Headquarters", hq),
        ("Phone", _phone(filing.get("phone"))),
        ("Website", web_html),
        ("Total Assets", _usd_b_thou(fdic_rec.get("ASSET"))),
        ("Total Deposits", _usd_b_thou(fdic_rec.get("DEP"))),
        ("Net Loans", _usd_b_thou(fdic_rec.get("LNLSNET"))),
        ("Total Equity", _usd_b_thou(fdic_rec.get("EQTOT"))),
        ("CIK", str(cik) if cik else None),
        ("FDIC Cert", str(cert) if cert else None),
    ]

    # Return the two ledgers + the title-bar identifier row so the caller can
    # pack a single 4-across row with Valuation + Performance.
    return _kv_table("Market Data", market), _kv_table("Company Profile", company), ids_html


def _valuation_history_chart(ticker: str, info: dict):
    """Quarter-end P/TBV and P/E over the last ~3 years, dual-axis. Price from
    market history ÷ per-share book/earnings from SEC filings."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from utils.chart_style import apply_standard_layout, COLOR_PRIMARY, COLOR_WARNING
    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None
    if not cik:
        return None
    try:
        from ui.financial_highlights import _per_share_for_ends
        from data.fmp_client import get_history
    except Exception:
        return None

    ends = []
    if cert:
        fh = fdic_client.get_historical_financials(cert, quarters=20)
        if fh is not None and not fh.empty:
            ds = pd.to_datetime(fh["REPDTE"]).dropna().sort_values()
            ends = [d.to_pydatetime() for d in ds][-12:]
    if not ends:
        return None

    try:
        ps = _per_share_for_ends(cik, ends, quarterly=False)
        px = get_history(ticker, "5Y")
    except Exception:
        return None
    if px is None or px.empty or "close" not in px.columns:
        return None
    px = px.dropna(subset=["close"]).copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px = px.dropna(subset=["date"]).sort_values("date")

    dates, ptbvs, pes = [], [], []
    for e in ends:
        rec = ps.get(e) or {}
        tbvps, eps = rec.get("tbvps"), rec.get("eps")
        sub = px[px["date"] <= pd.Timestamp(e)]
        if sub.empty:
            continue
        price = float(sub["close"].iloc[-1])
        dates.append(e)
        ptbvs.append(price / tbvps if (tbvps and tbvps > 0) else None)
        pes.append(price / eps if (eps and eps > 0) else None)
    if not dates or all(v is None for v in ptbvs):
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=dates, y=ptbvs, name="P/TBV", mode="lines+markers",
        connectgaps=True, line=dict(color=COLOR_PRIMARY, width=2), marker=dict(size=5),
        hovertemplate="%{x|%b %Y}<br>P/TBV %{y:.2f}x<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=dates, y=pes, name="P/E", mode="lines+markers",
        connectgaps=True, line=dict(color=COLOR_WARNING, width=2), marker=dict(size=5),
        hovertemplate="%{x|%b %Y}<br>P/E %{y:.1f}x<extra></extra>"), secondary_y=True)
    apply_standard_layout(fig, title="P/TBV & P/E — quarter-end", height=420,
                          show_legend=True, hovermode="x unified")
    fig.update_yaxes(title_text="P/TBV", secondary_y=False, ticksuffix="x")
    fig.update_yaxes(title_text="P/E", secondary_y=True, ticksuffix="x", showgrid=False)
    return fig


def _render_price_panel(ticker: str):
    """Interactive price + volume chart with a period selector. Rendered in the
    snapshot band's open right column so it fills the space beside the ledgers;
    its natural height lines up with the ledger block."""
    st.markdown("**Price**")
    per = st.radio("Period", ["1M", "3M", "1Y", "5Y"], index=2, horizontal=True,
                   key=f"ov_price_per_{ticker}", label_visibility="collapsed")
    hist_df = pd.DataFrame()
    try:
        from data.fmp_client import get_history
        hist_df = get_history(ticker, per)
    except Exception:
        pass
    st.plotly_chart(price_chart(hist_df, ticker), use_container_width=True,
                    key=f"ov_price_{ticker}")


def _render_valuation_panel(ticker: str, info: dict):
    """Quarter-end P/TBV & P/E history chart, shown below the snapshot band."""
    st.markdown("**Valuation — P/TBV & P/E**")
    fig = _valuation_history_chart(ticker, info)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, key=f"ov_val_{ticker}")
    else:
        st.caption("Valuation history unavailable for this bank.")


def render_corporate_profile(ticker: str, all_metrics_df: pd.DataFrame):
    """Overview ▸ Corporate Profile — identity snapshot, market + company data,
    quick links, and the valuation/performance key-stat cards."""
    info = get_bank_info(ticker)
    name = info["name"] if info else ticker

    bank_row = all_metrics_df[all_metrics_df["ticker"] == ticker]
    if bank_row.empty:
        st.info("No metrics available for this bank yet.")
        return
    row = bank_row.iloc[0]
    # Fetch the latest FDIC record once and share it — the snapshot's Company
    # Profile and the Performance table both read from it (live, not the batch
    # metrics row which can drop FDIC fields on a transient API failure).
    cert = info.get("fdic_cert") if info else None
    fdic_rec = {}
    if cert:
        try:
            fdic_rec = fdic_client.get_latest_financials(cert) or {}
        except Exception:
            fdic_rec = {}
    # Capital-IQ-style snapshot: identity + quick links, then two stacked pairs
    # of reference tables — Market Data over Performance (col 1) and Valuation
    # over Company Profile (col 2). Keep the original four-column widths so each
    # table stays exactly its prior size; only their positions change. Each pair
    # is one markdown block so the only break between the two tables is the lower
    # table's heading (no Streamlit inter-block gap).
    mkt_html, co_html, ids_html = _render_snapshot(ticker, info, name, row, fdic_rec)
    from ui.chrome import title_bar
    title_bar(f"{name} ({ticker})", "Corporate Profile", ids_html)
    val_html, perf_html = _render_valuation_performance_tables(row, fdic_rec)
    _gap = '<div style="margin-top:0.5rem"></div>'  # heading-sized break only
    # Two stacked-pair ledger columns at 1/6 width each, the price chart filling
    # the open space beside them (next third), and the far right left empty. The
    # chart's natural height lines up with the ledger block.
    _cols = st.columns([1, 1, 2, 2])
    _cols[0].markdown(mkt_html + _gap + perf_html, unsafe_allow_html=True)
    _cols[1].markdown(val_html + _gap + co_html, unsafe_allow_html=True)
    with _cols[2]:
        _render_price_panel(ticker)
    st.markdown(
        '<div style="margin-top:5px; font-size:0.75rem; color:var(--text-secondary);">'
        'Sources: SEC filings (EDGAR) &nbsp;·&nbsp; FDIC Call Report &nbsp;·&nbsp; '
        'FMP (market data)</div>', unsafe_allow_html=True)
    st.markdown("---")
    # Valuation-multiple history below the snapshot, kept to ~half width so it
    # doesn't stretch across the full page.
    _vc, _ = st.columns(2)
    with _vc:
        _render_valuation_panel(ticker, info)
    st.markdown("---")
    # Highlights (year-ago vs latest) beside the activity feed so both fill the
    # width instead of each spreading across the page.
    _hl, _act = st.columns([1, 2])
    with _hl:
        _render_financial_highlights_table(ticker, info)
    with _act:
        _render_latest_activity(ticker, info)

    # Click-through to the primary data sources for this bank.
    cik = info.get("cik") if info else None
    cert = info.get("fdic_cert") if info else None
    links = []
    if cik:
        links.append(
            f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
            f'&CIK={cik}&type=10-K&dateb=&owner=include&count=40" target="_blank" '
            'style="text-decoration:none;">SEC filings (EDGAR)</a>')
    if cert:
        links.append(
            f'<a href="https://banks.data.fdic.gov/bankfind-suite/bankfind/details/'
            f'{cert}" target="_blank" style="text-decoration:none;">FDIC BankFind</a>')
    links.append('<span title="Price, change, market cap, P/E and the price chart">'
                 'FMP (market data)</span>')
    if links:
        st.markdown(
            '<div style="margin-top:7px; font-size:0.8rem; color:var(--text-secondary);">'
            'Sources: ' + " &nbsp;·&nbsp; ".join(links) + "</div>",
            unsafe_allow_html=True,
        )


def render_price_trends(ticker: str, all_metrics_df: pd.DataFrame = None):
    """Overview ▸ Price & Trends — price chart plus the FDIC metric and balance-
    sheet trend charts."""
    info = get_bank_info(ticker)

    # ── Price chart ──────────────────────────────────────────────────
    st.subheader("Price History")
    duration_options = {"1W": "1 W", "1M": "1 M", "3M": "3 M", "1Y": "1 Y", "5Y": "5 Y"}
    selected_duration = st.radio(
        "Period", list(duration_options.keys()), horizontal=True, key="price_period"
    )

    # Try IBKR first (when running locally with TWS); fall back to FMP
    # (works in cloud + offline IBKR).
    ibkr = get_ibkr_client()
    hist_df = pd.DataFrame()
    if ibkr.connected:
        duration_str = duration_options[selected_duration]
        bar_size = "1 day" if selected_duration in ("3M", "1Y", "5Y") else "1 hour" if selected_duration == "1M" else "15 mins"
        hist_df = ibkr.get_historical_data(ticker, duration_str, bar_size)
    if hist_df is None or hist_df.empty:
        try:
            from data.fmp_client import get_history
            hist_df = get_history(ticker, selected_duration)
        except Exception as e:
            print(f"[bank_detail] FMP history fallback failed: {e}")
            hist_df = pd.DataFrame()

    # Constrain to ~70% width (a full-width chart is too stretched to read) and
    # use the remaining space for period stats.
    _chart_col, _stats_col = st.columns([7, 3])
    with _chart_col:
        st.plotly_chart(price_chart(hist_df, ticker), use_container_width=True)
    with _stats_col:
        _render_price_stats(hist_df)

    # ── FDIC metrics trend ──────────────────────────────────────────
    st.subheader("Key Metrics Trend")
    cert = info["fdic_cert"] if info else None
    fdic_hist = pd.DataFrame()
    if cert:
        fdic_hist = fdic_client.get_historical_financials(cert, quarters=20)

    # One metric per chart (separate axes — different scales), all on one row.
    _km = [("roaa", "ROAA"), ("nim", "Net Interest Margin"),
           ("npl_ratio", "NPL Ratio"), ("nco_ratio", "Net Charge-Off Ratio")]
    for _col, (_k, _lbl) in zip(st.columns(4), _km):
        with _col:
            st.plotly_chart(metrics_trend_chart(fdic_hist, [_k], _lbl), use_container_width=True)

    # ── Balance sheet trend + composition snapshots ─────────────────
    st.subheader("Balance Sheet")
    _bst, _ = st.columns([2, 1])  # the trend line doesn't need full width
    with _bst:
        st.plotly_chart(balance_sheet_chart(fdic_hist), use_container_width=True)

    st.markdown("**Composition & funding** — latest quarter")
    _bc1, _bc2, _bc3 = st.columns(3)
    with _bc1:
        st.plotly_chart(asset_composition_chart(fdic_hist), use_container_width=True)
    with _bc2:
        st.plotly_chart(loan_mix_chart(fdic_hist), use_container_width=True)
    with _bc3:
        st.plotly_chart(funding_mix_chart(fdic_hist), use_container_width=True)

    st.markdown("**Capital & growth**")
    _gc1, _gc2, _gc3 = st.columns(3)
    with _gc1:
        st.plotly_chart(
            metrics_trend_chart(fdic_hist, ["cet1_ratio", "total_capital_ratio", "leverage_ratio"],
                                "Capital Ratios"), use_container_width=True)
    with _gc2:
        st.plotly_chart(growth_trend_chart(fdic_hist), use_container_width=True)
    with _gc3:
        st.plotly_chart(loans_deposits_chart(fdic_hist), use_container_width=True)


def render_all_metrics_section(ticker: str, all_metrics_df: pd.DataFrame):
    """Overview ▸ All Metrics — the full metric grid, peer-comparison radar, and
    recent SEC filings list."""
    info = get_bank_info(ticker)
    bank_row = all_metrics_df[all_metrics_df["ticker"] == ticker]

    # ── All metrics (compact grid + explanations) ───────────────────
    if not bank_row.empty:
        _render_all_metrics(bank_row.iloc[0])

    # ── Peer comparison radar ───────────────────────────────────────
    st.subheader("Peer Comparison")
    peer_metrics = ["roatce", "nim", "cet1_ratio", "efficiency_ratio", "npl_ratio", "pe_ratio"]
    peers = get_peer_group_by_asset_size(all_metrics_df, ticker, n=4)
    compare_tickers = [ticker] + peers

    radar = build_radar_data(all_metrics_df, compare_tickers, peer_metrics)
    st.plotly_chart(peer_radar_chart(radar), use_container_width=True)

    # ── SEC filings ─────────────────────────────────────────────────
    st.subheader("Recent SEC Filings")
    cik = info["cik"] if info else None
    if cik:
        filing_info = sec_client.get_filing_info(cik)
        if filing_info and filing_info.get("recent_filings"):
            for f in filing_info["recent_filings"]:
                accession_clean = f["accession"].replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}"
                st.markdown(f"- **{f['form']}** — {f['date']} — [{f.get('description', 'View')}]({url})")
        else:
            st.info("No recent filings found.")
    else:
        st.info("SEC CIK not mapped for this bank.")


def render_bank_detail(ticker: str, all_metrics_df: pd.DataFrame):
    """Full single-bank detail — all Overview sections stacked. Kept for any
    caller that wants the whole page; the company nav calls the sections directly."""
    render_corporate_profile(ticker, all_metrics_df)
    st.markdown("---")
    render_price_trends(ticker, all_metrics_df)
    render_all_metrics_section(ticker, all_metrics_df)


# One-line explanation per metric category.
_CATEGORY_DESC = {
    "Market": "Live price and trading data (FMP).",
    "Valuation": "What you pay per dollar of earnings and tangible book value.",
    "Fair Value": "Model estimate of intrinsic value vs the current price.",
    "Profitability": "Returns on assets/equity, margin, and cost efficiency.",
    "Credit Quality": "Problem loans, charge-offs, and reserve coverage.",
    "Capital": "Regulatory capital ratios — the loss-absorbing cushion.",
    "Balance Sheet": "Size of the balance sheet — assets, loans, deposits, equity.",
    "Loan Mix": "How the loan book is split across categories.",
    "Loan Concentration": "Exposure to specific lending segments (e.g. CRE).",
    "Deposits": "Deposit base size and composition.",
    "Deposit Ratios": "Funding-quality ratios (non-interest, uninsured, brokered).",
    "Capital Dynamics": "Capital generation and buyback capacity.",
    "Capital Return": "Dividends and buybacks returned to shareholders.",
    "Credit Dynamics": "Direction and alerts in credit quality.",
    "Deposit Dynamics": "Deposit beta and cost-of-funds trends.",
    "Securities": "Investment securities portfolio.",
    "Composition": "Asset/liability composition shares.",
    "Credit Detail": "Detailed credit and past-due breakdowns.",
    "Income": "Income-statement lines.",
    "Operational": "Operating and efficiency measures.",
    "NIM Metrics": "Net interest margin drivers — asset yields and funding cost.",
}

# Short tooltip per metric key (hover the ⓘ). Only the ones worth explaining.
_METRIC_DESC = {
    "change_pct": "Price change vs the prior close.",
    "volume": "Shares traded.",
    "market_cap": "Shares outstanding × price.",
    "eps": "Trailing-12-month diluted EPS (SEC).",
    "pe_ratio": "Price ÷ TTM diluted EPS. Lower = cheaper on earnings.",
    "tbvps": "Tangible book value per share = (equity − intangibles) ÷ shares.",
    "ptbv_ratio": "Price ÷ tangible book value per share. 1.0× = trading at tangible book.",
    "dividend_yield": "TTM dividends per share ÷ price.",
    "roatce_blended": "Return on average tangible common equity, blended over trailing quarters.",
    "roatce": "Net income ÷ tangible common equity (equity − intangibles).",
    "roatce_normalized": "ROATCE with one-time items removed — the sustainable run-rate.",
    "earnings_distorted": "Flag: a non-recurring item distorted the latest earnings.",
    "fair_ptbv": "Warranted P/TBV = ROATCE ÷ cost of equity. The multiple the returns justify.",
    "fair_price": "Model fair value per share (warranted P/TBV × TBV/share).",
    "ptbv_discount": "How far the price sits below model fair value. Higher = cheaper.",
    "roaa": "Annualized net income ÷ average assets.",
    "roaa_4q": "Trailing-4-quarter ROAA (smoother).",
    "roatce_sub": "ROATCE at the bank subsidiary (FDIC Call Report).",
    "roatce_4q_sub": "Trailing-4-quarter bank-level ROATCE.",
    "roatce_holdco": "ROATCE at the holding company (SEC).",
    "nim": "Net interest income ÷ average earning assets.",
    "nim_4q": "Trailing-4-quarter NIM.",
    "efficiency_ratio": "Non-interest expense ÷ revenue. Lower = more efficient.",
    "npl_ratio": "Non-current loans (90+ days / nonaccrual) ÷ total loans.",
    "nco_ratio": "Annualized net charge-offs ÷ loans.",
    "allowance_loans": "Loan-loss reserves ÷ total loans (coverage).",
    "cet1_ratio": "Common equity tier 1 capital ÷ risk-weighted assets.",
    "total_capital_ratio": "Total risk-based capital ÷ risk-weighted assets.",
    "leverage_ratio": "Tier 1 capital ÷ average total assets.",
    "total_assets": "Total assets (Call Report).",
    "total_loans": "Net loans and leases.",
    "total_deposits": "Total deposits.",
    "total_equity": "Total bank equity capital.",
    "securities": "Investment securities (HTM + AFS).",
    "uninsured_dep_pct": "Uninsured deposits as a share of total — run-risk gauge.",
    "nonint_dep_pct": "Non-interest-bearing deposits ÷ total — low-cost, sticky funding.",
    "brokered_pct": "Brokered deposits ÷ total — flightier wholesale funding.",
    "loans_deposits": "Net loans ÷ deposits — a liquidity/funding gauge.",
}


def _render_all_metrics(row):
    """Compact metric grid grouped by category, with a one-line explanation per
    section and a hover tooltip (ⓘ) on the metrics worth explaining."""
    import pandas as _pd
    st.subheader("All Metrics")
    st.caption("Hover the ⓘ on a metric for its definition.")
    st.markdown(
        """<style>
        .m-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:6px;margin:1px 0 14px;}
        .m-card{background:rgba(148,163,184,0.05);border:1px solid rgba(148,163,184,0.16);
          border-radius:8px;padding:6px 10px;}
        .m-card .m-lbl{font-size:0.6rem;color:var(--text-secondary);font-weight:600;text-transform:uppercase;
          letter-spacing:0.02em;}
        .m-card .m-lbl .i{color:#b6c0cc;cursor:help;font-weight:400;}
        .m-card .m-val{font-size:0.96rem;font-weight:700;color:var(--text-primary);line-height:1.3;}
        .m-cat{font-weight:700;color:var(--brand-hover);font-size:0.78rem;text-transform:uppercase;
          letter-spacing:0.03em;margin-top:6px;}
        .m-cat-desc{font-size:0.76rem;color:var(--text-secondary);margin:0 0 4px;}
        </style>""",
        unsafe_allow_html=True,
    )
    for category in METRIC_CATEGORIES:
        cat_metrics = [m for m in METRICS if m["category"] == category]
        if not cat_metrics:
            continue
        cards = []
        for m in cat_metrics:
            val = row.get(m["key"])
            disp = (format_value(val, m["format"], m.get("decimals", 2))
                    if val is not None and not _pd.isna(val) else "—")
            desc = _METRIC_DESC.get(m["key"], "")
            tip = f' title="{desc}"' if desc else ""
            ic = ' <span class="i">ⓘ</span>' if desc else ""
            cards.append(
                f'<div class="m-card"{tip}><div class="m-lbl">{m["label"]}{ic}</div>'
                f'<div class="m-val">{disp}</div></div>')
        cdesc = _CATEGORY_DESC.get(category, "")
        cd = f'<div class="m-cat-desc">{cdesc}</div>' if cdesc else ""
        st.markdown(f'<div class="m-cat">{category}</div>{cd}'
                    f'<div class="m-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _render_price_stats(hist_df):
    """Compact period stats beside the price chart (fills the right column)."""
    if hist_df is None or hist_df.empty or "close" not in hist_df.columns:
        return
    d = hist_df.sort_values("date")
    close = d["close"].astype(float)
    last, first = float(close.iloc[-1]), float(close.iloc[0])
    hi = float(d["high"].max()) if ("high" in d.columns and d["high"].notna().any()) else float(close.max())
    lo = float(d["low"].min()) if ("low" in d.columns and d["low"].notna().any()) else float(close.min())
    chg = ((last - first) / first * 100) if first else 0.0
    chg_color = "var(--success)" if chg >= 0 else "var(--danger)"
    rows = [
        ("Last", f"${last:,.2f}"),
        ("Period change", f'<span style="color:{chg_color};">{chg:+.2f}%</span>'),
        ("Period high", f"${hi:,.2f}"),
        ("Period low", f"${lo:,.2f}"),
        ("Range", f"{((hi - lo) / lo * 100):.1f}%" if lo else "—"),
    ]
    if "volume" in d.columns and d["volume"].notna().any():
        avgv = float(d["volume"].mean())
        rows.append(("Avg volume", f"{avgv/1e6:.2f}M" if avgv >= 1e6 else f"{avgv:,.0f}"))
    from ui.chrome import ledger
    ledger("Period Stats", rows)


