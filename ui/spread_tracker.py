"""
Spread tracker — the interactive chart component under the Recent Deals
board (owner 2026-10-07). Reads ONLY data/deal_spreads.SPREADS_KEY (built by
the refresh-deal-comps job); never fetches.

  1. Overlay: every selected pending deal's daily spread since announcement
     (gross or annualized), one line per deal, end-of-line labels, milestone
     markers, range buttons. Deal picker: all, one or a few.
  2. Drill-down: one deal — summary pills (current spread, 1W / 1M change,
     widest / tightest since announce, days to close) over a two-panel
     chart: acquirer, target and implied offer rebased to 100 at
     announcement, and the gross spread as a filled area with milestone and
     expected-close markers.
"""
from __future__ import annotations

import html as _h
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from ui.components import pill_row, section_header, stat_pill
from utils.chart_style import (CATEGORICAL_PALETTE, COLOR_DANGER, COLOR_GREY_LIGHT,
                               COLOR_NEUTRAL, COLOR_PRIMARY, COLOR_SUCCESS,
                               apply_standard_layout)

_METRICS = {"Gross spread": "gross", "Annualized spread": "annualized"}
# A deal days from its stated close annualizes to a meaningless spike; the
# overlay clips the annualized axis to this band (points beyond it stay in
# the hover data and in the drill-down's gross panel).
_ANNUALIZED_AXIS_CAP = 0.60

_END_LABELS_MAX = 4
_FEW_POINTS = 15             # below this many days, draw markers too

_RANGE_BUTTONS = [dict(count=1, label="1M", step="month", stepmode="backward"),
                  dict(count=3, label="3M", step="month", stepmode="backward"),
                  dict(count=6, label="6M", step="month", stepmode="backward"),
                  dict(step="all", label="All")]


def _pct(x, places=1):
    return "—" if x is None else f"{x * 100:.{places}f}%"


def _signed_pts(x):
    """Spread change in percentage points, signed."""
    return "—" if x is None else f"{x * 100:+.2f} pts"


def _color(i: int) -> str:
    return CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)]


def _value_at_or_before(series: list[dict], iso: str, field: str = "gross"):
    best = None
    for p in series:
        if p["date"] <= iso and p.get(field) is not None:
            best = p[field]
    return best


def summary(deal: dict, today: date | None = None) -> dict:
    """Pure: current / 1W / 1M change / widest / tightest / days for one deal."""
    s = deal["series"]
    last = s[-1]
    today = today or date.fromisoformat(last["date"])
    wk = _value_at_or_before(s, (today - timedelta(days=7)).isoformat())
    mo = _value_at_or_before(s, (today - timedelta(days=30)).isoformat())
    widest = max(s, key=lambda p: p["gross"])
    tightest = min(s, key=lambda p: p["gross"])
    return {"gross": last["gross"], "annualized": last.get("annualized"),
            "as_of": last["date"], "days": last.get("days"),
            "chg_1w": (last["gross"] - wk) if wk is not None else None,
            "chg_1m": (last["gross"] - mo) if mo is not None else None,
            "widest": widest, "tightest": tightest}


def overlay_figure(deals: dict, keys: list[str], metric: str) -> go.Figure:
    fig = go.Figure()
    for i, k in enumerate(keys):
        d = deals[k]
        col = _color(i)
        pts = [p for p in d["series"] if p.get(metric) is not None]
        if not pts:
            continue
        x = [p["date"] for p in pts]
        y = [p[metric] for p in pts]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines", name=d["label"], line=dict(color=col, width=2),
            customdata=[[p["offer"], p["tgt"], p["acq"]] for p in pts],
            hovertemplate=(f"<b>{_h.escape(d['label'])}</b>  %{{y:.2%}}<br>"
                           "offer $%{customdata[0]:.2f} · target $%{customdata[1]:.2f}"
                           "<extra></extra>")))
        # Milestones sit ON the line (diamond markers).
        ms = [m for m in d.get("milestones") or [] if m["date"] >= x[0]]
        if ms:
            fig.add_trace(go.Scatter(
                x=[m["date"] for m in ms],
                y=[_value_at_or_before(pts, m["date"], metric) for m in ms],
                mode="markers", showlegend=False,
                marker=dict(symbol="diamond", size=9, color=col,
                            line=dict(color="white", width=1.5)),
                text=[m["label"] for m in ms],
                hovertemplate=(f"<b>{_h.escape(d['label'])}</b><br>%{{text}} "
                               "%{x|%b %d, %Y}<extra></extra>")))
        # A dot on each deal's latest point (a one-day-old deal is a point,
        # not a line, and would otherwise be invisible).
        fig.add_trace(go.Scatter(
            x=[x[-1]], y=[y[-1]], mode="markers", showlegend=False,
            marker=dict(size=7, color=col, line=dict(color="white", width=1.5)),
            hovertemplate=(f"<b>{_h.escape(d['label'])}</b> latest %{{y:.2%}}"
                           "<extra></extra>")))
        # End-of-line label (only for a few deals — eight labels collide).
        if len(keys) <= _END_LABELS_MAX:
          fig.add_annotation(x=x[-1], y=y[-1], text=f"{d['target_ticker']} {_pct(y[-1])}",
                           showarrow=False, xanchor="left", xshift=6,
                           font=dict(size=10, color=col))
    fig.add_hline(y=0, line=dict(color=COLOR_GREY_LIGHT, width=1, dash="dot"))
    apply_standard_layout(fig, height=420, show_legend=True, hovermode="x unified")
    fig.update_yaxes(tickformat=".1%", zeroline=False)
    if metric == "annualized":
        ys = [p["annualized"] for k in keys for p in deals[k]["series"]
              if p.get("annualized") is not None]
        if ys and (max(ys) > _ANNUALIZED_AXIS_CAP or min(ys) < -_ANNUALIZED_AXIS_CAP):
            fig.update_yaxes(range=[max(min(ys), -_ANNUALIZED_AXIS_CAP) * 1.05,
                                    min(max(ys), _ANNUALIZED_AXIS_CAP) * 1.05])
    fig.update_xaxes(rangeselector=dict(buttons=_RANGE_BUTTONS, x=0, y=1.08,
                                        bgcolor="rgba(30,64,175,0.06)",
                                        activecolor="rgba(30,64,175,0.22)",
                                        font=dict(size=10)))
    fig.update_layout(margin=dict(r=70))
    return fig


