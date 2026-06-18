"""
Macro Dashboard — Fed funds, yield curve, credit spreads, unemployment.

Standalone top-level section. Also provides helpers used by Home and NIM pages.
"""

import streamlit as st
import pandas as pd

from data.fred_client import (
    fetch_series, latest_value, get_macro_snapshot, recession_probability, SERIES,
)
from utils.chart_style import (
    apply_standard_layout, tighten_yaxis,
    CHART_HEIGHT_HERO, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT, ALERT_STYLE,
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
    "Funding & Deposits",
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
        "Funding & Deposits": _render_funding_deposits,
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

    # ── Current rates grid: rate, cap, spread to Fed Funds ─────────────
    body = ""
    for field, label in _DEPOSIT_PRODUCTS:
        prod = rates.get(field) or {}
        rate = prod.get("rate_pct")
        cap = prod.get("cap_pct")
        if rate is None and cap is None:
            continue
        rate_txt = f"{rate:.2f}%" if rate is not None else '<span style="color:var(--text-muted);">n/a</span>'
        cap_txt = f"{cap:.2f}%" if cap is not None else '<span style="color:var(--text-muted);">n/a</span>'
        if rate is not None and ff is not None:
            spread = rate - ff
            spread_txt = f"{spread:+.2f}pp"
        else:
            spread_txt = '<span style="color:var(--text-muted);">n/a</span>'
        body += (
            "<tr>"
            f"<td>{_html.escape(label)}</td>"
            f"<td>{rate_txt}</td>"
            f"<td>{cap_txt}</td>"
            f'<td style="color:var(--text-secondary);">{spread_txt}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid"><table style="width:100%;">'
        "<thead><tr>"
        "<th>Product</th><th>National Rate</th><th>Rate Cap</th><th>vs Fed Funds</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption("National Rate = FDIC deposit-weighted national average. Rate Cap = "
               "§337.7 cap (national rate + 75bps, or the Treasury-yield-based cap). "
               "Spread vs Fed Funds shows how far deposit pricing lags policy.")

    # ── History: key deposit rates vs Fed Funds (the deposit-beta picture) ──
    hist = get_national_rate_history(weeks=260)  # ~5y, covers the revised-rule series
    if hist:
        dates = [r["asof"] for r in hist]
        fig = go.Figure()
        for field, label, color in [
            ("savings", "Savings", "#0891b2"),
            ("mmda", "Money Market", "#9333ea"),
            ("cd_12mo", "12-Month CD", "#1e40af"),
        ]:
            ys = [(r.get(field) or {}).get("rate_pct") for r in hist]
            if any(y is not None for y in ys):
                fig.add_trace(go.Scatter(
                    x=dates, y=ys, name=label, mode="lines+markers",
                    line=dict(color=color, width=2), marker=dict(size=4),
                ))
        ffdf = fetch_series("FEDFUNDS", years=5)
        if not ffdf.empty:
            fig.add_trace(go.Scatter(
                x=ffdf["date"], y=ffdf["value"], name="Fed Funds", mode="lines",
                line=dict(color="#64748b", width=2, dash="dot"),
            ))
        apply_standard_layout(fig, title="Deposit rates vs Fed Funds — the deposit-beta picture",
                              height=CHART_HEIGHT_FULL, yaxis_title="Rate")
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Deposit rates rise far less than Fed Funds (low deposit beta) and "
                   "lag both up and down. Source: FDIC national rates · FRED (Fed Funds).")

        # Export the full history (one row per month, rate per product).
        export_rows = []
        for r in hist:
            row = {"asof": r["asof"]}
            for field, label in _DEPOSIT_PRODUCTS:
                row[field] = (r.get(field) or {}).get("rate_pct")
            export_rows.append(row)
        table_export(pd.DataFrame(export_rows), "fdic_national_rates",
                     key="fdic_rates_export")


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


