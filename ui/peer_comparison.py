"""
Peer Comparison UI — side-by-side bank comparison with percentile color-coding.

Top-level section in sidebar nav.
"""

import streamlit as st
import pandas as pd

from config import METRICS, METRICS_BY_KEY
from data.bank_mapping import get_name
from analysis.peer_groups import (
    asset_size_tier, business_mix_tier, compute_peer_percentile,
)
from utils.formatting import format_value
from ui.chrome import table_export, title_bar


# Curated metric sets per category (focused on what analysts actually look at).
# All keys validated against METRICS_BY_KEY at module load.
CATEGORY_METRICS = {
    "Valuation": [
        "price", "pe_ratio", "ptbv_ratio", "dividend_yield",
        "fair_ptbv", "ptbv_discount", "market_cap",
    ],
    "Profitability": [
        "roaa", "roatce", "roatce_4q", "nim", "nim_4q",
        "efficiency_ratio", "pretax_roa", "net_op_income_assets",
    ],
    "Credit": [
        "npl_ratio", "npl_cre", "nco_ratio",
        "past_due_30_89", "reserve_coverage_pct", "reserve_to_loans",
        "nco_4q_trend_bps", "credit_alerts_count",
    ],
    "Capital": [
        "cet1_ratio", "cet1_current", "cet1_qoq_pp",
        "total_capital_ratio", "leverage_ratio",
        "equity_to_assets", "tbv_cagr_1y",
        "payout_ratio_4q", "buyback_capacity_usd",
    ],
    "Deposits": [
        "total_deposits", "nonint_dep_pct", "uninsured_pct",
        "core_dep_pct", "brokered_pct",
        "deposit_cycle_beta", "deposit_rolling_beta",
        "dep_qoq_growth", "cod_qoq_bps",
    ],
    "Balance Sheet": [
        "total_assets", "total_loans", "loans_to_deposits",
        "ln_cre_pct", "ln_ci_pct", "ln_resi_pct", "ln_consumer_pct",
        "cre_to_capital", "sec_to_assets_pct", "htm_pct",
    ],
}


# Percentile color scale — single source of truth for BOTH the table cells and
# the legend (they previously diverged: the legend showed an old palette that no
# longer appeared anywhere in the table).
# (min_effective_percentile, label, background, text color, bold)
_PCT_SCALE = [
    (80, "Top 20%", "#d1fae5", "#065f46", True),
    (60, "60–80th", "#ecfdf5", "#047857", False),
    (40, "40–60th", "#f8fafc", "#475569", False),
    (20, "20–40th", "#fef2f2", "#991b1b", False),
    (0,  "Bottom 20%", "#fee2e2", "#991b1b", True),
]


def _percentile_color(pct: float | None, higher_better: bool = True) -> str:
    """Color based on percentile rank (0-100)."""
    if pct is None:
        return ""
    # Normalize: if lower-is-better, invert the percentile
    effective = pct if higher_better else (100 - pct)
    for floor, _label, bg, fg, bold in _PCT_SCALE:
        if effective >= floor:
            weight = " font-weight:600;" if bold else ""
            return f"background-color: {bg}; color:{fg};{weight}"
    return ""


