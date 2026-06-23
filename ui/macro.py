"""
Macro Dashboard — Fed funds, yield curve, credit spreads, unemployment.

Standalone top-level section. Also provides helpers used by Home and NIM pages.
"""

import streamlit as st
import pandas as pd

from data.fred_client import (
    fetch_series, latest_value, recession_probability, SERIES,
)
from utils.chart_style import (
    apply_standard_layout, tighten_yaxis,
    CHART_HEIGHT_HERO, CHART_HEIGHT_COMPACT, ALERT_STYLE,
)


def _trend_arrow(df: pd.DataFrame, lookback_days: int = 30) -> str:
    """Return ↑/↓/→ based on trailing trend."""
    if df.empty or len(df) < 2:
        return "→"
    recent = df["value"].tail(lookback_days).dropna()
    if len(recent) < 2:
        return "→"
    change = recent.iloc[-1] - recent.iloc[0]
    if change > 0.05:
        return "↑"
    elif change < -0.05:
        return "↓"
    return "→"


# ── "Market & Macro" sections (docs/HOME-MACRO-PLAN.md, user-approved) ──
# Sections-as-data, same principle as ui/company_nav.py: the list drives the
# radio AND the dispatch. Sections marked pending render an honest note —
# their contents are built part-by-part with the user (never placeholders
# pretending to be data).
MACRO_SECTIONS = [
    "Economic Data",
    "Rates & Curve",
    "Bank Sector",
    "Credit & Spreads",
    "Regime",
]


# Macro nav styling — mirrors the Company two-level nav in ui/styles.py:
# the section row renders as an underline tab bar, the sub-selectors (ETF /
# window) as pills (active = navy). Scoped to the macro keyed widgets and
# injected here (rather than styles.py) to keep macro styling self-contained.
# A plain st.markdown <style> (not components.html), so CSS var() tokens work.
_MACRO_NAV_CSS = """
<style>
/* Tighten the page header so the title card hugs its content (title+subtitle)
   and the section tabs sit right beneath it — pulls the whole page up.
   Scoped to the macro render; the shared .dashboard-header rule is untouched. */
.dashboard-header{padding:0.45rem 1.2rem 0.5rem!important;margin-bottom:0.4rem!important;}
.dashboard-header h1{font-size:1.3rem!important;}
.dashboard-header p{margin-top:0.15rem!important;}
.st-key-macro_section_nav{margin-top:0!important;}
/* Bank Sector sub-tab bar (Sector ETFs · Funding & Deposits) — a lighter
   secondary underline tab bar under the main section nav. */
.st-key-bank_sector_sub_nav div[role="radiogroup"]{display:flex;flex-wrap:wrap;gap:2px 6px;align-items:flex-end;border-bottom:1px solid rgba(148,163,184,0.22);margin:0 0 12px;}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label{margin:0!important;padding:5px 12px;cursor:pointer;border-bottom:2px solid transparent;transition:border-color .12s,color .12s;}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label:hover{background:rgba(37,99,235,0.05);}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label>div:first-of-type{display:none!important;}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label p{font-size:0.86rem;color:var(--text-secondary);font-weight:600;}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label:has(input:checked){border-bottom-color:#3b82f6;}
.st-key-bank_sector_sub_nav div[role="radiogroup"]>label:has(input:checked) p{color:#1e40af;font-weight:700;}
.st-key-macro_section_nav div[role="radiogroup"]{display:flex;flex-wrap:wrap;gap:2px 6px;align-items:flex-end;border-bottom:1px solid rgba(148,163,184,0.28);margin-bottom:8px;}
.st-key-macro_section_nav div[role="radiogroup"]>label{margin:0!important;padding:7px 14px;cursor:pointer;border-bottom:2px solid transparent;border-radius:6px 6px 0 0;transition:background .12s,border-color .12s;}
.st-key-macro_section_nav div[role="radiogroup"]>label:hover{background:rgba(37,99,235,0.06);}
.st-key-macro_section_nav div[role="radiogroup"]>label>div:first-of-type{display:none!important;}
.st-key-macro_section_nav div[role="radiogroup"]>label p{font-size:0.95rem;color:var(--text-secondary);font-weight:600;}
.st-key-macro_section_nav div[role="radiogroup"]>label:has(input:checked){border-bottom-color:#2563eb;}
.st-key-macro_section_nav div[role="radiogroup"]>label:has(input:checked) p{color:#2563eb;font-weight:700;}
.st-key-bank_sector_etf div[role="radiogroup"],.st-key-bank_sector_period div[role="radiogroup"],.st-key-macro_cal_window div[role="radiogroup"]{display:flex;flex-wrap:wrap;gap:2px 14px;align-items:flex-end;margin:2px 0 8px;}
.st-key-bank_sector_etf div[role="radiogroup"]>label,.st-key-bank_sector_period div[role="radiogroup"]>label,.st-key-macro_cal_window div[role="radiogroup"]>label{margin:0!important;padding:2px 2px;cursor:pointer;border-bottom:2px solid transparent;transition:border-color .12s,color .12s;}
.st-key-bank_sector_etf div[role="radiogroup"]>label:hover p,.st-key-bank_sector_period div[role="radiogroup"]>label:hover p,.st-key-macro_cal_window div[role="radiogroup"]>label:hover p{color:var(--text-secondary);}
.st-key-bank_sector_etf div[role="radiogroup"]>label>div:first-of-type,.st-key-bank_sector_period div[role="radiogroup"]>label>div:first-of-type,.st-key-macro_cal_window div[role="radiogroup"]>label>div:first-of-type{display:none!important;}
.st-key-bank_sector_etf div[role="radiogroup"]>label p,.st-key-bank_sector_period div[role="radiogroup"]>label p,.st-key-macro_cal_window div[role="radiogroup"]>label p{font-size:0.78rem;color:var(--text-muted);font-weight:500;letter-spacing:0.04em;text-transform:uppercase;}
.st-key-bank_sector_etf div[role="radiogroup"]>label:has(input:checked),.st-key-bank_sector_period div[role="radiogroup"]>label:has(input:checked),.st-key-macro_cal_window div[role="radiogroup"]>label:has(input:checked){border-bottom-color:#3b82f6;}
.st-key-bank_sector_etf div[role="radiogroup"]>label:has(input:checked) p,.st-key-bank_sector_period div[role="radiogroup"]>label:has(input:checked) p,.st-key-macro_cal_window div[role="radiogroup"]>label:has(input:checked) p{color:#1e40af;font-weight:700;}
</style>
"""


def render_macro_dashboard():
    """Render the standalone Market & Macro section."""
    st.markdown(_MACRO_NAV_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Market & Macro</h1>"
        "<p>Rates, curve, bank sector, funding, credit, economy & regime</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.container(key="macro_section_nav"):
        section = st.radio("Section", MACRO_SECTIONS, key="macro_section",
                           horizontal=True, label_visibility="collapsed")

    {
        "Economic Data": _render_economy_calendar,
        "Rates & Curve": _render_rates_curve,
        "Bank Sector": _render_bank_sector,
        "Credit & Spreads": _render_credit_spreads,
        "Regime": _render_regime,
    }[section]()


def _fmt_vol(v) -> str:
    """Average daily volume in human units, or n/a."""
    if v is None:
        return '<span style="color:var(--text-muted);">n/a</span>'
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:,.0f}"


_NA_HTML = '<span style="color:var(--text-muted);">n/a</span>'


def _fmt_usd(v) -> str:
    return f"${v:,.2f}" if v is not None else _NA_HTML


def _fmt_money(v) -> str:
    """Large dollar amount (AUM / market cap) in B/M, or n/a."""
    if v is None:
        return _NA_HTML
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _fmt_change(change, change_pct) -> str:
    """Signed absolute change + percent, colored — e.g. +0.12 (+0.16%)."""
    if change is None:
        return _NA_HTML
    color = ("var(--success)" if change > 0 else
             "var(--danger)" if change < 0 else "var(--text-secondary)")
    pct = f" ({change_pct:+.2f}%)" if change_pct is not None else ""
    return f'<span style="color:{color};">{change:+.2f}{pct}</span>'


def _fmt_range(lo, hi) -> str:
    return f"${lo:,.2f} – ${hi:,.2f}" if (lo is not None and hi is not None) else _NA_HTML


def _render_bank_sector():
    """Bank Sector, with two sub-sections: equity sector ETFs (default) and the
    funding & deposits view (folded in from its old top-level tab)."""
    with st.container(key="bank_sector_sub_nav"):
        sub = st.radio("Bank Sector view", ["Sector ETFs", "Funding & Deposits"],
                       horizontal=True, key="bank_sector_sub", label_visibility="collapsed")
    if sub == "Funding & Deposits":
        _render_funding_deposits()
    else:
        _render_bank_sector_etfs()


