"""
Home page — branded landing with live summary stats, top opportunities,
recent filings, and navigation cards.
"""

from html import escape as _esc

import streamlit as st

from data.bank_mapping import get_name


# ══════════════════════════════════════════════════════════════════════
# Markets & Rates
# ══════════════════════════════════════════════════════════════════════

def _rates_bundle() -> dict:
    """The Rates · Credit anchor bundle ({series_id: {level,d1,w1,m1,ytd,lo,hi}}),
    job-warmed (home_rates_full_snap, 30-min TTL) so the render reads cache
    instead of fanning out ~25 FRED fetches on the request thread. Falls back to
    a live build only if no instance/job has warmed it yet."""
    from data.cache import served_snapshot
    from data.live_rates import build_rates_anchor_bundle
    return served_snapshot("home_rates_full_snap", 1800, build_rates_anchor_bundle)


def _rate_anchors(series_id: str, bundle: dict):
    """Anchors for one series from the warmed bundle; live fallback for any id
    the bundle is missing (JSON round-trip keeps the dict shape)."""
    from data.live_rates import rate_anchors_live
    a = bundle.get(series_id)
    return a if a is not None else rate_anchors_live(series_id)


# ── Feed helpers (shared by the above-the-fold news rail) ─────────────

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


# ══════════════════════════════════════════════════════════════════════
# ABOVE-THE-FOLD GRID (redesign, owner-locked 2026-06-16)
# 3 equal columns × 3 rows + a full-height feed rail (col 3). One dense
# table system; every row deep-links. Rendered as a single st.markdown
# HTML blob so the CSS grid holds the exact approved geometry; selectors
# use the codebase's ?param= deep-link pattern (state-preserving hrefs).
# See memory home-above-fold-spec for the spec.
# ══════════════════════════════════════════════════════════════════════

_AF_ETFS = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow Jones"),
    ("IWM", "Russell 2000"), ("IWO", "R2000 Growth"), ("IWN", "R2000 Value"),
    ("IJR", "S&P SmallCap"), ("KRE", "Regional Banks"), ("KBE", "S&P Banks"),
    ("XLF", "Financials"), ("KBWB", "KBW Banks"),
]
_AF_DEFAULT_OVERLAY = ("SPY", "QQQ", "KRE")

# Rates · Credit board, sectioned. Each row is (label, kind, a, b):
#   tenor  — live yfinance level/1D/1W, FRED series `a` for the 1M/YTD/52w anchors
#   fred   — everything from FRED series `a`
#   spread — live 10Y−2Y level/1D/1W, FRED series `a` (T10Y2Y) for anchors
#   calc   — FRED series `a` minus `b`, all anchors (no live; 52w range n/a)
# Live tenors map to their FRED fallback series for the history anchors.
_LIVE_FRED = {"3M": "DGS3MO", "2Y": "DGS2", "5Y": "DGS5",
              "10Y": "DGS10", "30Y": "DGS30"}
_AF_RATES_SECTIONS = [
    ("Treasuries", [
        ("1M", "fred", "DGS1MO", None), ("3M", "tenor", "3M", None),
        ("6M", "fred", "DGS6MO", None), ("1Y", "fred", "DGS1", None),
        ("2Y", "tenor", "2Y", None), ("3Y", "fred", "DGS3", None),
        ("5Y", "tenor", "5Y", None), ("7Y", "fred", "DGS7", None),
        ("10Y", "tenor", "10Y", None), ("20Y", "fred", "DGS20", None),
        ("30Y", "tenor", "30Y", None),
    ]),
    ("Spreads", [   # convention: shorter tenor first (short − long)
        ("3M − 5Y", "calc", "DGS3MO", "DGS5"),
        ("2Y − 10Y", "spread", "T10Y2Y", None),
        ("3M − 10Y", "fredn", "T10Y3M", None),
        ("10Y − 30Y", "calc", "DGS10", "DGS30"),
        ("Fed Funds − 2Y", "calc", "DFF", "DGS2"),
    ]),
    ("Credit · OAS", [
        ("AAA", "fred", "BAMLC0A1CAAA", None),
        ("BBB", "fred", "BAMLC0A4CBBB", None),
        ("IG", "fred", "BAMLC0A0CM", None),
        ("BB", "fred", "BAMLH0A1HYBB", None),
        ("HY", "fred", "BAMLH0A0HYM2", None),
        ("CCC", "fred", "BAMLH0A3HYC", None),
        ("EM", "fred", "BAMLEMCBPIOAS", None),
    ]),
    ("Funding · Real", [
        ("Fed Funds", "fred", "DFF", None),
        ("SOFR", "fred", "SOFR", None),
        ("Prime", "fred", "DPRIME", None),
        ("30Y Mtg", "fred", "MORTGAGE30US", None),
        ("10Y Real", "fred", "DFII10", None),
        ("10Y B/E", "fred", "T10YIE", None),
    ]),
]

_AF_TIERS = [("all", "All"), ("mc", "Money-Center"), ("lg", "Large Regional"),
             ("reg", "Regional"), ("comm", "Community")]
_AF_TIER_NAME = {"mc": "Money-Center (>$1T)", "lg": "Large Regional ($100B-$1T)",
                 "reg": "Regional ($10-100B)", "comm": "Community (<$10B)"}

_AF_OVERLAY_COLORS = ["#1e3a8a", "#0e7490", "#b45309", "#6d28d9", "#047857",
                      "#be185d", "#0369a1", "#a16207"]
_AF_TF_OPTS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "2Y"]
# Every window except 1D is served from EOD daily bars (one fetch, tailed by
# trading days) — so none routes through the 15-min/1-hour intraday endpoints,
# which return too few bars for these spans (e.g. tailing 5 one-hour bars
# mislabels ~5 hours as a week). 1D is the only intraday path (see
# _af_overlay_1d). Each value is a get_history period resolving to an EOD-daily
# endpoint in fmp_client._PERIOD_TO_ENDPOINT; refresh_home_snapshot warms them.
_AF_TF_FETCH = {"1W": "3M", "1M": "3M", "3M": "6M", "6M": "1Y",
                "YTD": "1Y", "1Y": "1Y", "2Y": "2Y"}
_AF_TF_TAIL = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "2Y": 504}