def render_peer_comparison(all_metrics: list[dict]):
    """Render the Peer Comparison top-level page."""
    title_bar("KSK Investors", "Peer Comparison", ids_html="")

    if not all_metrics:
        st.warning("No bank data loaded. Check your watchlist.")
        return

    # ── Scope (the shared Bank-Groups selector) + display picker ───────
    from ui.bank_scope import render_scope_selector

    # Screen → Compare handoff: a "Compare these banks" click on a screen stashes
    # the result set; pre-seed Manual scope with them on arrival, then consume it.
    handoff = st.session_state.pop("_compare_handoff_tickers", None)
    if handoff:
        st.session_state["compare_scope_type"] = "Manual"
        st.session_state["compare_manual"] = list(handoff)

    c1, c2 = st.columns([3, 1])
    with c1:
        cohort, _cohort_tk, peer_label = render_scope_selector(
            all_metrics, key_prefix="compare", include_manual=True)
    with c2:
        category = st.selectbox(
            "Metric category",
            list(CATEGORY_METRICS.keys()),
            key="peer_category",
        )

    if not cohort:
        st.info("Pick a scope above to compare — a saved group, a tier, a state, or a manual set.")
        return

    if len(cohort) < 2:
        st.warning(f"Only {len(cohort)} bank in this scope — widen it to compare.")

    # The side-by-side table puts banks in COLUMNS, so it caps at a readable
    # number; percentiles and the median still resolve against the FULL cohort.
    # Scatter and radar use the whole cohort.
    TABLE_CAP = 12
    cohort_tickers = [m["ticker"] for m in cohort]
    if len(cohort) > TABLE_CAP:
        disp_tickers = st.multiselect(
            f"Banks to tabulate (of {len(cohort)} in scope; percentiles vs the full scope)",
            cohort_tickers, default=cohort_tickers[:TABLE_CAP],
            max_selections=TABLE_CAP, key="compare_display",
        )
        display_peers = [m for m in cohort if m["ticker"] in disp_tickers] or cohort[:TABLE_CAP]
    else:
        display_peers = cohort

    st.caption(f"**Scope:** {peer_label} · {len(cohort)} banks · Category: **{category}**")

    # ── View tabs ──────────────────────────────────────────────────────
    view_tab, scatter_tab, radar_tab = st.tabs([
        "Metrics Table",
        "Scatter Plots",
        "Radar Chart",
    ])

    with view_tab:
        _render_metrics_table(cohort, display_peers, category)

    with scatter_tab:
        _render_peer_scatters(cohort)

    with radar_tab:
        _render_peer_radar(cohort)

    # ── Summary stats ──────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("Peer group composition"):
        comp_rows = []
        for m in cohort:
            # total_assets is always raw dollars (converted at the metrics
            # boundary) — no unit guessing.
            assets = m.get("total_assets")
            comp_rows.append({
                "Ticker": m["ticker"],
                "Bank": get_name(m["ticker"]),
                "Assets": format_value(assets, "dollars_auto", 2),
                "Size Tier": asset_size_tier(assets) or "—",
                "Business Mix": business_mix_tier(m),
            })
        comp_df = pd.DataFrame(comp_rows)
        st.dataframe(comp_df, use_container_width=True, hide_index=True)
        table_export(comp_df, "peer_group_composition",
                     key="exp_peer_group_composition")


def _render_metrics_table(cohort: list[dict], display_peers: list[dict], category: str):
    """Metrics table — banks (the display subset) in columns, metrics in rows.
    Percentile color and the Peer Median resolve against the FULL ``cohort``, so
    a focused display still ranks each bank within its whole peer group."""
    metric_keys = CATEGORY_METRICS.get(category, [])

    tickers = [m["ticker"] for m in display_peers]   # columns
    rows = []
    style_map = {}  # (metric_key, ticker) → color

    for mkey in metric_keys:
        m_def = METRICS_BY_KEY.get(mkey)
        if not m_def:
            continue

        # Percentile basis = the full cohort, not just the displayed banks.
        cohort_values = [b.get(mkey) for b in cohort]
        numeric = [v for v in cohort_values if isinstance(v, (int, float)) and v is not None]
        if not numeric:
            continue

        peer_median = pd.Series(numeric).median()
        higher_better = m_def.get("color_rule") == "higher_better"
        lower_better = m_def.get("color_rule") == "lower_better"

        row = {"Metric": m_def["label"]}
        fmt = m_def.get("format", "number")
        dec = m_def.get("decimals", 2)

        for d in display_peers:
            t = d["ticker"]
            v = d.get(mkey)
            row[t] = format_value(v, fmt, dec) if v is not None else "—"
            pct = compute_peer_percentile(v, numeric)   # vs full cohort
            if higher_better:
                style_map[(mkey, t)] = _percentile_color(pct, True)
            elif lower_better:
                style_map[(mkey, t)] = _percentile_color(pct, False)
            else:
                style_map[(mkey, t)] = ""

        row["Peer Median"] = format_value(peer_median, fmt, dec)

        row["_mkey"] = mkey
        rows.append(row)

    if not rows:
        st.warning(f"No metrics to display for {category}.")
        return

    df = pd.DataFrame(rows)
    display_cols = ["Metric"] + tickers + ["Peer Median"]
    df_display = df[display_cols].copy()

    # Apply styling
    def _style_row(row):
        styles = [""] * len(row)
        # Get the metric key for this row from original df (by index)
        mkey = df["_mkey"].iloc[row.name]
        for i, col in enumerate(row.index):
            if col in tickers:
                styles[i] = style_map.get((mkey, col), "")
        return styles

    styled = df_display.style.apply(_style_row, axis=1).set_properties(
        **{"font-size": "0.80rem", "padding": "4px 8px"}
    )

    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=min(800, 60 + 38 * len(df_display)),
    )
    # Display frame (formatted values) — the raw per-metric values are not
    # kept as a frame here.
    fname = f"peer_metrics_{category.lower().replace(' ', '_')}"
    table_export(df_display, fname, key=f"exp_{fname}")

    # ── Legend — built from the same scale that colors the cells ────────
    chips = "".join(
        f'<div style="background:{bg}; padding:4px 10px; border-radius:4px; '
        f'color:{fg};">{"<b>" + label + "</b>" if bold else label}</div>'
        for _floor, label, bg, fg, bold in _PCT_SCALE
    )
    st.markdown(
        '<div style="display:flex; gap:12px; margin-top:8px; flex-wrap:wrap; '
        f'font-size:0.8rem;">{chips}</div>',
        unsafe_allow_html=True,
    )