def _render_bank_sector_etfs():
    import plotly.graph_objects as go
    from data.bank_etf import get_etf_history, get_etf_market_data, compute_stats, ETFS, PERIODS
    from data.etf_valuation import get_etf_valuation
    from ui.chrome import ledger

    names = {e["ticker"]: e["name"] for e in ETFS}
    # ETF selector on its own row (top-left); the timeframe selector lives
    # directly above the chart it controls (rendered inside c_chart below).
    c_etf, _ = st.columns([1, 3])
    with c_etf:
        ticker = st.radio("ETF", [e["ticker"] for e in ETFS], index=0, horizontal=True,
                          format_func=lambda t: t, key="bank_sector_etf",
                          label_visibility="collapsed")

    # The timeframe radio renders above the chart (below), so read the current
    # selection from session state here to drive the EOD fetch.
    st.session_state.setdefault("bank_sector_period", "1Y")
    period = st.session_state["bank_sector_period"]

    st.caption(f"**{ticker}** — {names.get(ticker, '')} · {period} · EOD closes")

    df = get_etf_history(ticker, period=period)
    if df.empty:
        st.info(
            f"Price history for {ticker} comes from FMP end-of-day data "
            "(needs FMP_API_KEY, mounted in production). Unavailable in this "
            "environment, or the fetch returned no rows for the window."
        )
        return

    stats = compute_stats(df)
    md = get_etf_market_data(ticker)  # live quote + ETF fund fields (Premium)

    # ── Compact price panel (~1/3 width, taller): timeframe tabs directly
    # above the price chart, volume below; the Price/Range/Valuation stats
    # sit tight to its right. ──────────────────────────────────────────
    c_chart, c_stats, _ = st.columns([1, 0.7, 1.3])
    with c_chart:
        st.radio("Window", PERIODS, key="bank_sector_period", horizontal=True,
                 format_func=lambda p: p, label_visibility="collapsed")
        figp = go.Figure()
        figp.add_trace(go.Scatter(
            x=df["date"], y=df["close"], name=ticker, mode="lines",
            line=dict(color="#1e40af", width=2), fill="tozeroy",
            fillcolor="rgba(37, 99, 235, 0.06)",
        ))
        if stats["period_high"] is not None:
            figp.add_hline(y=stats["period_high"], line_color="#94a3b8", line_width=1,
                           line_dash="dash",
                           annotation_text=f"period high ${stats['period_high']:,.2f}",
                           annotation_position="top left",
                           annotation_font=dict(size=10, color="#64748b"))
        ret = stats["period_return_pct"]
        ret_txt = f" · {ret:+.1f}%" if ret is not None else ""
        apply_standard_layout(figp, title=f"{ticker} — price ({period}){ret_txt}",
                              height=CHART_HEIGHT_HERO, yaxis_title="Close",
                              show_legend=False)
        tighten_yaxis(figp, df["close"].tolist(), tickprefix="$")
        st.plotly_chart(figp, use_container_width=True)

        figv = go.Figure()
        if "volume" in df.columns and df["volume"].notna().any():
            figv.add_trace(go.Bar(
                x=df["date"], y=df["volume"], name="Volume",
                marker_color="#93c5fd",
            ))
        apply_standard_layout(figv, title="Volume", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="Shares", show_legend=False)
        st.plotly_chart(figv, use_container_width=True)

    with c_stats:
        rows = [
            ("Last Price", _fmt_usd(md["price"])),
            ("Change", _fmt_change(md["change"], md["change_pct"])),
            ("Previous Close", _fmt_usd(md["prev_close"])),
            ("Open", _fmt_usd(md["open"])),
            ("Day Range", _fmt_range(md["day_low"], md["day_high"])),
            ("52-Week Range", _fmt_range(md["year_low"], md["year_high"])),
            ("Volume", _fmt_vol(md["volume"])),
        ]
        if md["avg_volume"] is not None:
            rows.append(("Avg Volume", _fmt_vol(md["avg_volume"])))
        # Fund size: net assets (AUM) for an ETF; fall back to market cap.
        if md["aum"] is not None:
            rows.append(("Net Assets", _fmt_money(md["aum"])))
        elif md["market_cap"] is not None:
            rows.append(("Market Cap", _fmt_money(md["market_cap"])))
        if md["nav"] is not None:
            rows.append(("NAV", _fmt_usd(md["nav"])))
        if md["expense_ratio"] is not None:
            rows.append(("Expense Ratio", f'{md["expense_ratio"]:.2f}%'))
        ledger("Market Data", rows)

        # Look-through valuation (blended from holdings; FMP Ultimate).
        with st.spinner("Computing look-through valuation…"):
            val = get_etf_valuation(ticker)
        if any(val[k] is not None for k in ("pe", "ptbv", "dividend_yield")):
            st.markdown('<div style="height:22px"></div>', unsafe_allow_html=True)
            ledger("Valuation (look-through)", [
                ("Weighted P/E",
                 f'{val["pe"]:.1f}x' if val["pe"] is not None else _NA_HTML),
                ("Weighted P/TBV",
                 f'{val["ptbv"]:.2f}x' if val["ptbv"] is not None else _NA_HTML),
                ("Dividend Yield",
                 f'{val["dividend_yield"]:.2f}%' if val["dividend_yield"] is not None else _NA_HTML),
            ])

    cov = ""
    if val.get("n_holdings"):
        cov = (f" Valuation is a look-through blend across {val['n_pe']} of "
               f"{val['n_holdings']} holdings — harmonic P/E & P/TBV, "
               f"weighted-average dividend yield.")
    st.caption("Live market data via FMP (quote + ETF info); price line is "
               "end-of-day. Net Assets = assets under management." + cov)


# Display order + labels for the FDIC national-rate products.
_DEPOSIT_PRODUCTS = [
    ("savings", "Savings"),
    ("interest_checking", "Interest Checking"),
    ("mmda", "Money Market"),
    ("cd_1mo", "1-Month CD"),
    ("cd_3mo", "3-Month CD"),
    ("cd_6mo", "6-Month CD"),
    ("cd_12mo", "12-Month CD"),
    ("cd_24mo", "24-Month CD"),
    ("cd_36mo", "36-Month CD"),
    ("cd_48mo", "48-Month CD"),
    ("cd_60mo", "60-Month CD"),
]


def _render_funding_deposits():
    import html as _html
    import plotly.graph_objects as go
    from data.national_rates import get_national_rates, get_national_rate_history
    from ui.chrome import table_export

    rates = get_national_rates()
    if not rates:
        st.info("FDIC national deposit rates come from the FDIC national-rates "
                "publication (public, no key). The fetch returned no data — "
                "the source may be temporarily unavailable.")
        return

    ff = latest_value("FEDFUNDS")
    asof = rates.get("asof", "—")
    ff_txt = f"{ff:.2f}%" if ff is not None else "n/a"
    st.caption(f"FDIC national deposit rates · as of {asof} · published monthly "
               f"(third Monday) · Fed Funds {ff_txt} for the spread.")
    st.markdown(
        "<style>"
        'div[class*="st-key-fundchart"]{padding:2px 6px 0!important;}'
        'div[class*="st-key-fundchart"] [data-testid="stElementContainer"]'
        "{padding:0!important;margin:0!important;}"
        'div[class*="st-key-fundtable"]{padding:6px 10px!important;}'
        "</style>",
        unsafe_allow_html=True,
    )

    # ~5y of monthly prints (covers the revised-rule series) for the chart, the
    # per-product trend sparklines, and the export.
    hist = get_national_rate_history(weeks=260)
    sparks = {}
    if hist:
        for field, _label in _DEPOSIT_PRODUCTS:
            sparks[field] = [float(y) for y in
                             ((r.get(field) or {}).get("rate_pct") for r in hist)
                             if y is not None]

    # Content-hug rates table (LEFT) beside the deposit-beta chart (RIGHT) —
    # never width:100% on this sparse table (it leaves empty bands; the chart
    # fills the freed space instead). See [[dense-html-table-no-slack]].
    # Narrow table column so its caption wraps to the table width (not past it),
    # chart sized to a fixed block, and the right kept open (owner-specified).
    tbl_col, chart_col, _spacer = st.columns([1.15, 1.6, 1.0])
    with tbl_col:
        body = ""
        for field, label in _DEPOSIT_PRODUCTS:
            prod = rates.get(field) or {}
            rate = prod.get("rate_pct")
            cap = prod.get("cap_pct")
            if rate is None and cap is None:
                continue
            rate_txt = f"{rate:.2f}%" if rate is not None else _NA_HTML
            cap_txt = f"{cap:.2f}%" if cap is not None else _NA_HTML
            room_txt = (f"{cap - rate:.2f}pp" if (rate is not None and cap is not None)
                        else _NA_HTML)
            spread_txt = (f"{rate - ff:+.2f}pp" if (rate is not None and ff is not None)
                          else _NA_HTML)
            body += (
                "<tr>"
                f'<td style="text-align:left;">{_html.escape(label)}</td>'
                f'<td style="text-align:right;font-weight:600;">{rate_txt}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{cap_txt}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{room_txt}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{spread_txt}</td>'
                f'<td style="text-align:center;">{_sparkline_svg(sparks.get(field, []))}</td>'
                "</tr>"
            )
        with st.container(border=True, key="fundtable", height=345):
            st.markdown(
                '<div class="ksk-grid"><table><thead><tr>'
                '<th style="text-align:left;">Product</th>'
                '<th style="text-align:right;">National Rate</th>'
                '<th style="text-align:right;">Rate Cap</th>'
                '<th style="text-align:right;">Cap room</th>'
                '<th style="text-align:right;">vs Fed Funds</th>'
                '<th style="text-align:center;">Trend (5Y)</th>'
                "</tr></thead><tbody>" + body + "</tbody></table></div>",
                unsafe_allow_html=True,
            )
        st.caption("National Rate = FDIC deposit-weighted national average. Rate Cap = "
                   "§337.7 cap (national rate + 75bps, or Treasury-yield-based). Cap room = "
                   "headroom to that cap. vs Fed Funds = how far pricing lags policy. Source: FDIC.")
        if hist:
            export_rows = []
            for r in hist:
                row = {"asof": r["asof"]}
                for field, _label in _DEPOSIT_PRODUCTS:
                    row[field] = (r.get(field) or {}).get("rate_pct")
                export_rows.append(row)
            table_export(pd.DataFrame(export_rows), "fdic_national_rates",
                         key="fdic_rates_export")

    with chart_col:
        if not hist:
            st.info("Deposit-rate history is unavailable in this environment.")
            return
        dates = [r["asof"] for r in hist]
        with st.container(border=True, key="fundchart", height=345):
            fig = go.Figure()
            for field, label, color in [
                ("savings", "Savings", "#0891b2"),
                ("mmda", "Money Market", "#9333ea"),
                ("cd_12mo", "12-Month CD", "#1e40af"),
            ]:
                ys = [(r.get(field) or {}).get("rate_pct") for r in hist]
                if any(y is not None for y in ys):
                    fig.add_trace(go.Scatter(
                        x=dates, y=ys, name=label, mode="lines",
                        line=dict(color=color, width=2)))
            ffdf = fetch_series("FEDFUNDS", years=5)
            if not ffdf.empty:
                fig.add_trace(go.Scatter(
                    x=ffdf["date"], y=ffdf["value"], name="Fed Funds", mode="lines",
                    line=dict(color="#64748b", width=2, dash="dot")))
            apply_standard_layout(fig, title="Deposit rates vs Fed Funds — the deposit-beta picture",
                                  height=330, yaxis_title="Rate")
            fig.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig, use_container_width=True)
        st.caption("Deposit rates rise far less than Fed Funds (low deposit beta) and lag "
                   "both up and down. Source: FDIC national rates · FRED (Fed Funds).")


# ── Economic Data formatting helpers ──────────────────────────────────
# Bases that report as a percentage (pp deltas); the rest carry "K" units.
_PCT_BASES = {"yoy_pct", "mom_pct", "level_pct"}


def _fmt_level(v, basis: str) -> str:
    """Latest/prior value in the indicator's natural unit, or n/a."""
    if v is None:
        return '<span style="color:var(--text-muted);">n/a</span>'
    if basis in _PCT_BASES:
        return f"{v:.1f}%"
    if basis == "mom_chg_k":
        return f"{v:+,.0f}K"
    if basis in ("level_k", "level_k_raw"):
        return f"{v:,.0f}K"
    return f"{v:.1f}"  # level_idx and fallback