_AF_CSS = r"""
<style>
.afwrap{--mono:'SFMono-Regular','SF Mono','JetBrains Mono',ui-monospace,'Roboto Mono',Menlo,Consolas,monospace;color:#111827;}
.afwrap .pane{background:#fff;border:1px solid #dde3ec;border-radius:0;display:flex;flex-direction:column;overflow:hidden;}
.afwrap .hd{flex:0 0 auto;display:flex;justify-content:space-between;align-items:center;padding:9px 14px 7px;border-bottom:1px solid #eceff4;}
.afwrap .hd .t{font-size:var(--fs-grid-11);font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#1e293b;}
.afwrap .hd .s{font-size:var(--fs-grid-9);font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:#94a3b8;display:flex;align-items:center;gap:5px;}
.afwrap .live{width:6px;height:6px;border-radius:50%;background:#059669;display:inline-block;}
.afwrap .body{flex:0 0 auto;overflow:visible;}
.afwrap .etf{display:flex;flex-direction:column;}
.afwrap .erow{display:grid;align-items:center;column-gap:6px;padding:0 14px;border-bottom:1px solid #f6f8fa;grid-template-columns:20px 1.5fr .7fr 1fr 1fr .75fr .85fr;box-sizing:border-box;}
.afwrap .erow:last-child{border-bottom:none;}
/* Let grid cells shrink below their content's intrinsic width so a long
   (nowrap) bank name ellipsizes instead of forcing its track wide and
   spilling the row past the card. Canonical grid/flex overflow fix. */
.afwrap .erow>*,.afwrap .fitem>*{min-width:0;}
.afwrap .erow.eh{flex:0 0 auto;height:21px;border-bottom:1px solid #eceff4;}
/* Fixed, tight row height (top-aligned) so density is uniform across panes —
   sparse panes (e.g. Calendar) get whitespace at the bottom, never stretched
   rows; full panes (11 ETFs) still fit without a scroll. */
.afwrap .erow.ed{flex:0 0 auto;height:16px;}
.afwrap .erow.r2{grid-template-columns:1.55fr 1fr .8fr .85fr;}
.afwrap .erow.m5{grid-template-columns:1.5fr .62fr 1fr .8fr;}
.afwrap .erow.a4{grid-template-columns:.58fr 1.5fr .92fr .72fr;}
.afwrap .erow.e1{grid-template-columns:1.3fr .58fr .95fr .85fr .62fr .62fr .62fr .62fr .8fr;column-gap:4px;padding:0 10px;}
.afwrap .erow.selrow{background:#f3f7ff;}
.afwrap .erow.e1 .num{font-size:var(--fs-grid-10);}
.afwrap .erow.m1{grid-template-columns:1.3fr .55fr .9fr .8fr .58fr .58fr .58fr .72fr;column-gap:4px;padding:0 12px;}
.afwrap .erow.m1 .num{font-size:var(--fs-grid-10);}
.afwrap .erow.v1{grid-template-columns:1.45fr .58fr .95fr .66fr .72fr .98fr;column-gap:5px;padding:0 12px;}
.afwrap .erow.v1 .num{font-size:var(--fs-grid-10);}
/* Rates · Credit board, 10 cols: Instrument | Level | 1D bp | 1W bp | 1W range |
   1M bp | 1M range | YTD bp | YTD range | 52wk. Each window is two SEPARATE
   columns — a bp number ('bp' header) and a range bar ('range' header). */
.afwrap .erow.r10{grid-template-columns:.92fr .54fr .4fr .44fr .52fr .44fr .52fr .46fr .52fr .56fr;column-gap:12px;padding:0 10px;}
.afwrap .erow.r10 .num{font-size:var(--fs-grid-10);}
.afwrap .h.rh{text-align:center;}
.afwrap .rsec{font-size:var(--fs-grid-8);font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#1e3a8a;background:#f7f9fc;padding:4px 10px 3px;border-bottom:1px solid #eef1f5;}
.afwrap .rng{position:relative;height:4px;width:100%;border-radius:0;background:#eef2f7;align-self:center;}
.afwrap .rng .rngdot{position:absolute;top:50%;width:5px;height:5px;border-radius:50%;background:#1e3a8a;transform:translate(-50%,-50%);}
.afwrap .h{font-size:var(--fs-grid-8_5);font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9aa6b4;}
.afwrap .num{text-align:right;font-family:var(--mono);font-size:var(--fs-grid-11);font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:#1f2937;}
.afwrap .num.h{font-family:inherit;}
.afwrap .nm{font-size:var(--fs-grid-11);color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.afwrap .tk{font-size:var(--fs-grid-10_5);font-weight:700;letter-spacing:.03em;color:#1e3a8a;text-decoration:none;}
.afwrap .up{color:#047857;}.afwrap .dn{color:#b91c1c;}.afwrap .mut{color:#aab4c2;}
.afwrap a.crow{text-decoration:none;color:inherit;display:contents;}
.afwrap .cbx{width:11px;height:11px;border:1px solid #1e3a8a;border-radius:0;background:#1e3a8a;position:relative;display:inline-block;}
.afwrap .cbx.off{background:#fff;}
.afwrap .cbx:not(.off):after{content:"";position:absolute;left:3px;top:1px;width:2.5px;height:5.5px;border:solid #fff;border-width:0 1.4px 1.4px 0;transform:rotate(45deg);}
.afwrap .ph{display:flex;align-items:center;justify-content:center;color:#aab4c2;font-size:var(--fs-grid-11);font-style:italic;}
.afwrap .pend{padding:7px 14px;color:#aab4c2;font-size:var(--fs-grid-10);font-style:italic;}
.afwrap .ctl{flex:0 0 auto;display:flex;align-items:center;gap:6px;padding:6px 12px 5px;border-bottom:1px solid #f4f6f9;flex-wrap:wrap;}
.afwrap .seg{display:flex;gap:2px;}
.afwrap .seg a{font-family:var(--mono);font-size:var(--fs-grid-8);font-weight:700;padding:2px 5px;border-radius:0;color:#7c8a9c;background:#f1f4f8;text-decoration:none;}
.afwrap .seg a.on{background:#1e3a8a;color:#fff;}
.afwrap .cdiv{width:1px;height:10px;background:#e2e8f0;margin:0 3px;}
.afwrap .seglbl{font-size:var(--fs-grid-7_5);font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#aab4c2;}
.afwrap .dd{position:relative;font-size:var(--fs-grid-8);overflow:visible!important;}
.afwrap .dd summary{list-style:none;cursor:pointer;font-family:var(--mono);font-size:var(--fs-grid-8)!important;line-height:1.5!important;font-weight:700;color:#7c8a9c;background:#f1f4f8;border:none;border-radius:0;padding:2px 6px!important;min-height:0!important;display:inline-block;}
.afwrap .dd summary::-webkit-details-marker{display:none;}
.afwrap .dd .menu{position:absolute;z-index:6;top:115%;left:0;background:#fff;border:1px solid #d8dee8;border-radius:0;box-shadow:0 4px 12px rgba(15,23,42,.12);min-width:128px;padding:3px;}
.afwrap .dd .menu a{display:block;padding:3px 8px;font-size:var(--fs-grid-9);color:#334155;text-decoration:none;border-radius:0;white-space:nowrap;}
.afwrap .dd .menu a.on{background:#eef2fb;color:#1e3a8a;font-weight:600;}
.afwrap .dotc{width:7px;height:7px;border-radius:50%;display:inline-block;flex:0 0 auto;}
.afwrap .evt{font-size:var(--fs-grid-11);color:#334155;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.afwrap .evt .sym{color:#64748b;font-weight:700;}
.afwrap .fitem{display:grid;grid-template-columns:34px 1fr auto;align-items:center;column-gap:8px;height:19px;padding:0 12px;border-bottom:1px solid #f6f8fa;white-space:nowrap;text-decoration:none;}
.afwrap a.fitem:hover{background:#f7f9fc;}
.afwrap .ftag{font-family:var(--mono);font-size:var(--fs-grid-8);font-weight:700;}
.afwrap .ftag.ma{color:#1e3a8a;}.afwrap .ftag.k,.afwrap .ftag.pr,.afwrap .ftag.ex,.afwrap .ftag.tr{color:#9aa6b4;}
.afwrap .fhl{font-size:var(--fs-grid-10_5);color:#1e3a8a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.afwrap .fhl .sym{color:#64748b;font-weight:700;}
.afwrap .fwhen{font-family:var(--mono);font-size:var(--fs-grid-8_5);color:#aab4c2;font-variant-numeric:tabular-nums;text-align:right;}
.afwrap .cbar{flex:0 0 auto;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 12px 3px;}
.afwrap .leg{display:flex;gap:10px;flex-wrap:wrap;}
.afwrap .leg span{display:flex;align-items:center;gap:4px;font-size:var(--fs-grid-9_5);font-weight:600;color:#475569;font-variant-numeric:tabular-nums;}
.afwrap .chart{flex:1 1 auto;min-height:0;padding:0 6px 4px;}
.afwrap .chart svg{width:100%;height:100%;display:block;}
/* ── Native-widget panes (st.container cards) — compaction + alignment ──
   Streamlit 1.58 testids: segmented control = stButtonGroup, its buttons =
   stBaseButton-segmented_control[ Active]; dropdown = stSelectbox. */
/* Each pane card sizes to its OWN content. Streamlit otherwise pins nested
   vertical blocks to a flex-distributed height (the panes in a column split the
   column height equally), squeezing the 11-row ETF/Rates panes and clipping
   their last row. Force content height + visible overflow down the wrapper chain
   (card → stVerticalBlock/ElementContainer/Markdown → .afwrap/.body/.etf). */
/* Uniform grid gutters: the row gap between stacked panes is the pane
   margin-bottom + a constant 6.75px baseline gap, and the column gap is
   Streamlit "small" = 9px. Set margin-bottom to 2.25px so the row gap lands at
   2.25 + 6.75 = 9px, matching the column gap — tight, even gutters both ways. */
div[class*="st-key-afpane"]{border:1px solid #dde3ec!important;border-radius:0!important;background:#fff!important;padding:0 0 5px!important;margin-bottom:2.25px;flex:0 0 auto!important;align-self:flex-start!important;display:block!important;height:auto!important;}
div[class*="st-key-afpane"] [data-testid="stVerticalBlock"],
div[class*="st-key-afpane"] [data-testid="stMarkdown"]>div{display:block!important;height:auto!important;overflow:visible!important;}
div[class*="st-key-afpane"] [data-testid="stElementContainer"],
div[class*="st-key-afpane"] [data-testid="stMarkdown"],
div[class*="st-key-afpane"] [data-testid="stMarkdownContainer"]{height:auto!important;min-height:0!important;overflow:visible!important;margin-bottom:0!important;}
div[class*="st-key-afpane"] .afwrap,div[class*="st-key-afpane"] .body,div[class*="st-key-afpane"] .etf{height:auto!important;overflow:visible!important;}
/* Calendar pane scrolls past ~17 rows (owner request) so the broad econ
   calendar + bank earnings both fit without growing the pane unboundedly.
   Overrides the .body overflow:visible above via later source order. */
div[class*="st-key-afpane"] .calbody{max-height:292px!important;overflow-y:auto!important;}
div[class*="st-key-afpane"] [data-testid="stVerticalBlock"]{gap:.3rem!important;}
div[class*="st-key-afpane"] [data-testid="stElementContainer"]{padding:0!important;}
div[class*="st-key-afpane"] [data-testid="stHorizontalBlock"]{padding:3px 10px 0!important;gap:.35rem!important;}
/* control widgets that sit directly in the pane (overlay tickers / timeframe)
   get the same 10px side inset as the table; the same widgets inside a
   columns row inherit the row's inset instead (override to 0). */
div[class*="st-key-afpane"] [data-testid="stElementContainer"]:has(>[data-testid="stButtonGroup"]),
div[class*="st-key-afpane"] [data-testid="stElementContainer"]:has(>div>[data-testid="stSelectbox"]){padding:3px 10px 0!important;}
div[class*="st-key-afpane"] [data-testid="stHorizontalBlock"] [data-testid="stElementContainer"]{padding:0!important;}
div[class*="st-key-afpane"] [data-testid="stButtonGroup"]{gap:2px!important;}
div[class*="st-key-afpane"] [data-testid^="stBaseButton-segmented_control"]{min-height:0!important;height:22px!important;padding:0 9px!important;border-radius:0!important;}
div[class*="st-key-afpane"] [data-testid^="stBaseButton-segmented_control"] p{font-size:var(--fs-grid-10)!important;line-height:1!important;font-weight:600!important;}
/* Match the Size dropdown to the 22px segmented-control pills: force the height
   and kill BaseWeb's ~7.5px inner vertical padding (that padding, not min-height,
   is what inflates the select to ~38px). */
div[class*="st-key-afpane"] [data-baseweb="select"]>div{min-height:22px!important;height:22px!important;}
div[class*="st-key-afpane"] [data-baseweb="select"]>div>div{padding-top:0!important;padding-bottom:0!important;}
div[class*="st-key-afpane"] [data-baseweb="select"] *{font-size:var(--fs-grid-10_5)!important;}
</style>
"""