def detail_figure(deal: dict) -> go.Figure:
    s = deal["series"]
    x = [p["date"] for p in s]
    a0, t0 = s[0]["acq"], s[0]["tgt"]
    mode = "lines+markers" if len(s) < _FEW_POINTS else "lines"
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=[0.56, 0.44],
                        subplot_titles=("Prices rebased to 100 at announcement",
                                        "Gross spread"))
    fig.add_trace(go.Scatter(x=x, y=[p["tgt"] / t0 * 100 for p in s], mode=mode,
                             name=f"{deal['target_ticker']} (target)",
                             line=dict(color=COLOR_PRIMARY, width=2),
                             customdata=[p["tgt"] for p in s],
                             hovertemplate="target %{y:.1f}  ($%{customdata:.2f})<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=[p["offer"] / t0 * 100 for p in s], mode=mode,
                             name="Implied offer", line=dict(color=COLOR_SUCCESS, width=2, dash="dash"),
                             customdata=[p["offer"] for p in s],
                             hovertemplate="offer %{y:.1f}  ($%{customdata:.2f})<extra></extra>"),
                  row=1, col=1)
    if a0:
      fig.add_trace(go.Scatter(x=x, y=[p["acq"] / a0 * 100 for p in s], mode=mode,
                             name=f"{deal['buyer_ticker']} (acquirer)",
                             line=dict(color=COLOR_NEUTRAL, width=1.5),
                             customdata=[p["acq"] for p in s],
                             hovertemplate="acquirer %{y:.1f}  ($%{customdata:.2f})<extra></extra>"),
                  row=1, col=1)
    # Spread as a filled area, green above zero, red below.
    g = [p["gross"] for p in s]
    fig.add_trace(go.Scatter(x=x, y=[max(v, 0) for v in g], mode="lines", fill="tozeroy",
                             line=dict(color=COLOR_SUCCESS, width=0),
                             fillcolor="rgba(5,150,105,0.18)", showlegend=False,
                             hoverinfo="skip"), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=[min(v, 0) for v in g], mode="lines", fill="tozeroy",
                             line=dict(color=COLOR_DANGER, width=0),
                             fillcolor="rgba(220,38,38,0.18)", showlegend=False,
                             hoverinfo="skip"), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=g, mode=mode, name="Gross spread",
                             line=dict(color="#0f172a", width=1.6),
                             hovertemplate="spread %{y:.2%}<extra></extra>"), row=2, col=1)
    for m in deal.get("milestones") or []:
        if x[0] <= m["date"] <= x[-1]:
            fig.add_vline(x=m["date"], line=dict(color=COLOR_PRIMARY, width=1, dash="dot"))
            fig.add_annotation(x=m["date"], y=1, yref="paper", text=m["label"],
                               showarrow=False, yanchor="bottom", font=dict(size=9,
                               color=COLOR_PRIMARY), textangle=0)
    apply_standard_layout(fig, height=520, show_legend=True, hovermode="x unified")
    fig.update_yaxes(tickformat=".1%", row=2, col=1)
    fig.update_xaxes(type="date", tickformat="%b %d")
    if len(s) < _FEW_POINTS:                  # a few days: give them room
        lo = date.fromisoformat(x[0]) - timedelta(days=3)
        hi = date.fromisoformat(x[-1]) + timedelta(days=3)
        fig.update_xaxes(range=[lo.isoformat(), hi.isoformat()])
    fig.update_xaxes(rangeselector=dict(buttons=_RANGE_BUTTONS, x=0, y=1.1,
                                        bgcolor="rgba(30,64,175,0.06)",
                                        activecolor="rgba(30,64,175,0.22)",
                                        font=dict(size=10)), row=1, col=1)
    for ann in fig.layout.annotations[:2]:          # subplot titles: small, left
        ann.update(font=dict(size=11, color=COLOR_NEUTRAL), x=0, xanchor="left")
    return fig


