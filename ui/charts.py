"""
Plotly charts for the bank detail page.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import METRICS_BY_KEY


from utils.chart_style import CHART_LAYOUT


def price_readout(df: pd.DataFrame, ticker: str, over_period: bool = True) -> str:
    """The compact 'TICKER $last +pct%' readout (HTML, the move colored). Used as
    the chart's own title AND, when a caller renders its own header bar, as the
    standalone readout beside the timeframe buttons — so both read identically.
    Returns a '— no price data' line when the series is empty (never a fake number)."""
    if df is None or df.empty or "close" not in df.columns:
        return f"{ticker} — no price data"
    y = df.sort_values("date")["close"].astype(float)
    first, last = float(y.iloc[0]), float(y.iloc[-1])
    color = "#059669" if last >= first else "#dc2626"
    pct = ((last - first) / first * 100) if first else 0.0
    suffix = " over period" if over_period else ""
    return (f"{ticker}  ${last:,.2f}  "
            f"<span style='color:{color}'>{pct:+.2f}%{suffix}</span>")


def price_chart(df: pd.DataFrame, ticker: str, show_title: bool = True) -> go.Figure:
    """A proper price chart: candlesticks (when OHLC is available) + a volume
    panel, taller aspect ratio, gridlines. Falls back to a clean line if only
    close prices exist. `show_title=False` drops the in-chart title for callers
    that render the readout themselves (e.g. a header bar with the timeframe
    buttons) — avoids showing it twice."""
    from utils.chart_style import apply_standard_layout
    if df is None or df.empty or "close" not in df.columns:
        fig = go.Figure()
        apply_standard_layout(fig, title=(price_readout(df, ticker) if show_title else ""),
                              height=300, show_legend=False)
        if not show_title:
            fig.update_layout(title_text="")   # else plotly renders "undefined"
        return fig

    d = df.sort_values("date")
    y = d["close"].astype(float)
    first, last = float(y.iloc[0]), float(y.iloc[-1])
    up = last >= first
    color = "#059669" if up else "#dc2626"
    title = price_readout(df, ticker) if show_title else ""

    has_vol = "volume" in d.columns and d["volume"].notna().any()

    if has_vol:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.74, 0.26])
    else:
        fig = make_subplots(rows=1, cols=1)

    # Price: clean area line (green if up over the window, red if down).
    fig.add_trace(go.Scatter(
        x=d["date"], y=y, mode="lines", name=ticker, showlegend=False,
        line=dict(color=color, width=2), fill="tozeroy",
        fillcolor=("rgba(5,150,105,0.07)" if up else "rgba(220,38,38,0.07)"),
        hovertemplate="%{x|%b %d, %Y}<br>$%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    if has_vol:
        ref = d["close"].shift(1).fillna(d["close"])
        vcolors = ["rgba(5,150,105,0.45)" if c >= o else "rgba(220,38,38,0.45)"
                   for o, c in zip(ref, d["close"])]
        fig.add_trace(go.Bar(x=d["date"], y=d["volume"], marker_color=vcolors,
                             name="Volume", showlegend=False), row=2, col=1)
        fig.update_yaxes(showgrid=False, row=2, col=1)

    apply_standard_layout(fig, title=title, height=420, show_legend=False,
                          hovermode="x unified")
    if not show_title:
        # apply_standard_layout leaves an empty title object ({}) for a falsy
        # title, which plotly.js renders as the literal "undefined". Force an
        # empty text so nothing shows.
        fig.update_layout(title_text="")
        # The readout bar above the chart is the heading, so there's no in-chart
        # title — drop the title-sized top margin (t=30) that left a dead white
        # band inside the card. l is just wide enough for the $-axis labels; r=0
        # so the plot reaches the right edge (the box border hugs it, no gutter).
        fig.update_layout(margin=dict(l=34, r=0, t=6, b=22))
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.12)")
    # Zoom the price y-axis to the data range so the move reads clearly.
    ymin, ymax = float(y.min()), float(y.max())
    pad = (ymax - ymin) * 0.08 or max(ymax * 0.01, 0.5)
    fig.update_yaxes(range=[ymin - pad, ymax + pad], tickprefix="$", showgrid=True,
                     gridcolor="rgba(148,163,184,0.12)", row=1, col=1)
    # Collapse non-trading gaps so the line isn't flat across nights/weekends —
    # i.e. show trading sessions back-to-back. Weekends always; overnight too
    # when the data is intraday (median bar < 12h).
    breaks = [dict(bounds=["sat", "mon"])]
    try:
        gaps = d["date"].diff().dropna()
        if not gaps.empty and gaps.median() < pd.Timedelta(hours=12):
            breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    except Exception:
        pass
    fig.update_xaxes(rangebreaks=breaks)
    return fig


def metrics_trend_chart(
    fdic_df: pd.DataFrame,
    metric_keys: list[str],
    title: str = "Key Metrics Over Time",
) -> go.Figure:
    """Plot FDIC metrics over time (quarterly)."""
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_COMPACT, CATEGORICAL_PALETTE)
    if fdic_df.empty:
        fig = go.Figure()
        apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT, show_legend=False)
        return fig

    fig = go.Figure()
    for i, key in enumerate(metric_keys):
        m = METRICS_BY_KEY.get(key)
        if not m:
            continue
        field = m.get("fdic_field")
        if field and field in fdic_df.columns:
            fig.add_trace(go.Scatter(
                x=fdic_df["REPDTE"],
                y=fdic_df[field],
                mode="lines+markers",
                name=m["label"],
                line=dict(color=CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)], width=2),
                marker=dict(size=5),
            ))

    # Single-metric charts don't need a legend.
    apply_standard_layout(fig, title=title, height=CHART_HEIGHT_COMPACT,
                          yaxis_title="%", show_legend=len(fig.data) > 1,
                          hovermode="x unified")
    tighten_yaxis(fig)  # zoom to data so small moves read clearly
    return fig


# Map a metric's `format` to the y-axis family it belongs on. Metrics sharing a
# family stack on one axis; a chart mixing two families gets a secondary axis.
def _axis_family(fmt: str) -> str:
    if fmt in ("billions", "millions"):
        return "$B"      # dollar levels, scaled to $B below
    if fmt == "pct":
        return "%"
    if fmt == "ratio":
        return "x"
    return "#"


def grouped_trend_chart(
    fdic_df: pd.DataFrame,
    metric_keys: list[str],
    title: str = "",
) -> go.Figure:
    """Multi-metric trend chart over time.

    Metrics that share a unit family ($, %, x) plot on one y-axis; a group that
    mixes dollar LEVELS with a RATIO (e.g. Loans & Deposits in $B alongside the
    Loan/Deposit ratio in %) gets a secondary y-axis on the right, each axis
    labelled with its unit. Dollar fields (FDIC $thousands) are scaled to $B so
    a $16B level and a 1.6% ratio are both legible. Keys with no FDIC field, or
    fields absent from this bank's history, are silently skipped — a chart that
    can't source any series renders empty (never fabricated)."""
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_DENSE, CATEGORICAL_PALETTE)
    fig = go.Figure()
    if fdic_df is None or fdic_df.empty:
        apply_standard_layout(fig, title=title, height=CHART_HEIGHT_DENSE,
                              show_legend=False)
        return fig

    # Resolve the plottable series (key has a field present in this history).
    plot = []
    for key in metric_keys:
        m = METRICS_BY_KEY.get(key)
        if not m:
            continue
        field = m.get("fdic_field")
        if field and field in fdic_df.columns:
            plot.append((m, field, _axis_family(m.get("format"))))
    if not plot:
        apply_standard_layout(fig, title=title, height=CHART_HEIGHT_DENSE,
                              show_legend=False)
        return fig

    # First family seen is primary; the first DIFFERING family is secondary.
    families = []
    for _, _, fam in plot:
        if fam not in families:
            families.append(fam)
    primary = families[0]
    secondary = families[1] if len(families) > 1 else None

    primary_vals = []
    for i, (m, field, fam) in enumerate(plot):
        y = fdic_df[field]
        if fam == "$B":
            y = y / 1e6   # FDIC $thousands -> $B
        on_secondary = secondary is not None and fam == secondary
        if not on_secondary:
            primary_vals.extend(v for v in y.tolist() if v is not None)
        fig.add_trace(go.Scatter(
            x=fdic_df["REPDTE"], y=y, mode="lines+markers", name=m["label"],
            line=dict(color=CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)], width=2),
            marker=dict(size=4),
            yaxis="y2" if on_secondary else "y",
        ))

    apply_standard_layout(fig, title=title, height=CHART_HEIGHT_DENSE,
                          yaxis_title=primary, show_legend=len(fig.data) > 1,
                          hovermode="x unified")
    if secondary:
        # Dual-axis: a tight zoom on one axis distorts the other, so leave both
        # on auto-range; label the right axis with its unit.
        fig.update_layout(yaxis2=dict(title=secondary, overlaying="y",
                                      side="right", showgrid=False,
                                      zeroline=False))
    else:
        tighten_yaxis(fig, values=primary_vals or None)
    return fig


