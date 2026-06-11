"""
Bank detail page — deep dive on a single bank.
"""

import pandas as pd
import streamlit as st

from config import METRICS, METRICS_BY_KEY, METRIC_CATEGORIES
from data.bank_mapping import get_name, get_bank_info
from data import fdic_client, sec_client
from data.ibkr_client import get_ibkr_client
from analysis.peer_comparison import build_radar_data, get_peer_group_by_asset_size
from utils.formatting import format_value
from ui.charts import (
    price_chart, metrics_trend_chart, peer_radar_chart, balance_sheet_chart,
    asset_composition_chart, loan_mix_chart, funding_mix_chart,
    growth_trend_chart, loans_deposits_chart,
)


def render_bank_detail(ticker: str, all_metrics_df: pd.DataFrame):
    """Render the full detail page for a single bank."""
    info = get_bank_info(ticker)
    name = info["name"] if info else ticker

    st.markdown(f"### {name} ({ticker})")

    # ── Key stats grid ───────────────────────────────────────────────
    bank_row = all_metrics_df[all_metrics_df["ticker"] == ticker]
    if not bank_row.empty:
        row = bank_row.iloc[0]
        _render_keystat_grid(ticker, info, name, row)

        # Click-through to the primary data sources for this bank.
        cik = info.get("cik") if info else None
        cert = info.get("fdic_cert") if info else None
        links = []
        if cik:
            links.append(
                f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
                f'&CIK={cik}&type=10-K&dateb=&owner=include&count=40" target="_blank" '
                'style="text-decoration:none;">📄 SEC filings (EDGAR)</a>')
        if cert:
            links.append(
                f'<a href="https://banks.data.fdic.gov/bankfind-suite/bankfind/details/'
                f'{cert}" target="_blank" style="text-decoration:none;">🏦 FDIC BankFind</a>')
        # Price, change, market cap, P/E and the price chart come from FMP — credit it.
        links.append('<span title="Price, change, market cap, P/E and the price chart">'
                     '📈 FMP (market data)</span>')
        if links:
            st.markdown(
                '<div style="margin-top:7px; font-size:0.8rem; color:#64748b;">'
                'Sources: ' + " &nbsp;·&nbsp; ".join(links) + "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")

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

    # ── All metrics table ───────────────────────────────────────────
    st.subheader("All Metrics")
    if not bank_row.empty:
        row = bank_row.iloc[0]
        for category in METRIC_CATEGORIES:
            cat_metrics = [m for m in METRICS if m["category"] == category]
            if not cat_metrics:
                continue
            st.markdown(f"**{category}**")
            metric_cols = st.columns(min(4, len(cat_metrics)))
            for i, m in enumerate(cat_metrics):
                val = row.get(m["key"])
                with metric_cols[i % len(metric_cols)]:
                    st.metric(
                        label=m["label"],
                        value=format_value(val, m["format"], m.get("decimals", 2)),
                    )

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
    st.metric("Last", f"${last:,.2f}", delta=f"{chg:+.2f}% over period")
    st.metric("Period High", f"${hi:,.2f}")
    st.metric("Period Low", f"${lo:,.2f}")
    st.metric("Range", f"{((hi - lo) / lo * 100):.1f}%" if lo else "—")
    if "volume" in d.columns and d["volume"].notna().any():
        avgv = float(d["volume"].mean())
        st.metric("Avg Volume", f"{avgv/1e6:.2f}M" if avgv >= 1e6 else f"{avgv:,.0f}")


