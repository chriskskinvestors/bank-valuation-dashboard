"""
Shared chart styling for dashboards — consistent heights, margins, colors, fonts.
Premium dark theme matching the KSK design system.
"""

# Standard chart dimensions
CHART_HEIGHT_FULL = 300     # main charts (bigger, detailed)
CHART_HEIGHT_COMPACT = 260  # secondary charts (bars, bar of one dim)
CHART_HEIGHT_DENSE = 220    # very small inline charts

# Standard margins
CHART_MARGIN = dict(l=50, r=20, t=40, b=40)
CHART_MARGIN_WIDE_LEFT = dict(l=65, r=20, t=40, b=40)

# Design token colors (matched to light-theme styles.py)
_BG_SURFACE = "#ffffff"
_BG_ELEVATED = "#ffffff"
_GRID_COLOR = "rgba(15, 23, 42, 0.05)"
_AXIS_COLOR = "rgba(15, 23, 42, 0.14)"
_TEXT_PRIMARY = "#0f172a"
_TEXT_SECONDARY = "#475569"

# Brand/semantic colors
COLOR_PRIMARY = "#2563eb"          # refined blue
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
    "#2563eb",  # blue
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
    """Apply standard formatting to a plotly figure."""
    margin = CHART_MARGIN_WIDE_LEFT if wide_left_margin else CHART_MARGIN
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="#1a1a1a")) if title else None,
        height=height,
        margin=margin,
        yaxis_title=yaxis_title,
        xaxis_title=xaxis_title,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            font=dict(size=11),
        ) if show_legend else None,
        showlegend=show_legend,
        hovermode=hovermode,
        **CHART_LAYOUT,
    )
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