def _fmt_delta(row: dict) -> str:
    """Signed change vs the prior period, colored by whether the move is
    favorable for this indicator (inflation down = good, jobs up = good, …)."""
    d = row.get("delta")
    if d is None:
        return '<span style="color:var(--text-muted);">n/a</span>'
    basis = row["basis"]
    if basis in _PCT_BASES:
        txt = f"{d:+.1f}pp"
    elif basis == "level_idx":
        txt = f"{d:+.1f}"
    else:
        txt = f"{d:+,.0f}K"
    if abs(d) < 1e-9:
        color = "var(--text-secondary)"
    else:
        good = (d < 0) if row["favorable"] == "down" else (d > 0)
        color = "var(--success)" if good else "var(--danger)"
    return f'<span style="color:{color};">{txt}</span>'


def _sparkline_svg(values, width: int = 92, height: int = 22) -> str:
    """Tiny inline-SVG trend line of a real series (last ~3y of the indicator's
    displayed metric) — a lightweight micro-visual, not a decorative placeholder."""
    vals = [float(v) for v in (values or []) if v is not None and v == v]
    if len(vals) < 2:
        return '<span style="color:var(--text-muted);">—</span>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n, pad = len(vals), 2.0
    pts = []
    for i, v in enumerate(vals):
        x = pad + i / (n - 1) * (width - 2 * pad)
        y = pad + (1 - (v - lo) / rng) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    lx, ly = pts[-1].split(",")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="vertical-align:middle;display:inline-block;">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#1e40af" stroke-width="1.4"/>'
            f'<circle cx="{lx}" cy="{ly}" r="1.7" fill="#1e40af"/></svg>')


def _fmt_z(z) -> str:
    """Historical context: z-score of the latest reading vs ~10y of its own
    history, as ±Nσ (bold when |z| ≥ 2, i.e. an unusual reading)."""
    if z is None:
        return '<span style="color:var(--text-muted);">—</span>'
    weight = "font-weight:600;" if abs(z) >= 2 else ""
    return f'<span style="color:var(--text-secondary);{weight}">{z:+.1f}σ</span>'


def _fmt_as_of(ts, freq: str) -> str:
    """Period label for the latest reading, by series frequency."""
    if ts is None:
        return "—"
    if freq == "Q":
        return f"Q{(ts.month - 1) // 3 + 1} {ts.year}"
    if freq == "W":
        return ts.strftime("%b %d, %Y").replace(" 0", " ")
    return ts.strftime("%b %Y")


# Impact tags for the FMP economic calendar (High/Medium/Low).
_ECON_IMPACT_TAG = {
    "High":   '<span style="color:var(--brand-primary);font-weight:700;">High</span>',
    "Medium": '<span style="color:var(--text-secondary);font-weight:600;">Med</span>',
    "Low":    '<span style="color:var(--text-muted);">Low</span>',
}


def _fmt_econ_val(v, unit) -> str:
    """An econ-calendar value with its unit (%, M, K, B, bps…), or em-dash."""
    if v is None:
        return '<span style="color:var(--text-muted);">—</span>'
    u = (unit or "").strip()
    if u in ("%", "M", "K", "B"):
        return f"{v:g}{u}"
    return f"{v:g}{(' ' + u) if u else ''}"


def _econ_surprise_html(ev: dict) -> str:
    """Signed actual−consensus surprise, colored by deviation (above consensus
    green / below red) — direction vs expectations, not good/bad."""
    s = ev.get("surprise")
    if s is None:
        return '<span style="color:var(--text-muted);">—</span>'
    color = ("var(--success)" if s > 0 else
             "var(--danger)" if s < 0 else "var(--text-secondary)")
    return f'<span style="color:{color};">{s:+g}</span>'


def _et_time(dt_str: str) -> str:
    """FMP UTC datetime string → US/Eastern 'h:mm AM/PM ET' (blank for
    midnight placeholders or parse failure)."""
    from datetime import datetime as _dt2
    try:
        from zoneinfo import ZoneInfo
        utc = _dt2.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
    except Exception:
        return ""
    if utc.hour == 0 and utc.minute == 0:
        return ""  # FMP's no-scheduled-time placeholder (00:00 UTC)
    d = utc.astimezone(ZoneInfo("America/New_York"))
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} ET"


_BASIS_TAG = {"yoy_pct": "YoY", "mom_pct": "MoM", "mom_chg_k": "MoM chg",
              "level_pct": "level", "level_k": "level", "level_k_raw": "level",
              "level_idx": "index"}


def _board_table(rows: list[dict]) -> str:
    """Dense, content-hugging HTML table for a subset of indicator rows (one
    half of the split board). The table carries NO width:100% stretch, so it
    sizes to its content — every column hugs its value and there are no empty
    bands. (ksk-grid CSS already applies white-space:nowrap to all cells.)"""
    import html as _h
    themes = []
    for r in rows:
        if r["theme"] not in themes:
            themes.append(r["theme"])
    body = ""
    for theme in themes:
        body += ('<tr><td colspan="7" style="text-align:left;background:var(--grid-head-bg);'
                 'color:var(--brand-primary);font-weight:700;text-transform:uppercase;'
                 f'font-size:var(--fs-2xs);letter-spacing:0.06em;">{_h.escape(theme)}</td></tr>')
        for r in (x for x in rows if x["theme"] == theme):
            body += (
                "<tr>"
                f'<td style="text-align:left;">{_h.escape(r["label"])} '
                f'<span style="color:var(--text-muted);font-size:var(--fs-2xs);">{_BASIS_TAG.get(r["basis"], "")}</span></td>'
                f'<td style="text-align:right;">{_fmt_level(r["latest"], r["basis"])}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_level(r.get("prior"), r["basis"])}</td>'
                f'<td style="text-align:right;">{_fmt_delta(r)}</td>'
                f'<td style="text-align:right;">{_fmt_z(r.get("zscore"))}</td>'
                f'<td style="text-align:center;">{_sparkline_svg(r.get("spark"))}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_as_of(r["as_of"], r["freq"])}</td>'
                "</tr>"
            )
    return (
        '<div class="ksk-grid"><table>'
        '<thead><tr><th style="text-align:left;">Indicator</th>'
        '<th style="text-align:right;">Latest</th>'
        '<th style="text-align:right;">Prior</th>'
        '<th style="text-align:right;">Δ</th>'
        '<th style="text-align:right;">vs hist</th>'
        '<th style="text-align:center;">Trend</th>'
        '<th style="text-align:right;">As of</th></tr></thead>'
        "<tbody>" + body + "</tbody></table></div>"
    )


def _fmt_fed_range(lo, hi) -> str:
    return f"{lo:.2f}–{hi:.2f}%" if (lo is not None and hi is not None) else _NA_HTML


def _fmt_last_move(mv: dict) -> str:
    """Most recent policy move, e.g. '−25 bps cut · Dec 11, 2025', or 'Hold'."""
    d = mv.get("date")
    when = d.strftime("%b %d, %Y").replace(" 0", " ") if d is not None else ""
    direction = mv.get("direction")
    if direction == "hold" or not mv.get("bps"):
        return "Hold" + (f" · since {when}" if when else "")
    bps = abs(int(mv.get("bps") or 0))
    color = "var(--success)" if direction == "cut" else "var(--danger)"
    arrow = "−" if direction == "cut" else "+"
    return (f'<span style="color:{color};">{arrow}{bps} bps {direction}</span>'
            + (f' · {when}' if when else ""))


def _sep_cell(v, suffix: str = "%") -> str:
    return f"{v:.2f}{suffix}" if v is not None else _NA_HTML


def _render_fed_policy_strip():
    """Fed policy snapshot strip: target range · effective · last move · next
    meeting · as-of (data/fomc.fed_policy_snapshot)."""
    import html as _h
    from data.fomc import fed_policy_snapshot
    snap = fed_policy_snapshot()
    nm = snap.get("next_meeting")
    nm_txt = nm.strftime("%b %d, %Y").replace(" 0", " ") if nm is not None else "—"
    ao = snap.get("as_of")
    ao_txt = ao.strftime("%b %d, %Y").replace(" 0", " ") if ao is not None else "—"
    st.markdown("**Federal Reserve — policy & projections**")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Target range</th>'
        '<th style="text-align:right;">Effective</th>'
        '<th style="text-align:left;">Last move</th>'
        '<th style="text-align:left;">Next meeting</th>'
        '<th style="text-align:right;">As of</th>'
        '</tr></thead><tbody><tr>'
        f'<td style="text-align:left;font-weight:700;">{_fmt_fed_range(snap.get("target_lower"), snap.get("target_upper"))}</td>'
        f'<td style="text-align:right;">{_sep_cell(snap.get("effective"))}</td>'
        f'<td style="text-align:left;">{_fmt_last_move(snap.get("last_move") or {})}</td>'
        f'<td style="text-align:left;">{_h.escape(nm_txt)}</td>'
        f'<td style="text-align:right;color:var(--text-secondary);">{_h.escape(ao_txt)}</td>'
        "</tr></tbody></table></div>",
        unsafe_allow_html=True,
    )


def _render_fed_words():
    """The FOMC's own words: latest statement (left) + curated Fed headlines
    (right)."""
    import html as _h
    from data.fomc import fetch_fomc_statement
    stmt_c, head_c = st.columns([1.4, 1])
    with stmt_c:
        st.markdown("**Latest FOMC statement**")
        stmt = fetch_fomc_statement()
        if stmt and stmt.get("paragraphs"):
            sdt = stmt["date"].strftime("%b %d, %Y").replace(" 0", " ")
            paras = "".join(f'<p style="margin:0 0 6px;">{_h.escape(p)}</p>'
                            for p in stmt["paragraphs"])
            st.markdown(
                '<div style="border-left:3px solid var(--brand-primary);padding:4px 12px;'
                'background:var(--bg-surface);font-size:var(--fs-sm);line-height:1.45;">'
                f'{paras}</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"FOMC statement · {sdt} · [full release]({stmt['url']}) · "
                       "Source: Federal Reserve.")
        else:
            st.caption("FOMC statement unavailable (federalreserve.gov fetch failed).")
    with head_c:
        st.markdown("**Fed headlines**")
        try:
            from data.events.store import get_topic_news
            from data.events.topic_curation import curate_topic_news
            news = curate_topic_news(
                get_topic_news("macro", hours=120, limit=60), "macro")[:6]
        except Exception:
            news = []
        if news:
            lis = "".join(
                '<li style="margin:0 0 4px;">'
                f'<a href="{_h.escape(n.get("url", ""))}" target="_blank" '
                f'style="color:var(--brand-primary);">{_h.escape(n.get("headline", ""))}</a>'
                ' <span style="color:var(--text-muted);font-size:var(--fs-2xs);">'
                f'{_h.escape(n.get("source_name") or "")}</span></li>'
                for n in news)
            st.markdown(
                f'<ul style="margin:0;padding-left:16px;font-size:var(--fs-sm);">{lis}</ul>',
                unsafe_allow_html=True)
        else:
            st.caption("Fed/Powell headlines populate from the macro news feed in production.")


