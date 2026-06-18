"""
Main screening table with configurable columns and conditional formatting.
"""

import pandas as pd
import streamlit as st

from config import METRICS, METRICS_BY_KEY, METRIC_CATEGORIES, DEFAULT_TABLE_COLUMNS
from data.bank_mapping import get_name
from utils.formatting import format_value, get_bg_color
from ui.chrome import table_export


def render_column_selector():
    """Render column visibility toggles in the sidebar. Returns list of selected metric keys."""
    st.sidebar.markdown("---")
    st.sidebar.subheader("Columns")

    # Initialize from defaults, filtering out any stale keys
    all_keys = [m["key"] for m in METRICS]
    all_labels = {m["key"]: m["label"] for m in METRICS}

    if "selected_columns" not in st.session_state:
        st.session_state.selected_columns = list(DEFAULT_TABLE_COLUMNS)
    else:
        # Remove any keys that no longer exist in the registry
        st.session_state.selected_columns = [
            k for k in st.session_state.selected_columns if k in all_keys
        ]

    selected = st.sidebar.multiselect(
        "Visible columns",
        options=all_keys,
        default=st.session_state.selected_columns,
        format_func=lambda k: f"{all_labels.get(k, k)} ({METRICS_BY_KEY[k]['category']})",
        key="col_selector",
    )

    st.session_state.selected_columns = selected
    return selected


def _apply_row_colors(row, columns):
    """Generate CSS styles for each cell in a row."""
    styles = []
    for col in row.index:
        if col in columns and col in METRICS_BY_KEY:
            bg = get_bg_color(col, row[col])
            if bg:
                styles.append(bg)
            else:
                styles.append("")
        else:
            styles.append("")
    return styles


def _apply_row_colors_renamed(row, renamed_cols, rename_map, original_cols):
    """Generate CSS styles for renamed columns."""
    # Build reverse map: label -> original key
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


def render_overview_table(metrics_data: list[dict], selected_columns: list[str]):
    """
    Render the main bank screening table.

    metrics_data: list of dicts from build_all_bank_metrics()
    selected_columns: list of metric keys to show
    """
    if not metrics_data:
        st.warning("No bank data available. Check your watchlist and data connections.")
        return None

    df = pd.DataFrame(metrics_data)

    # Add bank name column
    df.insert(0, "Bank", df["ticker"].apply(get_name))

    # Build display columns
    show_cols = ["ticker", "Bank"] + [c for c in selected_columns if c in df.columns]
    display_df = df[show_cols].copy()

    # Create formatted version for display
    formatted = display_df.copy()
    for col in selected_columns:
        if col in formatted.columns and col in METRICS_BY_KEY:
            m = METRICS_BY_KEY[col]
            formatted[col] = formatted[col].apply(
                lambda v, m=m: format_value(v, m["format"], m.get("decimals", 2))
            )

    # Rename columns to labels
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

    # Rename columns first, then apply styling
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
    # renamed_df holds the raw numeric values (formatting is Styler-only)
    table_export(renamed_df, "bank_screener", key="exp_bank_screener")

    # Return raw df for detail page navigation
    return df


def render_data_freshness(fdic_ages: dict, sec_ages: dict, ibkr_connected: bool):
    """One compact, left-aligned row of data-provenance freshness badges.

    Was ``st.columns(3)``, which pinned each badge to the left edge of its own
    third of the page — three tiny pills flung across the full width with big
    empty gaps. A single flex row keeps them tight together, which is also how
    the design system treats pill groups.
    """
    badges: list[tuple[str, str]] = []

    # Price source: IBKR when connected (local), else FMP (cloud) — show real status.
    if ibkr_connected:
        badges.append(("IBKR LIVE", "freshness-live"))
    else:
        try:
            from data.fmp_client import _has_key
            fmp_ok = _has_key()
        except Exception:
            fmp_ok = False
        badges.append(("FMP LIVE", "freshness-live") if fmp_ok
                      else ("PRICES OFFLINE", "freshness-stale"))

    # FDIC + SEC fundamentals: average age across the sampled tickers.
    for src, ages_map in (("FDIC", fdic_ages), ("SEC", sec_ages)):
        ages = [a for a in ages_map.values() if a is not None]
        if ages:
            avg_hours = sum(ages) / len(ages) / 3600
            cls = "freshness-live" if avg_hours < 24 else "freshness-cached"
            badges.append((f"{src} {avg_hours:.0f}h ago", cls))
        else:
            badges.append((f"{src} NO DATA", "freshness-stale"))

    spans = "".join(
        f'<span class="freshness-badge {cls}">{label}</span>'
        for label, cls in badges)
    st.markdown(
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
        f'margin:0 0 8px;">{spans}</div>',
        unsafe_allow_html=True,
    )