def _af_hd(title: str, status_html: str = "") -> str:
    """Pane header row (title + optional right-aligned status). The selector
    controls are now native Streamlit widgets (see _af_grid), so panes no
    longer carry query-param links; this just builds the .afwrap header."""
    return (f'<div class="hd"><span class="t">{title}</span>'
            f'<span class="s">{status_html}</span></div>')


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


def _af_vol(v):
    """Compact share-volume label (45.1M / 820K / —)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e6:
        return f"{v/1e6:.1f}M"
    if v >= 1e3:
        return f"{v/1e3:.0f}K"
    return f"{v:.0f}"


def _af_dollar_vol(price, volume):
    """Dollar volume (price × shares) as $1.2B / $340M / $5M / —."""
    try:
        v = float(price) * float(volume)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v/1e3:.0f}K"


def _af_etf_table() -> str:
    """Markets·ETFs quote table (HTML). The overlay selection lives in the
    Overlay pane's native multi-select now; selected ETFs get a row tint."""
    syms = [t for t, _ in _AF_ETFS]
    try:
        from data.price_cache_store import get_prices
        warm = get_prices(syms)
    except Exception:
        warm = {}
    try:
        # cache_only: never live-fetch aftermarket on the render thread (a cold
        # cache was ~13s of FMP calls); the home-snapshot job warms it.
        from data import fmp_client
        aftq = fmp_client.get_aftermarket_quote_batch(syms, cache_only=True)
    except Exception:
        aftq = {}
    from data import fmp_client as _fc
    from data.market_session import is_premarket
    # The extended-hours column IS the pre-market move during 4:00–9:30 ET; label
    # it "Pre" then, "Aft" otherwise (after-hours / spread fallback).
    _ext_lbl = "Pre %" if is_premarket() else "Aft %"
    sel = set(st.session_state.get("af_overlay") or _AF_DEFAULT_OVERLAY)
    # Headers carry the unit so $ moves (Chg) read distinctly from % moves
    # (day %, Pre/Aft, 1W, YTD).
    rows = ('<div class="erow e1 eh"><span class="h">Name</span>'
            '<span class="h">Tkr</span><span class="num h">Last</span>'
            '<span class="num h">Chg $</span><span class="num h">%</span>'
            f'<span class="num h">{_ext_lbl}</span><span class="num h">1W %</span>'
            '<span class="num h">YTD %</span><span class="num h">Vol</span></div>')
    for t, name in _AF_ETFS:
        q = warm.get(t) or {}
        price = q.get("price")
        last = _af_n(price) or "—"
        chg_t, chg_c = _af_signed(q.get("change"))
        pct_t, pct_c = _af_signed(q.get("change_pct"))
        aq = aftq.get(t) or {}
        aft = _fc.aftermarket_move(aq.get("bid"), aq.get("ask"), price)
        aft_t, aft_c = _af_signed(aft) if aft is not None else ("—", "mut")
        # 1W / YTD from the same warm cache the Movers pane reads — populated by
        # the nightly refresh_avg_volume job (chg_1w EOD-derived, chg_ytd from
        # FMP's year-anchored field). One source for all 1W/YTD on this page.
        w1_t, w1_c = (_af_signed(q.get("chg_1w"), dp=1) if q.get("chg_1w") is not None
                      else ("—", "mut"))
        ytd_t, ytd_c = (_af_signed(q.get("chg_ytd"), dp=1) if q.get("chg_ytd") is not None
                        else ("—", "mut"))
        vol = _af_vol(q.get("volume"))
        sel_cls = " selrow" if t in sel else ""
        rows += (
            f'<div class="erow e1 ed{sel_cls}">'
            f'<span class="nm">{name}</span><span class="tk">{t}</span>'
            f'<span class="num">{last}</span>'
            f'<span class="num {chg_c}">{chg_t}</span>'
            f'<span class="num {pct_c}">{pct_t}</span>'
            f'<span class="num {aft_c}">{aft_t}</span>'
            f'<span class="num {w1_c}">{w1_t}</span>'
            f'<span class="num {ytd_c}">{ytd_t}</span>'
            f'<span class="num">{vol}</span></div>')
    return (_af_hd("Markets · ETFs", '<span class="live"></span>Live')
            + f'<div class="body"><div class="etf">{rows}</div></div>')