def peer_radar_chart(radar_data: dict) -> go.Figure:
    """Create a radar/spider chart comparing banks across metrics."""
    categories = radar_data.get("categories", [])
    series = radar_data.get("series", [])

    if not categories or not series:
        fig = go.Figure()
        fig.update_layout(title="Peer Comparison — No data", **CHART_LAYOUT)
        return fig

    fig = go.Figure()
    from utils.chart_style import CATEGORICAL_PALETTE
    colors = CATEGORICAL_PALETTE

    for i, s in enumerate(series):
        values = s["values"] + [s["values"][0]]  # close the polygon
        cats = categories + [categories[0]]
        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=cats,
            fill="toself",
            name=s["name"],
            line=dict(color=colors[i % len(colors)]),
            opacity=0.6,
        ))

    fig.update_layout(
        title="Peer Comparison (Percentile Rank)",
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            # Light-theme grid — the old white rgba gridlines were invisible
            # on the app's white background.
            radialaxis=dict(visible=True, range=[0, 100],
                            gridcolor="rgba(15, 23, 42, 0.10)"),
            angularaxis=dict(gridcolor="rgba(15, 23, 42, 0.10)"),
        ),
        **CHART_LAYOUT,
    )
    return fig


def _b(v):
    """FDIC $thousands → $billions."""
    try:
        return float(v) / 1e6
    except (TypeError, ValueError):
        return 0.0