# ── Scatter Plots ────────────────────────────────────────────────────

_CURATED_SCATTERS = [
    {"name": "Profitability vs Efficiency",
     "x": "efficiency_ratio", "y": "roatce",
     "x_label": "Efficiency Ratio (%)", "y_label": "ROATCE (%)",
     "x_invert": True,  # lower efficiency = better
     "quadrants": {"TR": "Best-in-class", "TL": "High ROE but inefficient",
                   "BR": "Efficient but low ROE", "BL": "Laggards"}},
    {"name": "Margin vs Capital",
     "x": "cet1_ratio", "y": "nim",
     "x_label": "CET1 Ratio (%)", "y_label": "NIM (%)",
     "quadrants": {"TR": "Strong margin + capital", "TL": "Strong margin, thin capital",
                   "BR": "Over-capitalized, weak margin", "BL": "Weak across both"}},
    {"name": "Valuation vs Profitability",
     "x": "roatce", "y": "ptbv_ratio",
     "x_label": "ROATCE (%)", "y_label": "P/TBV",
     "quadrants": {"TR": "Richly valued, high-ROE", "TL": "Rich multiple, low-ROE",
                   "BR": "Cheap, high-ROE (value)", "BL": "Cheap, low-ROE (value trap)"}},
    {"name": "Growth vs Credit",
     "x": "npl_ratio", "y": "dep_qoq_growth",
     "x_label": "NPL Ratio (%)", "y_label": "Deposit QoQ Growth (%)",
     "x_invert": True,
     "quadrants": {"TR": "Clean growth", "TL": "Deteriorating + growing",
                   "BR": "Shrinking but clean", "BL": "Shrinking + deteriorating"}},
]


def _render_peer_scatters(selected_peers: list[dict]):
    """Render curated preset scatters + custom 2-axis picker."""
    import plotly.graph_objects as go

    if len(selected_peers) < 3:
        st.info("Need at least 3 banks in the peer group for meaningful scatter plots.")
        return

    # ── Curated presets (2x2 grid) ────────────────────────────────────
    st.markdown("##### Curated Presets")
    col_l, col_r = st.columns(2)
    for i, preset in enumerate(_CURATED_SCATTERS):
        container = col_l if i % 2 == 0 else col_r
        with container:
            fig = _build_scatter(selected_peers, preset)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)

    # ── Custom picker ─────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("##### Custom Scatter")

    # Build list of pickable metrics — numeric ones only
    numeric_metrics = [
        (k, METRICS_BY_KEY[k]["label"])
        for k in METRICS_BY_KEY
        if METRICS_BY_KEY[k].get("format") in ("pct", "ratio", "currency", "number", "millions", "billions", "dollars_auto")
    ]
    numeric_metrics.sort(key=lambda x: x[1])
    metric_keys = [k for k, _ in numeric_metrics]
    metric_labels = {k: lbl for k, lbl in numeric_metrics}

    cc1, cc2, cc3 = st.columns(3)
    with cc1:
        default_x = "efficiency_ratio" if "efficiency_ratio" in metric_keys else metric_keys[0]
        x_key = st.selectbox(
            "X axis", metric_keys,
            index=metric_keys.index(default_x) if default_x in metric_keys else 0,
            format_func=lambda k: metric_labels.get(k, k),
            key="custom_scatter_x",
        )
    with cc2:
        default_y = "roatce" if "roatce" in metric_keys else metric_keys[1]
        y_key = st.selectbox(
            "Y axis", metric_keys,
            index=metric_keys.index(default_y) if default_y in metric_keys else 1,
            format_func=lambda k: metric_labels.get(k, k),
            key="custom_scatter_y",
        )
    with cc3:
        size_metric = st.selectbox(
            "Bubble size", ["(uniform)"] + metric_keys,
            index=(metric_keys.index("total_assets") + 1) if "total_assets" in metric_keys else 0,
            format_func=lambda k: metric_labels.get(k, "Uniform") if k != "(uniform)" else "Uniform",
            key="custom_scatter_size",
        )

    custom_preset = {
        "name": f"{metric_labels.get(y_key, y_key)} vs {metric_labels.get(x_key, x_key)}",
        "x": x_key, "y": y_key,
        "x_label": metric_labels.get(x_key, x_key),
        "y_label": metric_labels.get(y_key, y_key),
        "size": size_metric if size_metric != "(uniform)" else None,
    }

    fig = _build_scatter(selected_peers, custom_preset, height=420)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)


