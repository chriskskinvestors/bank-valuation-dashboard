"""
Home page — branded landing with live summary stats, top opportunities,
recent filings, and navigation cards.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name, get_cik
from data.sec_client import get_filing_info
from data.filing_summarizer import fetch_filing_text, find_press_release_url, summarize_filing
from data.bank_universe import get_universe_count_fast


def _section_header(emoji: str, title: str, subtitle: str = ""):
    """Consistent section divider so each block on the home page reads as its
    own clearly-delineated section."""
    sub = (f'<span style="font-size:0.78rem; color:#94a3b8; font-weight:500; '
           f'margin-left:auto;">{subtitle}</span>') if subtitle else ""
    st.markdown(
        '<div style="display:flex; align-items:baseline; gap:9px; margin:22px 0 9px; '
        'padding-bottom:6px; border-bottom:2px solid #e2e8f0;">'
        f'<span style="font-size:1.0rem; font-weight:700; color:#0f172a; '
        f'letter-spacing:-0.01em;">{emoji} {title}</span>{sub}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════
# Markets & Rates
# ══════════════════════════════════════════════════════════════════════

_TENORS = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"),
           ("10Y", "DGS10"), ("30Y", "DGS30")]


def _fred_points(series_id: str):
    """Return (latest, prior_business_day, ~1-week-ago) for a FRED series."""
    try:
        from data.fred_client import fetch_series
        df = fetch_series(series_id, years=1)
        if df is None or df.empty:
            return (None, None, None)
        vals = df.dropna(subset=["value"])["value"].tolist()
        if not vals:
            return (None, None, None)
        latest = float(vals[-1])
        prior = float(vals[-2]) if len(vals) >= 2 else None
        weekago = float(vals[-6]) if len(vals) >= 6 else float(vals[0])
        return (latest, prior, weekago)
    except Exception:
        return (None, None, None)


def _prices_asof(tickers):
    """(label, color) describing how fresh the warm price cache is for these
    tickers — so stale data is never shown as if it were live. Returns the
    freshest row's timestamp; flags ⚠ when the data is more than a day old
    (i.e. the refresh-prices job has stalled)."""
    try:
        from data.price_cache_store import get_prices
        rows = get_prices(list(tickers))
        cand = [(r.get("age_seconds"), r.get("updated_at"))
                for r in rows.values()
                if r.get("updated_at") and r.get("age_seconds") is not None]
        if not cand:
            return "prices: unavailable", "#dc2626"
        age, iso = min(cand, key=lambda x: x[0])
        import datetime as dt
        stamp = dt.datetime.fromisoformat(iso).strftime("%b %d, %H:%M UTC")
        if age < 2 * 3600:
            return f"prices live · {stamp}", "#059669"
        if age < 30 * 3600:
            return f"prices as of {stamp}", "#64748b"
        days = age / 86400
        return f"⚠ stale prices · {stamp} ({days:.0f}d old)", "#dc2626"
    except Exception:
        return "", "#94a3b8"


def _market_status():
    """(label, color) for US equity market state in Eastern time."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return "Closed", "#94a3b8"
        hm = now.hour * 60 + now.minute
        if hm < 9 * 60 + 30:
            return "Pre-market", "#d97706"
        if hm < 16 * 60:
            return "Open", "#059669"
        return "Closed", "#94a3b8"
    except Exception:
        return "", "#94a3b8"


def _rates_asof():
    try:
        from data.fred_client import latest_date
        d = latest_date("DGS10")
        return d.strftime("%b %d") if d is not None else "—"
    except Exception:
        return "—"


def _delta_html(change, unit, up_is_good=True):
    """Small colored Δ chip. change in the unit's terms (bp or pt)."""
    if change is None or abs(change) < (0.5 if unit == "bp" else 0.05):
        return '<span style="font-size:0.55rem; color:#94a3b8; font-weight:600;">unch</span>'
    good = (change > 0) == up_is_good
    col = "#059669" if good else "#dc2626"
    dp = 0 if unit == "bp" else 1
    return (f'<span style="font-size:0.56rem; font-weight:700; color:{col};">'
            f'{change:+.{dp}f} {unit}</span>')


def _stat_pill(label, value_html, delta_html):
    return (
        '<span style="display:inline-flex; flex-direction:column; '
        'padding:3px 11px; border-radius:7px; background:rgba(148,163,184,0.08); '
        'border:1px solid rgba(148,163,184,0.18); line-height:1.22;">'
        f'<span style="font-size:0.55rem; color:#94a3b8; font-weight:600; '
        f'letter-spacing:0.04em;">{label}</span>'
        f'<span style="font-size:0.84rem; font-weight:700; color:#0f172a; '
        f'white-space:nowrap;">{value_html} {delta_html}</span></span>'
    )