def _render_sep_block():
    """FOMC Summary of Economic Projections — the medians table STACKED above
    the dot-plot (median diamond · central-tendency band · range whisker, with
    a dotted median path), for the narrow Fed column."""
    import plotly.graph_objects as go
    import html as _h
    from data.fomc import sep_projections
    proj = sep_projections()
    funds = proj.get("funds") or []
    sep_asof = proj.get("as_of")
    sep_txt = sep_asof.strftime("%b %Y") if sep_asof is not None else "—"
    if not funds:
        st.caption("FOMC Summary of Economic Projections unavailable.")
        return
    st.markdown(f"**Summary of Economic Projections (SEP {sep_txt})**")
    macro = proj.get("macro") or {}
    horizons = [f["horizon"] for f in funds]

    def _mm(key):
        return {m["horizon"]: m.get("median") for m in (macro.get(key) or [])}
    gdp, ur, pce, cpce = _mm("gdp"), _mm("unemployment"), _mm("pce"), _mm("core_pce")
    fundmed = {f["horizon"]: f.get("median") for f in funds}
    tbody = ""
    for hz in horizons:
        tbody += (
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(str(hz))}</td>'
            f'<td style="text-align:right;">{_sep_cell(fundmed.get(hz))}</td>'
            f'<td style="text-align:right;">{_sep_cell(gdp.get(hz))}</td>'
            f'<td style="text-align:right;">{_sep_cell(ur.get(hz))}</td>'
            f'<td style="text-align:right;">{_sep_cell(pce.get(hz))}</td>'
            f'<td style="text-align:right;">{_sep_cell(cpce.get(hz))}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Horizon</th>'
        '<th style="text-align:right;">Funds</th>'
        '<th style="text-align:right;">GDP</th>'
        '<th style="text-align:right;">Unemp</th>'
        '<th style="text-align:right;">PCE</th>'
        '<th style="text-align:right;">Core</th>'
        "</tr></thead><tbody>" + tbody + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption("SEP medians by horizon. Funds = fed funds, Core = core PCE. Source: FRED.")
    fig = go.Figure()
    allvals = []
    med_x, med_y = [], []
    for f in funds:
        x = f["horizon"]
        rl, rh, cl, ch, md = (f.get("range_low"), f.get("range_high"),
                              f.get("ct_low"), f.get("ct_high"), f.get("median"))
        if rl is not None and rh is not None:
            fig.add_trace(go.Scatter(x=[x, x], y=[rl, rh], mode="lines",
                line=dict(color="#cbd5e1", width=2), showlegend=False, hoverinfo="skip"))
            allvals += [rl, rh]
        if cl is not None and ch is not None:
            fig.add_trace(go.Scatter(x=[x, x], y=[cl, ch], mode="lines",
                line=dict(color="#93c5fd", width=14), showlegend=False, hoverinfo="skip"))
            allvals += [cl, ch]
        if md is not None:
            med_x.append(x)
            med_y.append(md)
            allvals.append(md)
    if len(med_x) > 1:
        fig.add_trace(go.Scatter(x=med_x, y=med_y, mode="lines",
            line=dict(color="#1e3a8a", width=1.5, dash="dot"),
            showlegend=False, hoverinfo="skip"))
    for x, md in zip(med_x, med_y):
        fig.add_trace(go.Scatter(x=[x], y=[md], mode="markers",
            marker=dict(symbol="diamond", color="#1e3a8a", size=11), showlegend=False,
            hovertemplate=f"{x}<br>median %{{y:.2f}}%<extra></extra>"))
    apply_standard_layout(fig, title="Fed funds dot-plot — median · central tendency · range",
                          height=250, yaxis_title="%", show_legend=False)
    fig.update_xaxes(type="category")
    if allvals:
        tighten_yaxis(fig, allvals, ticksuffix="%")
    st.plotly_chart(fig, use_container_width=True)


def _shade_recessions(fig, years: int = 5):
    """Light NBER recession bands behind a time-series macro chart (polish)."""
    from data.macro_indicators import recession_periods
    from data.fred_client import fetch_series
    for x0, x1 in recession_periods(fetch_series("USREC", years=years)):
        fig.add_vrect(x0=x0, x1=x1, fillcolor="rgba(15,23,42,0.06)",
                      line_width=0, layer="below")


def _macro_trend_fig(spec: dict, years: int = 8):
    """Polished single-indicator trend for the explorer: the indicator's
    displayed metric over `years`, NBER recessions shaded, latest value called
    out. Well-proportioned (HERO height in a 3-up grid)."""
    import plotly.graph_objects as go
    from data.macro_indicators import basis_series
    from data.fred_client import fetch_series
    pct = spec["basis"] in ("yoy_pct", "mom_pct", "level_pct")
    df = fetch_series(spec["series_id"], years=years)
    s = basis_series(df, spec["basis"]) if df is not None else None
    fig = go.Figure()
    if s is not None and not s.empty:
        _shade_recessions(fig, years=years)
        fig.add_trace(go.Scatter(x=s["date"], y=s["value"], mode="lines",
                                 line=dict(color="#1e40af", width=2)))
        lx, ly = s["date"].iloc[-1], float(s["value"].iloc[-1])
        lbl = f"{ly:,.1f}%" if pct else f"{ly:,.0f}"
        fig.add_trace(go.Scatter(
            x=[lx], y=[ly], mode="markers+text", marker=dict(color="#1e3a8a", size=7),
            text=["  " + lbl], textposition="middle right",
            textfont=dict(size=11, color="#1e3a8a"), showlegend=False, hoverinfo="skip"))
    apply_standard_layout(fig, title=f'{spec["label"]} ({_BASIS_TAG.get(spec["basis"], "")})',
                          height=CHART_HEIGHT_HERO, show_legend=False)
    if pct:
        fig.update_yaxes(ticksuffix="%")
    return fig


def _render_surprise_summary(recent):
    """Compact tally of how recent releases printed vs consensus — direction vs
    expectations (above/below), NOT good/bad. Fills the freed column beside the
    calendars."""
    import html as _h
    items = [e for e in (recent or []) if e.get("surprise") is not None]
    if not items:
        st.markdown("**Surprise tracker**")
        st.caption("No released surprises in the window.")
        return
    beats = sum(1 for e in items if e["surprise"] > 0)
    misses = sum(1 for e in items if e["surprise"] < 0)
    inline = len(items) - beats - misses
    if beats > misses:
        tilt, color = "Above consensus", "var(--success)"
    elif misses > beats:
        tilt, color = "Below consensus", "var(--danger)"
    else:
        tilt, color = "In line", "var(--text-secondary)"
    ranked = sorted(items, key=lambda e: abs(e["surprise"]), reverse=True)[:3]
    big = "".join(
        f'<tr><td style="text-align:left;">{_h.escape(e["event"])}</td>'
        f'<td style="text-align:right;">{_econ_surprise_html(e)}</td></tr>'
        for e in ranked)
    st.markdown("**Surprise tracker**")
    st.markdown(
        '<div class="ksk-grid"><table><tbody>'
        f'<tr><td style="text-align:left;">Above cons.</td><td style="text-align:right;color:var(--success);font-weight:700;">{beats}</td></tr>'
        f'<tr><td style="text-align:left;">Below cons.</td><td style="text-align:right;color:var(--danger);font-weight:700;">{misses}</td></tr>'
        f'<tr><td style="text-align:left;">In line</td><td style="text-align:right;">{inline}</td></tr>'
        '</tbody></table></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div style="margin-top:4px;font-size:var(--fs-sm);">Net tilt: '
        f'<span style="color:{color};font-weight:700;">{tilt}</span> '
        f'<span style="color:var(--text-muted);">· {len(items)} releases</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin-top:6px;font-size:var(--fs-2xs);color:var(--text-muted);'
        'letter-spacing:0.06em;">BIGGEST SURPRISES</div>'
        f'<div class="ksk-grid"><table><tbody>{big}</tbody></table></div>',
        unsafe_allow_html=True,
    )


# ── Cached figure builders (render-path perf) ────────────────────────────
# Economic Data renders 6 grid charts + the explorer on EVERY Streamlit
# rerun. A go.Figure build (data transforms + trace construction + layout)
# costs ~300–500ms, so a warm rerun spent ~2.9s rebuilding identical charts.
# Each builder is @st.cache_data-memoized: the underlying series are already
# warm-cache reads (jobs/refresh_macro */30 + fetch_series memo), so after
# the first build the figure comes from cache (cache_data returns a fresh
# copy per call — safe to hand to st.plotly_chart). ttl matches the */30
# warm-refresh cadence so charts pick up new prints within the window.
_GRID_H = 272
_FIG_TTL = 1800

# Timeframe selector for the grid. fetch_series caches FULL history in the
# cloud cache (the years arg only slices the return), so we pull a long window
# once (warm read, no live FRED) and slice client-side per timeframe. "Max" =
# all available history for that series. Builders are keyed on `window`, so
# each (chart, timeframe) is cached after first view.
_TF_OPTIONS = ["1Y", "2Y", "5Y", "10Y", "Max"]
_TF_YEARS = {"1Y": 1, "2Y": 2, "5Y": 5, "10Y": 10, "Max": None}
_FETCH_YEARS = 100  # effectively "all" for these monthly/quarterly series


def _win(s, window):
    """Slice a (date,value) frame to the selected timeframe ('Max' = no slice)."""
    yrs = _TF_YEARS.get(window)
    if s is None or s.empty or yrs is None:
        return s
    cutoff = s["date"].iloc[-1] - pd.Timedelta(days=365 * yrs)
    return s[s["date"] >= cutoff]