def _build_scatter(peers: list[dict], preset: dict, height: int = 320):
    """Build a single scatter plot from a preset config."""
    import plotly.graph_objects as go
    from utils.chart_style import (
        apply_standard_layout, COLOR_PRIMARY, COLOR_NEUTRAL, COLOR_GREY_LIGHT,
    )

    x_key = preset["x"]
    y_key = preset["y"]
    size_key = preset.get("size")

    # Collect data
    points = []
    for p in peers:
        x = p.get(x_key)
        y = p.get(y_key)
        if x is None or y is None:
            continue
        size = p.get(size_key) if size_key else None
        points.append({
            "ticker": p["ticker"],
            "name": get_name(p["ticker"]),
            "x": x, "y": y,
            "size": size,
        })

    if len(points) < 2:
        return None

    x_vals = [pt["x"] for pt in points]
    y_vals = [pt["y"] for pt in points]

    # Size: scale to [12, 40] bubble area
    if size_key and any(pt.get("size") is not None for pt in points):
        raw_sizes = [pt.get("size") or 0 for pt in points]
        max_s = max(abs(s) for s in raw_sizes) or 1
        sizes = [12 + 28 * (abs(s) / max_s) for s in raw_sizes]
    else:
        sizes = [18] * len(points)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_vals, y=y_vals,
        mode="markers+text",
        text=[pt["ticker"] for pt in points],
        textposition="top center",
        textfont=dict(size=10, color=COLOR_NEUTRAL),
        marker=dict(
            size=sizes,
            color=COLOR_PRIMARY,
            opacity=0.65,
            line=dict(color=COLOR_PRIMARY, width=1),
        ),
        customdata=[[pt["name"], pt["x"], pt["y"]] for pt in points],
        hovertemplate=(
            "<b>%{text}</b> — %{customdata[0]}<br>"
            f"{preset['x_label']}: %{{x:.2f}}<br>"
            f"{preset['y_label']}: %{{y:.2f}}<extra></extra>"
        ),
    ))

    # Median crosshairs
    x_med = pd.Series(x_vals).median()
    y_med = pd.Series(y_vals).median()
    fig.add_vline(x=x_med, line_color=COLOR_GREY_LIGHT, line_width=1, line_dash="dot")
    fig.add_hline(y=y_med, line_color=COLOR_GREY_LIGHT, line_width=1, line_dash="dot")

    # Invert x-axis if lower-is-better
    apply_standard_layout(
        fig, title=preset["name"],
        height=height,
        xaxis_title=preset["x_label"], yaxis_title=preset["y_label"],
        show_legend=False, hovermode="closest",
    )
    if preset.get("x_invert"):
        fig.update_xaxes(autorange="reversed")

    return fig


# ── Radar Chart ──────────────────────────────────────────────────────

