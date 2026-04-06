"""
Generic table renderer — works for any tab by accepting a column list.
"""

import pandas as pd
import streamlit as st

from config import METRICS_BY_KEY
from data.bank_mapping import get_name
from utils.formatting import format_value, get_bg_color


def _apply_row_colors(row, renamed_cols, rename_map):
    """Generate CSS styles for renamed columns."""
    reverse = {v: k for k, v in rename_map.items()}
    styles = []
    for col in row.index:
        orig_key = reverse.get(col, col)
        if col in renamed_cols and orig_key in METRICS_BY_KEY:
            bg = get_bg_color(orig_key, row[col])
            styles.append(bg if bg else "")
        else:
            styles.append("")
    return styles


def render_generic_table(
    metrics_data: list[dict],
    columns: list[str],
    table_key: str = "default",
):
    """
    Render a screening table for any tab.

    metrics_data: list of dicts from build_all_bank_metrics()
    columns: list of metric keys to display
    table_key: unique key for Streamlit widget state
    """
    if not metrics_data:
        st.warning("No bank data available. Check your watchlist and data connections.")
        return None

    df = pd.DataFrame(metrics_data)
    df.insert(0, "Bank", df["ticker"].apply(get_name))

    # Filter to only columns that exist in the dataframe
    valid_cols = [c for c in columns if c in df.columns]
    show_cols = ["ticker", "Bank"] + valid_cols
    display_df = df[show_cols].copy()

    # Build rename map
    rename = {"ticker": "Ticker", "Bank": "Bank"}
    for col in valid_cols:
        m = METRICS_BY_KEY.get(col)
        if m:
            rename[col] = m["label"]

    # Build format dict
    format_dict = {}
    for col in valid_cols:
        if col in METRICS_BY_KEY:
            label = rename.get(col, col)
            m = METRICS_BY_KEY[col]
            format_dict[label] = lambda v, m=m: format_value(v, m["format"], m.get("decimals", 2))

    renamed_df = display_df.rename(columns=rename)
    renamed_cols = [rename.get(c, c) for c in valid_cols]

    st.dataframe(
        renamed_df.style.apply(
            lambda row: _apply_row_colors(row, renamed_cols, rename), axis=1
        ).format(format_dict)
        .set_properties(**{"font-size": "0.65rem", "padding": "1px 4px"}),
        use_container_width=True,
        height=min(900, 28 + 24 * len(display_df)),
        hide_index=True,
    )

    return df