def balance_sheet_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Tightened trend LINE of total assets / net loans / total deposits ($B)."""
    from utils.chart_style import apply_standard_layout
    if fdic_df is None or fdic_df.empty:
        fig = go.Figure()
        apply_standard_layout(fig, title="Balance Sheet — no data", height=300, show_legend=False)
        return fig
    d = fdic_df.sort_values("REPDTE")
    fig = go.Figure()
    for field, label, color in [("ASSET", "Total Assets", "#2563eb"),
                                ("LNLSNET", "Net Loans", "#059669"),
                                ("DEP", "Total Deposits", "#d97706")]:
        if field in d.columns:
            fig.add_trace(go.Scatter(
                x=d["REPDTE"], y=d[field] / 1e6, mode="lines+markers", name=label,
                line=dict(color=color, width=2.5), marker=dict(size=4),
                hovertemplate="%{x|%b %Y}<br>$%{y:.2f}B<extra></extra>"))
    from utils.chart_style import CHART_HEIGHT_FULL
    apply_standard_layout(fig, title="Balance Sheet Trend ($B)", height=CHART_HEIGHT_FULL,
                          hovermode="x unified")
    fig.update_yaxes(tickprefix="$", ticksuffix="B")
    return fig


def _donut(labels, values, title, colors):
    """Reusable donut for composition snapshots (values in $B)."""
    pairs = [(l, v, c) for l, v, c in zip(labels, values, colors) if v and v > 0]
    if not pairs:
        fig = go.Figure()
        fig.update_layout(title=f"{title} — no data", height=300, **CHART_LAYOUT)
        return fig
    ls, vs, cs = zip(*pairs)
    fig = go.Figure(go.Pie(
        labels=ls, values=vs, hole=0.58, sort=False,
        domain=dict(x=[0.0, 0.52]),  # pie on the left, legend fills the right
        marker=dict(colors=cs, line=dict(color="#ffffff", width=1)),
        textinfo="percent", textfont_size=11, textposition="inside",
        hovertemplate="%{label}: $%{value:.2f}B (%{percent})<extra></extra>"))
    fig.update_layout(
        title=title, height=270, showlegend=True,
        legend=dict(orientation="v", x=0.54, xanchor="left", y=0.5, yanchor="middle",
                    font=dict(size=9.5)),
        margin=dict(l=6, r=6, t=34, b=6),
        **CHART_LAYOUT)
    return fig


def asset_composition_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Donut of the latest balance sheet's asset mix."""
    if fdic_df is None or fdic_df.empty:
        return _donut([], [], "Asset Composition", [])
    r = fdic_df.sort_values("REPDTE").iloc[-1]
    asset = _b(r.get("ASSET")); loans = _b(r.get("LNLSNET"))
    sec = _b(r.get("SC")); cash = _b(r.get("CHBAL"))
    other = max(0.0, asset - loans - sec - cash)
    return _donut(["Net loans", "Securities", "Cash & balances", "Other"],
                  [loans, sec, cash, other], "Asset Composition (latest)",
                  ["#2563eb", "#059669", "#d97706", "#94a3b8"])