_RADAR_METRICS = [
    # Normalized ROATCE (one-time spikes winsorized) for a fair peer comparison
    # — the single-quarter "roatce" can spike (e.g. CARE's loan-recovery quarter
    # → ~71%) and distort the radar.
    ("roatce_normalized", "ROATCE", True),   # higher better
    ("nim", "NIM", True),
    ("efficiency_ratio", "Efficiency", False),  # lower better — will invert
    ("cet1_ratio", "CET1", True),
    ("npl_ratio", "NPL", False),              # lower better
    ("nonint_dep_pct", "Non-Int Dep %", True),
    ("tbv_cagr_1y", "TBV CAGR", True),
    ("ptbv_ratio", "P/TBV", False),           # lower better (cheaper)
]


def _render_peer_radar(selected_peers: list[dict]):
    """Render a radar chart comparing banks on 8 key metrics by percentile rank."""
    import plotly.graph_objects as go
    from utils.chart_style import (
        apply_standard_layout, CATEGORICAL_PALETTE, CHART_HEIGHT_FULL,
        COLOR_NEUTRAL, _GRID_COLOR, _AXIS_COLOR, _BG_SURFACE,
    )

    if len(selected_peers) < 2:
        st.info("Need at least 2 banks for radar comparison.")
        return

    # Bank picker — up to 5 banks overlay
    tickers = [p["ticker"] for p in selected_peers]
    default = tickers[:min(4, len(tickers))]
    picked = st.multiselect(
        "Banks to overlay (up to 5)",
        tickers,
        default=default,
        max_selections=5,
        key="radar_picker",
    )

    if not picked:
        st.info("Pick at least 1 bank above to render the radar.")
        return

    # Compute percentile for each metric across the entire peer group
    # (always use full peer group for percentile, even if user picked subset)
    metric_data = []
    for mkey, label, higher_better in _RADAR_METRICS:
        values = [p.get(mkey) for p in selected_peers]
        numeric = [v for v in values if isinstance(v, (int, float)) and v is not None]
        if not numeric:
            continue
        metric_data.append({
            "key": mkey, "label": label, "higher_better": higher_better,
            "values": values, "numeric": numeric,
        })

    if len(metric_data) < 3:
        st.info("Not enough metrics with data for a meaningful radar.")
        return

    categories = [m["label"] for m in metric_data]

    fig = go.Figure()
    # Series colors come from the shared categorical palette; the translucent
    # fill is derived from each palette hex (no ad-hoc series hexes).
    colors = CATEGORICAL_PALETTE
    def _fill(hex_color: str, alpha: float = 0.14) -> str:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, {alpha})"
    fill_colors = [_fill(c) for c in colors]

    for i, ticker in enumerate(picked):
        bank = next((p for p in selected_peers if p["ticker"] == ticker), None)
        if not bank:
            continue

        # Compute percentile rank for each metric (0-100)
        r_values = []
        for m in metric_data:
            val = bank.get(m["key"])
            if val is None:
                r_values.append(0)
                continue
            numeric = m["numeric"]
            below = sum(1 for v in numeric if v < val)
            pct = (below / len(numeric)) * 100 if numeric else 0
            # Invert if lower-is-better
            effective_pct = pct if m["higher_better"] else (100 - pct)
            r_values.append(effective_pct)

        # Close the polygon
        r_closed = r_values + [r_values[0]]
        cats_closed = categories + [categories[0]]

        color = colors[i % len(colors)]
        fill = fill_colors[i % len(fill_colors)]
        fig.add_trace(go.Scatterpolar(
            r=r_closed, theta=cats_closed,
            fill="toself",
            fillcolor=fill,
            name=ticker,
            line=dict(color=color, width=2),
            opacity=0.9,
        ))

    # Standard chrome (bg / font / legend-below / tight margins / title);
    # the polar block (which apply_standard_layout doesn't model) is layered
    # on after, using the shared chart tokens.
    apply_standard_layout(
        fig, title="Peer Radar — Percentile Rank (0–100)",
        height=CHART_HEIGHT_FULL, show_legend=True, hovermode="closest",
    )
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 100],
                tickfont=dict(size=10, color=COLOR_NEUTRAL),
                gridcolor=_GRID_COLOR,
                linecolor=_AXIS_COLOR,
            ),
            angularaxis=dict(
                tickfont=dict(size=11, color=COLOR_NEUTRAL),
                gridcolor=_GRID_COLOR,
                linecolor=_AXIS_COLOR,
            ),
            bgcolor=_BG_SURFACE,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Each axis = percentile rank of that metric within the full peer group "
        "(higher = better for this metric; lower-is-better metrics inverted). "
        "Bigger polygon = stronger overall."
    )
