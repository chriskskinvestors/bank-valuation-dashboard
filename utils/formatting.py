"""
Number formatting and color coding helpers for the dashboard UI.
"""

import pandas as pd
from config import METRICS_BY_KEY


def fmt_dollars(dollars: float | None, decimals: int = 2) -> str:
    """
    Auto-scale a dollar value to T / B / M / K / $.
    Input: raw dollars (not thousands).
    """
    if dollars is None or (isinstance(dollars, float) and pd.isna(dollars)):
        return "—"
    try:
        v = float(dollars)
    except (TypeError, ValueError):
        return "—"
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1e12:
        return f"{sign}${abs_v/1e12:,.{decimals}f}T"
    elif abs_v >= 1e9:
        return f"{sign}${abs_v/1e9:,.{decimals}f}B"
    elif abs_v >= 1e6:
        return f"{sign}${abs_v/1e6:,.{decimals}f}M"
    elif abs_v >= 1e3:
        return f"{sign}${abs_v/1e3:,.0f}K"
    return f"{sign}${abs_v:,.0f}"


def fmt_dollars_from_thousands(amount_k: float | None, decimals: int = 2) -> str:
    """Format a thousands-of-dollars value with auto T/B/M/K scaling."""
    if amount_k is None:
        return "—"
    try:
        return fmt_dollars(float(amount_k) * 1000, decimals)
    except (TypeError, ValueError):
        return "—"


def format_value(value, fmt: str, decimals: int = 2) -> str:
    """Format a single value according to its format type."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    if fmt == "currency":
        return f"${value:,.{decimals}f}"
    elif fmt == "pct":
        return f"{value:.{decimals}f}%"
    elif fmt == "ratio":
        return f"{value:.{decimals}f}x"
    elif fmt == "millions":
        # Auto-upgrade to B if >= $1B
        if abs(value) >= 1e9:
            return f"${value / 1e9:,.{decimals}f}B"
        return f"${value / 1e6:,.{decimals}f}M"
    elif fmt == "billions":
        # Auto-upgrade to T if >= $1T
        if abs(value) >= 1e12:
            return f"${value / 1e12:,.{decimals}f}T"
        return f"${value / 1e9:,.{decimals}f}B"
    elif fmt == "dollars_auto":
        # Auto-scale dollars: T ≥ $1T, B ≥ $1B, M ≥ $1M, K ≥ $1K
        abs_v = abs(value)
        sign = "-" if value < 0 else ""
        if abs_v >= 1e12:
            return f"{sign}${abs_v / 1e12:,.{decimals}f}T"
        elif abs_v >= 1e9:
            return f"{sign}${abs_v / 1e9:,.{decimals}f}B"
        elif abs_v >= 1e6:
            return f"{sign}${abs_v / 1e6:,.{decimals}f}M"
        elif abs_v >= 1e3:
            return f"{sign}${abs_v / 1e3:,.0f}K"
        else:
            return f"{sign}${abs_v:,.0f}"
    elif fmt == "number":
        if value >= 1e6:
            return f"{value / 1e6:,.1f}M"
        elif value >= 1e3:
            return f"{value / 1e3:,.1f}K"
        return f"{value:,.{decimals}f}"
    return str(value)


def get_color(key: str, value) -> str:
    """
    Return a CSS color for a metric value based on its thresholds.
    Green = good, yellow = warning, red = bad, white = neutral.
    """
    m = METRICS_BY_KEY.get(key)
    if not m or not m.get("color_rule") or value is None:
        return ""

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""

    rule = m["color_rule"]
    thresholds = m.get("thresholds", {})
    good = thresholds.get("good")
    warn = thresholds.get("warn")

    if good is None or warn is None:
        return ""

    # Light theme — emerald / amber / red
    if rule == "higher_better":
        if value >= good:
            return "color: #059669"  # emerald
        elif value >= warn:
            return "color: #d97706"  # amber
        else:
            return "color: #dc2626"  # red
    elif rule == "lower_better":
        if value <= good:
            return "color: #059669"
        elif value <= warn:
            return "color: #d97706"
        else:
            return "color: #dc2626"

    return ""


def get_bg_color(key: str, value) -> str:
    """Return a subtle background color for conditional formatting in tables.
    Tuned for light theme — soft pastel overlays."""
    m = METRICS_BY_KEY.get(key)
    if not m or not m.get("color_rule") or value is None:
        return ""

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""

    rule = m["color_rule"]
    thresholds = m.get("thresholds", {})
    good = thresholds.get("good")
    warn = thresholds.get("warn")

    if good is None or warn is None:
        return ""

    _GOOD = "background-color: #ecfdf5; color: #065f46"
    _WARN = "background-color: #fffbeb; color: #92400e"
    _BAD  = "background-color: #fef2f2; color: #991b1b"

    if rule == "higher_better":
        if value >= good:
            return _GOOD
        elif value >= warn:
            return _WARN
        else:
            return _BAD
    elif rule == "lower_better":
        if value <= good:
            return _GOOD
        elif value <= warn:
            return _WARN
        else:
            return _BAD

    return ""


def style_dataframe(df: pd.DataFrame, columns: list[str]) -> pd.io.formats.style.Styler:
    """
    Apply conditional background coloring to a DataFrame for display in Streamlit.
    """
    def _apply_colors(row):
        styles = []
        for col in row.index:
            if col in columns and col in METRICS_BY_KEY:
                bg = get_bg_color(col, row[col])
                styles.append(bg)
            else:
                styles.append("")
        return styles

    return df.style.apply(_apply_colors, axis=1)


def format_dataframe_display(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """
    Create a display-ready copy of the DataFrame with formatted string values.
    """
    display_df = df.copy()
    for col in columns:
        m = METRICS_BY_KEY.get(col)
        if m:
            display_df[col] = display_df[col].apply(
                lambda v, m=m: format_value(v, m["format"], m.get("decimals", 2))
            )
    # Rename columns to labels
    rename_map = {}
    for col in columns:
        m = METRICS_BY_KEY.get(col)
        if m:
            rename_map[col] = m["label"]
    display_df = display_df.rename(columns=rename_map)
    return display_df
