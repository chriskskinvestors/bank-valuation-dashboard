"""
Balance sheet screening table — same architecture as the overview table
but pre-configured for balance sheet, deposit, credit, capital, and income metrics.
"""

import pandas as pd
import streamlit as st

from config import METRICS, METRICS_BY_KEY, DEFAULT_BALANCE_SHEET_COLUMNS
from data.bank_mapping import get_name
from utils.formatting import format_value, get_bg_color


def render_bs_column_selector():
    """Column selector for the balance sheet tab. Returns list of selected metric keys."""
    all_keys = [m["key"] for m in METRICS]
    all_labels = {m["key"]: m["label"] for m in METRICS}

    if "bs_selected_columns" not in st.session_state:
        st.session_state.bs_selected_columns = list(DEFAULT_BALANCE_SHEET_COLUMNS)
    else:
        st.session_state.bs_selected_columns = [
            k for k in st.session_state.bs_selected_columns if k in all_keys
        ]

    selected = st.sidebar.multiselect(
        "Balance sheet columns",
        options=all_keys,
        default=st.session_state.bs_selected_columns,
        format_func=lambda k: f"{all_labels.get(k, k)} ({METRICS_BY_KEY[k]['category']})",
        key="bs_col_selector",
    )

    st.session_state.bs_selected_columns = selected
    return selected


def _apply_row_colors_renamed(row, renamed_cols, rename_map, original_cols):
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


def render_balance_sheet_table(metrics_data: list[dict], selected_columns: list[str]):
    """
    Render the balance sheet screening table.
    Same data source as the overview — just different default columns.
    """
    if not metrics_data:
        st.warning("No bank data available.")
        return None

    df = pd.DataFrame(metrics_data)
    df.insert(0, "Bank", df["ticker"].apply(get_name))

    show_cols = ["ticker", "Bank"] + [c for c in selected_columns if c in df.columns]
    display_df = df[show_cols].copy()

    # Build rename map
    rename = {"ticker": "Ticker", "Bank": "Bank"}
    for col in selected_columns:
        m = METRICS_BY_KEY.get(col)
        if m:
            rename[col] = m["label"]

    # Build format dict using renamed labels
    format_dict = {}
    for col in selected_columns:
        if col in METRICS_BY_KEY and col in display_df.columns:
            label = rename.get(col, col)
            m = METRICS_BY_KEY[col]
            format_dict[label] = lambda v, m=m: format_value(v, m["format"], m.get("decimals", 2))

    renamed_df = display_df[show_cols].rename(columns=rename)
    renamed_cols = [rename.get(c, c) for c in selected_columns if c in display_df.columns]

    st.dataframe(
        renamed_df.style.apply(
            lambda row: _apply_row_colors_renamed(row, renamed_cols, rename, selected_columns), axis=1
        ).format(format_dict)
        .set_properties(**{"font-size": "0.85rem"}),
        use_container_width=True,
        height=min(800, 40 + 35 * len(display_df)),
        hide_index=True,
    )

    return df
