"""
Home page — branded landing with live summary stats, top opportunities,
recent filings, and navigation cards.
"""

import html as _html
from html import escape as _esc

import streamlit as st
import pandas as pd

from data.bank_mapping import get_name
from ui.components import (
    section_header,
    delta_chip,
    stat_pill,
    pill_row,
    bank_link_row,
    list_column,
    news_card,
    alert_row,
    external_link,
)


# ══════════════════════════════════════════════════════════════════════
# Markets & Rates
# ══════════════════════════════════════════════════════════════════════

_TENORS = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"),
           ("10Y", "DGS10"), ("30Y", "DGS30")]


# Every FRED series Markets & Rates renders. Fetched as ONE persisted
# bundle (30-min TTL, cross-instance) — live per-series fetches cost a
# measured 3.6s on each cold instance.
_FRED_HOME_SERIES = ([sid for _, sid in _TENORS]
                     + ["FEDFUNDS", "T10Y2Y", "BAMLH0A0HYM2",
                        "BAMLC0A0CM", "VIXCLS"])


def _fred_points_live(series_id: str):
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


def _fred_points(series_id: str):
    """(latest, prior, ~1wk) from the persisted home-rates bundle.
    JSON round-trip returns lists, not tuples — callers index/unpack only."""
    from data.cache import served_snapshot
    bundle = served_snapshot(
        "home_rates_snap", 1800,
        lambda: {sid: _fred_points_live(sid) for sid in _FRED_HOME_SERIES})
    pts = bundle.get(series_id)
    if pts is None:  # series outside the bundle — fetch live, don't hide it
        return _fred_points_live(series_id)
    return (pts[0], pts[1], pts[2])


