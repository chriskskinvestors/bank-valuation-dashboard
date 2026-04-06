"""
Number formatting and color coding helpers for the dashboard UI.
"""

import pandas as pd
from config import METRICS_BY_KEY


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
        return f"${value / 1e6:,.{decimals}f}M"
    elif fmt == "billions":
        return f"${value / 1e9:,.{decimals}f}B"
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

    if rule == "higher_better":
        if value >= good:
            return "color: #00c853"  # green
        elif value >= warn:
            return "color: #ffd600"  # yellow
        else:
            return "color: #ff1744"  # red
    elif rule == "lower_better":
        if value <= good:
            return "color: #00c853"
        elif value <= warn:
            return "color: #ffd600"
        else:
            return "color: #ff1744"

    return ""


def get_bg_color(key: str, value) -> str:
    """Return a subtle background color for conditional formatting in tables."""
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

    if rule == "higher_better":
        if value >= good:
            return "background-color: #e8f5e9; color: #1b5e20"
        elif value >= warn:
            return "background-color: #fff8e1; color: #e65100"
        else:
            return "background-color: #ffebee; color: #b71c1c"
    elif rule == "lower_better":
        if value <= good:
            return "background-color: #e8f5e9; color: #1b5e20"
        elif value <= warn:
            return "background-color: #fff8e1; color: #e65100"
        else:
            return "background-color: #ffebee; color: #b71c1c"

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