def _neg(an):
    """Negate an anchors dict (for a spread quoted short − long off a FRED series
    that's stored long − short, e.g. T10Y2Y). level/Δ negate; every range flips:
    [lo, hi] → [-hi, -lo] (52-week and each 1W/1M/YTD window)."""
    if not an:
        return {}
    out = {k: (-an[k] if an.get(k) is not None else None)
           for k in ("level", "d1", "w1", "m1", "ytd")}
    for lo_k, hi_k in (("lo", "hi"), ("w_lo", "w_hi"),
                       ("m_lo", "m_hi"), ("y_lo", "y_hi")):
        lo, hi = an.get(lo_k), an.get(hi_k)
        out[lo_k] = -hi if hi is not None else None
        out[hi_k] = -lo if lo is not None else None
    return out


def _af_row_anchors(kind, a, b, bundle, ly):
    """Resolve {level,d1,w1,m1,ytd,lo,hi} + is_live for one board row.

    Curve-spread convention: SHORTER tenor first (short − long), so a steep
    upward curve reads negative. Live overlays (intraday level/1D/1W) ride on
    top of the FRED history anchors. Computed (calc) spreads get no 52w range —
    the min of a difference ≠ the difference of the per-leg extremes, so n/a,
    never a guess; FRED-series spreads keep a real (possibly negated) range."""
    if kind == "calc":   # a − b, config ordered short − long
        A = _rate_anchors(a, bundle) or {}
        B = _rate_anchors(b, bundle) or {}
        out = {k: ((A.get(k) - B.get(k))
                   if (A.get(k) is not None and B.get(k) is not None) else None)
               for k in ("level", "d1", "w1", "m1", "ytd")}
        # No range bars for a computed difference (min of a−b ≠ min a − min b).
        for k in ("lo", "hi", "w_lo", "w_hi", "m_lo", "m_hi", "y_lo", "y_hi"):
            out[k] = None
        return out, False
    if kind == "tenor":
        # `a` is the live key (e.g. "10Y"); its history anchors live under the
        # FRED fallback series. Overlay the intraday level/1D/1W when live.
        an = dict(_rate_anchors(_LIVE_FRED[a], bundle) or {})
        v = ly.get(a)
        if v and v[0] is not None:
            an["level"] = v[0]
            an["d1"] = v[1] if v[1] is not None else an.get("d1")
            an["w1"] = v[2] if v[2] is not None else an.get("w1")
            return an, True
        return an, False
    if kind == "spread":   # 2Y − 10Y: negated T10Y2Y anchors + live overlay
        an = _neg(_rate_anchors(a, bundle) or {})
        t10, t2 = ly.get("10Y"), ly.get("2Y")
        if t10 and t2 and t10[0] is not None and t2[0] is not None:
            an["level"] = t2[0] - t10[0]
            if t2[1] is not None and t10[1] is not None:
                an["d1"] = t2[1] - t10[1]
            if t2[2] is not None and t10[2] is not None:
                an["w1"] = t2[2] - t10[2]
            return an, True
        return an, False
    if kind == "fredn":   # short − long off a long − short FRED series (−T10Y3M)
        return _neg(_rate_anchors(a, bundle) or {}), False
    return dict(_rate_anchors(a, bundle) or {}), False   # fred


def _af_range_bar(level, lo, hi, label="52-wk"):
    """A range bar: dot positioned where `level` sits in the window's [lo, hi].
    n/a (—) when the range is unavailable (e.g. computed spreads)."""
    try:
        if lo is None or hi is None or hi <= lo:
            return '<span class="num mut">—</span>'
        pct = max(0.0, min(100.0, (level - lo) / (hi - lo) * 100.0))
        return (f'<span class="rng" title="{label} {lo:.2f} – {hi:.2f}">'
                f'<span class="rngdot" style="left:{pct:.0f}%"></span></span>')
    except Exception:
        return '<span class="num mut">—</span>'


def _af_rates_table() -> str:
    """Sectioned rates & credit board: full Treasury curve, curve spreads,
    credit OAS by rating, and funding/real rates — each with Level + 1D/1W/1M/YTD
    bp and a 52-week range bar. Live tenors (yfinance ~15m) carry a green dot and
    refresh the level/1D/1W; the 1M/YTD/range anchors and everything else are
    daily FRED. Missing inputs render '—', never a guess."""
    try:
        from data.live_rates import live_yields
        ly = live_yields() or {}
    except Exception:
        ly = {}
    bundle = _rates_bundle()

    # Each of 1W/1M/YTD is two SEPARATE columns: a bp-change number with a 'bp'
    # header, and a range bar (where the level sits in that window's hi–lo) with a
    # 'range' header. 1D is a number only (no intraday hi/lo in daily FRED); 52wk
    # is the long-run range bar.
    head = ('<div class="erow r10 eh"><span class="h">Instrument</span>'
            '<span class="num h">Level %</span><span class="num h">1D bp</span>'
            '<span class="num h">1W bp</span><span class="h rh">range</span>'
            '<span class="num h">1M bp</span><span class="h rh">range</span>'
            '<span class="num h">YTD bp</span><span class="h rh">range</span>'
            '<span class="h rh">52wk</span></div>')
    body = ""
    for section, rows in _AF_RATES_SECTIONS:
        is_spread = section == "Spreads"
        body += f'<div class="rsec">{section}</div>'
        for label, kind, a, b in rows:
            an, is_live = _af_row_anchors(kind, a, b, bundle, ly)
            lv = an.get("level")
            dot = ('<span class="dotc" style="background:#059669;margin-right:4px;"'
                   ' title="live ~15m"></span>') if is_live else ""
            if lv is None:
                body += (f'<div class="erow r10 ed"><span class="nm">{dot}{label}</span>'
                         + '<span class="num mut">—</span>' * 9 + '</div>')
                continue
            lvl = f'{lv:+.2f}' if is_spread else f'{lv:.2f}'

            def _bp(anchor):
                return (_af_signed((lv - anchor) * 100, dp=0)
                        if anchor is not None else ("—", "mut"))

            def _win(anchor, lo, hi, wlabel):
                # bp number cell + its own range-bar cell (two grid columns)
                txt, cls = _bp(anchor)
                bar = _af_range_bar(lv, lo, hi, wlabel)
                return f'<span class="num {cls}">{txt}</span>{bar}'

            d1t, d1c = _bp(an.get("d1"))
            wc_w = _win(an.get("w1"), an.get("w_lo"), an.get("w_hi"), "1W")
            wc_m = _win(an.get("m1"), an.get("m_lo"), an.get("m_hi"), "1M")
            wc_y = _win(an.get("ytd"), an.get("y_lo"), an.get("y_hi"), "YTD")
            rng = _af_range_bar(lv, an.get("lo"), an.get("hi"))
            body += (f'<div class="erow r10 ed"><span class="nm">{dot}{label}</span>'
                     f'<span class="num">{lvl}</span>'
                     f'<span class="num {d1c}">{d1t}</span>'
                     f'{wc_w}{wc_m}{wc_y}{rng}</div>')
    return (_af_hd("Rates · Credit", '<span class="live"></span>live · FRED daily')
            + f'<div class="body"><div class="etf">{head}{body}</div></div>')