def _prices_asof(tickers):
    """(label, color) describing how fresh the warm price cache is for these
    tickers — so stale data is never shown as if it were live. Returns the
    freshest row's timestamp; flags when the data is more than a day old
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
        return f"stale prices · {stamp} ({days:.0f}d old)", "#dc2626"
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
    section_header("", "Markets & Rates", sub)

    # ── Treasury curve: level + daily bps move ──────────────────────────
    td = {lab: _fred_points(sid) for lab, sid in _TENORS}
    ff = _fred_points("FEDFUNDS")[0]

    def _rate_pill(label, level, prior, is_spread=False):
        if level is None:
            val = "—"
        elif is_spread:
            col = "var(--danger)" if level < 0 else "var(--text-primary)"
            val = f'<span style="color:{col};">{level:+.2f}</span>'
        else:
            val = f"{level:.2f}%"
        chg = (level - prior) * 100 if (level is not None and prior is not None) else None
        return stat_pill(label, val, delta_chip(chg, "bp"))

    pills = [stat_pill("FED FUNDS", f"{ff:.2f}%" if ff is not None else "—")]
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
            return stat_pill(label, "—")
        val = f"{lv:.2f}%" if unit == "bp" else f"{lv:.1f}"
        chg = (lv - pr) * (100 if unit == "bp" else 1) if pr is not None else None
        return stat_pill(label, val, delta_chip(chg, unit, up_is_good=False))

    risk = [_risk_pill("HY OAS", hy, "bp"), _risk_pill("IG OAS", ig, "bp"),
            _risk_pill("VIX", vix, "pt")]

    c1, c2 = st.columns([4, 1])
    with c1:
        # All rate / spread / risk pills in one dense wrap — fills the width
        # instead of leaving the empty band that was here before.
        pill_row(pills + sp_pills + risk, margin="2px 0 0")
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
            val, sub, sub_col = "—", "", "var(--text-muted)"
        else:
            val = f"${price:,.2f}"
            sub_col = ("var(--danger)" if (chg is not None and chg < 0)
                       else "var(--success)")
            sub = f"{chg:+.2f}%" if chg is not None else ""
        c = ctx.get(t, {})

        def _ctx(lbl, v):
            if v is None:
                return ""
            cc = "var(--success)" if v >= 0 else "var(--danger)"
            return (f'<span style="color:var(--text-muted);">{lbl}</span>'
                    f'<span style="color:{cc}; font-weight:600;"> {v:+.1f}%</span>')
        ctx_bits = " · ".join(b for b in [_ctx("1w", c.get("w1")),
                                          _ctx("ytd", c.get("ytd"))] if b)
        value = (f'{val}<span style="font-size:var(--fs-2xs); font-weight:600; '
                 f'margin-left:5px; color:{sub_col};">{sub}</span>')
        return stat_pill(t, value, href=f"?bench={t}", hover_title=desc,
                         selected=(t == sel), foot_html=ctx_bits)

    pills = [_pill_link(t, desc, quotes.get(t)) for t, desc in MARKET_BENCHMARKS]
    pill_row(pills)

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
            if st.button("× close chart", key="bench_close"):
                del st.query_params["bench"]
                st.rerun()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# ABOVE-THE-FOLD GRID (redesign, owner-locked 2026-06-16)
# 3 equal columns × 3 rows + a full-height feed rail (col 3). One dense
# table system; every row deep-links. Rendered as a single st.markdown
# HTML blob so the CSS grid holds the exact approved geometry; selectors
# use the codebase's ?param= deep-link pattern (state-preserving hrefs).
# See memory home-above-fold-spec + _scratch_abovefold.py for the spec.
# ══════════════════════════════════════════════════════════════════════

_AF_ETFS = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow Jones"),
    ("IWM", "Russell 2000"), ("IWO", "R2000 Growth"), ("IWN", "R2000 Value"),
    ("IJR", "S&P SmallCap"), ("KRE", "Regional Banks"), ("KBE", "S&P Banks"),
    ("XLF", "Financials"), ("KBWB", "KBW Banks"),
]
_AF_DEFAULT_OVERLAY = ("SPY", "QQQ", "KRE")

# (label, source, key, kind). source: live (yfinance intraday), fred (daily),
# spread (computed from live 10Y−2Y, FRED fallback). kind drives unit fmt.
_AF_RATES = [
    ("Fed Funds", "fred", "FEDFUNDS", "pct"),
    ("3M", "live", "3M", "pct"),
    ("6M", "fred", "DGS6MO", "pct"),
    ("1Y", "fred", "DGS1", "pct"),
    ("2Y", "live", "2Y", "pct"),
    ("5Y", "live", "5Y", "pct"),
    ("10Y", "live", "10Y", "pct"),
    ("30Y", "live", "30Y", "pct"),
    ("10Y − 2Y", "spread", None, "spread"),
    ("HY OAS", "fred", "BAMLH0A0HYM2", "pct"),
    ("IG OAS", "fred", "BAMLC0A0CM", "pct"),
]
_AF_LIVE_FALLBACK = {"3M": "DGS3MO", "2Y": "DGS2", "5Y": "DGS5",
                     "10Y": "DGS10", "30Y": "DGS30"}

_AF_TIERS = [("all", "All"), ("mc", "Money-Center"), ("lg", "Large Regional"),
             ("reg", "Regional"), ("comm", "Community")]
_AF_TIER_NAME = {"mc": "Money-Center (>$1T)", "lg": "Large Regional ($100B-$1T)",
                 "reg": "Regional ($10-100B)", "comm": "Community (<$10B)"}

_AF_OVERLAY_COLORS = ["#1e3a8a", "#0e7490", "#b45309", "#6d28d9", "#047857",
                      "#be185d", "#0369a1", "#a16207"]
_AF_TF_OPTS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "2Y"]
_AF_TF_FETCH = {"1W": "1M", "1M": "3M", "3M": "6M", "6M": "1Y",
                "YTD": "1Y", "1Y": "1Y", "2Y": "2Y"}
_AF_TF_TAIL = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "2Y": 504}

_AF_CSS = r"""
<style>
.afwrap{--mono:'SFMono-Regular','SF Mono','JetBrains Mono',ui-monospace,'Roboto Mono',Menlo,Consolas,monospace;color:#111827;}
.afwrap .kg{display:grid;grid-template-columns:1fr 1fr 1fr;grid-auto-rows:244px;gap:14px;}
.afwrap .pane{background:#fff;border:1px solid #dde3ec;border-radius:3px;display:flex;flex-direction:column;overflow:hidden;}
.afwrap .hd{flex:0 0 auto;display:flex;justify-content:space-between;align-items:center;padding:9px 14px 7px;border-bottom:1px solid #eceff4;}
.afwrap .hd .t{font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#1e293b;}
.afwrap .hd .s{font-size:9px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:#94a3b8;display:flex;align-items:center;gap:5px;}
.afwrap .live{width:6px;height:6px;border-radius:50%;background:#059669;display:inline-block;}
.afwrap .body{flex:1 1 auto;overflow:auto;}
.afwrap .etf{display:flex;flex-direction:column;}
.afwrap .erow{display:grid;align-items:center;column-gap:6px;padding:0 14px;border-bottom:1px solid #f6f8fa;grid-template-columns:20px 1.5fr .7fr 1fr 1fr .75fr .85fr;}
.afwrap .erow:last-child{border-bottom:none;}
.afwrap .erow.eh{flex:0 0 auto;height:21px;border-bottom:1px solid #eceff4;}
/* Fixed, tight row height (top-aligned) so density is uniform across panes —
   sparse panes (e.g. Calendar) get whitespace at the bottom, never stretched
   rows; full panes (11 ETFs) still fit without a scroll. */
.afwrap .erow.ed{flex:0 0 auto;height:16px;}
.afwrap .erow.r2{grid-template-columns:1.55fr 1fr .8fr .85fr;}
.afwrap .erow.m5{grid-template-columns:1.5fr .62fr 1fr .8fr;}
.afwrap .erow.a4{grid-template-columns:.62fr 2fr .72fr;}
.afwrap .h{font-size:8.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9aa6b4;}
.afwrap .num{text-align:right;font-family:var(--mono);font-size:11px;font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:#1f2937;}
.afwrap .num.h{font-family:inherit;}
.afwrap .nm{font-size:11px;color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.afwrap .tk{font-size:10.5px;font-weight:700;letter-spacing:.03em;color:#1e3a8a;text-decoration:none;}
.afwrap .up{color:#047857;}.afwrap .dn{color:#b91c1c;}.afwrap .mut{color:#aab4c2;}
.afwrap a.crow{text-decoration:none;color:inherit;display:contents;}
.afwrap .cbx{width:11px;height:11px;border:1px solid #1e3a8a;border-radius:2px;background:#1e3a8a;position:relative;display:inline-block;}
.afwrap .cbx.off{background:#fff;}
.afwrap .cbx:not(.off):after{content:"";position:absolute;left:3px;top:1px;width:2.5px;height:5.5px;border:solid #fff;border-width:0 1.4px 1.4px 0;transform:rotate(45deg);}
.afwrap .ph{display:flex;align-items:center;justify-content:center;color:#aab4c2;font-size:11px;font-style:italic;}
.afwrap .pend{padding:7px 14px;color:#aab4c2;font-size:10px;font-style:italic;}
.afwrap .ctl{flex:0 0 auto;display:flex;align-items:center;gap:6px;padding:6px 12px 5px;border-bottom:1px solid #f4f6f9;flex-wrap:wrap;}
.afwrap .seg{display:flex;gap:2px;}
.afwrap .seg a{font-family:var(--mono);font-size:8px;font-weight:700;padding:2px 5px;border-radius:3px;color:#7c8a9c;background:#f1f4f8;text-decoration:none;}
.afwrap .seg a.on{background:#1e3a8a;color:#fff;}
.afwrap .cdiv{width:1px;height:10px;background:#e2e8f0;margin:0 3px;}
.afwrap .seglbl{font-size:7.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#aab4c2;}
.afwrap .dd{position:relative;font-size:8.5px;}
.afwrap .dd summary{list-style:none;cursor:pointer;font-weight:600;color:#334155;background:#fff;border:1px solid #d8dee8;border-radius:3px;padding:2px 6px;}
.afwrap .dd summary::-webkit-details-marker{display:none;}
.afwrap .dd .menu{position:absolute;z-index:6;top:115%;left:0;background:#fff;border:1px solid #d8dee8;border-radius:4px;box-shadow:0 4px 12px rgba(15,23,42,.12);min-width:128px;padding:3px;}
.afwrap .dd .menu a{display:block;padding:3px 8px;font-size:9px;color:#334155;text-decoration:none;border-radius:3px;white-space:nowrap;}
.afwrap .dd .menu a.on{background:#eef2fb;color:#1e3a8a;font-weight:600;}
.afwrap .dotc{width:7px;height:7px;border-radius:50%;display:inline-block;flex:0 0 auto;}
.afwrap .evt{font-size:11px;color:#334155;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.afwrap .evt .sym{color:#64748b;font-weight:700;}
.afwrap .fitem{display:grid;grid-template-columns:34px 1fr auto;align-items:center;column-gap:8px;height:19px;padding:0 12px;border-bottom:1px solid #f6f8fa;white-space:nowrap;text-decoration:none;}
.afwrap a.fitem:hover{background:#f7f9fc;}
.afwrap .ftag{font-family:var(--mono);font-size:8px;font-weight:700;}
.afwrap .ftag.ma{color:#1e3a8a;}.afwrap .ftag.k,.afwrap .ftag.pr,.afwrap .ftag.ex,.afwrap .ftag.tr{color:#9aa6b4;}
.afwrap .fhl{font-size:10.5px;color:#1e3a8a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.afwrap .fhl .sym{color:#64748b;font-weight:700;}
.afwrap .fwhen{font-family:var(--mono);font-size:8.5px;color:#aab4c2;font-variant-numeric:tabular-nums;text-align:right;}
.afwrap .cbar{flex:0 0 auto;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 12px 3px;}
.afwrap .leg{display:flex;gap:10px;flex-wrap:wrap;}
.afwrap .leg span{display:flex;align-items:center;gap:4px;font-size:9.5px;font-weight:600;color:#475569;font-variant-numeric:tabular-nums;}
.afwrap .chart{flex:1 1 auto;min-height:0;padding:0 6px 4px;}
.afwrap .chart svg{width:100%;height:100%;display:block;}
</style>
"""


def _af_state() -> dict:
    qp = st.query_params
    oc = qp.get("oc")
    overlay = tuple(t for t in oc.split(",") if t) if oc else _AF_DEFAULT_OVERLAY
    return {"overlay": overlay, "tf": qp.get("tf") or "3M",
            "mv": qp.get("mv") or "g", "mh": qp.get("mh") or "d",
            "msz": qp.get("msz") or "all", "vsz": qp.get("vsz") or "all",
            "vp": qp.get("vp") or "1d"}


def _af_href(**changes) -> str:
    """Same-tab href preserving current params (so section=Home + the other
    selectors survive a click), with the given keys overridden."""
    from urllib.parse import urlencode
    params = {k: v for k, v in st.query_params.items()}
    params["s"] = "Home"
    params.pop("bank", None)
    for k, v in changes.items():
        if v is None:
            params.pop(k, None)
        else:
            params[k] = v
    return "?" + urlencode(params)


def _af_n(v, dp=2):
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return None


def _af_signed(v, dp=2, suffix=""):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ("—", "mut")
    cls = "up" if f > 0 else ("dn" if f < 0 else "mut")
    return (f"{f:+,.{dp}f}{suffix}", cls)


def _af_seg(key: str, cur: str, opts: list) -> str:
    return ('<span class="seg">' + "".join(
        f'<a class="{"on" if cur == v else ""}" href="{_af_href(**{key: v})}">{lbl}</a>'
        for v, lbl in opts) + "</span>")


def _af_size_dropdown(cur: str, key: str = "msz") -> str:
    label = dict(_AF_TIERS).get(cur, "All")
    menu = "".join(
        f'<a class="{"on" if cur == k else ""}" href="{_af_href(**{key: k})}">{lbl}</a>'
        for k, lbl in _AF_TIERS)
    return (f'<span class="seglbl">Size</span><details class="dd">'
            f'<summary>{label} ▾</summary><div class="menu">{menu}</div></details>')


def _af_etf_pane(state: dict) -> str:
    syms = [t for t, _ in _AF_ETFS]
    try:
        from data.price_cache_store import get_prices
        warm = get_prices(syms)
    except Exception:
        warm = {}
    try:
        from data import fmp_client
        aftq = fmp_client.get_aftermarket_quote_batch(syms)
    except Exception:
        aftq = {}
    from data import fmp_client as _fc
    sel = set(state["overlay"])
    rows = ('<div class="erow eh"><span></span><span class="h">Name</span>'
            '<span class="h">Tkr</span><span class="num h">Last</span>'
            '<span class="num h">Chg</span><span class="num h">%</span>'
            '<span class="num h">Aft</span></div>')
    for t, name in _AF_ETFS:
        q = warm.get(t) or {}
        price = q.get("price")
        last = _af_n(price) or "—"
        chg_t, chg_c = _af_signed(q.get("change"))
        pct_t, pct_c = _af_signed(q.get("change_pct"))
        aq = aftq.get(t) or {}
        aft = _fc.aftermarket_move(aq.get("bid"), aq.get("ask"), price)
        aft_t, aft_c = _af_signed(aft) if aft is not None else ("—", "mut")
        on = "" if t in sel else " off"
        new_sel = (sorted(sel - {t}) if t in sel else sorted(sel | {t}))
        href = _af_href(oc=",".join(new_sel) or None)
        rows += (
            f'<a class="crow" href="{href}" target="_self"><div class="erow ed">'
            f'<span class="cbx{on}"></span>'
            f'<span class="nm">{name}</span><span class="tk">{t}</span>'
            f'<span class="num">{last}</span>'
            f'<span class="num {chg_c}">{chg_t}</span>'
            f'<span class="num {pct_c}">{pct_t}</span>'
            f'<span class="num {aft_c}">{aft_t}</span></div></a>')
    return ('<div class="pane" style="grid-column:1;grid-row:1;">'
            '<div class="hd"><span class="t">Markets · ETFs</span>'
            '<span class="s"><span class="live"></span>Live</span></div>'
            f'<div class="body"><div class="etf">{rows}</div></div></div>')


def _af_rates_pane() -> str:
    """Live intraday yields (yfinance) for 3M/2Y/5Y/10Y/30Y + a live 10Y−2Y
    spread; FRED daily for 6M/1Y/Fed Funds + the credit OAS. Live rows carry
    a green marker; a failed live tenor falls back to FRED (never a guess)."""
    try:
        from data.live_rates import live_yields
        ly = live_yields() or {}
    except Exception:
        ly = {}

    def pts(source, key):
        if source == "live":
            v = ly.get(key)
            if v and v[0] is not None:
                return (v[0], v[1], v[2], True)
            lv, pr, wk = _fred_points(_AF_LIVE_FALLBACK[key])
            return (lv, pr, wk, False)
        if source == "spread":
            t10, t2 = ly.get("10Y"), ly.get("2Y")
            if t10 and t2 and t10[0] is not None and t2[0] is not None:
                pr = (t10[1] - t2[1]) if (t10[1] is not None and t2[1] is not None) else None
                wk = (t10[2] - t2[2]) if (t10[2] is not None and t2[2] is not None) else None
                return (t10[0] - t2[0], pr, wk, True)
            lv, pr, wk = _fred_points("T10Y2Y")
            return (lv, pr, wk, False)
        lv, pr, wk = _fred_points(key)
        return (lv, pr, wk, False)

    rows = ('<div class="erow r2 eh"><span class="h">Instrument</span>'
            '<span class="num h">Level</span><span class="num h">1D bp</span>'
            '<span class="num h">1W bp</span></div>')
    for label, source, key, kind in _AF_RATES:
        lv, pr, wk, is_live = pts(source, key)
        dot = ('<span class="dotc" style="background:#059669;margin-right:5px;" '
               'title="live ~15m"></span>') if is_live else ""
        if lv is None:
            rows += (f'<div class="erow r2 ed"><span class="nm">{dot}{label}</span>'
                     '<span class="num mut">—</span><span class="num mut">—</span>'
                     '<span class="num mut">—</span></div>')
            continue
        lvl = (f'<span class="num">{lv:+.2f}</span>' if kind == "spread"
               else f'<span class="num">{lv:.2f}</span>')
        d1_t, d1_c = _af_signed((lv - pr) * 100, dp=0) if pr is not None else ("—", "mut")
        d1w_t, d1w_c = _af_signed((lv - wk) * 100, dp=0) if wk is not None else ("—", "mut")
        rows += (f'<div class="erow r2 ed"><span class="nm">{dot}{label}</span>{lvl}'
                 f'<span class="num {d1_c}">{d1_t}</span>'
                 f'<span class="num {d1w_c}">{d1w_t}</span></div>')
    return ('<div class="pane" style="grid-column:1;grid-row:3;">'
            '<div class="hd"><span class="t">Rates · Credit</span>'
            '<span class="s"><span class="live"></span>live · FRED daily</span></div>'
            f'<div class="body"><div class="etf">{rows}</div></div></div>')


def _af_movers_pane(all_metrics: list[dict], state: dict) -> str:
    from analysis.peer_groups import asset_size_tier
    mv, mh, msz = state["mv"], state["mh"], state["msz"]
    want = _AF_TIER_NAME.get(msz)
    wk = {}
    if mh == "w":  # chg_1w lives in the warm cache (nightly job), not all_metrics
        try:
            from data.price_cache_store import get_prices
            wk = get_prices([m.get("ticker") for m in (all_metrics or [])
                             if m.get("ticker")])
        except Exception:
            wk = {}
    data = []
    for m in (all_metrics or []):
        tk = m.get("ticker")
        if not tk:
            continue
        v = ((wk.get(tk) or {}).get("chg_1w") if mh == "w"
             else m.get("change_pct"))
        if v is None:
            continue
        if want and asset_size_tier(m.get("total_assets")) != want:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        data.append((tk, m.get("price"), v))
    asc = (mv == "l")
    data = [d for d in data if (d[2] < 0 if asc else d[2] > 0)]
    data.sort(key=lambda d: d[2], reverse=not asc)
    data = data[:12]
    ctl = ('<div class="ctl">' + _af_seg("mv", mv, [("g", "Gainers"), ("l", "Losers")])
           + '<span class="cdiv"></span>'
           + _af_seg("mh", mh, [("d", "Day"), ("w", "Week")])
           + '<span class="cdiv"></span>' + _af_size_dropdown(msz) + '</div>')
    if not data:
        note = ("Week movers populate with the nightly history job."
                if mh == "w" else "No movers match this filter.")
        body = f'<div class="pend">{note}</div>'
    else:
        rows = ('<div class="erow m5 eh"><span class="h">Name</span>'
                '<span class="h">Tkr</span><span class="num h">Last</span>'
                '<span class="num h">%</span></div>')
        for tk, price, v in data:
            pct_t, pct_c = _af_signed(v)
            last = _af_n(price) or "—"
            rows += (
                f'<a class="crow" href="?s=Home&bank={tk}" target="_self">'
                f'<div class="erow m5 ed"><span class="nm">{_esc((get_name(tk) or "")[:22])}</span>'
                f'<span class="tk">{tk}</span><span class="num">{last}</span>'
                f'<span class="num {pct_c}">{pct_t}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return ('<div class="pane" style="grid-column:2;grid-row:1;">'
            '<div class="hd"><span class="t">Movers</span>'
            '<span class="s">All coverage</span></div>'
            f'{ctl}<div class="body">{body}</div></div>')


def _af_calendar_pane(watchlist: list[str]) -> str:
    import datetime as dt
    items = []
    for a in _collect_earnings_alerts(watchlist):
        eps = a.get("eps_est")
        items.append({"kind": "earn", "date": a["date"], "ticker": a["ticker"],
                      "name": get_name(a["ticker"]) or a["ticker"],
                      "detail": (f"${eps:.2f}e" if eps is not None else "")})
    try:
        from data.macro_calendar import get_upcoming_prints
        for p in get_upcoming_prints(days=7):
            items.append({"kind": "macro", "date": p.get("date"),
                          "ticker": None, "name": p.get("name") or "",
                          "detail": ("FOMC" if p.get("kind") == "fomc" else "")})
    except Exception:
        pass
    items = [i for i in items if i.get("date")]
    items.sort(key=lambda i: i["date"])
    items = items[:14]
    if not items:
        body = '<div class="pend">No earnings or macro prints in the window.</div>'
    else:
        today = dt.date.today().isoformat()
        rows = ('<div class="erow a4 eh"><span class="h">When</span>'
                '<span class="h">Event</span><span class="num h">Est./Time</span></div>')
        for i in items:
            is_today = i["date"] == today
            try:
                when = "Today" if is_today else dt.datetime.strptime(
                    i["date"], "%Y-%m-%d").strftime("%b %d")
            except Exception:
                when = i["date"]
            wstyle = ' style="color:#1e3a8a;font-weight:700;"' if is_today else ""
            dot = "#1e3a8a" if i["kind"] == "earn" else "#b45309"
            sym = (f' <span class="sym">&gt;{i["ticker"]}</span>' if i["ticker"] else "")
            ev = (f'<span class="evt"><span class="dotc" style="background:{dot};'
                  f'margin-right:7px;"></span>{_esc(i["name"][:30])}{sym}</span>')
            href = (f'?s=Home&bank={i["ticker"]}' if i["ticker"] else "?s=Home")
            rows += (
                f'<a class="crow" href="{href}" target="_self"><div class="erow a4 ed">'
                f'<span class="nm"{wstyle}>{when}</span>{ev}'
                f'<span class="num mut">{i["detail"] or "—"}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return ('<div class="pane" style="grid-column:2;grid-row:3;">'
            '<div class="hd"><span class="t">Calendar</span>'
            '<span class="s"><span class="dotc" style="background:#1e3a8a;"></span>earnings'
            '&nbsp;<span class="dotc" style="background:#b45309;"></span>macro</span></div>'
            f'<div class="body">{body}</div></div>')


def _af_feed_items(watchlist: list[str]) -> list[dict]:
    from data.cache import served_snapshot
    return served_snapshot("home_af_feed_snap", 1800,
                           lambda: _af_feed_items_live(watchlist),
                           guard=len(watchlist or []))


def _af_feed_items_live(watchlist: list[str]) -> list[dict]:
    import datetime as dt
    out = []
    try:
        from data.events import get_universe_recent
        from data.events.wire_base import is_safe_news_url, is_junk_news
        # Disclosures only — NO google_news (it floods the feed with
        # third-party/analyst mentions, e.g. "Morgan Stanley lowers Brent
        # forecast" tagged >MS). SEC 8-K + the PR wires + IR-site only.
        _feed_sources = ["sec_8k", "businesswire", "prnewswire",
                         "globenewswire", "ir_site"]
        for r in get_universe_recent(limit=150, sources=_feed_sources):
            if not is_safe_news_url(r.get("url")):
                continue
            head = (r.get("headline") or "").strip()
            tk = r.get("ticker")
            if not head or is_junk_news(head, tk):
                continue
            et = r.get("event_type") or ""
            if et == "m_and_a" or _is_ma_headline(head):
                tag, cls = "M&A", "ma"
            elif r.get("source") == "sec_8k":
                tag, cls = "8-K", "k"
            elif et == "executive_change":
                tag, cls = "EXEC", "ex"
            else:
                tag, cls = "PR", "pr"
            out.append({"tag": tag, "cls": cls, "tk": tk, "head": head,
                        "ts": r.get("published_at")})
    except Exception:
        pass
    try:
        from data.form4_client import recent_open_market_transactions
        from data.bank_mapping import get_cik
        ciks = {t: get_cik(t) for t in (watchlist or [])}
        for tx in recent_open_market_transactions(ciks, days=14, limit=40):
            buy = tx.get("direction") == "Buy"
            sh = tx.get("shares")
            verb = "buys" if buy else "sells"
            who = (tx.get("role") or "Insider").split(",")[0]
            nm = get_name(tx["ticker"]) or tx["ticker"]
            qty = f"{int(sh):,} of " if sh else ""
            out.append({"tag": "BUY" if buy else "SELL", "cls": "tr",
                        "tk": tx["ticker"],
                        "head": f"{who} {verb} {qty}{nm}", "ts": tx.get("date")})
    except Exception:
        pass

    def _k(o):
        p = o.get("ts")
        try:
            t = p if hasattr(p, "year") else dt.datetime.fromisoformat(
                str(p).replace("Z", "+00:00"))
            return t.timestamp()
        except Exception:
            return 0.0
    out.sort(key=_k, reverse=True)
    return out[:40]


def _af_feed_pane(watchlist: list[str]) -> str:
    items = _af_feed_items(watchlist)
    if not items:
        body = '<div class="pend">Feed populates with the next poll / insider job.</div>'
    else:
        body = ""
        for it in items:
            tk = it.get("tk")
            sym = f' <span class="sym">&gt;{tk}</span>' if tk else ""
            when = _relative_time(it.get("ts"))
            href = (f'?s=Home&bank={tk}' if tk else "?s=Home")
            body += (
                f'<a class="fitem" href="{href}" target="_self">'
                f'<span class="ftag {it["cls"]}">{_esc(it["tag"])}</span>'
                f'<span class="fhl">{_esc(it["head"][:90])}{sym}</span>'
                f'<span class="fwhen">{when}</span></a>')
    return ('<div class="pane" style="grid-column:3;grid-row:1 / span 3;">'
            '<div class="hd"><span class="t">Bank News Feed</span>'
            '<span class="s">universe</span></div>'
            f'<div class="body" style="overflow:auto;">{body}</div></div>')


def _af_overlay_series(sel, tf):
    from data import fmp_client
    out = []
    for i, tk in enumerate(sel[:8]):
        try:
            h = fmp_client.get_history(tk, period=_AF_TF_FETCH.get(tf, "1Y"))
            if h is None or h.empty or "close" not in h:
                continue
            h = h.dropna(subset=["close"]).sort_values("date")
            if tf == "YTD":
                yr = h["date"].iloc[-1].year
                h = h[h["date"].dt.year == yr]
            else:
                h = h.tail(_AF_TF_TAIL.get(tf, 252))
            closes = h["close"].tolist()
            dates = h["date"].tolist()
            if len(closes) < 2 or not closes[0]:
                continue
            base = closes[0]
            pcts = [(c / base - 1.0) * 100.0 for c in closes]
            out.append((tk, _AF_OVERLAY_COLORS[i % len(_AF_OVERLAY_COLORS)], pcts, dates))
        except Exception:
            continue
    return out


def _af_overlay_svg(series) -> str:
    allp = [p for _, _, pcts, _ in series for p in pcts]
    if not allp:
        return ""
    ymin, ymax = min(allp), max(allp)
    if ymax - ymin < 0.5:
        ymin -= 0.5; ymax += 0.5
    pad = (ymax - ymin) * 0.12
    ymin -= pad; ymax += pad
    L, R, T, B = 30, 412, 12, 126

    def Y(v):
        return T + (1 - (v - ymin) / (ymax - ymin)) * (B - T)

    def X(i, n):
        return L + (i / (n - 1)) * (R - L) if n > 1 else L
    parts = []
    for k in range(4):
        v = ymin + (k / 3) * (ymax - ymin)
        y = Y(v)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="#f1f5f9" stroke-width="1"/>')
        parts.append(f'<text x="{L-3}" y="{y+3:.1f}" font-family="monospace" font-size="8" fill="#aab4c2" text-anchor="end">{v:+.1f}%</text>')
    if ymin < 0 < ymax:
        y0 = Y(0)
        parts.append(f'<line x1="{L}" y1="{y0:.1f}" x2="{R}" y2="{y0:.1f}" stroke="#cbd5e1" stroke-width="1"/>')
    longest = max(series, key=lambda s: len(s[3]))[3]
    n = len(longest)
    if n >= 2:
        for frac, anchor in [(0.0, "start"), (1/3, "middle"), (2/3, "middle"), (1.0, "end")]:
            idx = min(n - 1, int(frac * (n - 1)))
            d = longest[idx]
            try:
                lbl = d.strftime("%b %d")
            except Exception:
                lbl = str(d)[:6]
            parts.append(f'<text x="{X(idx,n):.1f}" y="140" font-family="monospace" font-size="8" fill="#aab4c2" text-anchor="{anchor}">{lbl}</text>')
    for tk, color, pcts, _d in series:
        n = len(pcts)
        pts = " ".join(f"{X(i,n):.1f},{Y(v):.1f}" for i, v in enumerate(pcts))
        last = pcts[-1]
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.7" stroke-linejoin="round" points="{pts}"/>')
        parts.append(f'<circle cx="{X(n-1,n):.1f}" cy="{Y(last):.1f}" r="2.2" fill="{color}"/>')
        parts.append(f'<text x="{R+3}" y="{Y(last)+2.5:.1f}" font-family="monospace" font-size="8.5" font-weight="700" fill="{color}">{last:+.1f}</text>')
    return ('<svg viewBox="0 0 460 150" preserveAspectRatio="xMidYMid meet">' + "".join(parts) + "</svg>")


def _af_overlay_pane(state: dict) -> str:
    sel = list(state["overlay"])
    tf = state["tf"]
    series = [] if (not sel or tf == "1D") else _af_overlay_series(sel, tf)
    last_by = {tk: pcts[-1] for tk, _c, pcts, _d in series}
    color_by = {tk: c for tk, c, _p, _d in series}
    leg = '<div class="leg">'
    for i, tk in enumerate(sel[:8]):
        c = color_by.get(tk, _AF_OVERLAY_COLORS[i % len(_AF_OVERLAY_COLORS)])
        tail = (f' {last_by[tk]:+.1f}%' if tk in last_by else "")
        leg += (f'<span><span class="dotc" style="background:{c};"></span>{tk}{tail}</span>')
    leg += "</div>"
    cbar = (f'<div class="cbar">{leg}'
            + _af_seg("tf", tf, [(x, x) for x in _AF_TF_OPTS]) + "</div>")
    if not sel:
        body = '<div class="pend">Check ETFs in the Markets pane to overlay them here.</div>'
    elif tf == "1D":
        body = '<div class="pend">1D intraday goes live once the Premium key deploys.</div>'
    elif not series:
        body = '<div class="pend">No history available for the selection.</div>'
    else:
        body = f'<div class="chart">{_af_overlay_svg(series)}</div>'
    return ('<div class="pane" style="grid-column:1;grid-row:2;">'
            '<div class="hd"><span class="t">Overlay · Selected</span>'
            f'<span class="s">{len(sel)} of 11</span></div>'
            f'{cbar}{body}</div>')


def _af_volume_pane(all_metrics: list[dict], state: dict) -> str:
    from analysis.peer_groups import asset_size_tier
    vp, vsz = state["vp"], state["vsz"]
    ctl = ('<div class="ctl">' + _af_size_dropdown(vsz, key="vsz")
           + '<span class="cdiv"></span>'
           + _af_seg("vp", vp, [("1d", "1D"), ("1w", "1W"), ("1m", "1M"), ("6m", "6M")])
           + '</div>')
    relfield = {"1d": "rel_volume", "1w": "relvol_1w",
                "1m": "relvol_1m", "6m": "relvol_6m"}.get(vp, "rel_volume")
    try:
        from data.price_cache_store import get_prices
        warm = get_prices([m.get("ticker") for m in (all_metrics or [])
                           if m.get("ticker")])
    except Exception:
        warm = {}
    want = _AF_TIER_NAME.get(vsz)
    data = []
    for m in (all_metrics or []):
        tk = m.get("ticker")
        rv = (warm.get(tk) or {}).get(relfield)
        if not tk or rv is None:
            continue
        if want and asset_size_tier(m.get("total_assets")) != want:
            continue
        data.append((tk, rv, m.get("change_pct")))
    data.sort(key=lambda d: d[1], reverse=True)
    data = data[:12]
    if not data:
        body = ('<div class="pend">Unusual volume populates once the '
                'nightly history job has run.</div>')
    else:
        rows = ('<div class="erow m5 eh"><span class="h">Name</span>'
                '<span class="h">Tkr</span><span class="num h">Vol ×avg</span>'
                '<span class="num h">%</span></div>')
        for tk, rv, pct in data:
            pct_t, pct_c = _af_signed(pct)
            rows += (
                f'<a class="crow" href="?s=Home&bank={tk}" target="_self">'
                f'<div class="erow m5 ed"><span class="nm">{_esc((get_name(tk) or "")[:22])}</span>'
                f'<span class="tk">{tk}</span>'
                f'<span class="num">{rv:.1f}×</span>'
                f'<span class="num {pct_c}">{pct_t}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return ('<div class="pane" style="grid-column:2;grid-row:2;">'
            '<div class="hd"><span class="t">Unusual Volume</span>'
            '<span class="s">All coverage</span></div>'
            f'{ctl}<div class="body">{body}</div></div>')


def _af_safe(fn, title, col, row, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"[home.af] pane {title!r} failed: {type(e).__name__}: {e}")
        return (f'<div class="pane ph" style="grid-column:{col};grid-row:{row};">'
                f'<div><div class="hd" style="border:none;">'
                f'<span class="t">{_esc(title)}</span></div>'
                'temporarily unavailable</div></div>')


def _render_above_fold(all_metrics: list[dict], watchlist: list[str]):
    """Render the locked above-the-fold grid as one HTML blob; each pane is
    isolated so a slow/failing source degrades to its own error state."""
    state = _af_state()
    panes = [
        _af_safe(_af_etf_pane, "Markets · ETFs", 1, "1", state),
        _af_safe(_af_movers_pane, "Movers", 2, "1", all_metrics, state),
        _af_safe(_af_feed_pane, "Bank News Feed", 3, "1 / span 3", watchlist),
        _af_safe(_af_overlay_pane, "Overlay · Selected", 1, "2", state),
        _af_safe(_af_volume_pane, "Unusual Volume", 2, "2", all_metrics, state),
        _af_safe(_af_rates_pane, "Rates · Credit", 1, "3"),
        _af_safe(_af_calendar_pane, "Calendar", 2, "3", watchlist),
    ]
    st.markdown(_AF_CSS + '<div class="afwrap"><div class="kg">'
                + "".join(panes) + "</div></div>", unsafe_allow_html=True)


def render_home(all_metrics: list[dict], watchlist: list[str]):
    """Render the home/dashboard page."""

    # Drop the page a touch below the top nav so the title bar + data-source
    # freshness strip ("Live · FDIC …") clear the nav and aren't clipped.
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

    # ── Title bar (DESIGN-SYSTEM.md) ──────────────────────────────────
    from ui.chrome import title_bar
    title_bar("KSK Investors", "Home",
              f'<span class="ksk-dot ok"></span>Live · FDIC · SEC EDGAR · FMP · '
              f'{len(watchlist)} US banks covered')

    from utils.timing import timed

    # Above-the-fold redesign (owner-locked 2026-06-16): one dense 3×3 grid
    # + full-height feed. Replaces the old stacked sections; the old
    # _render_* helpers remain below for reference until the cleanup pass.
    with timed("home.above_fold"):
        _render_above_fold(all_metrics, watchlist)


def _render_rates_strip():
    """One thin pill row of the numbers a desk checks first — pinned above
    everything (user decision 2026-06-12). All values come from the
    persisted FRED bundle + warm price cache: zero live calls."""
    ff = _fred_points("FEDFUNDS")
    t10 = _fred_points("DGS10")
    sp = _fred_points("T10Y2Y")
    hy = _fred_points("BAMLH0A0HYM2")
    vix = _fred_points("VIXCLS")

    def _p(label, pts, fmt="{:.2f}%", spread=False, unit="bp"):
        lv, pr = pts[0], pts[1]
        if lv is None:
            return stat_pill(label, "—")
        val = fmt.format(lv)
        if spread and lv < 0:
            val = f'<span style="color:var(--danger);">{val}</span>'
        if pr is None:
            chg = None
        else:  # bp = rate delta ×100; pt = level delta as-is (VIX)
            chg = (lv - pr) * 100 if unit == "bp" else (lv - pr)
        return stat_pill(label, val, delta_chip(chg, unit))

    pills = [_p("FF", ff), _p("10Y", t10),
             _p("10Y−2Y", sp, "{:+.2f}", spread=True),
             _p("HY OAS", hy), _p("VIX", vix, "{:.1f}", unit="pt")]
    try:
        from data.price_cache_store import get_prices
        kre = (get_prices(["KRE"]) or {}).get("KRE") or {}
        px, prev = kre.get("price"), kre.get("prev_close")
        if px:
            chg_pct = kre.get("change_pct")
            if chg_pct is None and prev:
                chg_pct = ((px / prev) - 1) * 100
            # Single $ in the whole pill row — no LaTeX pairing risk, and
            # the backslash escape renders literally inside the HTML pill.
            pills.append(stat_pill(
                "KRE", f"${px:,.2f}",
                delta_chip(chg_pct, "%") if chg_pct is not None else ""))
    except Exception:
        pass
    pill_row(pills, margin="0 0 2px")


def _render_overnight_breaking():
    """Overnight & Breaking — the world since yesterday's close, categorized
    (HOME-MACRO-PLAN.md): Macro / Geopolitical / Domestic / Markets, fed by
    the Google News topic poll, curated to reputable sources + market
    relevance (data/events/topic_curation.py). Bank alerts render below."""
    section_header("", "Overnight & Breaking",
                   "macro · geopolitical · domestic · markets — last 24h · "
                   "curated wires only")
    try:
        from data.events import get_topic_news
        from data.events.topic_curation import curate_topic_news
    except Exception:
        st.caption("Topic feeds unavailable.")
        return
    cats = [("macro", "MACRO"), ("geopolitical", "GEOPOLITICAL"),
            ("domestic", "DOMESTIC"), ("markets", "MARKETS (EX-BANKS)")]
    cols = st.columns(4)
    any_items = False
    for col, (cat, label) in zip(cols, cats):
        try:
            items = curate_topic_news(
                get_topic_news(cat, hours=24, limit=40), cat, limit=5)
        except Exception:
            items = []
        rows = ""
        for it in items[:6]:
            head = _esc((it.get("headline") or "")[:110])
            url = _esc(it.get("url") or "")
            src_n = _esc((it.get("source_name") or it.get("source") or "")[:24])
            rows += (
                '<div style="padding:3px 0;border-bottom:0.5px solid var(--border-subtle);'
                'font-size:var(--fs-sm);line-height:1.35;">'
                f'<a href="{url}" target="_blank" style="color:var(--text-primary);'
                f'text-decoration:none;">{head}</a>'
                f'<div style="color:var(--text-muted);font-size:var(--fs-2xs);">{src_n}</div></div>')
        if rows:
            any_items = True
        empty = ('<div style="color:var(--text-muted);font-size:var(--fs-sm);'
                 'padding:4px 0;">—</div>')
        with col:
            st.markdown(list_column(label, rows or empty,
                                    accent="var(--text-secondary)"),
                        unsafe_allow_html=True)
    if not any_items:
        st.caption("Topic feeds populate with the next news poll cycle "
                   "(every 30 min in market hours).")


# ══════════════════════════════════════════════════════════════════════
# Market movers — biggest moves across coverage
# ══════════════════════════════════════════════════════════════════════

def _render_watchlist_movers(all_metrics: list[dict]):
    """Biggest price moves across all covered banks — gainers and losers side by
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
    section_header("", "Market Movers", _sub)

    def _row(tk, price, chg):
        col = ("var(--success)" if chg > 0
               else ("var(--danger)" if chg < 0 else "var(--text-secondary)"))
        px = f"${price:,.2f}" if isinstance(price, (int, float)) else "—"
        right = (f'<span style="color:var(--text-secondary); font-size:var(--fs-base); '
                 f'margin-right:8px;">{px}</span>'
                 f'<b style="color:{col};">{chg:+.2f}%</b>')
        return bank_link_row(tk, (get_name(tk) or "")[:24], right)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            list_column("▲ TOP GAINERS", "".join(_row(*it) for it in gainers),
                        accent="var(--success)", empty_text="None"),
            unsafe_allow_html=True)
    with c2:
        st.markdown(
            list_column("▼ TOP LOSERS", "".join(_row(*it) for it in losers),
                        accent="var(--danger)", empty_text="None"),
            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# Today's calendar
# ══════════════════════════════════════════════════════════════════════

def _render_todays_calendar(watchlist: list[str]):
    """Today's Agenda — covered-bank earnings (next 14 days) merged with the
    macro print calendar (next 7 days): one view of what matters today."""
    section_header("", "Today's Agenda", "bank earnings · macro prints · FOMC")
    alerts = _collect_earnings_alerts(watchlist)

    # Macro prints column (data/macro_calendar: FRED release dates + FOMC).
    prints_html = ""
    try:
        from data.macro_calendar import get_upcoming_prints
        for pr in get_upcoming_prints(days=7)[:8]:
            hot = pr.get("importance") == "high"
            nm = _esc(pr.get("name") or "")
            kd = (' <span style="color:var(--danger);font-weight:600;">FOMC</span>'
                  if pr.get("kind") == "fomc" else "")
            wt = "font-weight:600;" if hot else ""
            prints_html += (
                '<div style="display:flex;justify-content:space-between;gap:8px;'
                'padding:3px 0;border-bottom:0.5px solid var(--border-subtle);'
                f'font-size:var(--fs-sm);"><span style="{wt}">{nm}{kd}</span>'
                f'<span style="color:var(--text-secondary);">{_esc(pr.get("date") or "")}'
                '</span></div>')
    except Exception:
        prints_html = ""

    if not alerts and not prints_html:
        st.caption("No covered-bank earnings in the next 14 days and no "
                   "tracked macro prints in the next 7.")
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
        tk = a["ticker"]
        eps = a.get("eps_est")
        est = (f' <span style="color:var(--text-muted); font-size:var(--fs-sm);">'
               f'est ${eps:.2f}</span>') if eps is not None else ""
        right = (f'<span style="color:var(--text-secondary); font-size:var(--fs-sm);">'
                 f'{a["date"]}{est}</span>')
        return bank_link_row(tk, (get_name(tk) or "")[:22], right)

    cols = st.columns(4)
    for col, (label, items) in zip(cols, buckets):
        with col:
            accent = "var(--danger)" if label == "Today" else "var(--text-secondary)"
            st.markdown(
                list_column(f"{label.upper()} · {len(items)}",
                            "".join(_chip(a) for a in items), accent=accent),
                unsafe_allow_html=True,
            )
    _empty = ('<div style="color:var(--text-muted);font-size:var(--fs-sm);'
              'padding:4px 0;">—</div>')
    with cols[3]:
        st.markdown(list_column("MACRO PRINTS · 7D", prints_html or _empty,
                                accent="var(--brand-primary)"),
                    unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# Coverage leaderboard — ranked extremes
# ══════════════════════════════════════════════════════════════════════

def _render_coverage_leaderboard(all_metrics: list[dict]):
    """Ranked extremes across coverage — the value-screen lists you scan each
    morning: cheapest, highest-returning, best-yielding, widest discount."""
    section_header("", "Coverage Leaderboard", "ranked extremes across all covered banks")
    df = pd.DataFrame(all_metrics)

    def _list(title, col, ascending, fmt, color="var(--text-primary)"):
        if col not in df.columns:
            return list_column(title, "")
        sub = df[["ticker", col]].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=[col]).sort_values(col, ascending=ascending).head(5)
        rows = "".join(
            bank_link_row(r["ticker"], (get_name(r["ticker"]) or "")[:20],
                          f'<b style="color:{color};">{fmt(r[col])}</b>')
            for _, r in sub.iterrows()
        )
        return list_column(title, rows)

    lists = [
        ("CHEAPEST · P/TBV", "ptbv_ratio", True, lambda v: f"{v:.2f}x", "var(--success)"),
        ("WIDEST DISCOUNT TO FAIR", "ptbv_discount", False, lambda v: f"{v:+.0f}%", "var(--success)"),
        ("HIGHEST ROATCE", "roatce_blended", False, lambda v: f"{v:.1f}%"),
        ("HIGHEST DIVIDEND YIELD", "dividend_yield", False, lambda v: f"{v:.1f}%"),
    ]
    cols = st.columns(2)
    for i, (title, col, asc, fmt, *c) in enumerate(lists):
        with cols[i % 2]:
            st.markdown(_list(title, col, asc, fmt, *(c or [])), unsafe_allow_html=True)
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
    section_header("", "Sector M&A & Deals", "universe-wide · deals move the comps")
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
        tkr = (f'<a href="?bank={tk}" target="_self" style="text-decoration:none;">'
               f'<strong style="color:var(--text-primary);">{tk}</strong></a> '
               f'<span style="color:var(--text-muted);">{_html.escape(name)}</span>') if tk else \
              '<span style="color:var(--text-muted);">Sector</span>'
        summ = a["summary"]
        if summ and len(summ) > 200:
            summ = summ[:197].rstrip() + "…"
        # Headline/summary are third-party feed text — news_card escapes them
        # before interpolating into unsafe_allow_html.
        news_card(f'M&amp;A · {tkr} · {when}{external_link(a["url"])}',
                  a["headline"], summ, severity="high")


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

    section_header("", "Industry Valuations", "median multiples by asset-size tier")

    if "total_assets" not in df.columns:
        df = df.copy()
        df["total_assets"] = None

    # total_assets is always raw dollars (analysis/metrics.py converts FDIC
    # $thousands at the boundary) — the old "< 1e9 → ×1000" guess here
    # misclassified genuine sub-$1B banks into trillion-dollar tiers.
    def _assets_usd(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

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
    pill_row([stat_pill(lbl, val, accent="brand") for lbl, val in head_items],
             margin="0 0 10px", gap=7)

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
        st.info("Loading bank data...")
        return

    section_header("", "Alert Inbox", "news · earnings · dynamics · insider · value opps")
    st.caption("Prioritized across all covered banks. Click into any row to jump to the bank.")

    # Collect alerts from each source
    news_alerts = _collect_news_alerts(watchlist)
    earnings_alerts = _collect_earnings_alerts(watchlist)
    dynamics_alerts = _collect_dynamics_alerts(all_metrics)
    insider_alerts = _collect_insider_alerts(watchlist)
    valuation_alerts = _collect_valuation_alerts(all_metrics)

    # Important News is the main (first) tab.
    tab_news, tab1, tab2, tab3, tab4 = st.tabs([
        f"Important News ({len(news_alerts)})",
        f"Earnings ({len(earnings_alerts)})",
        f"Dynamics Alerts ({len(dynamics_alerts)})",
        f"Insider Buys ({len(insider_alerts)})",
        f"Value Opps ({len(valuation_alerts)})",
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


# event_type → (label, weight). Weight + recency rank "important news".
_NEWS_TYPES = {
    "earnings": ("Earnings", 5), "m_and_a": ("M&A", 5),
    "regulatory": ("Regulatory", 4), "executive_change": ("Leadership", 3),
    "shareholder_vote": ("Shareholder vote", 2), "filing": ("Filing", 2),
    "press_release": ("Press release", 1), "news": ("News", 1),
}


# Only actionable sources: SEC filings (8-K/10-K/10-Q) and real press releases
# (the news wires + IR-site press releases). Deliberately EXCLUDES the yfinance
# news aggregator — that's the noisy/low-signal "crap". IR-site PRs are kept
# only for banks the wires don't cover (the wires are cleaner; see below), so
# we don't double-list the same press release.
_WIRE_SOURCES = {"businesswire", "prnewswire", "globenewswire"}
_NEWS_SOURCES = ["sec_8k", "businesswire", "prnewswire", "globenewswire",
                 "ir_site", "google_news"]


# Junk filtering is unified in data/events/wire_base.is_junk_news — the same
# filter the adapters apply at ingest (it previously lived split between there
# and here, with drifting regexes).


def _collect_news_alerts(watchlist: list[str]) -> list[dict]:
    """Actionable news across all covered banks — SEC filings + real press releases
    (wires, plus IR-site for non-wire banks) — with their AI summaries."""
    import datetime as dt
    try:
        from data.events import get_universe_recent
        rows = get_universe_recent(limit=300, sources=_NEWS_SOURCES)
    except Exception:
        return []

    from data.events.wire_base import is_safe_news_url, is_junk_news
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
        if is_junk_news(head, tk):
            continue  # third-party mentions, structured notes, branch-hire trivia
        seen.add(key)
        et = r.get("event_type") or "news"
        weight = _NEWS_TYPES.get(et, ("News", 1))[1]
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
        st.info("No recent SEC filings or press releases for covered banks. "
                "(News aggregator noise is intentionally excluded — only filings "
                "and real press releases appear here.)")
        return
    for a in alerts:
        label, weight = _NEWS_TYPES.get(a["event_type"], ("News", 1))
        tk = a["ticker"]; name = get_name(tk)[:34]
        when = _relative_time(a["published_at"])
        sev = "high" if weight >= 5 else ("medium" if weight >= 3 else "")
        summ = a["summary"]
        if summ and len(summ) > 240:
            summ = summ[:237].rstrip() + "…"
        meta = (
            f'{label} &nbsp;·&nbsp; '
            f'<strong style="color:var(--text-primary);">{tk}</strong> '
            f'<span style="color:var(--text-secondary);">{_html.escape(name)}</span> '
            f'&nbsp;·&nbsp; {when}{external_link(a["url"])}'
        )
        # Headline/summary are third-party feed text — news_card escapes them.
        news_card(meta, a["headline"], summ, severity=sev)


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
    """Banks with recent net-positive insider buying. Cross-instance
    snapshot (30 min) over the GCS-cached Form 4 data — the per-ticker GCS
    reads cost seconds on a cold instance."""
    from data.cache import served_snapshot
    return served_snapshot(
        "home_insider_alerts_snap", 1800,
        lambda: _collect_insider_alerts_live(watchlist),
        guard=len(watchlist))


def _collect_insider_alerts_live(watchlist: list[str]) -> list[dict]:
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
        st.markdown(alert_row(sev, left, right), unsafe_allow_html=True)


def _render_dynamics_tab(alerts: list[dict]):
    if not alerts:
        st.markdown(
            alert_row("ok",
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
        st.markdown(alert_row(sev, left, right), unsafe_allow_html=True)

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
        st.markdown(alert_row("ok" if net_m > 0 else "", left, right), unsafe_allow_html=True)


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

        sev = "ok" if discount > 20 else ""

        extras = []
        if price: extras.append(f"Now ${price:.2f}")
        if fair: extras.append(f"Fair ${fair:.2f}")
        if roatce is not None:
            if a.get("distorted") and a.get("roatce_norm") is not None:
                extras.append(
                    f"ROATCE {roatce:.1f}% (adj {a['roatce_norm']:.1f}%)")
            else:
                extras.append(f"ROATCE {roatce:.1f}%")

        left = (
            f'<strong>{ticker}</strong> '
            f'<span style="color:var(--text-secondary);">{name[:36]}</span>'
            f' <span style="color:var(--success); font-weight:600; margin-left:8px;">{discount:.0f}% below fair</span>'
        )
        right = " · ".join(extras)
        st.markdown(alert_row(sev, left, right), unsafe_allow_html=True)