def _render_fed_panel(full: bool = True):
    """Fed policy snapshot + SEP projections, shared by Rates & Curve (full:
    policy strip + dot-plot chart + macro-medians table) and Economic Data
    (compact: policy strip + median funds path). FRED-sourced; the individual
    SEP dots aren't published machine-readably, so the median + central-tendency
    band + full range convey the distribution (see data/fomc.py)."""
    import html as _h
    from data.fomc import fed_policy_snapshot, sep_projections, fetch_fomc_statement

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

    proj = sep_projections()
    funds = proj.get("funds") or []
    sep_asof = proj.get("as_of")
    sep_txt = sep_asof.strftime("%b %Y") if sep_asof is not None else "—"
    if not funds:
        st.caption("FOMC Summary of Economic Projections unavailable.")
        return

    if not full:
        path = " → ".join(f'{f["horizon"]}: {f["median"]:.2f}%'
                          for f in funds if f.get("median") is not None)
        st.caption(f"SEP median fed funds path — {path}. Source: FRED (SEP {sep_txt}).")
        return

    # Dot-plot-style chart: median (diamond) · central-tendency band (thick) ·
    # full range (thin whisker), per horizon.
    import plotly.graph_objects as go
    fig = go.Figure()
    allvals = []
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
                line=dict(color="#93c5fd", width=11), showlegend=False, hoverinfo="skip"))
            allvals += [cl, ch]
        if md is not None:
            fig.add_trace(go.Scatter(x=[x], y=[md], mode="markers",
                marker=dict(symbol="diamond", color="#1e3a8a", size=12), showlegend=False,
                hovertemplate=f"{x}<br>median %{{y:.2f}}%<extra></extra>"))
            allvals.append(md)
    apply_standard_layout(
        fig, title=f"FOMC fed funds projections — median · central tendency · range (SEP {sep_txt})",
        height=300, yaxis_title="%", show_legend=False)
    if allvals:
        tighten_yaxis(fig, allvals, ticksuffix="%")
    st.plotly_chart(fig, use_container_width=True)

    macro = proj.get("macro") or {}
    horizons = [f["horizon"] for f in funds]

    def _mm(key):
        return {m["horizon"]: m.get("median") for m in (macro.get(key) or [])}
    gdp, ur, pce, cpce = _mm("gdp"), _mm("unemployment"), _mm("pce"), _mm("core_pce")
    fundmed = {f["horizon"]: f.get("median") for f in funds}
    body = ""
    for hz in horizons:
        body += (
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
        '<th style="text-align:right;">Fed funds</th>'
        '<th style="text-align:right;">Real GDP</th>'
        '<th style="text-align:right;">Unemployment</th>'
        '<th style="text-align:right;">PCE</th>'
        '<th style="text-align:right;">Core PCE</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption("FOMC median projections by horizon; fed funds shown with central-tendency band "
               "+ full range in the chart above. Individual participant dots aren't published "
               f"machine-readably — band/range conveys the spread. Source: FRED (SEP {sep_txt}).")

    # ── Statement & commentary: the FOMC's own words + recent Fed headlines ──
    st.markdown("---")
    sc1, sc2 = st.columns([1.4, 1])
    with sc1:
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
    with sc2:
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