def _af_movers_table(all_metrics: list[dict], mv: str, mh: str, msz: str) -> str:
    from analysis.peer_groups import asset_size_tier
    from data.market_session import is_premarket
    want = _AF_TIER_NAME.get(msz)
    # Warm cache holds the derived columns (chg_1w/chg_ytd/volume) + the
    # week sort field — one read for the whole universe.
    try:
        from data.price_cache_store import get_prices
        warm = get_prices([m.get("ticker") for m in (all_metrics or [])
                           if m.get("ticker")])
    except Exception:
        warm = {}
    # Pre-market window: the regular-session day move is stale (yesterday's
    # close), so rank/show each bank's pre-market move instead — warmed by
    # jobs/refresh_premarket, read cache-only.
    pm = is_premarket()
    premkt = {}
    if pm:
        try:
            from data import cache as _pmc
            premkt = (_pmc.get("premarket_moves:v1") or {}).get("value") or {}
        except Exception:
            premkt = {}
    data = []
    for m in (all_metrics or []):
        tk = m.get("ticker")
        if not tk:
            continue
        if want and asset_size_tier(m.get("total_assets")) != want:
            continue
        w = warm.get(tk) or {}
        # Day move from the LIVE warm price cache (~2 min, refreshed by
        # jobs/refresh_prices) — fall back to the 15-min metrics snapshot only
        # for a bank that isn't warm-cached. Week uses the nightly chg_1w.
        if pm:
            day_pct = premkt.get(tk)        # pre-market move
        else:
            day_pct = w.get("change_pct")
            if day_pct is None:
                day_pct = m.get("change_pct")
        sortval = day_pct if mh == "d" else w.get("chg_1w")
        if sortval is None:
            continue
        try:
            sortval = float(sortval)
        except (TypeError, ValueError):
            continue
        price = w.get("price") if w.get("price") is not None else m.get("price")
        if pm:
            # Pre-market $ change derived from the move % and the prior close
            # (warm cache's last regular-session price) — not the stale day chg.
            chg = (price * day_pct / 100.0) if (price is not None
                                                and day_pct is not None) else None
        else:
            chg = w.get("change") if w.get("change") is not None else m.get("change")
        data.append({"tk": tk, "price": price, "chg": chg,
                     "pct": day_pct, "w1": w.get("chg_1w"),
                     "ytd": w.get("chg_ytd"),
                     "vol": w.get("volume") if w.get("volume") is not None
                     else m.get("volume"), "sort": sortval})
    asc = (mv == "l")
    data = [d for d in data if (d["sort"] < 0 if asc else d["sort"] > 0)]
    data.sort(key=lambda d: d["sort"], reverse=not asc)
    data = data[:12]
    if not data:
        note = ("Week movers populate with the nightly history job."
                if mh == "w" else "No movers match this filter.")
        body = f'<div class="pend">{note}</div>'
    else:
        rows = ('<div class="erow m1 eh"><span class="h">Name</span>'
                '<span class="h">Tkr</span><span class="num h">Last</span>'
                '<span class="num h">Chg $</span>'
                f'<span class="num h">{"Pre %" if pm else "%"}</span>'
                '<span class="num h">1W %</span><span class="num h">YTD %</span>'
                '<span class="num h">Vol</span></div>')
        for d in data:
            last = _af_n(d["price"]) or "—"
            chg_t, chg_c = _af_signed(d["chg"])
            pct_t, pct_c = _af_signed(d["pct"])
            w1_t, w1_c = (_af_signed(d["w1"], dp=1) if d["w1"] is not None
                          else ("—", "mut"))
            ytd_t, ytd_c = (_af_signed(d["ytd"], dp=1) if d["ytd"] is not None
                            else ("—", "mut"))
            rows += (
                f'<a class="crow" href="?s=Company&bank={d["tk"]}" target="_self">'
                f'<div class="erow m1 ed"><span class="nm">{_esc((get_name(d["tk"]) or "")[:20])}</span>'
                f'<span class="tk">{d["tk"]}</span>'
                f'<span class="num">{last}</span>'
                f'<span class="num {chg_c}">{chg_t}</span>'
                f'<span class="num {pct_c}">{pct_t}</span>'
                f'<span class="num {w1_c}">{w1_t}</span>'
                f'<span class="num {ytd_c}">{ytd_t}</span>'
                f'<span class="num">{_af_vol(d["vol"])}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return f'<div class="body">{body}</div>'


def _af_calendar_table(watchlist: list[str]) -> str:
    import datetime as dt
    items = []
    # Earnings — wider window than the old 14d inbox so the calendar isn't
    # empty between reporting seasons (bank Q2 starts ~mid-July); we show the
    # soonest events overall, however far out.
    try:
        from data.estimates import fetch_earnings_calendar
        from data import earnings_call as _ecall
        # Two layers: FMP's earnings calendar gives reliable, universe-wide report
        # timing ("Before open" / "After close"); the PR/IR parser adds the precise
        # call time + webcast where a bank publishes it (and takes precedence).
        _call_map = _ecall.merged_call_info()
        _timing = _ecall.earnings_timing_map()
        _td = dt.date.today()
        for e in fetch_earnings_calendar(tuple(watchlist)):
            ds = e.get("next_earnings_date")
            try:
                d = dt.datetime.strptime(ds, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if not (0 <= (d - _td).days <= 60):
                continue
            eps = e.get("eps_estimate")
            ci = _call_map.get(e["ticker"]) or {}
            # Cons./Prior column: precise PR/IR call time + webcast if known, else
            # FMP's before/after-open label. Row links to the webcast when present.
            # A webcast-only entry (the curated megabank links carry no time) keeps
            # FMP's timing label and appends the webcast cue, so neither signal is lost.
            when_lbl = (_timing.get(e["ticker"]) or {}).get("when") or ""
            if ci.get("webcast_url") and not ci.get("call_time") and when_lbl:
                mid = f"{when_lbl} · webcast ↗"
            else:
                mid = _ecall.mid_label(ci) or when_lbl
            items.append({"kind": "earn", "date": ds, "ticker": e["ticker"],
                          "name": get_name(e["ticker"]) or e["ticker"], "mid": mid,
                          "webcast": ci.get("webcast_url"), "dial_in": ci.get("dial_in"),
                          "detail": (f"${eps:.2f}e" if eps is not None else "")})
    except Exception:
        pass
    try:
        # Econ prints from FMP's economic calendar (consensus + previous, which
        # the FRED schedule lacks) — marquee US releases only, soonest first.
        from data import econ_calendar as _ec
        for p in _ec.get_upcoming_releases(days=30):
            est = _ec.fmt_value(p.get("estimate"), p.get("unit"))
            prev = _ec.fmt_value(p.get("previous"), p.get("unit"))
            mid = f"{est or '—'} / {prev or '—'}" if (est or prev) else ""
            items.append({"kind": "macro", "date": p.get("date"),
                          "ticker": None, "name": p.get("event") or "", "mid": mid,
                          "detail": _ec.et_time(p.get("datetime")) or "—"})
    except Exception:
        pass
    items = [i for i in items if i.get("date")]
    # Keep both streams visible: the broad FMP econ calendar (weekly jobless
    # claims, etc.) would otherwise bury the later bank-earnings dates entirely.
    # Cap macro, keep earnings, show chronologically — the pane scrolls (.calbody).
    earn = sorted([i for i in items if i["kind"] == "earn"], key=lambda i: i["date"])
    macro = sorted([i for i in items if i["kind"] == "macro"], key=lambda i: i["date"])
    items = sorted(earn[:24] + macro[:10], key=lambda i: i["date"])[:30]
    if not items:
        body = '<div class="pend">No earnings or macro prints in the window.</div>'
    else:
        today = dt.date.today().isoformat()
        rows = ('<div class="erow a4 eh"><span class="h">When</span>'
                '<span class="h">Event</span>'
                '<span class="num h">Cons./Prior</span>'
                '<span class="num h">Est./Time</span></div>')
        for i in items:
            is_today = i["date"] == today
            try:
                when = "Today" if is_today else dt.datetime.strptime(
                    i["date"], "%Y-%m-%d").strftime("%b %d")
            except Exception:
                when = i["date"]
            wstyle = ' style="color:#1e3a8a;font-weight:700;"' if is_today else ""
            dot = "#1e3a8a" if i["kind"] == "earn" else "#b45309"
            sym = (f' <span class="sym">&gt;{_esc(i["ticker"])}</span>' if i["ticker"] else "")
            ev = (f'<span class="evt"><span class="dotc" style="background:{dot};'
                  f'margin-right:7px;"></span>{_esc(i["name"][:30])}{sym}</span>')
            href = (f'?s=Company&bank={i["ticker"]}' if i["ticker"] else "?s=Home")
            target, rel = "_self", ""
            if i.get("webcast"):           # earnings row links to the call webcast
                href, target, rel = _esc(i["webcast"]), "_blank", ' rel="noopener noreferrer"'
            ttl = f' title="Dial-in: {_esc(i["dial_in"])}"' if i.get("dial_in") else ""
            rows += (
                f'<a class="crow" href="{href}" target="{target}"{rel}{ttl}><div class="erow a4 ed">'
                f'<span class="nm"{wstyle}>{when}</span>{ev}'
                f'<span class="num mut">{_esc(i.get("mid")) if i.get("mid") else "—"}</span>'
                f'<span class="num mut">{_esc(i["detail"]) if i["detail"] else "—"}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return (_af_hd("Calendar",
                   '<span class="dotc" style="background:#1e3a8a;"></span>earnings'
                   '&nbsp;<span class="dotc" style="background:#b45309;"></span>macro')
            + f'<div class="body calbody">{body}</div>')


# Bumped to _v3 (2026-06-25): a stale v2 snapshot would keep serving the
# pre-filter feed (law-firm-spam drop, sibling-ticker canonicalization VYLD→JPM,
# RJF/FRHC exclusion) for up to its 30-min TTL — the bump forces a rebuild on
# this deploy's first render instead of waiting it out. The feed is ALSO warmed
# on a schedule by jobs.refresh_home_snapshot (warm_news_feed_snapshot), so it no
# longer goes stale waiting for someone to load Home after the TTL lapses.
_NEWS_FEED_SNAP_KEY = "home_af_feed_snap_v3"
_NEWS_FEED_SNAP_TTL = 1800   # 30 min


def _af_feed_items(watchlist: list[str]) -> list[dict]:
    from data.cache import served_snapshot
    return served_snapshot(_NEWS_FEED_SNAP_KEY, _NEWS_FEED_SNAP_TTL,
                           lambda: _af_feed_items_live(watchlist),
                           guard=len(watchlist or []))


def warm_news_feed_snapshot(watchlist: list[str]) -> int:
    """Rebuild + persist the Bank News Feed snapshot OFF the render path (called
    by jobs.refresh_home_snapshot). Invalidate first so the rebuild always runs
    even within the TTL, then go through the SAME served_snapshot path the render
    uses — identical key, guard and value shape — so the next Home load is a
    cache hit on a fresh feed. Returns the item count."""
    from data.cache import invalidate
    invalidate(_NEWS_FEED_SNAP_KEY)
    return len(_af_feed_items(watchlist))


def _af_feed_items_live(watchlist: list[str]) -> list[dict]:
    import datetime as dt
    out = []
    try:
        from data.events import get_universe_recent
        from data.events.wire_base import is_safe_news_url, is_junk_news
        # First-party disclosures + curated PR wires. NO google_news (it floods
        # the feed with third-party/analyst mentions, e.g. "Morgan Stanley lowers
        # Brent forecast" tagged >MS). fmp_news IS included: it carries first-
        # party Business Wire / PR Newswire releases (gated by its brand-core
        # subject guard) and is the stored copy whenever no direct-wire copy was
        # ingested — store._SOURCE_RANK now lets a wire copy upgrade it when both
        # exist, but fmp_news-only releases would otherwise never reach Home.
        _feed_sources = ["sec_8k", "businesswire", "prnewswire",
                         "globenewswire", "ir_site", "fmp_news"]
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
            # 8-K headlines are the bare SEC item category ("8-K · Other Material
            # Event"); a descriptive LLM summary (when the poll-events summarizer
            # ran) says what the filing is actually about. Wire headlines are
            # already specific, so keep them as-is.
            disp = head
            if r.get("source") == "sec_8k":
                disp = (r.get("summary") or "").strip() or head
            out.append({"tag": tag, "cls": cls, "tk": tk, "head": disp,
                        "url": r.get("url"), "ts": r.get("published_at")})
    except Exception:
        pass
    try:
        # Universe-wide insider rows, matching the news half above. Read the
        # PRE-AGGREGATED feed (one cache hit) instead of fanning out a Form-4 GCS
        # read per bank on the render thread — the heavy per-CIK scan (and the
        # dedup-by-CIK for multi-class names) runs in jobs/refresh_home_snapshot.
        # See data.form4_client.recent_open_market_universe.
        from data.form4_client import recent_open_market_universe
        for tx in recent_open_market_universe(limit=40):
            buy = tx.get("direction") == "Buy"
            sh = tx.get("shares")
            verb = "buys" if buy else "sells"
            who = (tx.get("role") or "Insider").split(",")[0]
            nm = get_name(tx["ticker"]) or tx["ticker"]
            qty = f"{int(sh):,} of " if sh else ""
            cik = tx.get("cik")
            edgar = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                     f"&CIK={cik}&type=4&dateb=&owner=include&count=40") if cik else None
            out.append({"tag": "BUY" if buy else "SELL", "cls": "tr",
                        "tk": tx["ticker"], "url": tx.get("url") or edgar,
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


def _af_feed_table(watchlist: list[str]) -> str:
    items = _af_feed_items(watchlist)
    if not items:
        body = '<div class="pend">Feed populates with the next poll / insider job.</div>'
    else:
        body = ""
        for it in items:
            tk = it.get("tk")
            # Ticker label LEADS the headline (was trailing) — always visible
            # even when a long headline ellipsizes inside .fhl.
            sym = f'<span class="sym">&gt;{tk}</span> ' if tk else ""
            when = _relative_time(it.get("ts"))
            # Link to the actual story / SEC filing (new tab); fall back to the
            # in-app company page only when no source URL is available.
            url = it.get("url")
            if url:
                href, target, rel = _esc(url), "_blank", ' rel="noopener noreferrer"'
            else:
                href = f'?s=Company&bank={tk}' if tk else "?s=Home"
                target, rel = "_self", ""
            body += (
                f'<a class="fitem" href="{href}" target="{target}"{rel}>'
                f'<span class="ftag {it["cls"]}">{_esc(it["tag"])}</span>'
                f'<span class="fhl">{sym}{_esc(it["head"][:90])}</span>'
                f'<span class="fwhen">{when}</span></a>')
    return (_af_hd("Bank News Feed", "universe")
            + f'<div class="body">{body}</div>')


def _af_overlay_1d(fmp_client, tk):
    """Intraday (15-min) series for the latest trading session, normalized to
    the PRIOR session's close — so the line shows today's full move including
    the opening gap, matching the day-change % in the Movers/ETF panes.

    Reads cache_only (like every overlay window — never live-fetch on the
    render thread); refresh_home_snapshot warms the "1W" 15-min bars. The prior
    close comes from that same 7-day window (last bar before today), so there's
    no extra call; with no prior session in the window we fall back to the
    session's first bar. Returns (pcts, dates) or None."""
    import pandas as pd
    h = fmp_client.get_history(tk, period="1W", cache_only=True,  # 15-min, 7d
                               allow_stale=True)
    if h is None or h.empty or "close" not in h:
        return None
    h = h.dropna(subset=["close"]).copy()
    # The cache round-trips through JSON (data/cache.py json.dumps default=str),
    # so "date" comes back as strings — coerce before any datetime op.
    h["date"] = pd.to_datetime(h["date"], errors="coerce")
    h = h.dropna(subset=["date"]).sort_values("date")
    if len(h) < 2:
        return None
    sess_day = h["date"].iloc[-1].normalize()      # midnight of the latest day
    sess = h[h["date"] >= sess_day]
    prior = h[h["date"] < sess_day]
    if len(sess) < 2:
        return None
    base = (float(prior["close"].iloc[-1]) if not prior.empty
            else float(sess["close"].iloc[0]))
    if not base:
        return None
    closes = sess["close"].tolist()
    dates = sess["date"].tolist()
    pcts = [(c / base - 1.0) * 100.0 for c in closes]
    return pcts, dates


def _af_overlay_series(sel, tf):
    from data import fmp_client
    import pandas as pd
    out = []
    for i, tk in enumerate(sel[:8]):
        try:
            if tf == "1D":
                row = _af_overlay_1d(fmp_client, tk)
                if row is None:
                    continue
                pcts, dates = row
            else:
                # cache_only: never do the live FMP history fetch on the render
                # thread — a cold/expired cache here was looping N×15s and
                # blocking the whole above-the-fold grid for ~84s.
                # jobs/refresh_home_snapshot warms these. allow_stale: ride
                # through a one-tick warm gap with last-known data instead of
                # blanking the chart to "No history".
                h = fmp_client.get_history(tk, period=_AF_TF_FETCH.get(tf, "1Y"),
                                           cache_only=True, allow_stale=True)
                if h is None or h.empty or "close" not in h:
                    continue
                h = h.dropna(subset=["close"]).copy()
                # Cache round-trips through JSON (dates come back as strings) —
                # coerce so YTD's .dt.year and the axis date labels work.
                h["date"] = pd.to_datetime(h["date"], errors="coerce")
                h = h.dropna(subset=["date"]).sort_values("date")
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
        # Intraday spans (1D) label the axis with clock times; multi-day spans
        # use calendar dates.
        try:
            intraday = (longest[-1] - longest[0]).total_seconds() < 36 * 3600
        except Exception:
            intraday = False
        axis_fmt = "%H:%M" if intraday else "%b %d"
        for frac, anchor in [(0.0, "start"), (1/3, "middle"), (2/3, "middle"), (1.0, "end")]:
            idx = min(n - 1, int(frac * (n - 1)))
            d = longest[idx]
            try:
                lbl = d.strftime(axis_fmt)
            except Exception:
                lbl = str(d)[:6]
            parts.append(f'<text x="{X(idx,n):.1f}" y="140" font-family="monospace" font-size="8" fill="#aab4c2" text-anchor="{anchor}">{lbl}</text>')
    # Time-aligned x (audit P3): each point is positioned by its DATE on the
    # longest series' span — a shorter/stale series ends mid-chart at its real
    # last date instead of being index-stretched to the right edge (which read
    # as current data).
    t0, t1 = longest[0], longest[-1]
    span = (t1 - t0).total_seconds() or 1.0

    def XT(d):
        frac = (d - t0).total_seconds() / span
        return L + max(0.0, min(1.0, frac)) * (R - L)

    for tk, color, pcts, dates in series:
        pts = " ".join(f"{XT(d):.1f},{Y(v):.1f}" for d, v in zip(dates, pcts))
        last = pcts[-1]
        x_last = XT(dates[len(pcts) - 1]) if len(dates) >= len(pcts) else R
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.7" stroke-linejoin="round" points="{pts}"/>')
        parts.append(f'<circle cx="{x_last:.1f}" cy="{Y(last):.1f}" r="2.2" fill="{color}"/>')
        parts.append(f'<text x="{R+3}" y="{Y(last)+2.5:.1f}" font-family="monospace" font-size="8.5" font-weight="700" fill="{color}">{last:+.1f}</text>')
    return ('<svg viewBox="0 0 460 150" preserveAspectRatio="xMidYMid meet">' + "".join(parts) + "</svg>")


def _af_overlay_table(sel: list, tf: str) -> str:
    """Normalized %-change chart + legend (HTML). The ticker multi-select and
    timeframe control are native widgets rendered above this by _af_grid."""
    series = _af_overlay_series(sel, tf) if sel else []
    last_by = {tk: pcts[-1] for tk, _c, pcts, _d in series}
    color_by = {tk: c for tk, c, _p, _d in series}
    leg = '<div class="leg">'
    for i, tk in enumerate(sel[:8]):
        c = color_by.get(tk, _AF_OVERLAY_COLORS[i % len(_AF_OVERLAY_COLORS)])
        tail = (f' {last_by[tk]:+.1f}%' if tk in last_by else "")
        leg += (f'<span><span class="dotc" style="background:{c};"></span>{tk}{tail}</span>')
    leg += "</div>"
    if not sel:
        body = '<div class="pend">Pick ETFs above to overlay them here.</div>'
    elif not series:
        body = '<div class="pend">No history available for the selection.</div>'
    else:
        body = f'<div class="chart">{_af_overlay_svg(series)}</div>'
    return f'<div class="cbar">{leg}</div>{body}'


def _af_volume_table(all_metrics: list[dict], vsz: str, vp: str) -> str:
    from analysis.peer_groups import asset_size_tier
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
        w = warm.get(tk) or {}
        rv = w.get(relfield)
        if not tk or rv is None:
            continue
        if want and asset_size_tier(m.get("total_assets")) != want:
            continue
        # Price/% from the LIVE warm cache (~2 min); snapshot only as fallback.
        price = w.get("price") if w.get("price") is not None else m.get("price")
        pct = w.get("change_pct") if w.get("change_pct") is not None else m.get("change_pct")
        vol = w.get("volume") if w.get("volume") is not None else m.get("volume")
        data.append({"tk": tk, "price": price, "pct": pct,
                     "rv": rv, "dvol": _af_dollar_vol(price, vol)})
    data.sort(key=lambda d: d["rv"], reverse=True)
    data = data[:12]
    if not data:
        body = ('<div class="pend">Unusual volume populates once the '
                'nightly history job has run.</div>')
    else:
        rows = ('<div class="erow v1 eh"><span class="h">Name</span>'
                '<span class="h">Tkr</span><span class="num h">Last</span>'
                '<span class="num h">%</span><span class="num h">×avg</span>'
                '<span class="num h">$Vol</span></div>')
        for d in data:
            pct_t, pct_c = _af_signed(d["pct"])
            last = _af_n(d["price"]) or "—"
            rows += (
                f'<a class="crow" href="?s=Company&bank={d["tk"]}" target="_self">'
                f'<div class="erow v1 ed"><span class="nm">{_esc((get_name(d["tk"]) or "")[:20])}</span>'
                f'<span class="tk">{d["tk"]}</span>'
                f'<span class="num">{last}</span>'
                f'<span class="num {pct_c}">{pct_t}</span>'
                f'<span class="num">{d["rv"]:.1f}×</span>'
                f'<span class="num">{d["dvol"]}</span></div></a>')
        body = f'<div class="etf">{rows}</div>'
    return f'<div class="body">{body}</div>'


def _md(html: str):
    """Emit a pane fragment of HTML, scoped to the .afwrap style namespace."""
    st.markdown(f'<div class="afwrap">{html}</div>', unsafe_allow_html=True)


def _af_seg_single(label, options, default, key):
    """Compact native single-select; coerce a deselect back to the default so
    a pane never renders with an empty control."""
    return st.segmented_control(label, options, default=default, key=key,
                                label_visibility="collapsed") or default


def _af_card(render_fn, key, title, *args):
    """Render one pane inside a bordered card, isolating failures so a slow or
    failing data source degrades to its own 'unavailable' card rather than
    taking the page down. The afpane key class is the CSS compaction hook."""
    with st.container(border=True, key=f"afpane_{key}"):
        try:
            render_fn(*args)
        except Exception as e:  # noqa: BLE001
            print(f"[home.af] pane {title!r} failed: {type(e).__name__}: {e}")
            _md(_af_hd(title) + '<div class="pend">temporarily unavailable</div>')


# ── Pane render functions: native selector widgets (soft rerun, NO page
#    reload) above the dense HTML table. Each runs inside an _af_card().
def _af_pane_etf(_all_metrics):
    _md(_af_etf_table())


def _af_pane_rates(_all_metrics):
    _md(_af_rates_table())


def _af_pane_calendar(watchlist):
    _md(_af_calendar_table(watchlist))


def _af_pane_feed(watchlist):
    _md(_af_feed_table(watchlist))


def _af_pane_overlay(_all_metrics):
    syms = [t for t, _ in _AF_ETFS]
    sel0 = st.session_state.get("af_overlay") or list(_AF_DEFAULT_OVERLAY)
    _md(_af_hd("Overlay · Selected", f"{len(sel0)} of 11"))
    sel = st.segmented_control(
        "Overlay tickers", syms, selection_mode="multi",
        default=list(_AF_DEFAULT_OVERLAY), key="af_overlay",
        label_visibility="collapsed")
    tf = _af_seg_single("Timeframe", _AF_TF_OPTS, "3M", "af_tf")
    _md(_af_overlay_table(list(sel or []), tf))


def _af_pane_movers(all_metrics):
    _md(_af_hd("Movers", "All coverage"))
    c1, c2 = st.columns(2)
    with c1:
        mv = _af_seg_single("Direction", ["Gainers", "Losers"], "Gainers", "af_mv")
    with c2:
        mh = _af_seg_single("Window", ["Day", "Week"], "Day", "af_mh")
    szlbl = st.selectbox("Size", [lbl for _, lbl in _AF_TIERS], key="af_msz",
                         label_visibility="collapsed")
    szkey = {lbl: k for k, lbl in _AF_TIERS}.get(szlbl, "all")
    _md(_af_movers_table(all_metrics, "l" if mv == "Losers" else "g",
                         "w" if mh == "Week" else "d", szkey))


def _af_pane_volume(all_metrics):
    _md(_af_hd("Unusual Volume", "All coverage"))
    c1, c2 = st.columns([1, 1.4])
    with c1:
        szlbl = st.selectbox("Size", [lbl for _, lbl in _AF_TIERS], key="af_vsz",
                             label_visibility="collapsed")
    with c2:
        vp = _af_seg_single("Period", ["1D", "1W", "1M", "6M"], "1D", "af_vp")
    szkey = {lbl: k for k, lbl in _AF_TIERS}.get(szlbl, "all")
    _md(_af_volume_table(all_metrics, szkey, vp.lower()))


@st.fragment
def _af_grid(all_metrics: list[dict], watchlist: list[str]):
    """Owner-locked above-the-fold, rendered as a fragment so a selector change
    re-runs ONLY this grid — instant, no full-page reload, no scroll jump.
    Layout: col1 Markets/Overlay/Rates · col2 Movers/Volume/Calendar · col3
    the full-height news feed."""
    st.markdown(_AF_CSS, unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3, gap="small")
    with c1:
        _af_card(_af_pane_etf, "etf", "Markets · ETFs", all_metrics)
        _af_card(_af_pane_overlay, "overlay", "Overlay · Selected", all_metrics)
        _af_card(_af_pane_rates, "rates", "Rates · Credit", all_metrics)
    with c2:
        _af_card(_af_pane_movers, "movers", "Movers", all_metrics)
        _af_card(_af_pane_volume, "volume", "Unusual Volume", all_metrics)
        _af_card(_af_pane_calendar, "calendar", "Calendar", watchlist)
    with c3:
        _af_card(_af_pane_feed, "feed", "Bank News Feed", watchlist)


def _render_above_fold(all_metrics: list[dict], watchlist: list[str]):
    _af_grid(all_metrics, watchlist)


def render_home(all_metrics: list[dict], watchlist: list[str]):
    """Render the home/dashboard page."""

    # Drop the page a touch below the top nav so the title bar + data-source
    # freshness strip ("Live · FDIC …") clear the nav and aren't clipped.
    st.markdown("<div style='height:9px'></div>", unsafe_allow_html=True)

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
