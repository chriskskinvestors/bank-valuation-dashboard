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

    # ── Macro KPIs (full Treasury curve) — compact strip ────────────────
    try:
        from data.fred_client import latest_value
        ff = latest_value("FEDFUNDS")
        t3m = latest_value("DGS3MO")
        t2 = latest_value("DGS2")
        t5 = latest_value("DGS5")
        t10 = latest_value("DGS10")
        t30 = latest_value("DGS30")
        spread = latest_value("T10Y2Y")
        spread_5y3m = (t5 - t3m) if (t5 is not None and t3m is not None) else None

        def _pill(label, val, is_spread=False):
            if val is None:
                disp, col = "—", "#94a3b8"
            elif is_spread:
                disp = f"{val:+.2f}"
                col = "#dc2626" if val < 0 else "#059669"
            else:
                disp, col = f"{val:.2f}%", "inherit"
            return (
                '<span style="display:inline-flex; flex-direction:column; '
                'padding:2px 11px; border-radius:7px; background:rgba(148,163,184,0.08); '
                'border:1px solid rgba(148,163,184,0.18); line-height:1.2;">'
                f'<span style="font-size:0.56rem; color:#94a3b8; font-weight:600; '
                'letter-spacing:0.04em;">' + label + '</span>'
                f'<span style="font-size:0.84rem; font-weight:700; color:' + col + ';">'
                + disp + '</span></span>'
            )

        yields = [
            _pill("FED FUNDS", ff), _pill("3M", t3m), _pill("2Y", t2),
            _pill("5Y", t5), _pill("10Y", t10), _pill("30Y", t30),
        ]
        spreads = [
            _pill("5Y−3M", spread_5y3m, is_spread=True),
            _pill("10Y−2Y", spread, is_spread=True),
        ]
        row = "display:flex; gap:6px; flex-wrap:wrap;"
        st.markdown(
            f'<div style="{row} margin:2px 0 6px;">' + "".join(yields) + "</div>"
            f'<div style="{row} margin:0 0 8px;">' + "".join(spreads) + "</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # ── Market benchmarks (price strip) ─────────────────────────────────
    try:
        from config import MARKET_BENCHMARKS
        syms = [t for t, _ in MARKET_BENCHMARKS]
        quotes = {}
        try:
            from data.price_cache_store import get_prices as _warm
            quotes = _warm(syms)  # warmed by the refresh-prices job
        except Exception:
            quotes = {}
        missing = [t for t in syms if t not in quotes]
        if missing:
            try:
                from data import fmp_client
                quotes.update(fmp_client.get_quote_batch(missing))
            except Exception:
                pass

        # Clickable: each benchmark is a popover that opens a 1-year price chart.
        from utils.chart_style import apply_standard_layout
        import plotly.graph_objects as go

        cols = st.columns(len(MARKET_BENCHMARKS))
        for col, (t, desc) in zip(cols, MARKET_BENCHMARKS):
            q = quotes.get(t) or {}
            price = q.get("price")
            chg = q.get("change_pct")
            if price is None:
                label = f"{t}  —"
            elif chg is not None:
                label = f"{t}  ${price:,.2f}  {'▲' if chg >= 0 else '▼'}{abs(chg):.1f}%"
            else:
                label = f"{t}  ${price:,.2f}"
            with col:
                with st.popover(label, use_container_width=True):
                    st.markdown(f"**{t}** · {desc}")
                    if price is not None:
                        delta = f"{chg:+.2f}% today" if chg is not None else ""
                        st.caption(f"${price:,.2f}   {delta}")
                    try:
                        from data import fmp_client
                        hist = fmp_client.get_history(t, period="1Y")
                        if hist is not None and not hist.empty and "close" in hist:
                            up = hist["close"].iloc[-1] >= hist["close"].iloc[0]
                            figh = go.Figure(go.Scatter(
                                x=hist["date"], y=hist["close"], mode="lines",
                                line=dict(color="#059669" if up else "#dc2626", width=2),
                            ))
                            apply_standard_layout(figh, title=f"{t} — 1 year",
                                                  height=240, show_legend=False)
                            figh.update_yaxes(tickprefix="$")
                            st.plotly_chart(figh, use_container_width=True,
                                            key=f"bench_{t}")
                        else:
                            st.caption("Chart unavailable.")
                    except Exception:
                        st.caption("Chart unavailable.")
    except Exception:
        pass

    st.markdown("")

    # ── Watchlist summary ──────────────────────────────────────────────
    if all_metrics:
        df = pd.DataFrame(all_metrics)

        def _avg(col):
            return df[col].mean() if col in df.columns and df[col].notna().any() else None

        def _fmt(v, suffix="%", dp=1):
            return f"{v:.{dp}f}{suffix}" if v is not None else "—"

        # Row 1 — profitability & capital
        r1 = st.columns(4)
        r1[0].metric("Avg ROATCE", _fmt(_avg("roatce_blended")))
        r1[1].metric("Avg NIM", _fmt(_avg("nim"), dp=2))
        r1[2].metric("Avg Efficiency", _fmt(_avg("efficiency_ratio")))
        r1[3].metric("Avg CET1", _fmt(_avg("cet1_ratio"), dp=2))

        # Row 2 — returns, credit, valuation, funding risk
        r2 = st.columns(4)
        r2[0].metric("Avg ROAA", _fmt(_avg("roaa"), dp=2))
        r2[1].metric("Avg NPL", _fmt(_avg("npl_ratio"), dp=2))
        r2[2].metric("Avg P/TBV", _fmt(_avg("ptbv_ratio"), suffix="x", dp=2))
        r2[3].metric("Avg Uninsured Dep", _fmt(_avg("uninsured_pct"), dp=0))

    st.markdown("")

    # ── ALERT INBOX ────────────────────────────────────────────────────
    _render_alert_inbox(all_metrics, watchlist)


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

    st.markdown("### 🔔 Alert Inbox")
    st.caption("Prioritized across your watchlist. Click into any row to jump to the bank.")

    # Collect alerts from each source
    earnings_alerts = _collect_earnings_alerts(watchlist)
    dynamics_alerts = _collect_dynamics_alerts(all_metrics)
    insider_alerts = _collect_insider_alerts(watchlist)
    valuation_alerts = _collect_valuation_alerts(all_metrics)

    # Tabs for the 4 alert types
    total_counts = [
        len(earnings_alerts), len(dynamics_alerts),
        len(insider_alerts), len(valuation_alerts),
    ]
    tab1, tab2, tab3, tab4 = st.tabs([
        f"📅 Earnings ({total_counts[0]})",
        f"⚠️ Dynamics Alerts ({total_counts[1]})",
        f"👥 Insider Buys ({total_counts[2]})",
        f"💰 Value Opps ({total_counts[3]})",
    ])

    with tab1:
        _render_earnings_tab(earnings_alerts)
    with tab2:
        _render_dynamics_tab(dynamics_alerts)
    with tab3:
        _render_insider_tab(insider_alerts)
    with tab4:
        _render_valuation_tab(valuation_alerts)


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