def _rec_years(window):
    """Recession-shading lookback that covers the visible window."""
    return _TF_YEARS.get(window) or _FETCH_YEARS


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_inflation(window="5Y"):
    import plotly.graph_objects as go
    from data.macro_indicators import to_yoy
    fig = go.Figure()
    for sid, label, color in [
        ("CPIAUCSL", "CPI", "#1e40af"),
        ("CPILFESL", "Core CPI", "#3b82f6"),
        ("PCEPILFE", "Core PCE", "#d97706"),
    ]:
        s = _win(to_yoy(fetch_series(sid, years=_FETCH_YEARS)), window)
        if s is not None and not s.empty:
            fig.add_trace(go.Scatter(
                x=s["date"], y=s["value"], name=label, mode="lines",
                line=dict(color=color, width=2)))
    _shade_recessions(fig, years=_rec_years(window))
    fig.add_hline(y=2.0, line_color="#059669", line_width=1, line_dash="dash",
                  annotation_text="Fed 2% target", annotation_position="top left",
                  annotation_font=dict(size=10, color="#059669"))
    apply_standard_layout(fig, title=f"Inflation — YoY % ({window})",
                          height=_GRID_H, yaxis_title="YoY")
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_labor(window="5Y"):
    import plotly.graph_objects as go
    from data.macro_indicators import to_mom_change
    fig = go.Figure()
    nfp = _win(to_mom_change(fetch_series("PAYEMS", years=_FETCH_YEARS)), window)
    if nfp is not None and not nfp.empty:
        bar_colors = ["#dc2626" if v < 0 else "#3b82f6" for v in nfp["value"]]
        fig.add_trace(go.Bar(
            x=nfp["date"], y=nfp["value"], name="Payrolls Δ (000s)",
            marker_color=bar_colors, yaxis="y"))
    unr = _win(fetch_series("UNRATE", years=_FETCH_YEARS), window)
    if unr is not None and not unr.empty:
        fig.add_trace(go.Scatter(
            x=unr["date"], y=unr["value"], name="Unemployment %",
            mode="lines", line=dict(color="#0f172a", width=2), yaxis="y2"))
    _shade_recessions(fig, years=_rec_years(window))
    apply_standard_layout(fig, title=f"Labor — payrolls Δ & unemployment ({window})",
                          height=_GRID_H, yaxis_title="Jobs Δ (000s)")
    fig.update_layout(yaxis2=dict(title="Unemp %", overlaying="y", side="right",
                                  ticksuffix="%", showgrid=False))
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_housing(window="5Y"):
    import plotly.graph_objects as go
    fig = go.Figure()
    for sid, label, color in [
        ("HOUST", "Housing Starts", "#1e40af"),
        ("PERMIT", "Building Permits", "#d97706"),
    ]:
        s = fetch_series(sid, years=_FETCH_YEARS)
        d = _win(s.dropna(subset=["value"]).sort_values("date") if not s.empty else s, window)
        if d is not None and not d.empty:
            fig.add_trace(go.Scatter(
                x=d["date"], y=d["value"], name=label, mode="lines",
                line=dict(color=color, width=2)))
    _shade_recessions(fig, years=_rec_years(window))
    apply_standard_layout(fig, title=f"Housing — Starts & Permits (000s SAAR, {window})",
                          height=_GRID_H, yaxis_title="000s")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_growth(window="5Y"):
    import plotly.graph_objects as go
    fig = go.Figure()
    gdp = fetch_series("A191RL1Q225SBEA", years=_FETCH_YEARS)
    g = _win(gdp.dropna(subset=["value"]).sort_values("date") if not gdp.empty else gdp, window)
    if g is not None and not g.empty:
        gcolors = ["#dc2626" if v < 0 else "#1e40af" for v in g["value"]]
        fig.add_trace(go.Bar(x=g["date"], y=g["value"], name="Real GDP QoQ SAAR",
                             marker_color=gcolors))
    _shade_recessions(fig, years=_rec_years(window))
    apply_standard_layout(fig, title=f"Growth — Real GDP (QoQ SAAR, {window})",
                          height=_GRID_H, yaxis_title="QoQ SAAR", show_legend=False)
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_activity(window="5Y"):
    import plotly.graph_objects as go
    from data.macro_indicators import to_yoy
    fig = go.Figure()
    for sid, label, color in [
        ("INDPRO", "Industrial Production", "#1e40af"),
        ("RSAFS", "Retail Sales", "#d97706"),
    ]:
        s = _win(to_yoy(fetch_series(sid, years=_FETCH_YEARS)), window)
        if s is not None and not s.empty:
            fig.add_trace(go.Scatter(
                x=s["date"], y=s["value"], name=label, mode="lines",
                line=dict(color=color, width=2)))
    _shade_recessions(fig, years=_rec_years(window))
    apply_standard_layout(fig, title=f"Activity — Industrial Production & Retail (YoY, {window})",
                          height=_GRID_H, yaxis_title="YoY")
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_sentiment(window="5Y"):
    import plotly.graph_objects as go
    from data.macro_indicators import to_yoy
    fig = go.Figure()
    sent = fetch_series("UMCSENT", years=_FETCH_YEARS)
    d = _win(sent.dropna(subset=["value"]).sort_values("date") if not sent.empty else sent, window)
    if d is not None and not d.empty:
        fig.add_trace(go.Scatter(
            x=d["date"], y=d["value"], name="UMich Sentiment",
            mode="lines", line=dict(color="#1e40af", width=2), yaxis="y"))
    m2 = _win(to_yoy(fetch_series("M2SL", years=_FETCH_YEARS)), window)
    if m2 is not None and not m2.empty:
        fig.add_trace(go.Scatter(
            x=m2["date"], y=m2["value"], name="M2 YoY", mode="lines",
            line=dict(color="#d97706", width=2), yaxis="y2"))
    _shade_recessions(fig, years=_rec_years(window))
    apply_standard_layout(fig, title=f"Sentiment & Money — UMich & M2 YoY ({window})",
                          height=_GRID_H, yaxis_title="Sentiment")
    fig.update_layout(yaxis2=dict(title="M2 YoY %", overlaying="y", side="right",
                                  ticksuffix="%", showgrid=False))
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _cached_print_board():
    """Memoize the indicator board so its 25 YoY/MoM transforms don't re-run
    every Streamlit rerun (underlying series are warm-cache reads)."""
    from data.macro_indicators import get_print_board
    return get_print_board()


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _cached_trend_fig(series_id: str, basis: str, label: str, years: int = 8):
    """Memoized explorer chart, keyed on the hashable spec fields (the spec
    dict itself isn't hashable, so _macro_trend_fig can't be decorated)."""
    return _macro_trend_fig({"series_id": series_id, "basis": basis, "label": label},
                            years=years)


def _render_economy_calendar():
    import html as _html
    from datetime import date as _date, datetime as _dt
    from data.econ_calendar import get_recent_releases, get_upcoming_releases
    from ui.chrome import table_export

    # ── Calendars stacked (left) · Key indicators board (right) ──
    recent = get_recent_releases(days=10, limit=16)
    up = get_upcoming_releases(days=14, limit=20)
    rows = _cached_print_board()
    # Calendars (left) · board (middle) · a 2×2 chart grid on the right:
    # Inflation/Labor stacked beside Growth/Activity stacked. board_col is sized
    # to hug the full 7-column board (indicator…Trend…As of) — wide enough not to
    # clip, tight enough to leave only a thin seam before the charts (no right
    # slack); the chart grid takes the rest and runs taller to fill it.
    cal_col, board_col, chart_col = st.columns([1, 1.04, 1.62])
    with cal_col:
        st.markdown("**Latest releases & surprises**")
        if recent:
            srows = ""
            for e in recent:
                d = _dt.strptime(e["date"], "%Y-%m-%d").date().strftime("%b %d").replace(" 0", " ")
                srows += (
                    "<tr>"
                    f'<td style="white-space:nowrap;">{d}</td>'
                    f'<td style="text-align:left;">{_html.escape(e["event"])}</td>'
                    f'<td style="text-align:right;"><strong>{_fmt_econ_val(e["actual"], e["unit"])}</strong></td>'
                    f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_econ_val(e["estimate"], e["unit"])}</td>'
                    f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_econ_val(e.get("previous"), e["unit"])}</td>'
                    f'<td style="text-align:right;">{_econ_surprise_html(e)}</td>'
                    f'<td style="text-align:right;">{_ECON_IMPACT_TAG.get(e["impact"], "")}</td>'
                    "</tr>"
                )
            st.markdown(
                '<div class="ksk-grid"><table>'
                "<thead><tr>"
                '<th style="text-align:left;">Date</th>'
                '<th style="text-align:left;">Release</th>'
                '<th style="text-align:right;">Actual</th>'
                '<th style="text-align:right;">Cons.</th>'
                '<th style="text-align:right;">Prior</th>'
                '<th style="text-align:right;">Surprise</th>'
                '<th style="text-align:right;">Impact</th>'
                "</tr></thead><tbody>" + srows + "</tbody></table></div>",
                unsafe_allow_html=True,
            )
            st.caption("Actual vs consensus; surprise colored by deviation (not good/bad). "
                       "Source: FMP economic calendar.")
        else:
            st.info("Latest-release surprises use FMP's economic calendar (Premium key, "
                    "mounted in production). Unavailable in this environment.")
        st.markdown("**Upcoming releases**")
        if not up:
            st.info("Upcoming-release calendar uses FMP's economic calendar (Premium key, "
                    "mounted in production). Unavailable in this environment.")
        else:
            today_iso = _date.today().isoformat()
            urows = ""
            for e in up:
                d = _dt.strptime(e["date"], "%Y-%m-%d").date()
                row_bg = ' style="background:rgba(30,64,175,0.04);"' if e["date"] == today_iso else ""
                urows += (
                    f"<tr{row_bg}>"
                    f'<td style="white-space:nowrap;">{d.strftime("%b %d").replace(" 0", " ")}</td>'
                    f'<td style="text-align:left;color:var(--text-secondary);white-space:nowrap;">{_et_time(e["datetime"])}</td>'
                    f'<td style="text-align:left;">{_html.escape(e["event"])}</td>'
                    f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_econ_val(e.get("previous"), e["unit"])}</td>'
                    f'<td style="text-align:right;color:var(--text-secondary);">{_fmt_econ_val(e["estimate"], e["unit"])}</td>'
                    f'<td style="text-align:right;">{_ECON_IMPACT_TAG.get(e["impact"], "")}</td>'
                    "</tr>"
                )
            st.markdown(
                '<div class="ksk-grid"><table>'
                "<thead><tr>"
                '<th style="text-align:left;">Date</th>'
                '<th style="text-align:left;">Time</th>'
                '<th style="text-align:left;">Release</th>'
                '<th style="text-align:right;">Prior</th>'
                '<th style="text-align:right;">Cons.</th>'
                '<th style="text-align:right;">Impact</th>'
                "</tr></thead><tbody>" + urows + "</tbody></table></div>",
                unsafe_allow_html=True,
            )
            st.caption("Scheduled US releases · consensus · impact · ET times. "
                       "Source: FMP economic calendar.")
    with board_col:
        st.markdown("**Key indicators**")
        st.markdown(_board_table(rows), unsafe_allow_html=True)
        st.caption(
            "Latest reading per series, grouped by theme. YoY = year-over-year, "
            "MoM = month-over-month, QoQ SAAR = quarter-over-quarter annualized. "
            "Δ colored by favorable direction (inflation lower / activity higher = green). "
            "vs hist = z-score of the latest vs ~10y of its own history (±σ; bold if |z|≥2). "
            "Source: FRED."
        )
        export_df = pd.DataFrame([{
            "theme": r["theme"], "indicator": r["label"], "basis": r["basis"],
            "latest": r["latest"], "prior": r["prior"], "delta": r["delta"],
            "zscore": r.get("zscore"),
            "as_of": r["as_of"].strftime("%Y-%m-%d") if r["as_of"] is not None else None,
            "series_id": r["series_id"],
        } for r in rows])
        table_export(export_df, "macro_print_board", key="macro_print_board_export")
    with chart_col:
        _render_macro_grid()

    st.markdown("---")

    # ── Explore any indicator (interactive; charts any of the 27 on demand) ──
    # In an st.fragment so changing the selection re-runs ONLY this block, not
    # the whole macro script (the calendars / board / grid stay put).
    _render_indicator_explorer()