def _render_keystat_grid(ticker, info, name, row):
    """Key-stat cards where every number is click-to-source: FDIC ratios link
    to the Call Report facsimile, SEC per-share figures to the exact 10-K/10-Q,
    computed ratios show their formula + inputs, price is labelled FMP live."""
    from ui.source_trace import render_traceable_cards, make_calc, fdic_calc, fmp_calc, sec_doc_for
    from ui.financial_highlights import _num, _thou

    cert = info.get("fdic_cert") if info else None
    cik = info.get("cik") if info else None
    entity = f"{name} ({ticker})"
    _cnt = lambda v: f"{v:,.0f}" if v else "—"

    fdic_rec = {}
    if cert:
        try:
            fdic_rec = fdic_client.get_latest_financials(cert) or {}
        except Exception:
            fdic_rec = {}
    facts = sec_client.fetch_company_facts(cik) if cik else {}
    fund = {}
    if cik:
        try:
            fund = sec_client.get_latest_fundamentals(cik) or {}
        except Exception:
            fund = {}

    def disp(key):
        m = METRICS_BY_KEY.get(key, {})
        v = row.get(key)
        return (format_value(v, m.get("format", "number"), m.get("decimals", 2))
                if v is not None and not pd.isna(v) else "—")

    price = _num(row.get("price"))
    eps = _num(row.get("eps")); tbvps = _num(row.get("tbvps"))
    shares = _num(fund.get("shares_outstanding")); equity = _num(fund.get("book_value_total"))
    dps = _num(fund.get("dividends_per_share"))
    tce = (tbvps * shares) if (tbvps is not None and shares) else None
    adj = (equity - tce) if (equity is not None and tce is not None) else None

    eps_doc = sec_doc_for(cik, facts, "EarningsPerShareDiluted", instant=False) if facts else None
    eq_doc = sec_doc_for(cik, facts, "StockholdersEquity", instant=True) if facts else None
    sh_doc = ((sec_doc_for(cik, facts, "EntityCommonStockSharesOutstanding", instant=True, ns="dei")
               or sec_doc_for(cik, facts, "CommonStockSharesOutstanding", instant=True))
              if facts else None)
    dps_doc = sec_doc_for(cik, facts, "CommonStockDividendsPerShareDeclared", instant=False) if facts else None

    FMP_PRICE = {"label": "Price (FMP live)", "val": (f"${price:,.2f}" if price is not None else "—")}
    sterm = lambda label, val, doc, sub=None: {"label": label, "val": val, "doc": doc, "sub": sub}

    cards = [
        {"label": "Price", "value": disp("price"),
         "calc": fmp_calc("Price", disp("price"), entity=entity, unit="$ / share",
                          definition="Last trade price from FMP market data.")},
        {"label": "Chg %", "value": disp("change_pct"),
         "accent": ("#059669" if (_num(row.get("change_pct")) or 0) >= 0 else "#dc2626"),
         "calc": fmp_calc("Daily change", disp("change_pct"), entity=entity, unit="%",
                          definition="Percent change in price since the prior close (FMP).")},
        {"label": "Mkt Cap", "value": disp("market_cap"),
         "calc": make_calc("Market cap", disp("market_cap"), entity=entity,
                           source="Computed (FMP price × SEC shares)", asof="latest", unit="$",
                           ref="price × shares outstanding",
                           definition="Shares outstanding × latest price.",
                           terms=[FMP_PRICE, sterm("Shares outstanding", _cnt(shares), sh_doc)],
                           op="Price × shares outstanding")},
        {"label": "P/E", "value": disp("pe_ratio"),
         "calc": make_calc("P/E (TTM)", disp("pe_ratio"), entity=entity,
                           source="Computed (FMP price ÷ SEC EPS)", asof="latest", unit="×",
                           ref="price ÷ diluted EPS (TTM)",
                           definition="Price divided by trailing-twelve-month diluted EPS.",
                           terms=[FMP_PRICE, sterm("Diluted EPS (TTM)",
                                                   (f"${eps:.2f}" if eps else "—"), eps_doc)],
                           op="Price ÷ diluted EPS")},
        {"label": "EPS", "value": disp("eps"),
         "calc": make_calc("Diluted EPS (TTM)", disp("eps"), entity=entity,
                           source="SEC filing (10-K/10-Q)",
                           asof=(eps_doc or {}).get("label", "latest filing"), unit="$ / share",
                           ref="XBRL EarningsPerShareDiluted",
                           definition="Trailing-twelve-month diluted EPS from the holding company's filings.",
                           terms=[sterm("Diluted EPS (TTM, reported)", disp("eps"), eps_doc)],
                           reported=True, link=(eps_doc or {}).get("url"))},
        {"label": "P/TBV", "value": disp("ptbv_ratio"),
         "calc": make_calc("P/TBV", disp("ptbv_ratio"), entity=entity,
                           source="Computed (FMP price ÷ SEC TBV/sh)", asof="latest", unit="×",
                           ref="price ÷ tangible book value per share",
                           definition="Price divided by tangible book value per share.",
                           terms=[FMP_PRICE, sterm("Tangible BV / share",
                                                   (f"${tbvps:.2f}" if tbvps else "—"), eq_doc)],
                           op="Price ÷ tangible BV per share")},
        {"label": "TBV/Sh", "value": disp("tbvps"),
         "calc": make_calc("Tangible BV / share", disp("tbvps"), entity=entity,
                           source="SEC filing (10-K/10-Q)",
                           asof=(eq_doc or {}).get("label", "latest filing"), unit="$ / share",
                           ref="(equity − intangibles) ÷ shares",
                           definition="Tangible common equity (equity − intangibles) ÷ shares outstanding.",
                           terms=[sterm("Tangible common equity",
                                        (_thou((tce or 0) / 1000) + " ($000)"), eq_doc,
                                        sub=(f"Equity {_thou((equity or 0)/1000)} − intangibles "
                                             f"{_thou((adj or 0)/1000)} ($000)")),
                                  sterm("Shares outstanding", _cnt(shares), sh_doc)],
                           op="Tangible common equity ÷ shares")},
        {"label": "Div Yield", "value": disp("dividend_yield"),
         "calc": make_calc("Dividend yield", disp("dividend_yield"), entity=entity,
                           source="Computed (SEC DPS ÷ FMP price)", asof="latest", unit="%",
                           ref="TTM dividends ÷ price",
                           definition="Trailing-twelve-month dividends per share ÷ price.",
                           terms=[sterm("Dividends / share (TTM)",
                                        (f"${dps:.2f}" if dps else "—"), dps_doc), FMP_PRICE],
                           op="DPS ÷ price × 100")},
    ]

    ni = _num(fdic_rec.get("NETINC")); eqf = _num(fdic_rec.get("EQTOT"))
    intanf = _num(fdic_rec.get("INTAN")) or 0
    tcef = (eqf - intanf) if eqf is not None else None
    cards.append(
        {"label": "ROATCE", "value": disp("roatce_blended"),
         "calc": fdic_calc("ROATCE", "NETINC", fdic_rec, cert, unit="%", entity=entity,
                           value=disp("roatce_blended"), reported=False,
                           definition="Blended trailing net income ÷ tangible common equity "
                                       "(equity − intangibles).",
                           terms=[{"label": "Tangible common equity",
                                   "val": (_thou(tcef) + " ($000)" if tcef is not None else "—"),
                                   "sub": (f"Equity {_thou(eqf)} − Intangibles {_thou(intanf)}"
                                           if eqf is not None else None)},
                                  {"label": "Net income", "val": "trailing-twelve-months",
                                   "sub": "blended across recent quarters — see Financials tab "
                                          "for the per-period figures"}],
                           op="Net income ÷ tangible common equity × 100")})

    for key, label, field, defi in [
        ("roaa", "ROAA", "ROA", "Annualized net income as a percent of average assets."),
        ("nim", "NIM", "NIMY", "Net interest income as a percent of average earning assets."),
        ("efficiency_ratio", "Efficiency", "EEFFR",
         "Non-interest expense ÷ (net interest income + non-interest income)."),
        ("cet1_ratio", "CET1", "IDT1CER",
         "Common equity tier 1 capital ÷ risk-weighted assets (bank-level)."),
        ("npl_ratio", "NPL", "NCLNLSR", "Non-current loans as a percent of total loans."),
    ]:
        cards.append({"label": label, "value": disp(key),
                      "calc": fdic_calc(label, field, fdic_rec, cert, unit="%", entity=entity,
                                        value=disp(key), reported=True, definition=defi)})

    render_traceable_cards(cards, key=f"keystat_{ticker}", columns=7)