def render_spread_tracker():
    from data.deal_spreads import get_spread_histories
    data = get_spread_histories()
    section_header("", "Spread tracker",
                   _h.escape(f"daily since announcement · built "
                             f"{str((data or {}).get('built_at', ''))[:16].replace('T', ' ')}")
                   if data else "")
    if not data or not data["deals"]:
        st.info("Spread history has not been built yet — the refresh-deal-comps "
                "job builds it after each pass.")
        return
    deals = data["deals"]
    # Widest current spread first: the deals an arb desk looks at first.
    keys = sorted(deals, key=lambda k: -deals[k]["series"][-1]["gross"])

    c1, c2 = st.columns([3, 1])
    with c1:
        picked = st.multiselect("Deals", options=keys, default=keys,
                                format_func=lambda k: deals[k]["label"],
                                key="spread_deals",
                                placeholder="Pick one or more deals")
    with c2:
        metric_label = st.segmented_control("Metric", list(_METRICS),
                                            default="Gross spread",
                                            key="spread_metric") or "Gross spread"
    metric = _METRICS[metric_label]
    if not picked:
        st.caption("Pick at least one deal to chart.")
    else:
        st.plotly_chart(overlay_figure(deals, picked, metric),
                        use_container_width=True, key="spread_overlay")
        if metric == "annualized":
            st.caption("Annualized = gross × 365 ÷ days to the STATED expected-close "
                       f"period end; the axis is clipped to ±{_ANNUALIZED_AXIS_CAP:.0%} "
                       "because a deal days from its close annualizes to a spike "
                       "(exact values in the hover).")

    # ── Drill-down ──
    pool = picked or keys
    # Open on the deal with the longest history (a one-day-old deal makes
    # an empty first impression).
    longest = max(range(len(pool)), key=lambda i: len(deals[pool[i]]["series"]))
    focus = st.selectbox("Deal detail", options=pool, index=longest,
                         format_func=lambda k: f"{deals[k]['label']} — "
                                               f"{deals[k].get('target_name') or ''}",
                         key="spread_focus")
    d = deals[focus]
    sm = summary(d)
    w, t = sm["widest"], sm["tightest"]
    pill_row([
        stat_pill("GROSS SPREAD", _pct(sm["gross"], 2), accent="brand",
                  foot_html=f"as of {sm['as_of']}"),
        stat_pill("ANNUALIZED", _pct(sm["annualized"], 1),
                  foot_html=(f"{sm['days']} days to stated close" if sm["days"] else "no stated close")),
        stat_pill("1W CHANGE", _signed_pts(sm["chg_1w"])),
        stat_pill("1M CHANGE", _signed_pts(sm["chg_1m"])),
        stat_pill("WIDEST", _pct(w["gross"], 2), foot_html=w["date"]),
        stat_pill("TIGHTEST", _pct(t["gross"], 2), foot_html=t["date"]),
    ], margin="4px 0 8px")
    st.plotly_chart(detail_figure(d), use_container_width=True, key="spread_detail")

    terms = []
    if d.get("exchange_ratio"):
        terms.append(f"{d['exchange_ratio']:g} {d['buyer_ticker']} shares")
    if d.get("cash_per_share"):
        terms.append(f"${d['cash_per_share']:,.2f} cash")
    st.caption(
        f"{_h.escape(d.get('target_name') or d['target_ticker'])} ← "
        f"{_h.escape(d.get('buyer_name') or d['buyer_ticker'])} · announced "
        f"{d['announce_date']} · {(d.get('consideration') or 'n/a')}: "
        f"{' + '.join(terms) or 'n/a'} per share · stated close "
        f"{d.get('expected_close_date') or 'not stated'}"
        + (f" · {d['dropped']} bad-print day(s) excluded" if d.get("dropped") else "")
        + ". Each day recomputed from that day's closing prices and the deal's fixed "
          "terms with the board's own formula: implied offer ÷ target close − 1.")

    if data.get("skipped"):
        with st.expander(f"Not charted ({len(data['skipped'])})"):
            for s in data["skipped"]:
                st.markdown(f"- **{_h.escape(s['label'])}** "
                            f"{_h.escape(s.get('target_name') or '')} — {_h.escape(s['reason'])}")
