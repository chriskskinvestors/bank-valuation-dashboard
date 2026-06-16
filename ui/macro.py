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
    CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT, ALERT_STYLE,
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
    "Rates & Curve",
    "Bank Sector",
    "Funding & Deposits",
    "Credit & Spreads",
    "Economy & Calendar",
    "Regime",
]


def render_macro_dashboard():
    """Render the standalone Market & Macro section."""
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
        "Rates & Curve": _render_rates_curve,
        "Bank Sector": _render_bank_sector,
        "Funding & Deposits": _render_funding_deposits,
        "Credit & Spreads": _render_credit_spreads,
        "Economy & Calendar": _render_economy_calendar,
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


def _fmt_signed_pct(v) -> str:
    """Signed % colored green (up) / red (down) — standard price convention."""
    if v is None:
        return '<span style="color:var(--text-muted);">n/a</span>'
    color = "var(--success)" if v > 0 else ("var(--danger)" if v < 0 else "var(--text-secondary)")
    return f'<span style="color:{color};">{v:+.1f}%</span>'


def _render_bank_sector():
    import plotly.graph_objects as go
    from data.bank_etf import get_etf_history, compute_stats, drawdown_series, ETFS, PERIODS
    from ui.chrome import ledger

    names = {e["ticker"]: e["name"] for e in ETFS}
    c_sel, c_per = st.columns([3, 2], vertical_alignment="center")
    with c_sel:
        ticker = st.radio("ETF", [e["ticker"] for e in ETFS], index=0, horizontal=True,
                          format_func=lambda t: t, key="bank_sector_etf",
                          label_visibility="collapsed")
    with c_per:
        period = st.radio("Window", PERIODS, index=1, horizontal=True,
                          format_func=lambda p: p, key="bank_sector_period",
                          label_visibility="collapsed")

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

    def _date(ts):
        return ts.strftime("%b %d, %Y").replace(" 0", " ") if ts is not None else "—"

    # ── Stat ledgers ───────────────────────────────────────────────────
    lc1, lc2 = st.columns(2)
    with lc1:
        ledger("Price", [
            ("Last close", f'${stats["last"]:,.2f}' if stats["last"] is not None else "n/a"),
            (f"Return ({period})", _fmt_signed_pct(stats["period_return_pct"])),
            ("Avg daily volume", _fmt_vol(stats["avg_volume"])),
        ])
    with lc2:
        hi = f'${stats["period_high"]:,.2f}' if stats["period_high"] is not None else "n/a"
        lo = f'${stats["period_low"]:,.2f}' if stats["period_low"] is not None else "n/a"
        ledger("Range", [
            (f"High ({_date(stats['period_high_date'])})", hi),
            ("Low", lo),
            ("Drawdown from high", _fmt_signed_pct(stats["drawdown_from_high_pct"])),
        ])

    # ── Price (close) over the window, with the period-high watermark ──
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
    apply_standard_layout(figp, title=f"{ticker} — price ({period})",
                          height=CHART_HEIGHT_FULL, yaxis_title="Close",
                          show_legend=False)
    figp.update_yaxes(tickprefix="$")
    tighten_yaxis(figp, df["close"].tolist(), tickprefix="$")
    st.plotly_chart(figp, use_container_width=True)

    # ── Drawdown (underwater) + volume ─────────────────────────────────
    dc1, dc2 = st.columns(2)
    with dc1:
        dd = drawdown_series(df)
        figd = go.Figure()
        if not dd.empty:
            figd.add_trace(go.Scatter(
                x=dd["date"], y=dd["value"], name="Drawdown", mode="lines",
                line=dict(color="#dc2626", width=1.5), fill="tozeroy",
                fillcolor="rgba(220, 38, 38, 0.08)",
            ))
        apply_standard_layout(figd, title="Drawdown from running high",
                              height=CHART_HEIGHT_COMPACT, yaxis_title="% below peak",
                              show_legend=False)
        figd.update_yaxes(ticksuffix="%")
        st.plotly_chart(figd, use_container_width=True)
    with dc2:
        figv = go.Figure()
        if "volume" in df.columns and df["volume"].notna().any():
            figv.add_trace(go.Bar(
                x=df["date"], y=df["volume"], name="Volume",
                marker_color="#93c5fd",
            ))
        apply_standard_layout(figv, title="Volume", height=CHART_HEIGHT_COMPACT,
                              yaxis_title="Shares", show_legend=False)
        st.plotly_chart(figv, use_container_width=True)

    st.caption("Source: FMP end-of-day prices. Drawdown = % below the highest "
               "close reached so far within the window.")


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


# ── Economy & Calendar formatting helpers ──────────────────────────────────
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
    if basis == "level_k":
        return f"{v:,.0f}K"
    return f"{v:.1f}"


def _fmt_delta(row: dict) -> str:
    """Signed change vs the prior period, colored by whether the move is
    favorable for this indicator (inflation down = good, jobs up = good, …)."""
    d = row.get("delta")
    if d is None:
        return '<span style="color:var(--text-muted);">n/a</span>'
    basis = row["basis"]
    txt = f"{d:+.1f}pp" if basis in _PCT_BASES else f"{d:+,.0f}K"
    if abs(d) < 1e-9:
        color = "var(--text-secondary)"
    else:
        good = (d < 0) if row["favorable"] == "down" else (d > 0)
        color = "var(--success)" if good else "var(--danger)"
    return f'<span style="color:{color};">{txt}</span>'


def _fmt_as_of(ts, freq: str) -> str:
    """Period label for the latest reading, by series frequency."""
    if ts is None:
        return "—"
    if freq == "Q":
        return f"Q{(ts.month - 1) // 3 + 1} {ts.year}"
    if freq == "W":
        return ts.strftime("%b %d, %Y").replace(" 0", " ")
    return ts.strftime("%b %Y")


