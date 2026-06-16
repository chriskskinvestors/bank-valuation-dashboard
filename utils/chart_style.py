"""
Shared chart styling for dashboards — consistent heights, margins, colors, fonts.
Premium dark theme matching the KSK design system.
"""

# Standard chart dimensions — institutional density: small, high signal-per-pixel.
CHART_HEIGHT_HERO = 360     # focal single chart shown in a narrow column (squarer aspect)
CHART_HEIGHT_FULL = 230     # main charts
CHART_HEIGHT_COMPACT = 180  # secondary charts (bars, single dimension)
CHART_HEIGHT_DENSE = 150    # small inline / grid charts

# Standard margins — tight. Top leaves room for the title; the legend sits
# *below* the plot (never overlapping the title), reserving extra bottom.
CHART_MARGIN = dict(l=42, r=12, t=30, b=28)
CHART_MARGIN_WIDE_LEFT = dict(l=54, r=12, t=30, b=28)
_LEGEND_BOTTOM_PAD = 22

# Design token colors (matched to light-theme styles.py)
_BG_SURFACE = "#ffffff"
_BG_ELEVATED = "#ffffff"
_GRID_COLOR = "rgba(15, 23, 42, 0.05)"
_AXIS_COLOR = "rgba(15, 23, 42, 0.14)"
_TEXT_PRIMARY = "#0f172a"
_TEXT_SECONDARY = "#475569"

# Brand/semantic colors
COLOR_PRIMARY = "#1e40af"          # institutional navy (DESIGN-SYSTEM.md)
COLOR_PRIMARY_LIGHT = "#3b82f6"    # accent blue
COLOR_SUCCESS = "#059669"          # emerald
COLOR_SUCCESS_LIGHT = "#10b981"
COLOR_WARNING = "#d97706"          # amber
COLOR_DANGER = "#dc2626"           # red
COLOR_NEUTRAL = "#475569"
COLOR_GREY_LIGHT = "#94a3b8"

# Fill colors (for area/fill charts) — slightly heavier alpha for light bg
COLOR_FILL_SUCCESS = "rgba(5, 150, 105, 0.10)"
COLOR_FILL_PRIMARY = "rgba(37, 99, 235, 0.10)"
COLOR_FILL_DANGER = "rgba(220, 38, 38, 0.10)"

# Categorical palette — refined for light mode (deeper tones)
CATEGORICAL_PALETTE = [
    "#1e40af",  # navy (brand primary)
    "#059669",  # emerald
    "#d97706",  # amber
    "#dc2626",  # red
    "#9333ea",  # purple
    "#0891b2",  # cyan
    "#ea580c",  # orange
    "#db2777",  # pink
]

# Standard layout kwargs
CHART_LAYOUT = dict(
    plot_bgcolor=_BG_SURFACE,
    paper_bgcolor=_BG_SURFACE,
    font=dict(
        family="Inter, -apple-system, system-ui, sans-serif",
        size=12,
        color=_TEXT_PRIMARY,
    ),
    hoverlabel=dict(
        font_size=11,
        font_family="Inter, system-ui, sans-serif",
        bgcolor="#ffffff",
        bordercolor=_AXIS_COLOR,
        font=dict(color=_TEXT_PRIMARY),
    ),
    colorway=CATEGORICAL_PALETTE,
    xaxis=dict(
        gridcolor=_GRID_COLOR,
        linecolor=_AXIS_COLOR,
        zerolinecolor=_AXIS_COLOR,
        tickfont=dict(color=_TEXT_SECONDARY, size=11),
        title_font=dict(color=_TEXT_SECONDARY, size=11),
    ),
    yaxis=dict(
        gridcolor=_GRID_COLOR,
        linecolor=_AXIS_COLOR,
        zerolinecolor=_AXIS_COLOR,
        tickfont=dict(color=_TEXT_SECONDARY, size=11),
        title_font=dict(color=_TEXT_SECONDARY, size=11),
    ),
)


def apply_standard_layout(fig, title: str = None, height: int = CHART_HEIGHT_FULL,
                          yaxis_title: str = None, xaxis_title: str = None,
                          show_legend: bool = True, hovermode: str = "x unified",
                          wide_left_margin: bool = False):
    """Apply standard formatting to a plotly figure.

    The legend, when shown, is anchored *below* the plot area so it can never
    collide with the title (a recurring layout bug). The bottom margin grows to
    reserve room for it.
    """
    margin = dict(CHART_MARGIN_WIDE_LEFT if wide_left_margin else CHART_MARGIN)
    if show_legend:
        margin = dict(margin, b=margin["b"] + _LEGEND_BOTTOM_PAD)
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=_TEXT_PRIMARY),
                   x=0, xanchor="left", y=0.98, yanchor="top") if title else None,
        height=height,
        margin=margin,
        yaxis_title=yaxis_title,
        xaxis_title=xaxis_title,
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.18,
            xanchor="center", x=0.5,
            font=dict(size=11),
        ) if show_legend else None,
        showlegend=show_legend,
        hovermode=hovermode,
        **CHART_LAYOUT,
    )
    return fig


def tighten_yaxis(fig, values=None, *, pad_frac: float = 0.14, floor_zero: bool = False,
                  min_pad: float = 0.05, ticksuffix: str = None, tickprefix: str = None):
    """Zoom the y-axis to the data range so small moves read clearly.

    The single standard for trend legibility — replaces the ad-hoc range math
    that was duplicated across chart functions. If ``values`` is omitted the
    range is taken from every trace's y data on the figure.

    pad_frac   fraction of the data span added above and below as breathing room.
    floor_zero clamp the lower bound at 0 (for ratios that shouldn't go negative).
    min_pad    minimum padding when the span is ~flat, so a flat line isn't a
               hairline against the axis.
    """
    if values is None:
        values = [v for tr in fig.data for v in (tr.y if tr.y is not None else [])]
    finite = [float(v) for v in values if v is not None and v == v]
    if not finite:
        return fig
    lo, hi = min(finite), max(finite)
    pad = (hi - lo) * pad_frac or max(abs(hi) * 0.05, min_pad)
    low = max(0.0, lo - pad) if floor_zero else lo - pad
    upd = dict(range=[low, hi + pad])
    if ticksuffix is not None:
        upd["ticksuffix"] = ticksuffix
    if tickprefix is not None:
        upd["tickprefix"] = tickprefix
    fig.update_yaxes(**upd)
    return fig


# Alert row styles — light theme with semantic color accents
ALERT_STYLE = {
    "high": (
        "background: rgba(220, 38, 38, 0.06); "
        "color: #991b1b; "
        "border: 1px solid rgba(220, 38, 38, 0.22); "
        "border-left: 3px solid #dc2626; "
        "padding: 9px 14px; margin: 4px 0; border-radius: 6px; "
        "font-size: 0.88rem; line-height: 1.45;"
    ),
    "medium": (
        "background: rgba(217, 119, 6, 0.06); "
        "color: #92400e; "
        "border: 1px solid rgba(217, 119, 6, 0.22); "
        "border-left: 3px solid #d97706; "
        "padding: 9px 14px; margin: 4px 0; border-radius: 6px; "
        "font-size: 0.88rem; line-height: 1.45;"
    ),
    "ok": (
        "background: rgba(5, 150, 105, 0.06); "
        "color: #065f46; "
        "border: 1px solid rgba(5, 150, 105, 0.22); "
        "border-left: 3px solid #059669; "
        "padding: 9px 14px; margin: 4px 0; border-radius: 6px; "
        "font-size: 0.88rem; line-height: 1.45;"
    ),
}