def _render_economy_calendar():
    import html as _html
    import plotly.graph_objects as go
    from datetime import date as _date, datetime as _dt
    from data.macro_indicators import get_print_board, to_yoy, to_mom_change
    from data.econ_calendar import get_recent_releases, get_upcoming_releases
    from ui.chrome import table_export

    # ── Calendars stacked (left) · Key indicators board (right) ──
    recent = get_recent_releases(days=10, limit=16)
    up = get_upcoming_releases(days=14, limit=20)
    rows = get_print_board()
    # Calendars (left) + board (right) packed adjacent; leftover to the right
    # edge. The board is ONE table (themes as sub-sections) so every row aligns.
    cal_col, board_col, _spacer = st.columns([1, 1, 1.1])
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

    st.markdown("---")

    # ── Trend charts — 3-up at a taller aspect, NBER recessions shaded ──
    st.markdown("**Trend charts**")
    c1, c2, c3 = st.columns(3)
    with c1:
        figi = go.Figure()
        for sid, label, color in [
            ("CPIAUCSL", "CPI", "#1e40af"),
            ("CPILFESL", "Core CPI", "#3b82f6"),
            ("PCEPILFE", "Core PCE", "#d97706"),
        ]:
            s = to_yoy(fetch_series(sid, years=6))
            if not s.empty:
                cutoff = s["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
                s = s[s["date"] >= cutoff]
                figi.add_trace(go.Scatter(
                    x=s["date"], y=s["value"], name=label, mode="lines",
                    line=dict(color=color, width=2),
                ))
        _shade_recessions(figi)
        figi.add_hline(y=2.0, line_color="#059669", line_width=1, line_dash="dash",
                       annotation_text="Fed 2% target", annotation_position="top left",
                       annotation_font=dict(size=10, color="#059669"))
        apply_standard_layout(figi, title="Inflation — YoY % (5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="YoY")
        figi.update_yaxes(ticksuffix="%")
        st.plotly_chart(figi, use_container_width=True)

    with c2:
        figl = go.Figure()
        nfp = to_mom_change(fetch_series("PAYEMS", years=6))
        if not nfp.empty:
            cutoff = nfp["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
            nfp = nfp[nfp["date"] >= cutoff]
            bar_colors = ["#dc2626" if v < 0 else "#3b82f6" for v in nfp["value"]]
            figl.add_trace(go.Bar(
                x=nfp["date"], y=nfp["value"], name="Payrolls Δ (000s)",
                marker_color=bar_colors, yaxis="y",
            ))
        unr = fetch_series("UNRATE", years=6)
        if not unr.empty:
            cutoff = unr["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
            unr = unr[unr["date"] >= cutoff]
            figl.add_trace(go.Scatter(
                x=unr["date"], y=unr["value"], name="Unemployment %",
                mode="lines", line=dict(color="#0f172a", width=2), yaxis="y2",
            ))
        _shade_recessions(figl)
        apply_standard_layout(figl, title="Labor — payrolls Δ & unemployment (5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="Jobs Δ (000s)")
        figl.update_layout(
            yaxis2=dict(title="Unemp %", overlaying="y", side="right",
                        ticksuffix="%", showgrid=False),
        )
        st.plotly_chart(figl, use_container_width=True)

    with c3:
        figg = go.Figure()
        gdp = fetch_series("A191RL1Q225SBEA", years=6)
        if not gdp.empty:
            g = gdp.dropna(subset=["value"]).sort_values("date")
            cutoff = g["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
            g = g[g["date"] >= cutoff]
            gcolors = ["#dc2626" if v < 0 else "#1e40af" for v in g["value"]]
            figg.add_trace(go.Bar(x=g["date"], y=g["value"], name="Real GDP QoQ SAAR",
                                  marker_color=gcolors))
        _shade_recessions(figg)
        apply_standard_layout(figg, title="Growth — Real GDP (QoQ SAAR, 5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="QoQ SAAR",
                              show_legend=False)
        figg.update_yaxes(ticksuffix="%")
        st.plotly_chart(figg, use_container_width=True)

    c4, c5, c6 = st.columns(3)
    with c4:
        figa = go.Figure()
        for sid, label, color in [
            ("INDPRO", "Industrial Production", "#1e40af"),
            ("RSAFS", "Retail Sales", "#d97706"),
        ]:
            s = to_yoy(fetch_series(sid, years=6))
            if not s.empty:
                cutoff = s["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
                s = s[s["date"] >= cutoff]
                figa.add_trace(go.Scatter(
                    x=s["date"], y=s["value"], name=label, mode="lines",
                    line=dict(color=color, width=2)))
        _shade_recessions(figa)
        apply_standard_layout(figa, title="Activity — Industrial Production & Retail (YoY, 5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="YoY")
        figa.update_yaxes(ticksuffix="%")
        st.plotly_chart(figa, use_container_width=True)

    with c5:
        figh = go.Figure()
        for sid, label, color in [
            ("HOUST", "Housing Starts", "#1e40af"),
            ("PERMIT", "Building Permits", "#d97706"),
        ]:
            s = fetch_series(sid, years=6)
            if not s.empty:
                d = s.dropna(subset=["value"]).sort_values("date")
                cutoff = d["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
                d = d[d["date"] >= cutoff]
                figh.add_trace(go.Scatter(
                    x=d["date"], y=d["value"], name=label, mode="lines",
                    line=dict(color=color, width=2)))
        _shade_recessions(figh)
        apply_standard_layout(figh, title="Housing — Starts & Permits (000s SAAR, 5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="000s")
        st.plotly_chart(figh, use_container_width=True)

    with c6:
        figm = go.Figure()
        sent = fetch_series("UMCSENT", years=6)
        if not sent.empty:
            d = sent.dropna(subset=["value"]).sort_values("date")
            cutoff = d["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
            d = d[d["date"] >= cutoff]
            figm.add_trace(go.Scatter(
                x=d["date"], y=d["value"], name="UMich Sentiment",
                mode="lines", line=dict(color="#1e40af", width=2), yaxis="y"))
        m2 = to_yoy(fetch_series("M2SL", years=6))
        if not m2.empty:
            cutoff = m2["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
            m2 = m2[m2["date"] >= cutoff]
            figm.add_trace(go.Scatter(
                x=m2["date"], y=m2["value"], name="M2 YoY", mode="lines",
                line=dict(color="#d97706", width=2), yaxis="y2"))
        _shade_recessions(figm)
        apply_standard_layout(figm, title="Sentiment & Money — UMich & M2 YoY (5Y)",
                              height=CHART_HEIGHT_HERO, yaxis_title="Sentiment")
        figm.update_layout(
            yaxis2=dict(title="M2 YoY %", overlaying="y", side="right",
                        ticksuffix="%", showgrid=False),
        )
        st.plotly_chart(figm, use_container_width=True)

    # ── Explore any indicator (interactive; charts any of the 27 on demand) ──
    st.markdown("---")
    st.markdown("**Explore any indicator**")
    from data.macro_indicators import INDICATORS
    by_label = {s["label"]: s for s in INDICATORS}
    _defaults = [d for d in ("CPI", "Nonfarm Payrolls", "Real GDP (QoQ SAAR)")
                 if d in by_label]
    picks = st.multiselect("Indicators", list(by_label.keys()), default=_defaults,
                           key="macro_explore", label_visibility="collapsed")
    if picks:
        ecols = st.columns(3)
        for i, lbl in enumerate(picks):
            with ecols[i % 3]:
                st.plotly_chart(_macro_trend_fig(by_label[lbl]), use_container_width=True)
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


def _render_rates_curve():
    # ── Federal Reserve / FOMC (full: policy + dot-plot + projections) ──
    _render_fed_panel(full=True)
    st.markdown("---")

    # ── Key Macro KPIs ─────────────────────────────────────────────────
    snap = get_macro_snapshot()
    ff = snap.get("FEDFUNDS", {}).get("value")
    t2 = snap.get("DGS2", {}).get("value")
    t10 = snap.get("DGS10", {}).get("value")
    t30 = snap.get("DGS30", {}).get("value")
    spread_2y = snap.get("T10Y2Y", {}).get("value")
    spread_3m = snap.get("T10Y3M", {}).get("value")
    mortgage = snap.get("MORTGAGE30US", {}).get("value")
    unemp = snap.get("UNRATE", {}).get("value")
    hy_spread = snap.get("BAMLH0A0HYM2", {}).get("value")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Fed Funds", f"{ff:.2f}%" if ff is not None else "—",
                  delta=snap.get("FEDFUNDS", {}).get("date"))
    with c2:
        st.metric("10Y Treasury", f"{t10:.2f}%" if t10 is not None else "—",
                  delta=f"2Y: {t2:.2f}%" if t2 is not None else None, delta_color="off")
    with c3:
        st.metric("10Y - 2Y Spread", f"{spread_2y:+.2f}pp" if spread_2y is not None else "—",
                  delta="Inverted" if (spread_2y is not None and spread_2y < 0) else "Normal",
                  delta_color="inverse")
    with c4:
        st.metric("30Y Mortgage", f"{mortgage:.2f}%" if mortgage is not None else "—")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        st.metric("Unemployment", f"{unemp:.1f}%" if unemp is not None else "—")
    with c6:
        st.metric("HY Spread", f"{hy_spread:.2f}%" if hy_spread is not None else "—")
    with c7:
        st.metric("10Y - 3M Spread", f"{spread_3m:+.2f}pp" if spread_3m is not None else "—",
                  delta="Inverted" if (spread_3m is not None and spread_3m < 0) else "Normal",
                  delta_color="inverse")
    with c8:
        st.metric("30Y Treasury", f"{t30:.2f}%" if t30 is not None else "—")

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    # Cohesive treasury palette: short→long maturities run light→dark blue so
    # the family reads as one curve; Fed funds (policy) is slate, non-treasury
    # series get a distinct accent.
    CURVE = {
        "DGS3MO": ("3M", "#93c5fd"),
        "DGS2":   ("2Y", "#60a5fa"),
        "DGS5":   ("5Y", "#3b82f6"),
        "DGS10":  ("10Y", "#2563eb"),
        "DGS30":  ("30Y", "#1e3a8a"),
    }
    import plotly.graph_objects as go

    # ── Chart 1: the actual yield curve — today / 3M ago / 1Y ago ──────
    def _at(df, days_ago):
        if df is None or df.empty:
            return None
        d = df.dropna(subset=["value"]).sort_values("date")
        if d.empty:
            return None
        if days_ago == 0:
            return float(d["value"].iloc[-1])
        cutoff = d["date"].iloc[-1] - pd.Timedelta(days=days_ago)
        prior = d[d["date"] <= cutoff]
        return float(prior["value"].iloc[-1]) if not prior.empty else None

    tenors = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"),
              ("10Y", "DGS10"), ("30Y", "DGS30")]
    labels, cur_y, m3_y, y1_y = [], [], [], []
    for lbl, sid in tenors:
        d = fetch_series(sid, years=2)
        labels.append(lbl)
        cur_y.append(_at(d, 0)); m3_y.append(_at(d, 90)); y1_y.append(_at(d, 365))

    figc = go.Figure()
    figc.add_trace(go.Scatter(
        x=labels, y=y1_y, name="1Y ago", mode="lines+markers",
        line=dict(color="#cbd5e1", width=2, dash="dot"),
        marker=dict(size=5, color="#cbd5e1"),
    ))
    figc.add_trace(go.Scatter(
        x=labels, y=m3_y, name="3M ago", mode="lines+markers",
        line=dict(color="#93c5fd", width=2, dash="dash"),
        marker=dict(size=5, color="#93c5fd"),
    ))
    figc.add_trace(go.Scatter(
        x=labels, y=cur_y, name="Today", mode="lines+markers+text",
        line=dict(color="#2563eb", width=3),
        marker=dict(size=9, color="#2563eb"),
        text=[f"{v:.2f}%" if v is not None else "" for v in cur_y],
        textposition="top center", textfont=dict(size=11, color="#1e3a8a"),
    ))
    apply_standard_layout(figc, title="Treasury Yield Curve — today vs 3M & 1Y ago",
                          height=CHART_HEIGHT_FULL, yaxis_title="Yield",
                          xaxis_title="Maturity", hovermode="x unified")
    figc.update_yaxes(ticksuffix="%")
    st.plotly_chart(figc, use_container_width=True)

    # ── Chart 2: rate history (3Y), cohesive palette ───────────────────
    fig1 = go.Figure()
    ffdf = fetch_series("FEDFUNDS", years=3)
    if not ffdf.empty:
        fig1.add_trace(go.Scatter(
            x=ffdf["date"], y=ffdf["value"], name="Fed Funds",
            mode="lines", line=dict(color="#64748b", width=2, dash="dot"),
        ))
    for sid, (label, color) in CURVE.items():
        df = fetch_series(sid, years=3)
        if not df.empty:
            fig1.add_trace(go.Scatter(
                x=df["date"], y=df["value"], name=label, mode="lines",
                line=dict(color=color, width=2),
            ))
    apply_standard_layout(fig1, title="Rate History (3Y)", height=CHART_HEIGHT_FULL,
                          yaxis_title="Rate")
    fig1.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig1, use_container_width=True)

    # Chart 3: curve spreads — 10Y-3M (the NY Fed recession indicator) is
    # emphasized, with the inverted (<0) zone shaded red and a live callout.
    fig2 = go.Figure()
    fig2.add_hrect(y0=-4, y1=0, fillcolor="rgba(220,38,38,0.07)",
                   line_width=0, layer="below")
    last_3m, last_3m_date = None, None
    for sid, label, color, width in [
        ("T10Y2Y", "10Y − 2Y", "#93c5fd", 1.6),
        ("T10Y3M", "10Y − 3M (recession signal)", "#dc2626", 2.8),
    ]:
        df = fetch_series(sid, years=5)
        if not df.empty:
            fig2.add_trace(go.Scatter(
                x=df["date"], y=df["value"], name=label, mode="lines",
                line=dict(color=color, width=width),
            ))
            if sid == "T10Y3M":
                dd = df.dropna(subset=["value"]).sort_values("date")
                if not dd.empty:
                    last_3m = float(dd["value"].iloc[-1])
                    last_3m_date = dd["date"].iloc[-1]
    fig2.add_hline(y=0, line_color="#94a3b8", line_width=1, line_dash="dash")
    if last_3m is not None:
        inv = last_3m < 0
        fig2.add_annotation(
            x=last_3m_date, y=last_3m,
            text=f"10Y−3M {last_3m:+.2f}pp · {'inverted' if inv else 'normal'}",
            showarrow=True, arrowhead=0, ax=-70, ay=-26,
            font=dict(size=10, color="#dc2626" if inv else "#059669"),
            bgcolor="#ffffff", bordercolor="#e5e7eb", borderpad=3,
        )
    apply_standard_layout(fig2, title="Curve Spreads (5Y) — 10Y−3M is the recession signal",
                          height=CHART_HEIGHT_COMPACT, yaxis_title="Spread")
    fig2.update_yaxes(ticksuffix="pp")
    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")
    st.caption(
        "Data from FRED (Federal Reserve Economic Data). Refreshed daily. "
        "Recession score combines 10Y-2Y spread, 10Y-3M spread (NY Fed indicator), "
        "and Sahm Rule proxy on unemployment."
    )


def _render_credit_spreads():
    import plotly.graph_objects as go
    from data.macro_indicators import credit_regime
    from data.fred_client import latest_date
    from ui.chrome import ledger

    # ICE BofA OAS series (percent / bps-over-treasuries).
    hy = latest_value("BAMLH0A0HYM2")     # US High Yield
    ig = latest_value("BAMLC0A0CM")       # US Corporate (investment grade)
    bbb = latest_value("BAMLC0A4CBBB")    # BBB
    ccc = latest_value("BAMLH0A3HYC")     # CCC & lower
    asof = latest_date("BAMLH0A0HYM2")
    asof_txt = asof.strftime("%b %d, %Y").replace(" 0", " ") if asof is not None else "—"

    # ── Credit regime banner (shared classifier, also used by Regime) ──
    reg = credit_regime(hy)
    dot = {"ok": "ok", "warn": "warn", "bad": "bad", "na": "warn"}[reg["level"]]
    style = ALERT_STYLE.get({"ok": "ok", "warn": "medium", "bad": "high", "na": "medium"}[reg["level"]],
                            ALERT_STYLE["medium"])
    hy_bps = f"{hy * 100:.0f} bps" if hy is not None else "n/a"
    st.markdown(
        f'<div style="{style}"><span class="ksk-dot {dot}"></span> '
        f'<strong>Credit regime: {reg["label"]}</strong> · HY OAS {hy_bps}'
        f'<br><span style="font-weight:normal; font-size:var(--fs-sm);">'
        f'Bands on the High Yield OAS: Tight &lt;350 · Normal 350–500 · '
        f'Elevated 500–800 · Stressed ≥800 bps. As of {asof_txt}.</span></div>',
        unsafe_allow_html=True,
    )

    lc, cc = st.columns([1, 2])
    with lc:
        def _bps(v):
            return f"{v * 100:.0f} bps" if v is not None else '<span style="color:var(--text-muted);">n/a</span>'
        diff = (hy - ig) if (hy is not None and ig is not None) else None
        ledger("Option-adjusted spreads", [
            ("High Yield (HY)", _bps(hy)),
            ("Investment Grade (IG)", _bps(ig)),
            ("BBB", _bps(bbb)),
            ("CCC & lower", _bps(ccc)),
            ("HY − IG differential", _bps(diff)),
        ])

    with cc:
        fig = go.Figure()
        last_hy = last_hy_date = None
        data_max = 0.0
        for sid, label, color, width in [
            ("BAMLC0A0CM", "IG OAS", "#1e40af", 1.8),
            ("BAMLH0A0HYM2", "HY OAS", "#dc2626", 2.6),
        ]:
            df = fetch_series(sid, years=5)
            if not df.empty:
                fig.add_trace(go.Scatter(
                    x=df["date"], y=df["value"], name=label, mode="lines",
                    line=dict(color=color, width=width),
                ))
                vmax = df["value"].dropna().max()
                if vmax == vmax:  # not NaN
                    data_max = max(data_max, float(vmax))
                if sid == "BAMLH0A0HYM2":
                    dd = df.dropna(subset=["value"]).sort_values("date")
                    if not dd.empty:
                        last_hy = float(dd["value"].iloc[-1])
                        last_hy_date = dd["date"].iloc[-1]
        # Y-axis top: enough to show the Elevated band, but never let the
        # regime shading blow out the scale and flatten the spread lines.
        top = max(9.0, data_max * 1.15)
        # Regime band shading (only the risk-alert zones, to avoid clutter),
        # clipped to the visible range.
        fig.add_hrect(y0=5.0, y1=8.0, fillcolor="rgba(217,119,6,0.06)", line_width=0, layer="below")
        fig.add_hrect(y0=8.0, y1=top, fillcolor="rgba(220,38,38,0.07)", line_width=0, layer="below")
        fig.update_yaxes(range=[0, top])
        if last_hy is not None:
            fig.add_annotation(
                x=last_hy_date, y=last_hy,
                text=f"HY {last_hy * 100:.0f} bps · {reg['label'].lower()}",
                showarrow=True, arrowhead=0, ax=-66, ay=-24,
                font=dict(size=10, color="#dc2626"),
                bgcolor="#ffffff", bordercolor="#e5e7eb", borderpad=3,
            )
        apply_standard_layout(fig, title="Credit spreads (5Y) — HY & IG OAS with regime bands",
                              height=CHART_HEIGHT_FULL, yaxis_title="OAS")
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True)

    st.caption("OAS = option-adjusted spread over Treasuries (ICE BofA indices via FRED). "
               "Shaded zones mark Elevated (500–800 bps) and Stressed (≥800 bps) HY regimes.")