def loan_mix_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Donut of the latest loan portfolio mix."""
    if fdic_df is None or fdic_df.empty:
        return _donut([], [], "Loan Mix", [])
    r = fdic_df.sort_values("REPDTE").iloc[-1]
    resi = _b(r.get("LNRERES"))
    cre = _b(r.get("LNRENRES")) + _b(r.get("LNREMULT"))
    constr = _b(r.get("LNRECONS"))
    ci = _b(r.get("LNCI")); cons = _b(r.get("LNCON")); ag = _b(r.get("LNREAG"))
    total = _b(r.get("LNLSNET"))
    named = resi + cre + constr + ci + cons + ag
    other = max(0.0, total - named)
    return _donut(["1-4 Family residential", "CRE (income property)", "Construction",
                   "C&I", "Consumer", "Ag / other"],
                  [resi, cre, constr, ci, cons, ag + other], "Loan Mix (latest)",
                  ["#2563eb", "#9333ea", "#ea580c", "#059669", "#0891b2", "#94a3b8"])


def funding_mix_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Donut of the latest funding stack (how the bank is funded)."""
    if fdic_df is None or fdic_df.empty:
        return _donut([], [], "Funding Mix", [])
    r = fdic_df.sort_values("REPDTE").iloc[-1]
    nib = _b(r.get("DEPNIDOM")); dep = _b(r.get("DEP"))
    ib = max(0.0, dep - nib)
    equity = _b(r.get("EQTOT")); liab = _b(r.get("LIAB"))
    borrow = max(0.0, liab - dep)
    fig = _donut(["Non-int deposits", "Interest-bearing deposits", "Borrowings / other", "Equity"],
                 [nib, ib, borrow, equity], "Funding Mix (latest)",
                 ["#059669", "#2563eb", "#d97706", "#9333ea"])
    unins = _b(r.get("DEPUNINS"))
    if dep and unins:
        fig.update_layout(title=f"Funding Mix (latest) · {unins/dep*100:.0f}% uninsured")
    return fig


def growth_trend_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Year-over-year growth of assets / loans / deposits (%)."""
    from utils.chart_style import apply_standard_layout
    if fdic_df is None or fdic_df.empty:
        fig = go.Figure()
        apply_standard_layout(fig, title="YoY Growth — no data", height=300, show_legend=False)
        return fig
    d = fdic_df.sort_values("REPDTE")
    fig = go.Figure()
    for field, label, color in [("ASSET", "Assets", "#2563eb"),
                                ("LNLSNET", "Loans", "#059669"),
                                ("DEP", "Deposits", "#d97706")]:
        if field in d.columns and len(d) > 4:
            yoy = d[field].pct_change(4) * 100
            fig.add_trace(go.Scatter(
                x=d["REPDTE"], y=yoy, mode="lines+markers", name=label,
                line=dict(color=color, width=2), marker=dict(size=4),
                hovertemplate="%{x|%b %Y}<br>%{y:+.1f}% YoY<extra></extra>"))
    fig.add_hline(y=0, line_color="#cbd5e1", line_width=1)
    from utils.chart_style import tighten_yaxis, CHART_HEIGHT_COMPACT
    apply_standard_layout(fig, title="YoY Growth (%)", height=CHART_HEIGHT_COMPACT,
                          hovermode="x unified")
    tighten_yaxis(fig, ticksuffix="%")
    return fig


def loans_deposits_chart(fdic_df: pd.DataFrame) -> go.Figure:
    """Loans-to-deposits ratio over time (a funding/liquidity gauge)."""
    from utils.chart_style import apply_standard_layout
    if (fdic_df is None or fdic_df.empty
            or "LNLSNET" not in fdic_df.columns or "DEP" not in fdic_df.columns):
        fig = go.Figure()
        apply_standard_layout(fig, title="Loans / Deposits — no data", height=240, show_legend=False)
        return fig
    d = fdic_df.sort_values("REPDTE")
    ld = (d["LNLSNET"] / d["DEP"] * 100)
    fig = go.Figure(go.Scatter(
        x=d["REPDTE"], y=ld, mode="lines+markers", name="Loans/Deposits",
        line=dict(color="#0891b2", width=2.5), marker=dict(size=4), fill="tozeroy",
        fillcolor="rgba(0,137,123,0.06)",
        hovertemplate="%{x|%b %Y}<br>%{y:.1f}%<extra></extra>"))
    from utils.chart_style import tighten_yaxis, CHART_HEIGHT_COMPACT
    apply_standard_layout(fig, title="Loans / Deposits (%)", height=CHART_HEIGHT_COMPACT,
                          show_legend=False, hovermode="x")
    tighten_yaxis(fig, ld.dropna().tolist(), ticksuffix="%")
    return fig