@st.fragment
def _render_macro_grid():
    """The two 3-tall chart columns beside the board, with a shared timeframe
    selector. In an st.fragment so changing the timeframe re-runs ONLY the grid
    (the calendars / board stay put). Each chart is a bordered card (same device
    as home.py's _af_card); the keyed CSS hook trims st.container's default
    ~1rem padding so the chart fills the card."""
    st.markdown(
        "<style>"
        'div[class*="st-key-macrochart"]{padding:2px 6px 0!important;}'
        'div[class*="st-key-macrochart"] [data-testid="stElementContainer"]'
        "{padding:0!important;margin:0!important;}"
        'div[class*="st-key-macrochart"] [data-testid="stVerticalBlock"]'
        "{gap:0!important;}"
        "</style>",
        unsafe_allow_html=True,
    )
    window = st.segmented_control(
        "Timeframe", _TF_OPTIONS, default="5Y", key="macro_grid_tf",
        label_visibility="collapsed") or "5Y"
    gl, gr = st.columns(2)
    with gl:
        for _name, _build in (("inflation", _fig_inflation), ("labor", _fig_labor),
                              ("housing", _fig_housing)):
            with st.container(border=True, key=f"macrochart_{_name}"):
                st.plotly_chart(_build(window), use_container_width=True)
    with gr:
        for _name, _build in (("growth", _fig_growth), ("activity", _fig_activity),
                              ("sentiment", _fig_sentiment)):
            with st.container(border=True, key=f"macrochart_{_name}"):
                st.plotly_chart(_build(window), use_container_width=True)


@st.fragment
def _render_indicator_explorer():
    from data.macro_indicators import INDICATORS
    st.markdown("**Explore any indicator**")
    by_label = {s["label"]: s for s in INDICATORS}
    _defaults = [d for d in ("CPI", "Nonfarm Payrolls", "Real GDP (QoQ SAAR)")
                 if d in by_label]
    picks = st.multiselect("Indicators", list(by_label.keys()), default=_defaults,
                           key="macro_explore", label_visibility="collapsed")
    if picks:
        ecols = st.columns(3)
        for i, lbl in enumerate(picks):
            spec = by_label[lbl]
            with ecols[i % 3]:
                st.plotly_chart(
                    _cached_trend_fig(spec["series_id"], spec["basis"], spec["label"]),
                    use_container_width=True)
        st.caption("Indicator's displayed metric over ~8y; NBER recessions shaded; latest value "
                   "labeled. Source: FRED.")
    else:
        st.caption("Pick one or more indicators to chart their history.")



def _render_regime():
    # ── Recession indicator (re-homed from the old single-page layout) ──
    rec = recession_probability()
    level = rec["level"]
    score = rec["score"]

    if level == "high":
        style = ALERT_STYLE["high"]
        icon = '<span class="ksk-dot bad"></span>'
        label = f"Elevated recession risk ({score}/100)"
    elif level == "medium":
        style = ALERT_STYLE["medium"]
        icon = '<span class="ksk-dot warn"></span>'
        label = f"Mixed recession signals ({score}/100)"
    else:
        style = ALERT_STYLE["ok"]
        icon = '<span class="ksk-dot ok"></span>'
        label = f"Low recession signal ({score}/100)"

    factors_html = ""
    if rec["factors"]:
        factors_html = "<br>".join([f"• {f}" for f in rec["factors"]])
    else:
        factors_html = "No recession signals triggered"

    st.markdown(
        f'<div style="{style}">{icon} <strong>{label}</strong><br>'
        f'<span style="font-weight:normal; font-size:var(--fs-sm);">{factors_html}</span></div>',
        unsafe_allow_html=True,
    )

    # ── One-glance regime panel: curve · credit · Fed path ─────────────
    from data.macro_indicators import curve_regime, credit_regime, fed_path

    def _value_days_ago(series_id: str, days: int):
        df = fetch_series(series_id, years=2)
        if df is None or df.empty:
            return None
        d = df.dropna(subset=["value"]).sort_values("date")
        if d.empty:
            return None
        cutoff = d["date"].iloc[-1] - pd.Timedelta(days=days)
        prior = d[d["date"] <= cutoff]
        return float(prior["value"].iloc[-1]) if not prior.empty else None

    # Use latest_value (the keyless fetch_series path the recession score uses)
    # rather than the macro snapshot, which intermittently returns None for
    # individual curve series and would flash the curve regime to n/a.
    s2 = latest_value("T10Y2Y")
    s3m = latest_value("T10Y3M")
    s2_prior = _value_days_ago("T10Y2Y", 90)
    hy = latest_value("BAMLH0A0HYM2")
    ff = latest_value("FEDFUNDS")
    ff_prior = _value_days_ago("FEDFUNDS", 180)

    curve = curve_regime(s2, s3m, s2_prior)
    credit = credit_regime(hy)
    path = fed_path(ff, ff_prior)

    def _dot(level: str) -> str:
        return f'<span class="ksk-dot {level if level in ("ok", "warn", "bad") else "warn"}"></span>'

    curve_state = curve["shape"] + (f", {curve['direction']}" if curve["direction"] else "")
    curve_detail = (f"10Y−2Y {s2:+.2f}pp · 10Y−3M {s3m:+.2f}pp"
                    if (s2 is not None and s3m is not None) else "n/a")
    credit_detail = f"HY OAS {hy * 100:.0f} bps" if hy is not None else "n/a"
    if path["change"] is not None and ff is not None:
        path_detail = f"Fed Funds {ff:.2f}% · {path['change']:+.2f}pp / 6mo"
    elif ff is not None:
        path_detail = f"Fed Funds {ff:.2f}%"
    else:
        path_detail = "n/a"

    panel = [
        ("Yield Curve", curve["level"], curve_state, curve_detail),
        ("Credit", credit["level"], credit["label"], credit_detail),
        ("Fed Path", path["level"], path["direction"], path_detail),
    ]
    body = "".join(
        "<tr>"
        f'<td>{dim}</td>'
        f'<td style="text-align:left;">{_dot(level)}{state}</td>'
        f'<td style="text-align:left;color:var(--text-secondary);">{detail}</td>'
        "</tr>"
        for dim, level, state, detail in panel
    )
    st.markdown(
        '<div class="ksk-grid"><table style="width:100%;">'
        '<thead><tr><th style="text-align:left;">Dimension</th>'
        '<th style="text-align:left;">State</th>'
        '<th style="text-align:left;">Detail</th></tr></thead>'
        f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Curve: 10Y−2Y / 10Y−3M shape + 3-month direction. Credit: HY OAS band "
        "(Tight <350 · Normal 350–500 · Elevated 500–800 · Stressed ≥800 bps). "
        "Fed Path: change in the effective funds rate over 6 months. Source: FRED."
    )


# ── Rates & Curve: cached board + chart builders ─────────────────────────
# Same playbook as the Economic Data grid: a dense board on the left + a 2×2
# grid of bordered chart cards on the right, all @st.cache_data-memoized and
# keyed on the timeframe window. Only warm-cached SERIES are used so reads
# stay fast (no live FRED on the render thread).
_RATE_GRID_H = 296
_CURVE_PALETTE = {
    "DGS3MO": ("3M", "#93c5fd"),
    "DGS2":   ("2Y", "#60a5fa"),
    "DGS5":   ("5Y", "#3b82f6"),
    "DGS10":  ("10Y", "#2563eb"),
    "DGS30":  ("30Y", "#1e3a8a"),
}
# (group, series_id, label, unit) — unit drives Latest formatting.
_RATE_BOARD = [
    ("Policy",   "DFF",          "Fed Funds (effective)", "%"),
    ("Treasury", "DGS3MO",       "3-Month",      "%"),
    ("Treasury", "DGS2",         "2-Year",       "%"),
    ("Treasury", "DGS5",         "5-Year",       "%"),
    ("Treasury", "DGS10",        "10-Year",      "%"),
    ("Treasury", "DGS30",        "30-Year",      "%"),
    ("Spreads",  "T10Y2Y",       "10Y − 2Y",     "pp"),
    ("Spreads",  "T10Y3M",       "10Y − 3M",     "pp"),
    ("Credit",   "BAMLH0A0HYM2", "HY OAS",       "bps"),
    ("Consumer", "MORTGAGE30US", "30-Yr Mortgage", "%"),
]


def _fmt_rate(v, unit) -> str:
    if v is None or v != v:
        return _NA_HTML
    if unit == "bps":
        return f"{v * 100:,.0f} bps"
    if unit == "pp":
        return f"{v:+.2f}pp"
    return f"{v:.2f}%"


def _fmt_rate_delta(dbps) -> str:
    """Signed change in basis points, neutral color (direction, not good/bad —
    rising/falling yields aren't inherently better)."""
    if dbps is None or dbps != dbps:
        return '<span style="color:var(--text-muted);">—</span>'
    if abs(dbps) < 0.5:
        return '<span style="color:var(--text-secondary);">0</span>'
    return f'<span style="color:var(--text-secondary);">{dbps:+.0f}</span>'


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _rates_board_rows():
    from data.macro_indicators import basis_series, zscore_latest
    rows = []
    for group, sid, label, unit in _RATE_BOARD:
        df = fetch_series(sid, years=11)
        d = (df.dropna(subset=["value"]).sort_values("date")
             if df is not None and not df.empty else None)
        latest = as_of = d1w = d3m = z = None
        spark = []
        if d is not None and not d.empty:
            latest = float(d["value"].iloc[-1])
            as_of = d["date"].iloc[-1]

            def _ago(days):
                cut = as_of - pd.Timedelta(days=days)
                prior = d[d["date"] <= cut]
                return float(prior["value"].iloc[-1]) if not prior.empty else None
            p1w, p3m = _ago(7), _ago(91)
            d1w = (latest - p1w) * 100 if p1w is not None else None
            d3m = (latest - p3m) * 100 if p3m is not None else None
            bs = basis_series(df, "level_pct")
            if bs is not None and not bs.empty:
                cut = bs["date"].iloc[-1] - pd.Timedelta(days=365 * 3)
                spark = [float(v) for v in bs[bs["date"] >= cut]["value"].tolist() if v == v]
            z = zscore_latest(df, "level_pct", years=10)
        rows.append({"group": group, "label": label, "unit": unit, "latest": latest,
                     "d1w": d1w, "d3m": d3m, "z": z, "spark": spark, "as_of": as_of})
    return rows