def _curve_svg(points):
    """Mini yield-curve sparkline: today (blue) vs ~1 week ago (gray).
    points = [(label, today, weekago), ...]."""
    vals = [p[1] for p in points if p[1] is not None] + \
           [p[2] for p in points if p[2] is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.18 or 0.1
    lo -= pad; hi += pad
    W, H, px, ptop, pbot = 300, 86, 10, 8, 18
    n = len(points)

    def X(i):
        return px + i * ((W - 2 * px) / (n - 1))

    def Y(v):
        return ptop + (1 - (v - lo) / (hi - lo)) * (H - ptop - pbot)

    def poly(idx, color, w, op):
        pts = [f"{X(i):.1f},{Y(p[idx]):.1f}" for i, p in enumerate(points)
               if p[idx] is not None]
        return (f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" '
                f'stroke-width="{w}" opacity="{op}" stroke-linejoin="round"/>') if len(pts) >= 2 else ""

    wk = poly(2, "#cbd5e1", 1.5, 1)
    td = poly(1, "#2563eb", 2.2, 1)
    dots = "".join(
        f'<circle cx="{X(i):.1f}" cy="{Y(p[1]):.1f}" r="2.3" fill="#2563eb"/>'
        for i, p in enumerate(points) if p[1] is not None)
    labels = "".join(
        f'<text x="{X(i):.1f}" y="{H-5}" font-size="8" fill="#94a3b8" '
        f'text-anchor="middle">{p[0]}</text>' for i, p in enumerate(points))
    return (f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'xmlns="http://www.w3.org/2000/svg">{wk}{td}{dots}{labels}</svg>')


@st.cache_data(ttl=3600, show_spinner=False)
def _etf_context(symbols: tuple) -> dict:
    """1-week and YTD % change per ETF (cached hourly). EOD history works on
    the FMP Starter plan; quote endpoints don't, so this is a separate call."""
    out = {}
    try:
        from data import fmp_client
        for s in symbols:
            try:
                h = fmp_client.get_history(s, "1Y")
                if h is None or h.empty or "close" not in h:
                    continue
                h = h.dropna(subset=["close"]).sort_values("date")
                if h.empty:
                    continue
                last = float(h["close"].iloc[-1])
                w = float(h["close"].iloc[-6]) if len(h) >= 6 else None
                yr = h[h["date"].dt.year == h["date"].iloc[-1].year]
                ytd0 = float(yr["close"].iloc[0]) if not yr.empty else None
                out[s] = {
                    "w1": (last / w - 1) * 100 if w else None,
                    "ytd": (last / ytd0 - 1) * 100 if ytd0 else None,
                }
            except Exception:
                continue
    except Exception:
        pass
    return out


def _render_markets_rates():
    status, status_col = _market_status()
    sub = (f'rates as of {_rates_asof()} · market '
           f'<span style="color:{status_col}; font-weight:700;">{status}</span>')
    _section_header("📈", "Markets & Rates", sub)

    # ── Treasury curve: level + daily bps move ──────────────────────────
    td = {lab: _fred_points(sid) for lab, sid in _TENORS}
    ff = _fred_points("FEDFUNDS")[0]

    def _rate_pill(label, level, prior, is_spread=False):
        if level is None:
            val = "—"
        elif is_spread:
            col = "#dc2626" if level < 0 else "#0f172a"
            val = f'<span style="color:{col};">{level:+.2f}</span>'
        else:
            val = f"{level:.2f}%"
        chg = (level - prior) * 100 if (level is not None and prior is not None) else None
        return _stat_pill(label, val, _delta_html(chg, "bp"))

    pills = [_stat_pill("FED FUNDS", f"{ff:.2f}%" if ff is not None else "—", "")]
    for lab, _ in _TENORS:
        lv, pr, _wk = td[lab]
        pills.append(_rate_pill(lab, lv, pr))

    sp2, sp2p, _ = _fred_points("T10Y2Y")
    t5, t5p = td["5Y"][0], td["5Y"][1]
    t3, t3p = td["3M"][0], td["3M"][1]
    sp53 = (t5 - t3) if (t5 is not None and t3 is not None) else None
    sp53p = (t5p - t3p) if (t5p is not None and t3p is not None) else None
    sp_pills = [
        _rate_pill("5Y−3M", sp53, sp53p, is_spread=True),
        _rate_pill("10Y−2Y", sp2, sp2p, is_spread=True),
    ]

    cpts = [(lab, td[lab][0], td[lab][2]) for lab, _ in _TENORS]
    svg = _curve_svg(cpts)

    # Risk & vol pills (HY / IG credit spreads, equity vol) — rendered inline
    # with the rates so everything packs into one dense row.
    hy = _fred_points("BAMLH0A0HYM2")
    ig = _fred_points("BAMLC0A0CM")
    vix = _fred_points("VIXCLS")

    def _risk_pill(label, p, unit):
        lv, pr, _ = p
        if lv is None:
            return _stat_pill(label, "—", "")
        val = f"{lv:.2f}%" if unit == "bp" else f"{lv:.1f}"
        chg = (lv - pr) * (100 if unit == "bp" else 1) if pr is not None else None
        return _stat_pill(label, val, _delta_html(chg, unit, up_is_good=False))

    risk = [_risk_pill("HY OAS", hy, "bp"), _risk_pill("IG OAS", ig, "bp"),
            _risk_pill("VIX", vix, "pt")]

    row = "display:flex; gap:6px; flex-wrap:wrap;"
    c1, c2 = st.columns([4, 1])
    with c1:
        # All rate / spread / risk pills in one dense wrap — fills the width
        # instead of leaving the empty band that was here before.
        st.markdown(
            f'<div style="{row} margin:2px 0 0;">' + "".join(pills + sp_pills + risk) + "</div>",
            unsafe_allow_html=True,
        )
    with c2:
        if svg:
            st.markdown(
                '<div style="text-align:right;">' + svg +
                '<div style="font-size:0.56rem; color:#94a3b8; margin-top:-2px;">'
                'curve · <span style="color:#2563eb;">today</span> vs '
                '<span style="color:#94a3b8;">1wk ago</span></div></div>',
                unsafe_allow_html=True,
            )

    # ── Bank & market ETFs: price, daily %, 1wk, YTD ────────────────────
    _render_etf_strip()


def _render_etf_strip():
    from config import MARKET_BENCHMARKS
    syms = [t for t, _ in MARKET_BENCHMARKS]
    quotes = {}
    try:
        from data.price_cache_store import get_prices as _warm
        quotes = _warm(syms)
    except Exception:
        quotes = {}
    missing = [t for t in syms if t not in quotes]
    if missing:
        try:
            from data import fmp_client
            quotes.update(fmp_client.get_quote_batch(missing))
        except Exception:
            pass
    ctx = _etf_context(tuple(syms))

    sel = st.query_params.get("bench")
    bench_map = dict(MARKET_BENCHMARKS)

    def _pill_link(t, desc, q):
        price = (q or {}).get("price")
        chg = (q or {}).get("change_pct")
        if price is None:
            val, sub, sub_col = "—", "", "#94a3b8"
        else:
            val = f"${price:,.2f}"
            sub_col = "#dc2626" if (chg is not None and chg < 0) else "#059669"
            sub = f"{chg:+.2f}%" if chg is not None else ""
        c = ctx.get(t, {})

        def _ctx(lbl, v):
            if v is None:
                return ""
            cc = "#059669" if v >= 0 else "#dc2626"
            return (f'<span style="color:#94a3b8;">{lbl}</span>'
                    f'<span style="color:{cc}; font-weight:600;"> {v:+.1f}%</span>')
        ctx_bits = " · ".join(b for b in [_ctx("1w", c.get("w1")),
                                          _ctx("ytd", c.get("ytd"))] if b)
        ctx_html = (f'<span style="font-size:0.56rem; margin-top:1px;">{ctx_bits}</span>'
                    if ctx_bits else "")
        border = "#2563eb" if t == sel else "rgba(148,163,184,0.18)"
        return (
            f'<a href="?bench={t}" target="_self" title="{desc}" '
            'style="text-decoration:none; color:inherit;">'
            '<span style="display:inline-flex; flex-direction:column; '
            'padding:3px 12px; border-radius:7px; background:rgba(148,163,184,0.08); '
            f'border:1px solid {border}; line-height:1.25; cursor:pointer;">'
            f'<span style="font-size:0.58rem; color:#94a3b8; font-weight:600; '
            f'letter-spacing:0.04em;">{t}</span>'
            f'<span style="font-size:0.86rem; font-weight:700;">{val}'
            f'<span style="font-size:0.64rem; font-weight:600; margin-left:5px; '
            f'color:{sub_col};">{sub}</span></span>{ctx_html}</span></a>'
        )

    pills = [_pill_link(t, desc, quotes.get(t)) for t, desc in MARKET_BENCHMARKS]
    st.markdown(
        '<div style="display:flex; gap:6px; flex-wrap:wrap; margin:0 0 6px;">'
        + "".join(pills) + "</div>",
        unsafe_allow_html=True,
    )

    if sel and sel in bench_map:
        try:
            from data import fmp_client
            from utils.chart_style import apply_standard_layout
            import plotly.graph_objects as go
            hist = fmp_client.get_history(sel, period="1Y")
            if hist is not None and not hist.empty and "close" in hist:
                up = hist["close"].iloc[-1] >= hist["close"].iloc[0]
                figh = go.Figure(go.Scatter(
                    x=hist["date"], y=hist["close"], mode="lines",
                    line=dict(color="#059669" if up else "#dc2626", width=2)))
                apply_standard_layout(
                    figh, title=f"{sel} — {bench_map[sel]} · 1 year",
                    height=240, show_legend=False)
                figh.update_yaxes(tickprefix="$")
                st.plotly_chart(figh, use_container_width=True, key=f"bench_chart_{sel}")
            if st.button("✕ close chart", key="bench_close"):
                del st.query_params["bench"]
                st.rerun()
        except Exception:
            pass


def render_home(all_metrics: list[dict], watchlist: list[str]):
    """Render the home/dashboard page."""

    # ── Hero ──────────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="ksk-hero">
            <div style="display:flex; align-items:center; gap:14px; margin-bottom:6px;">
                <div style="
                    width:42px; height:42px;
                    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
                    border-radius: 11px;
                    display:flex; align-items:center; justify-content:center;
                    font-size:1.2rem; font-weight:700; color:#fff;
                    box-shadow: 0 6px 16px rgba(37, 99, 235, 0.24);
                    letter-spacing:-0.02em;
                ">K</div>
                <div>
                    <h1>KSK Investors</h1>
                    <p class="ksk-hero-subtitle">Bank Valuation &amp; Analysis Platform</p>
                </div>
            </div>
            <div class="ksk-hero-meta">
                <span class="dot"></span>
                <span>Live · FDIC · SEC EDGAR · FMP</span>
                <span style="color:#cbd5e1;">—</span>
                <span><strong style="color:#0f172a;">{len(watchlist)}</strong> watchlist</span>
                <span style="color:#cbd5e1;">·</span>
                <span><strong style="color:#0f172a;">{get_universe_count_fast()}</strong> universe</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("")
    _render_markets_rates()

    # Industry valuations sit with the market context up top (sector P/TBV, P/E
    # by tier) — not buried at the bottom.
    if all_metrics:
        _render_industry_valuations(pd.DataFrame(all_metrics))

    # ── WHAT MOVED IN MY BOOK ──────────────────────────────────────────
    if all_metrics:
        _render_watchlist_movers(all_metrics)

    # ── ALERT INBOX ────────────────────────────────────────────────────
    _render_alert_inbox(all_metrics, watchlist)

    # ── COVERAGE LEADERBOARD (best / worst each way) ───────────────────
    if all_metrics:
        _render_coverage_leaderboard(all_metrics)

    # ── TODAY'S CALENDAR ───────────────────────────────────────────────
    _render_todays_calendar(watchlist)

    # ── SECTOR M&A / DEALS ─────────────────────────────────────────────
    _render_sector_ma(watchlist)


# ══════════════════════════════════════════════════════════════════════
# Watchlist movers — what moved in my book
# ══════════════════════════════════════════════════════════════════════

def _render_watchlist_movers(all_metrics: list[dict]):
    """Biggest price moves across the watchlist — gainers and losers side by
    side, each row a deep-link into that bank. The 'what happened to my book'
    panel. Uses the latest session's close-to-close change."""
    rows = []
    for m in all_metrics:
        tk = m.get("ticker")
        chg = m.get("change_pct")
        if not tk or chg is None:
            continue
        try:
            chg = float(chg)
        except (TypeError, ValueError):
            continue
        rows.append((tk, m.get("price"), chg))
    if not rows:
        return

    adv = sum(1 for _, _, c in rows if c > 0)
    dec = sum(1 for _, _, c in rows if c < 0)
    flat = len(rows) - adv - dec
    rows.sort(key=lambda r: r[2], reverse=True)
    gainers = [r for r in rows if r[2] > 0][:6]
    losers = [r for r in rows if r[2] < 0]
    losers = sorted(losers, key=lambda r: r[2])[:6]

    _tkrs = [m.get("ticker") for m in all_metrics if m.get("ticker")]
    _asof, _asof_col = _prices_asof(_tkrs)
    _counts = f"{adv} up · {dec} down · {flat} flat"
    _sub = (f'<span style="color:{_asof_col}; font-weight:600;">{_asof}</span> · {_counts}'
            if _asof else _counts)
    _section_header("📊", "Watchlist Movers", _sub)

    def _row(tk, price, chg):
        name = (get_name(tk) or "")[:24]
        col = "#059669" if chg > 0 else ("#dc2626" if chg < 0 else "#64748b")
        px = f"${price:,.2f}" if isinstance(price, (int, float)) else "—"
        return (
            f'<a href="?bank={tk}" target="_self" style="display:flex; '
            'align-items:baseline; justify-content:space-between; gap:8px; '
            'padding:5px 11px; border-radius:7px; text-decoration:none; '
            'border:1px solid #eef2f7;">'
            f'<span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
            f'<strong style="color:#0f172a;">{tk}</strong> '
            f'<span style="color:#94a3b8; font-size:0.8rem;">{name}</span></span>'
            f'<span style="white-space:nowrap; font-variant-numeric:tabular-nums;">'
            f'<span style="color:#64748b; font-size:0.82rem; margin-right:8px;">{px}</span>'
            f'<b style="color:{col};">{chg:+.2f}%</b></span></a>'
        )

    def _col(title, items, accent):
        head = (f'<div style="font-size:0.66rem; font-weight:700; letter-spacing:0.05em; '
                f'color:{accent}; margin:0 0 5px;">{title}</div>')
        body = "".join(_row(*it) for it in items) or \
            '<div style="color:#94a3b8; font-size:0.82rem; padding:5px 11px;">None</div>'
        return ('<div style="display:flex; flex-direction:column; gap:4px;">'
                + head + body + '</div>')

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(_col("▲ TOP GAINERS", gainers, "#059669"), unsafe_allow_html=True)
    with c2:
        st.markdown(_col("▼ TOP LOSERS", losers, "#dc2626"), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# Today's calendar
# ══════════════════════════════════════════════════════════════════════

def _render_todays_calendar(watchlist: list[str]):
    """What's on the schedule — watchlist names reporting earnings, grouped by
    urgency. (Macro releases / ex-div are the next add to this section.)"""
    _section_header("🗓️", "Today's Calendar", "watchlist earnings — next 14 days")
    alerts = _collect_earnings_alerts(watchlist)
    if not alerts:
        st.caption("No watchlist earnings in the next 14 days.")
        return

    buckets = [("Today", []), ("This week", []), ("Next week", [])]
    for a in alerts:
        d = a["days_until"]
        if d == 0:
            buckets[0][1].append(a)
        elif d <= 7:
            buckets[1][1].append(a)
        else:
            buckets[2][1].append(a)

    def _chip(a):
        tk = a["ticker"]; name = (get_name(tk) or "")[:22]
        eps = a.get("eps_est")
        est = f' <span style="color:#94a3b8; font-size:0.78rem;">est ${eps:.2f}</span>' if eps is not None else ""
        return (
            f'<a href="?bank={tk}" target="_self" style="display:flex; '
            'align-items:baseline; justify-content:space-between; gap:8px; '
            'padding:5px 11px; border-radius:7px; text-decoration:none; border:1px solid #eef2f7;">'
            f'<span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
            f'<strong style="color:#0f172a;">{tk}</strong> '
            f'<span style="color:#94a3b8; font-size:0.8rem;">{name}</span></span>'
            f'<span style="white-space:nowrap; color:#475569; font-size:0.8rem;">{a["date"]}{est}</span></a>'
        )

    cols = st.columns(3)
    for col, (label, items) in zip(cols, buckets):
        with col:
            accent = "#dc2626" if label == "Today" else "#475569"
            st.markdown(
                f'<div style="font-size:0.66rem; font-weight:700; letter-spacing:0.05em; '
                f'color:{accent}; margin:0 0 5px;">{label.upper()} · {len(items)}</div>'
                '<div style="display:flex; flex-direction:column; gap:4px;">'
                + ("".join(_chip(a) for a in items) or
                   '<div style="color:#cbd5e1; font-size:0.82rem; padding:5px 11px;">—</div>')
                + "</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════
# Coverage leaderboard — ranked extremes
# ══════════════════════════════════════════════════════════════════════

def _render_coverage_leaderboard(all_metrics: list[dict]):
    """Ranked extremes across coverage — the value-screen lists you scan each
    morning: cheapest, highest-returning, best-yielding, widest discount."""
    _section_header("🏅", "Coverage Leaderboard", "ranked extremes across your watchlist")
    df = pd.DataFrame(all_metrics)

    def _list(title, col, ascending, fmt, color="#0f172a"):
        if col not in df.columns:
            return f'<div style="font-size:0.66rem; font-weight:700; color:#94a3b8;">{title}</div>'
        sub = df[["ticker", col]].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=[col]).sort_values(col, ascending=ascending).head(5)
        rows = ""
        for _, r in sub.iterrows():
            tk = r["ticker"]; name = (get_name(tk) or "")[:20]
            rows += (
                f'<a href="?bank={tk}" target="_self" style="display:flex; '
                'align-items:baseline; justify-content:space-between; gap:8px; '
                'padding:4px 11px; border-radius:6px; text-decoration:none; border:1px solid #eef2f7;">'
                f'<span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
                f'<strong style="color:#0f172a;">{tk}</strong> '
                f'<span style="color:#94a3b8; font-size:0.78rem;">{name}</span></span>'
                f'<b style="color:{color}; white-space:nowrap; font-variant-numeric:tabular-nums;">'
                f'{fmt(r[col])}</b></a>'
            )
        if not rows:
            rows = '<div style="color:#cbd5e1; font-size:0.82rem; padding:4px 11px;">—</div>'
        return (f'<div style="font-size:0.66rem; font-weight:700; letter-spacing:0.05em; '
                f'color:#64748b; margin:0 0 5px;">{title}</div>'
                '<div style="display:flex; flex-direction:column; gap:3px;">' + rows + '</div>')

    lists = [
        ("CHEAPEST · P/TBV", "ptbv_ratio", True, lambda v: f"{v:.2f}x", "#059669"),
        ("WIDEST DISCOUNT TO FAIR", "ptbv_discount", False, lambda v: f"{v:+.0f}%", "#059669"),
        ("HIGHEST ROATCE", "roatce_blended", False, lambda v: f"{v:.1f}%"),
        ("HIGHEST DIVIDEND YIELD", "dividend_yield", False, lambda v: f"{v:.1f}%"),
    ]
    cols = st.columns(2)
    for i, (title, col, asc, fmt, *c) in enumerate(lists):
        with cols[i % 2]:
            st.markdown(_list(title, col, asc, fmt, *(c or ["#0f172a"])), unsafe_allow_html=True)
            st.markdown("")


# ══════════════════════════════════════════════════════════════════════
# Sector M&A / deals
# ══════════════════════════════════════════════════════════════════════

# Headline patterns that mark a deal even when the pipeline didn't tag it
# m_and_a — bank consolidation is constant and the language is formulaic.
_MA_KEYWORDS = (
    "to acquire", "acquisition of", "acquires", "to buy", "merger", "to merge",
    "combination with", "agrees to", "definitive agreement", "to combine",
    "completes acquisition", "completes merger", "all-stock", "merger of equals",
)


def _is_ma_headline(head: str) -> bool:
    h = (head or "").lower()
    return any(k in h for k in _MA_KEYWORDS)


def _render_sector_ma(watchlist: list[str]):
    """Bank M&A and deal announcements across the WHOLE universe (not just the
    watchlist) — consolidation is the sector's biggest catalyst and deals at
    banks you don't own still move comps and signal where multiples are going."""
    import datetime as dt
    _section_header("🤝", "Sector M&A & Deals", "universe-wide · deals move the comps")
    try:
        from data.events import get_universe_recent
        from data.events.wire_base import is_safe_news_url
        rows = get_universe_recent(limit=500, sources=_NEWS_SOURCES)
    except Exception:
        st.caption("Deal feed temporarily unavailable.")
        return

    wl = set(watchlist)
    out, seen = [], set()
    for r in rows:
        if not is_safe_news_url(r.get("url")):
            continue
        head = (r.get("headline") or "").strip()
        et = r.get("event_type") or ""
        if not head or (et != "m_and_a" and not _is_ma_headline(head)):
            continue
        tk = r.get("ticker")
        key = (tk, head[:64])
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": tk, "headline": head, "url": r.get("url") or "",
                    "published_at": r.get("published_at"),
                    "summary": (r.get("summary") or "").strip()})
    # newest first
    def _ts(p):
        try:
            t = p if hasattr(p, "year") else dt.datetime.fromisoformat(str(p).replace("Z", "+00:00"))
            return t.timestamp()
        except Exception:
            return 0.0
    out.sort(key=lambda o: _ts(o["published_at"]), reverse=True)
    out = out[:12]

    if not out:
        st.caption("No bank M&A or deal announcements in the recent window.")
        return

    for a in out:
        tk = a["ticker"]; name = (get_name(tk) or "")[:30] if tk else ""
        when = _relative_time(a["published_at"])
        owned = ' <span style="color:#2563eb; font-size:0.7rem; font-weight:700;">★ watchlist</span>' if tk in wl else ""
        tkr = (f'<a href="?bank={tk}" target="_self" style="text-decoration:none;">'
               f'<strong style="color:#0f172a;">{tk}</strong></a> '
               f'<span style="color:#94a3b8;">{name}</span>') if tk else \
              '<span style="color:#94a3b8;">Sector</span>'
        link = (f' <a href="{a["url"]}" target="_blank" style="color:var(--brand-accent); '
                f'text-decoration:none;">open ↗</a>') if a["url"] else ""
        summ = a["summary"]
        if summ and len(summ) > 200:
            summ = summ[:197].rstrip() + "…"
        st.markdown(
            '<div class="alert-row severity-high" style="display:block; padding:9px 14px;">'
            f'<div style="font-size:0.78rem; color:var(--text-muted);">🤝 M&amp;A · {tkr}{owned} · {when}{link}</div>'
            f'<div style="color:var(--text-primary); font-weight:600; margin-top:2px;">{a["headline"]}</div>'
            + (f'<div style="color:var(--text-secondary); font-size:0.86rem; margin-top:2px; '
               f'line-height:1.45;">{summ}</div>' if summ else "")
            + '</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════
# Industry valuations
# ══════════════════════════════════════════════════════════════════════

def _render_industry_valuations(df: pd.DataFrame):
    """
    Where bank valuations sit right now — median multiples across coverage,
    segmented by asset-size tier. Medians (not means) so a single mis-priced
    or thinly-traded name can't distort the read.
    """
    from analysis.peer_groups import asset_size_tier

    _section_header("🏦", "Industry Valuations", "median multiples by asset-size tier")

    if "total_assets" not in df.columns:
        df = df.copy()
        df["total_assets"] = None

    # Normalize assets to dollars (some flows store $thousands) before tiering.
    def _assets_usd(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        return v * 1000.0 if v < 1e9 else v

    df = df.copy()
    df["_tier"] = df["total_assets"].map(lambda v: asset_size_tier(_assets_usd(v)))

    def _med(sub, col):
        if col not in sub.columns:
            return None
        s = pd.to_numeric(sub[col], errors="coerce").dropna()
        return float(s.median()) if not s.empty else None

    def _x(v, dp=2):
        return f"{v:.{dp}f}x" if v is not None else "—"

    def _pct(v, dp=1):
        return f"{v:.{dp}f}%" if v is not None else "—"

    # Headline: overall median P/TBV, P/E, div yield, and how many screen cheap.
    n = len(df)
    disc = pd.to_numeric(df.get("ptbv_discount"), errors="coerce") if "ptbv_discount" in df.columns else pd.Series(dtype=float)
    n_cheap = int((disc > 0).sum()) if not disc.empty else 0
    head_items = [
        ("MEDIAN P/TBV", _x(_med(df, "ptbv_ratio"))),
        ("MEDIAN P/E", _x(_med(df, "pe_ratio"), dp=1)),
        ("MEDIAN DIV YIELD", _pct(_med(df, "dividend_yield"))),
        ("MEDIAN ROATCE", _pct(_med(df, "roatce_blended"))),
        ("BELOW FAIR VALUE", f"{n_cheap} / {n}"),
    ]
    pills = "".join(
        '<span style="display:inline-flex; flex-direction:column; padding:4px 13px; '
        'border-radius:8px; background:rgba(37,99,235,0.05); '
        'border:1px solid rgba(37,99,235,0.14); line-height:1.25;">'
        f'<span style="font-size:0.55rem; color:#64748b; font-weight:700; '
        f'letter-spacing:0.05em;">{lbl}</span>'
        f'<span style="font-size:0.98rem; font-weight:700; color:#1e3a8a;">{val}</span>'
        '</span>'
        for lbl, val in head_items
    )
    st.markdown(
        '<div style="display:flex; gap:7px; flex-wrap:wrap; margin:0 0 10px;">'
        + pills + "</div>",
        unsafe_allow_html=True,
    )

    # Tier table — order large → small, only tiers that have banks, then All.
    tier_order = [
        "Money-Center (>$1T)", "Large Regional ($100B-$1T)",
        "Regional ($10-100B)", "Community (<$10B)",
    ]
    rows = []
    for tier in tier_order:
        sub = df[df["_tier"] == tier]
        if len(sub):
            rows.append((tier, sub))
    rows.append(("All coverage", df))

    body = ""
    for i, (tier, sub) in enumerate(rows):
        bold = ' style="font-weight:700; border-top:2px solid #e2e8f0;"' if tier == "All coverage" else ""
        zebra = "background:rgba(148,163,184,0.05);" if (i % 2 == 1 and tier != "All coverage") else ""
        cells = "".join(
            f'<td style="text-align:right; padding:6px 12px; font-variant-numeric:tabular-nums;">{c}</td>'
            for c in [
                str(len(sub)),
                _x(_med(sub, "ptbv_ratio")),
                _x(_med(sub, "pe_ratio"), dp=1),
                _x(_med(sub, "pb_ratio")),
                _pct(_med(sub, "dividend_yield")),
                _pct(_med(sub, "roatce_blended")),
            ]
        )
        body += (
            f'<tr{bold} style="{zebra}">'
            f'<td style="text-align:left; padding:6px 12px; color:#0f172a;">{tier}</td>'
            f'{cells}</tr>'
        )

    headers = ["Asset-Size Tier", "Banks", "P/TBV", "P/E", "P/B", "Div Yld", "ROATCE"]
    head_html = "".join(
        f'<th style="text-align:{"left" if i == 0 else "right"}; padding:7px 12px; '
        'font-size:0.62rem; font-weight:700; letter-spacing:0.04em; color:#64748b; '
        f'border-bottom:1px solid #e2e8f0;">{h}</th>'
        for i, h in enumerate(headers)
    )
    st.markdown(
        '<table style="width:100%; border-collapse:collapse; font-size:0.82rem; '
        'background:#fff; border:1px solid #e2e8f0; border-radius:10px; overflow:hidden;">'
        f'<thead><tr>{head_html}</tr></thead><tbody>{body}</tbody></table>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Median valuation multiples across {n} covered banks · live FDIC + market data. "
        "Medians are robust to outliers; tiers by total assets."
    )


# ══════════════════════════════════════════════════════════════════════
# Alert Inbox
# ══════════════════════════════════════════════════════════════════════

def _render_alert_inbox(all_metrics: list[dict], watchlist: list[str]):
    """
    Alert feed that surfaces:
      1. Earnings reporting this week / next week
      2. Banks with new deposit / credit / capital alerts
      3. Insider buying signals
      4. Valuation opportunities (wide discount to fair value)
    """
    if not all_metrics:
        st.info("Loading watchlist data...")
        return

    _section_header("🔔", "Alert Inbox", "news · earnings · dynamics · insider · value opps")
    st.caption("Prioritized across your watchlist. Click into any row to jump to the bank.")

    # Collect alerts from each source
    news_alerts = _collect_news_alerts(watchlist)
    earnings_alerts = _collect_earnings_alerts(watchlist)
    dynamics_alerts = _collect_dynamics_alerts(all_metrics)
    insider_alerts = _collect_insider_alerts(watchlist)
    valuation_alerts = _collect_valuation_alerts(all_metrics)

    # Important News is the main (first) tab.
    tab_news, tab1, tab2, tab3, tab4 = st.tabs([
        f"📰 Important News ({len(news_alerts)})",
        f"📅 Earnings ({len(earnings_alerts)})",
        f"⚠️ Dynamics Alerts ({len(dynamics_alerts)})",
        f"👥 Insider Buys ({len(insider_alerts)})",
        f"💰 Value Opps ({len(valuation_alerts)})",
    ])

    with tab_news:
        _render_news_tab(news_alerts)
    with tab1:
        _render_earnings_tab(earnings_alerts)
    with tab2:
        _render_dynamics_tab(dynamics_alerts)
    with tab3:
        _render_insider_tab(insider_alerts)
    with tab4:
        _render_valuation_tab(valuation_alerts)


def _relative_time(p) -> str:
    import datetime as dt
    if p is None:
        return ""
    try:
        t = p if hasattr(p, "year") else dt.datetime.fromisoformat(str(p).replace("Z", "+00:00"))
    except Exception:
        return ""
    now = dt.datetime.now(dt.timezone.utc) if t.tzinfo else dt.datetime.now()
    secs = max(0, (now - t).total_seconds())
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    d = int(secs // 86400)
    return f"{d}d ago" if d < 30 else (t.strftime("%b %d") if hasattr(t, "strftime") else "")


# event_type → (emoji, label, weight). Weight + recency rank "important news".
_NEWS_TYPES = {
    "earnings": ("📊", "Earnings", 5), "m_and_a": ("🤝", "M&A", 5),
    "regulatory": ("🏛", "Regulatory", 4), "executive_change": ("👤", "Leadership", 3),
    "shareholder_vote": ("🗳", "Shareholder vote", 2), "filing": ("📄", "Filing", 2),
    "press_release": ("📰", "Press release", 1), "news": ("📰", "News", 1),
}


# Only actionable sources: SEC filings (8-K/10-K/10-Q) and real press releases
# (the news wires + IR-site press releases). Deliberately EXCLUDES the yfinance
# news aggregator — that's the noisy/low-signal "crap". IR-site PRs are kept
# only for banks the wires don't cover (the wires are cleaner; see below), so
# we don't double-list the same press release.
_WIRE_SOURCES = {"businesswire", "prnewswire", "globenewswire"}
_NEWS_SOURCES = ["sec_8k", "businesswire", "prnewswire", "globenewswire",
                 "ir_site", "google_news"]


import re as _re

# Headlines that mention a bank but aren't ABOUT it — third-party SEO/aggregator
# spam, structured-note issuance, and non-material branch trivia. These slip past
# name-matching because the bank's name appears in someone else's story.
_JUNK_RE = _re.compile(
    r"\b(issues?\s+(optimistic|pessimistic|bullish|bearish)\s+forecast|"
    r"price\s+target|forecast\s+for|target\s+price|"
    r"shares?\s+(sold|bought|purchased|acquired)\s+by|"
    r"(funding|investment)\s+from|to\s+(buy|sell)\s+\$?\d|"
    r"office\s+leader|branch\s+manager|relationship\s+manager|"
    r"new\s+\w+\s+(branch|location|office)|"
    r"contingent[-\s]?interest\s+notes|callable\s+\w*\s*notes|auto[-\s]?callable|"
    r"buffered\s+notes|structured\s+notes|market[-\s]?linked|leveraged\s+notes|"
    r"digital\s+notes|trigger\s+\w*\s*notes)\b",
    _re.I,
)
# A foreign ticker tag like (NYSE:CHWY) — if it's NOT this bank's ticker, the
# story is about another company.
_PAREN_TICKER_RE = _re.compile(
    r"\((?:NYSE|NASDAQ|NYSEAMERICAN|NYSEARCA|AMEX|OTC|CBOE)[:\s]+([A-Z.]{1,6})\)", _re.I)


def _is_junk_news(headline: str, ticker: str | None) -> bool:
    h = headline or ""
    if _JUNK_RE.search(h):
        return True
    for other in _PAREN_TICKER_RE.findall(h):
        if other.upper() != (ticker or "").upper():
            return True
    return False


def _collect_news_alerts(watchlist: list[str]) -> list[dict]:
    """Actionable news across the watchlist — SEC filings + real press releases
    (wires, plus IR-site for non-wire banks) — with their AI summaries."""
    import datetime as dt
    try:
        from data.events import get_universe_recent
        rows = get_universe_recent(limit=300, sources=_NEWS_SOURCES)
    except Exception:
        return []

    from data.events.wire_base import is_safe_news_url
    rows = [r for r in rows if is_safe_news_url(r.get("url"))]  # drop spam/social links

    wl = set(watchlist)
    # Banks that have wire-service press releases in this window — for these we
    # skip the IR-site scraper (it would just duplicate the cleaner wire PR).
    wire_covered = {r.get("ticker") for r in rows if r.get("source") in _WIRE_SOURCES}
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    out, seen = [], set()
    for r in rows:
        tk = r.get("ticker")
        if wl and tk not in wl:
            continue
        if r.get("source") == "ir_site" and tk in wire_covered:
            continue  # wires cover this bank — don't double-list IR-site PRs
        head = (r.get("headline") or "").strip()
        key = (tk, head[:60])
        if not head or key in seen:
            continue
        if _is_junk_news(head, tk):
            continue  # third-party mentions, structured notes, branch-hire trivia
        seen.add(key)
        et = r.get("event_type") or "news"
        weight = _NEWS_TYPES.get(et, ("📰", "News", 1))[2]
        # recency
        p = r.get("published_at")
        try:
            t = p if hasattr(p, "year") else dt.datetime.fromisoformat(str(p).replace("Z", "+00:00"))
            ts = t.timestamp()
        except Exception:
            ts = 0.0
        age_days = max(0.0, (now - ts) / 86400) if ts else 999
        score = weight / (1 + age_days * 0.35)  # recent + material floats up
        out.append({
            "ticker": tk, "event_type": et, "headline": head,
            "summary": (r.get("summary") or "").strip(), "url": r.get("url") or "",
            "published_at": p, "score": score,
        })
    out.sort(key=lambda o: o["score"], reverse=True)
    return out[:20]


def _render_news_tab(alerts: list[dict]):
    if not alerts:
        st.info("No recent SEC filings or press releases for your watchlist. "
                "(News aggregator noise is intentionally excluded — only filings "
                "and real press releases appear here.)")
        return
    for a in alerts:
        emoji, label, weight = _NEWS_TYPES.get(a["event_type"], ("📰", "News", 1))
        tk = a["ticker"]; name = get_name(tk)[:34]
        when = _relative_time(a["published_at"])
        sev = "high" if weight >= 5 else ("medium" if weight >= 3 else "")
        summ = a["summary"]
        if summ and len(summ) > 240:
            summ = summ[:237].rstrip() + "…"
        link = (f' <a href="{a["url"]}" target="_blank" style="color:var(--brand-accent); '
                f'text-decoration:none;">open ↗</a>') if a["url"] else ""
        body = (
            f'<div class="alert-row severity-{sev}" style="display:block; padding:9px 14px;">'
            f'<div style="font-size:0.78rem; color:var(--text-muted);">'
            f'{emoji} {label} &nbsp;·&nbsp; <strong style="color:var(--text-primary);">{tk}</strong> '
            f'<span style="color:var(--text-secondary);">{name}</span> &nbsp;·&nbsp; {when}{link}</div>'
            f'<div style="color:var(--text-primary); font-weight:600; margin-top:2px;">{a["headline"]}</div>'
            + (f'<div style="color:var(--text-secondary); font-size:0.86rem; margin-top:2px; '
               f'line-height:1.45;">{summ}</div>' if summ else "")
            + '</div>'
        )
        st.markdown(body, unsafe_allow_html=True)


def _collect_earnings_alerts(watchlist: list[str]) -> list[dict]:
    """Earnings reporting in the next 14 days."""
    from datetime import datetime, date
    try:
        from data.estimates import fetch_earnings_calendar
        cal = fetch_earnings_calendar(tuple(watchlist))
    except Exception:
        return []

    today = date.today()
    alerts = []
    for entry in cal:
        try:
            ed = datetime.strptime(entry["next_earnings_date"], "%Y-%m-%d").date()
            days_until = (ed - today).days
            if 0 <= days_until <= 14:
                alerts.append({
                    "ticker": entry["ticker"],
                    "date": entry["next_earnings_date"],
                    "days_until": days_until,
                    "eps_est": entry.get("eps_estimate"),
                    "analysts": entry.get("analyst_count"),
                })
        except (ValueError, TypeError):
            continue

    alerts.sort(key=lambda a: a["days_until"])
    return alerts


def _collect_dynamics_alerts(all_metrics: list[dict]) -> list[dict]:
    """Pull deposit / credit / capital alert counts per bank."""
    alerts = []
    for m in all_metrics:
        ticker = m.get("ticker")
        dep_n = m.get("deposit_alerts_count") or 0
        cred_n = m.get("credit_alerts_count") or 0
        cap_n = m.get("capital_alerts_count") or 0
        total = dep_n + cred_n + cap_n
        if total == 0:
            continue
        alerts.append({
            "ticker": ticker,
            "deposit": dep_n,
            "credit": cred_n,
            "capital": cap_n,
            "total": total,
        })
    alerts.sort(key=lambda a: a["total"], reverse=True)
    return alerts[:15]  # cap


def _collect_insider_alerts(watchlist: list[str]) -> list[dict]:
    """Banks with recent net-positive insider buying. Uses cache only — no fresh API calls."""
    from data.bank_mapping import get_cik
    from data.cloud_storage import load_json
    from data.form4_client import summarize_insider_activity, _is_fresh, FORM4_CACHE_PREFIX

    alerts = []
    # Only look at already-cached Form 4 data; never trigger fresh fetches on the Home page
    # (too slow for dashboard render). Insider data gets populated when user visits the
    # Filings > Insider Activity tab for a bank.
    for ticker in watchlist[:25]:
        cik = get_cik(ticker)
        if not cik:
            continue
        cached = load_json(FORM4_CACHE_PREFIX, f"{cik}.json")
        if not cached or not _is_fresh(cached):
            continue
        txs = cached.get("transactions", [])
        summary = summarize_insider_activity(txs)
        buys = summary.get("buys_6m_usd", 0)
        sells = summary.get("sells_6m_usd", 0)
        if buys > 0 and buys > sells * 0.5:
            alerts.append({
                "ticker": ticker,
                "buys": buys,
                "sells": sells,
                "net": buys - sells,
                "buyers": summary.get("buyer_count_6m", 0),
            })

    alerts.sort(key=lambda a: a["buys"], reverse=True)
    return alerts[:10]


def _collect_valuation_alerts(all_metrics: list[dict]) -> list[dict]:
    """Banks trading >15% below fair P/TBV."""
    alerts = []
    for m in all_metrics:
        discount = m.get("ptbv_discount")
        if discount is None or discount < 15:
            continue
        alerts.append({
            "ticker": m.get("ticker"),
            "discount": discount,
            "fair_price": m.get("fair_price"),
            "roatce": m.get("roatce_blended") or m.get("roatce"),
            "roatce_norm": m.get("roatce_normalized"),
            "distorted": m.get("earnings_distorted"),
            "price": m.get("price"),
        })
    alerts.sort(key=lambda a: a["discount"], reverse=True)
    return alerts[:15]


def _alert_row(severity: str, left_html: str, right_html: str) -> str:
    """Render a single alert row with the shared .alert-row style."""
    return (
        f'<div class="alert-row severity-{severity}">'
        f'<span>{left_html}</span>'
        f'<span style="color:var(--text-secondary);">{right_html}</span>'
        f'</div>'
    )


def _render_earnings_tab(alerts: list[dict]):
    if not alerts:
        st.info("No earnings reports in the next 14 days.")
        return

    for a in alerts:
        days = a["days_until"]
        ticker = a["ticker"]
        name = get_name(ticker)[:36]
        date_str = a["date"]
        eps = a.get("eps_est")
        analysts = a.get("analysts")

        if days == 0:
            sev = "high"
            urgency = "TODAY"
        elif days <= 3:
            sev = "high"
            urgency = f"in {days}d"
        elif days <= 7:
            sev = "medium"
            urgency = f"in {days}d"
        else:
            sev = ""  # default
            urgency = f"in {days}d"

        extras = []
        if eps is not None:
            extras.append(f"Qtr EPS ${eps:.2f}")
        if analysts:
            extras.append(f"{analysts} analysts")

        left = (
            f'<span style="font-weight:600; color:var(--brand-accent); margin-right:10px;">{urgency}</span>'
            f'<strong>{ticker}</strong> <span style="color:var(--text-secondary);">{name}</span>'
            f' <span style="color:var(--text-muted); font-size:0.8rem;">· {date_str}</span>'
        )
        right = " · ".join(extras) if extras else ""
        st.markdown(_alert_row(sev, left, right), unsafe_allow_html=True)


def _render_dynamics_tab(alerts: list[dict]):
    if not alerts:
        st.markdown(
            _alert_row("ok",
                       "<strong>All clear</strong> — no deposit, credit, or capital alerts",
                       ""),
            unsafe_allow_html=True,
        )
        return

    for a in alerts:
        ticker = a["ticker"]
        name = get_name(ticker)[:40]
        total = a["total"]
        parts = []
        if a["deposit"]: parts.append(f"{a['deposit']} deposit")
        if a["credit"]: parts.append(f"{a['credit']} credit")
        if a["capital"]: parts.append(f"{a['capital']} capital")

        sev = "high" if total >= 3 else ("medium" if total >= 2 else "")

        left = (
            f'<strong>{ticker}</strong> '
            f'<span style="color:var(--text-secondary);">{name}</span>'
        )
        right = " · ".join(parts)
        st.markdown(_alert_row(sev, left, right), unsafe_allow_html=True)

    st.caption("Click into a bank from Company Analysis to review details.")


def _render_insider_tab(alerts: list[dict]):
    if not alerts:
        st.info(
            "No net insider buying signals found. Visit a bank's Filings → Insider Activity tab to populate the cache."
        )
        return

    for a in alerts:
        ticker = a["ticker"]
        name = get_name(ticker)[:36]
        buys_m = a["buys"] / 1e6
        sells_m = a["sells"] / 1e6
        net_m = a["net"] / 1e6

        left = (
            f'<strong>{ticker}</strong> '
            f'<span style="color:var(--text-secondary);">{name}</span>'
            f' <span style="color:var(--text-muted); font-size:0.8rem;">· {a["buyers"]} buyers</span>'
        )
        right = (
            f'Buys <b style="color:var(--success);">${buys_m:.2f}M</b> · '
            f'Sells <span style="color:var(--text-secondary);">${sells_m:.2f}M</span> · '
            f'Net <b>${net_m:+.2f}M</b>'
        )
        st.markdown(_alert_row("ok" if net_m > 0 else "", left, right), unsafe_allow_html=True)


def _render_valuation_tab(alerts: list[dict]):
    if not alerts:
        st.info("No banks trading >15% below their fair P/TBV right now.")
        return

    for a in alerts:
        ticker = a["ticker"]
        name = get_name(ticker)
        discount = a["discount"]
        fair = a["fair_price"]
        roatce = a["roatce"]
        price = a["price"]

        if discount > 30:
            sev = "ok"
        elif discount > 20:
            sev = "ok"
        else:
            sev = ""

        extras = []
        if price: extras.append(f"Now ${price:.2f}")
        if fair: extras.append(f"Fair ${fair:.2f}")
        if roatce is not None:
            if a.get("distorted") and a.get("roatce_norm") is not None:
                extras.append(
                    f"ROATCE {roatce:.1f}% ⚠️ (adj {a['roatce_norm']:.1f}%)")
            else:
                extras.append(f"ROATCE {roatce:.1f}%")

        left = (
            f'<strong>{ticker}</strong> '
            f'<span style="color:var(--text-secondary);">{name[:36]}</span>'
            f' <span style="color:var(--success); font-weight:600; margin-left:8px;">{discount:.0f}% below fair</span>'
        )
        right = " · ".join(extras)
        st.markdown(_alert_row(sev, left, right), unsafe_allow_html=True)