_IMPORTANCE_TAG = {
    "high":   '<span style="color:var(--brand-primary);font-weight:600;">HIGH</span>',
    "medium": '<span style="color:var(--text-muted);">MED</span>',
}


def _render_economy_calendar():
    import html as _html
    import plotly.graph_objects as go
    from datetime import date as _date, datetime as _dt
    from data.macro_indicators import get_print_board, to_yoy, to_mom_change
    from data.macro_calendar import get_upcoming_prints
    from ui.chrome import table_export

    # ── Latest print board ─────────────────────────────────────────────
    rows = get_print_board()
    body = ""
    for r in rows:
        basis_tag = {"yoy_pct": "YoY", "mom_pct": "MoM", "mom_chg_k": "MoM chg",
                     "level_pct": "level", "level_k": "level"}.get(r["basis"], "")
        body += (
            "<tr>"
            f'<td>{_html.escape(r["label"])}'
            f' <span style="color:var(--text-muted);font-size:var(--fs-2xs);">{basis_tag}</span></td>'
            f'<td>{_fmt_level(r["latest"], r["basis"])}</td>'
            f'<td>{_fmt_level(r["prior"], r["basis"])}</td>'
            f'<td>{_fmt_delta(r)}</td>'
            f'<td style="text-align:right;color:var(--text-secondary);">'
            f'{_fmt_as_of(r["as_of"], r["freq"])}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ksk-grid"><table style="width:100%;">'
        "<thead><tr>"
        "<th>Indicator</th><th>Latest</th><th>Prior</th>"
        "<th>Δ vs prior</th><th>As of</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Latest published reading per series. YoY = year-over-year, "
        "MoM = month-over-month, QoQ SAAR = quarter-over-quarter annualized. "
        "Δ colored by favorable direction (inflation lower / activity higher = green). "
        "Source: FRED."
    )

    # Export of the print board.
    export_df = pd.DataFrame([{
        "indicator": r["label"], "basis": r["basis"],
        "latest": r["latest"], "prior": r["prior"], "delta": r["delta"],
        "as_of": r["as_of"].strftime("%Y-%m-%d") if r["as_of"] is not None else None,
        "series_id": r["series_id"],
    } for r in rows])
    table_export(export_df, "macro_print_board", key="macro_print_board_export")

    st.markdown("---")

    # ── Inflation: CPI / Core CPI / Core PCE YoY vs the Fed's 2% target ──
    c1, c2 = st.columns(2)
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
        figi.add_hline(y=2.0, line_color="#059669", line_width=1, line_dash="dash",
                       annotation_text="Fed 2% target", annotation_position="top left",
                       annotation_font=dict(size=10, color="#059669"))
        apply_standard_layout(figi, title="Inflation — YoY % (5Y)",
                              height=CHART_HEIGHT_FULL, yaxis_title="YoY")
        figi.update_yaxes(ticksuffix="%")
        st.plotly_chart(figi, use_container_width=True)

    # ── Labor: payrolls MoM change (bars) + unemployment rate (line) ────
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
        apply_standard_layout(figl, title="Labor — payrolls Δ & unemployment (5Y)",
                              height=CHART_HEIGHT_FULL, yaxis_title="Jobs Δ (000s)")
        figl.update_layout(
            yaxis2=dict(title="Unemp %", overlaying="y", side="right",
                        ticksuffix="%", showgrid=False),
        )
        st.plotly_chart(figl, use_container_width=True)

    st.markdown("---")

    # ── Upcoming release calendar ──────────────────────────────────────
    st.markdown("**Upcoming releases**")
    window = st.radio("Window", [7, 14, 30, 60], index=2, horizontal=True,
                      format_func=lambda d: f"{d}d", key="macro_cal_window",
                      label_visibility="collapsed")
    prints = get_upcoming_prints(days=window)
    if not prints:
        st.info("Upcoming-release calendar uses the FRED releases API "
                "(needs FRED_API_KEY, set in production). Unavailable in this "
                "environment or no releases scheduled in the window.")
    else:
        today_iso = _date.today().isoformat()
        crows = ""
        for e in prints:
            d = _dt.strptime(e["date"], "%Y-%m-%d").date()
            dow = d.strftime("%a")
            when = "today" if e["date"] == today_iso else f"in {(d - _date.today()).days}d"
            is_fomc = e["kind"] == "fomc"
            name_html = _html.escape(e["name"])
            if is_fomc:
                name_html = f'<strong style="color:var(--brand-primary);">{name_html}</strong>'
            row_bg = ' style="background:rgba(30,64,175,0.04);"' if e["date"] == today_iso else ""
            crows += (
                f"<tr{row_bg}>"
                f'<td>{d.strftime("%b %d").replace(" 0", " ")}</td>'
                f'<td style="text-align:left;color:var(--text-secondary);">{dow}</td>'
                f'<td style="text-align:left;">{name_html}</td>'
                f'<td>{_IMPORTANCE_TAG.get(e["importance"], "")}</td>'
                f'<td style="color:var(--text-muted);">{when}</td>'
                "</tr>"
            )
        st.markdown(
            '<div class="ksk-grid"><table style="width:100%;">'
            "<thead><tr>"
            '<th style="text-align:left;">Date</th><th style="text-align:left;">Day</th>'
            '<th style="text-align:left;">Release</th><th>Importance</th><th>When</th>'
            "</tr></thead><tbody>" + crows + "</tbody></table></div>",
            unsafe_allow_html=True,
        )
        st.caption("FRED-scheduled release dates + FOMC decision days. "
                   "Source: FRED releases API · Federal Reserve FOMC calendar.")


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