def _rates_board_table(rows) -> str:
    import html as _h
    groups = []
    for r in rows:
        if r["group"] not in groups:
            groups.append(r["group"])
    body = ""
    for g in groups:
        body += (f'<tr><td colspan="7" style="text-align:left;background:var(--grid-head-bg);'
                 'color:var(--brand-primary);font-weight:700;text-transform:uppercase;'
                 f'font-size:var(--fs-2xs);letter-spacing:0.06em;">{_h.escape(g)}</td></tr>')
        for r in (x for x in rows if x["group"] == g):
            aod = (r["as_of"].strftime("%b %d").replace(" 0", " ") if r["as_of"] is not None else "—")
            body += (
                "<tr>"
                f'<td style="text-align:left;">{_h.escape(r["label"])}</td>'
                f'<td style="text-align:right;font-weight:600;">{_fmt_rate(r["latest"], r["unit"])}</td>'
                f'<td style="text-align:right;">{_fmt_rate_delta(r["d1w"])}</td>'
                f'<td style="text-align:right;">{_fmt_rate_delta(r["d3m"])}</td>'
                f'<td style="text-align:right;">{_fmt_z(r["z"])}</td>'
                f'<td style="text-align:center;">{_sparkline_svg(r["spark"])}</td>'
                f'<td style="text-align:right;color:var(--text-secondary);">{aod}</td>'
                "</tr>"
            )
    return (
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Instrument</th>'
        '<th style="text-align:right;">Latest</th>'
        '<th style="text-align:right;">Δ1W</th>'
        '<th style="text-align:right;">Δ3M</th>'
        '<th style="text-align:right;">vs hist</th>'
        '<th style="text-align:center;">Trend (3Y)</th>'
        '<th style="text-align:right;">As of</th></tr></thead><tbody>'
        + body + "</tbody></table></div>"
    )


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_yield_curve():
    import plotly.graph_objects as go

    def _at(df, days):
        if df is None or df.empty:
            return None
        d = df.dropna(subset=["value"]).sort_values("date")
        if d.empty:
            return None
        if days == 0:
            return float(d["value"].iloc[-1])
        cut = d["date"].iloc[-1] - pd.Timedelta(days=days)
        prior = d[d["date"] <= cut]
        return float(prior["value"].iloc[-1]) if not prior.empty else None
    tenors = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"),
              ("10Y", "DGS10"), ("30Y", "DGS30")]
    labels, cur, m3, y1 = [], [], [], []
    for lbl, sid in tenors:
        d = fetch_series(sid, years=2)
        labels.append(lbl)
        cur.append(_at(d, 0))
        m3.append(_at(d, 90))
        y1.append(_at(d, 365))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=y1, name="1Y ago", mode="lines+markers",
        line=dict(color="#cbd5e1", width=2, dash="dot"), marker=dict(size=5, color="#cbd5e1")))
    fig.add_trace(go.Scatter(x=labels, y=m3, name="3M ago", mode="lines+markers",
        line=dict(color="#93c5fd", width=2, dash="dash"), marker=dict(size=5, color="#93c5fd")))
    fig.add_trace(go.Scatter(x=labels, y=cur, name="Today", mode="lines+markers+text",
        line=dict(color="#2563eb", width=3), marker=dict(size=9, color="#2563eb"),
        text=[f"{v:.2f}%" if v is not None else "" for v in cur],
        textposition="top center", textfont=dict(size=10, color="#1e3a8a")))
    apply_standard_layout(fig, title="Treasury yield curve — today vs 3M & 1Y ago",
                          height=_RATE_GRID_H, yaxis_title="Yield", xaxis_title="Maturity",
                          hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_rate_history(window="5Y"):
    import plotly.graph_objects as go
    fig = go.Figure()
    ff = _win(fetch_series("DFF", years=_FETCH_YEARS), window)
    if ff is not None and not ff.empty:
        fig.add_trace(go.Scatter(x=ff["date"], y=ff["value"], name="Fed Funds",
            mode="lines", line=dict(color="#64748b", width=2, dash="dot")))
    for sid, (label, color) in _CURVE_PALETTE.items():
        d = _win(fetch_series(sid, years=_FETCH_YEARS), window)
        if d is not None and not d.empty:
            fig.add_trace(go.Scatter(x=d["date"], y=d["value"], name=label,
                mode="lines", line=dict(color=color, width=2)))
    apply_standard_layout(fig, title=f"Rate history ({window})", height=_RATE_GRID_H,
                          yaxis_title="Rate")
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_curve_spreads(window="5Y"):
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_hrect(y0=-4, y1=0, fillcolor="rgba(220,38,38,0.07)", line_width=0, layer="below")
    for sid, label, color, w in [("T10Y2Y", "10Y − 2Y", "#93c5fd", 1.8),
                                 ("T10Y3M", "10Y − 3M (recession signal)", "#dc2626", 2.6)]:
        d = _win(fetch_series(sid, years=_FETCH_YEARS), window)
        if d is not None and not d.empty:
            fig.add_trace(go.Scatter(x=d["date"], y=d["value"], name=label,
                mode="lines", line=dict(color=color, width=w)))
    fig.add_hline(y=0, line_color="#94a3b8", line_width=1, line_dash="dash")
    apply_standard_layout(fig, title=f"Curve spreads ({window}) — below 0 = inverted",
                          height=_RATE_GRID_H, yaxis_title="Spread")
    fig.update_yaxes(ticksuffix="pp")
    return fig


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _fig_mortgage_10y(window="5Y"):
    import plotly.graph_objects as go
    fig = go.Figure()
    mort = _win(fetch_series("MORTGAGE30US", years=_FETCH_YEARS), window)
    t10 = _win(fetch_series("DGS10", years=_FETCH_YEARS), window)
    if mort is not None and not mort.empty:
        fig.add_trace(go.Scatter(x=mort["date"], y=mort["value"], name="30Y Mortgage",
            mode="lines", line=dict(color="#d97706", width=2.4)))
    if t10 is not None and not t10.empty:
        fig.add_trace(go.Scatter(x=t10["date"], y=t10["value"], name="10Y Treasury",
            mode="lines", line=dict(color="#2563eb", width=2)))
    apply_standard_layout(fig, title=f"Mortgage vs 10Y ({window}) — the gap is the spread",
                          height=_RATE_GRID_H, yaxis_title="Rate")
    fig.update_yaxes(ticksuffix="%")
    return fig


@st.fragment
def _render_rates_charts():
    """Timeframe selector + 2×2 grid of bordered chart cards (in a fragment so
    changing the timeframe re-runs only the charts, not the board)."""
    st.markdown(
        "<style>"
        'div[class*="st-key-ratechart"]{padding:2px 6px 0!important;}'
        'div[class*="st-key-ratechart"] [data-testid="stElementContainer"]'
        "{padding:0!important;margin:0!important;}"
        'div[class*="st-key-ratechart"] [data-testid="stVerticalBlock"]{gap:0!important;}'
        "</style>",
        unsafe_allow_html=True,
    )
    window = st.segmented_control(
        "Timeframe", _TF_OPTIONS, default="5Y", key="rates_grid_tf",
        label_visibility="collapsed") or "5Y"
    ca, cb = st.columns(2)
    with ca:
        with st.container(border=True, key="ratechart_curve"):
            st.plotly_chart(_fig_yield_curve(), use_container_width=True)
        with st.container(border=True, key="ratechart_spreads"):
            st.plotly_chart(_fig_curve_spreads(window), use_container_width=True)
    with cb:
        with st.container(border=True, key="ratechart_hist"):
            st.plotly_chart(_fig_rate_history(window), use_container_width=True)
        with st.container(border=True, key="ratechart_mort"):
            st.plotly_chart(_fig_mortgage_10y(window), use_container_width=True)


def _render_rates_curve():
    # Lead with the dense, scannable part (rates board + chart grid). Under the
    # board, two bordered cards: the Fed policy strip on top, the SEP table +
    # dot-plot beneath it. The FOMC's own words (statement + headlines) follow
    # full-width below.
    st.markdown(
        "<style>"
        'div[class*="st-key-fedcard"]{padding:6px 12px 8px!important;}'
        'div[class*="st-key-fedcard"] [data-testid="stElementContainer"]{margin:0!important;}'
        "</style>",
        unsafe_allow_html=True,
    )
    # Narrow left rail (board + the stacked Fed card) running full height;
    # the chart grid + the FOMC's own words fill the wide right column.
    board_col, chart_col = st.columns([1, 2.9])
    with board_col:
        st.markdown("**Rates & curve board**")
        st.markdown(_rates_board_table(_rates_board_rows()), unsafe_allow_html=True)
        st.caption(
            "Latest level per instrument; Δ 1W / Δ 3M in basis points; vs hist = "
            "z-score of the level vs ~10y of its own history (±σ, bold if |z|≥2). "
            "HY OAS shown in bps over Treasuries. Source: FRED."
        )
        with st.container(border=True, key="fedcard"):
            _render_fed_policy_strip()
            _render_sep_block()
    with chart_col:
        _render_rates_charts()
        st.markdown("---")
        # ── The FOMC's own words: latest statement + curated Fed headlines ──
        _render_fed_words()


# Full credit-quality ladder (ICE BofA OAS, % over Treasuries) — IG buckets
# AAA->BBB and HY buckets BB->CCC, plus the two master indices.
_CREDIT_LADDER = [
    ("Investment grade", "BAMLC0A0CM",   "IG (Corp Master)"),
    ("Investment grade", "BAMLC0A1CAAA", "AAA"),
    ("Investment grade", "BAMLC0A2CAA",  "AA"),
    ("Investment grade", "BAMLC0A3CA",   "A"),
    ("Investment grade", "BAMLC0A4CBBB", "BBB"),
    ("High yield",       "BAMLH0A0HYM2", "HY (Master)"),
    ("High yield",       "BAMLH0A1HYBB", "BB"),
    ("High yield",       "BAMLH0A2HYB",  "B"),
    ("High yield",       "BAMLH0A3HYC",  "CCC & lower"),
]


@st.cache_data(ttl=_FIG_TTL, show_spinner=False)
def _credit_oas_data():
    """Latest OAS + 3M/1Y change (bps), 5Y percentile and a 5Y spark for each
    ICE BofA OAS series in the ladder (not in the warm SERIES set, so cached)."""
    out = {}
    for _grp, sid, _lbl in _CREDIT_LADDER:
        df = fetch_series(sid, years=6)
        latest = d3m = d1y = pctile = as_of = None
        spark = []
        if df is not None and not df.empty:
            d = df.dropna(subset=["value"]).sort_values("date")
            if not d.empty:
                latest = float(d["value"].iloc[-1])
                as_of = d["date"].iloc[-1]
                p3 = d[d["date"] <= as_of - pd.Timedelta(days=91)]
                if not p3.empty:
                    d3m = (latest - float(p3["value"].iloc[-1])) * 100
                p1 = d[d["date"] <= as_of - pd.Timedelta(days=365)]
                if not p1.empty:
                    d1y = (latest - float(p1["value"].iloc[-1])) * 100
                cut5 = as_of - pd.Timedelta(days=365 * 5)
                spark = [float(v) for v in d[d["date"] >= cut5]["value"].tolist()]
                if spark:
                    pctile = round(100 * sum(1 for v in spark if v <= latest) / len(spark))
        out[sid] = {"latest": latest, "d3m": d3m, "d1y": d1y, "pctile": pctile,
                    "spark": spark, "as_of": as_of}
    return out


def _render_credit_spreads():
    import plotly.graph_objects as go
    from data.macro_indicators import credit_regime

    data = _credit_oas_data()
    hy = data["BAMLH0A0HYM2"]["latest"]
    ig = data["BAMLC0A0CM"]["latest"]
    diff = (hy - ig) if (hy is not None and ig is not None) else None
    asof = data["BAMLH0A0HYM2"]["as_of"]
    asof_txt = asof.strftime("%b %d, %Y").replace(" 0", " ") if asof is not None else "-"

    reg = credit_regime(hy)
    dot = {"ok": "ok", "warn": "warn", "bad": "bad", "na": "warn"}[reg["level"]]
    style = ALERT_STYLE.get({"ok": "ok", "warn": "medium", "bad": "high", "na": "medium"}[reg["level"]],
                            ALERT_STYLE["medium"])
    hy_bps = f"{hy * 100:.0f} bps" if hy is not None else "n/a"
    st.markdown(
        f'<div style="{style}"><span class="ksk-dot {dot}"></span> '
        f'<strong>Credit regime: {reg["label"]}</strong> &middot; HY OAS {hy_bps}'
        f'<br><span style="font-weight:normal; font-size:var(--fs-sm);">'
        f'Bands on the High Yield OAS: Tight &lt;350 &middot; Normal 350-500 &middot; '
        f'Elevated 500-800 &middot; Stressed &ge;800 bps. As of {asof_txt}.</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "<style>"
        'div[class*="st-key-creditfig"]{padding:2px 6px 0!important;}'
        'div[class*="st-key-creditfig"] [data-testid="stElementContainer"]'
        "{padding:0!important;margin:0!important;}"
        'div[class*="st-key-creditcard"]{padding:6px 10px!important;}'
        "</style>",
        unsafe_allow_html=True,
    )

    def _bps(v):
        return f"{v * 100:,.0f} bps" if v is not None else _NA_HTML

    def _dbps(v):
        if v is None:
            return '<span style="color:var(--text-muted);">-</span>'
        return f'<span style="color:var(--text-secondary);">{v:+.0f}</span>'

    def _pctile(v):
        if v is None:
            return '<span style="color:var(--text-muted);">-</span>'
        return f'<span style="color:var(--text-secondary);">{v}%</span>'

    groups = []
    for grp, _sid, _lbl in _CREDIT_LADDER:
        if grp not in groups:
            groups.append(grp)
    body = ""
    for grp in groups:
        body += (f'<tr><td colspan="6" style="text-align:left;background:var(--grid-head-bg);'
                 'color:var(--brand-primary);font-weight:700;text-transform:uppercase;'
                 f'font-size:var(--fs-2xs);letter-spacing:0.06em;">{grp}</td></tr>')
        for g, sid, label in _CREDIT_LADDER:
            if g != grp:
                continue
            dd = data[sid]
            body += (
                "<tr>"
                f'<td style="text-align:left;">{label}</td>'
                f'<td style="text-align:right;font-weight:600;">{_bps(dd["latest"])}</td>'
                f'<td style="text-align:right;">{_dbps(dd["d3m"])}</td>'
                f'<td style="text-align:right;">{_dbps(dd["d1y"])}</td>'
                f'<td style="text-align:right;">{_pctile(dd["pctile"])}</td>'
                f'<td style="text-align:center;">{_sparkline_svg(dd["spark"])}</td>'
                "</tr>"
            )
    body += (f'<tr><td colspan="6" style="text-align:left;background:var(--grid-head-bg);'
             'color:var(--brand-primary);font-weight:700;text-transform:uppercase;'
             'font-size:var(--fs-2xs);letter-spacing:0.06em;">Risk premium</td></tr>'
             '<tr><td style="text-align:left;">HY - IG differential</td>'
             f'<td style="text-align:right;font-weight:600;">{_bps(diff)}</td>'
             '<td style="text-align:right;color:var(--text-muted);">-</td>'
             '<td style="text-align:right;color:var(--text-muted);">-</td>'
             '<td style="text-align:right;color:var(--text-muted);">-</td>'
             '<td style="text-align:center;color:var(--text-muted);">-</td></tr>')
    table_html = (
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Spread</th>'
        '<th style="text-align:right;">OAS</th>'
        '<th style="text-align:right;">&Delta; 3M</th>'
        '<th style="text-align:right;">&Delta; 1Y</th>'
        '<th style="text-align:right;">5Y %ile</th>'
        '<th style="text-align:center;">Trend (5Y)</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
    )

    # Row 1: ladder table (hugs its card) + the HY/IG/BBB time series + the
    # HY−IG risk-premium line — both line charts at a readable (~2:1) aspect.
    lc, cc, dp = st.columns([1, 1.5, 1.1])
    with lc:
        with st.container(border=True, key="creditcard", height=415):
            st.markdown(table_html, unsafe_allow_html=True)
        st.caption("OAS = option-adjusted spread over Treasuries (ICE BofA via FRED). "
                   "Delta in bps; 5Y %ile = where today sits in the 5Y range (low = tight). "
                   "Source: FRED.")
    with cc:
        with st.container(border=True, key="creditfig_ts", height=415):
            fig = go.Figure()
            last_hy = last_hy_date = None
            data_max = 0.0
            for sid, label, color, width in [
                ("BAMLC0A0CM", "IG OAS", "#1e40af", 1.8),
                ("BAMLC0A4CBBB", "BBB OAS", "#7c3aed", 1.5),
                ("BAMLH0A0HYM2", "HY OAS", "#dc2626", 2.6),
            ]:
                df = fetch_series(sid, years=5)
                if not df.empty:
                    fig.add_trace(go.Scatter(
                        x=df["date"], y=df["value"], name=label, mode="lines",
                        line=dict(color=color, width=width)))
                    vmax = df["value"].dropna().max()
                    if vmax == vmax:
                        data_max = max(data_max, float(vmax))
                    if sid == "BAMLH0A0HYM2":
                        ddh = df.dropna(subset=["value"]).sort_values("date")
                        if not ddh.empty:
                            last_hy = float(ddh["value"].iloc[-1])
                            last_hy_date = ddh["date"].iloc[-1]
            top = max(9.0, data_max * 1.15)
            fig.add_hrect(y0=5.0, y1=8.0, fillcolor="rgba(217,119,6,0.06)", line_width=0, layer="below")
            fig.add_hrect(y0=8.0, y1=top, fillcolor="rgba(220,38,38,0.07)", line_width=0, layer="below")
            fig.update_yaxes(range=[0, top])
            if last_hy is not None:
                fig.add_annotation(
                    x=last_hy_date, y=last_hy,
                    text=f"HY {last_hy * 100:.0f} bps · {reg['label'].lower()}",
                    showarrow=True, arrowhead=0, ax=-66, ay=-24,
                    font=dict(size=10, color="#dc2626"),
                    bgcolor="#ffffff", bordercolor="#e5e7eb", borderpad=3)
            apply_standard_layout(fig, title="Credit spreads (5Y) - IG / BBB / HY OAS with regime bands",
                                  height=372, yaxis_title="OAS")
            fig.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig, use_container_width=True)
        st.caption("Shaded zones mark Elevated (500-800 bps) and Stressed (>=800 bps) HY regimes.")

    with dp:
        with st.container(border=True, key="creditfig_diff", height=415):
            hy_df = fetch_series("BAMLH0A0HYM2", years=5)
            ig_df = fetch_series("BAMLC0A0CM", years=5)
            figd = go.Figure()
            if not hy_df.empty and not ig_df.empty:
                m = hy_df.merge(ig_df, on="date", suffixes=("_hy", "_ig")).dropna()
                m = m.sort_values("date")
                figd.add_trace(go.Scatter(
                    x=m["date"], y=(m["value_hy"] - m["value_ig"]), mode="lines",
                    line=dict(color="#1e3a8a", width=2),
                    fill="tozeroy", fillcolor="rgba(30,58,138,0.06)", name="HY - IG"))
            apply_standard_layout(figd, title="HY − IG risk premium (5Y)",
                                  height=372, yaxis_title="pp", show_legend=False)
            figd.update_yaxes(ticksuffix="pp")
            st.plotly_chart(figd, use_container_width=True)
        st.caption("Extra spread for high yield over investment grade; widens when "
                   "risk appetite falls. Source: FRED.")

    # Row 2: the credit curve (OAS by rating) — a bar chart, full width (bars
    # read fine wide, unlike the line charts above).
    with st.container(border=True, key="creditfig_curve", height=300):
        curve = [(lbl, data[sid]["latest"], grp) for grp, sid, lbl in _CREDIT_LADDER
                 if "Master" not in lbl]
        xs = [c[0] for c in curve]
        ys = [c[1] * 100 if c[1] is not None else None for c in curve]
        colors = ["#1e40af" if c[2] == "Investment grade" else "#dc2626" for c in curve]
        figc = go.Figure(go.Bar(
            x=xs, y=ys, marker_color=colors,
            text=[f"{y:.0f}" if y is not None else "" for y in ys],
            textposition="outside", textfont=dict(size=11), cliponaxis=False))
        ymax = max([y for y in ys if y is not None], default=0)
        apply_standard_layout(figc, title="Credit curve — OAS by rating (bps), AAA → CCC",
                              height=258, yaxis_title="bps", show_legend=False)
        figc.update_yaxes(range=[0, ymax * 1.18 if ymax else 1])
        st.plotly_chart(figc, use_container_width=True)
    st.caption("OAS by rating: blue = investment grade, red = high yield. The curve steepens "
               "sharply into CCC. Source: FRED.")
