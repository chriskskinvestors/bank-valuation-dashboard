"""
Peer Comparison UI — side-by-side bank comparison with percentile color-coding.

Top-level section in sidebar nav.
"""

import streamlit as st
import pandas as pd

from config import METRICS, METRICS_BY_KEY
from data.bank_mapping import get_name
from analysis.peer_groups import (
    group_banks, asset_size_tier, business_mix_tier,
    compute_peer_percentile,
)
from utils.formatting import format_value


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


def _percentile_color(pct: float | None, higher_better: bool = True) -> str:
    """Color based on percentile rank (0-100)."""
    if pct is None:
        return ""
    # Normalize: if lower-is-better, invert the percentile
    effective = pct if higher_better else (100 - pct)
    # Light-theme percentile color scale — soft tints, dark text
    if effective >= 80:
        return "background-color: #d1fae5; color:#065f46; font-weight:600;"
    elif effective >= 60:
        return "background-color: #ecfdf5; color:#047857;"
    elif effective >= 40:
        return "background-color: #f8fafc; color:#475569;"
    elif effective >= 20:
        return "background-color: #fef2f2; color:#991b1b;"
    else:
        return "background-color: #fee2e2; color:#991b1b; font-weight:600;"


def render_peer_comparison(all_metrics: list[dict], watchlist: list[str], portfolio: list[str]):
    """Render the Peer Comparison top-level page."""
    st.markdown(
        '<div class="dashboard-header">'
        "<h1>Peer Comparison</h1>"
        "<p>Side-by-side comparison with percentile color-coding</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    if not all_metrics:
        st.warning("No bank data loaded. Check your watchlist.")
        return

    # ── Peer group selector ────────────────────────────────────────────
    c1, c2, c3 = st.columns([2, 2, 1])

    with c1:
        group_mode = st.radio(
            "Group by",
            ["Asset Size", "Business Mix", "Manual Selection"],
            horizontal=True,
            key="peer_group_mode",
        )

    # Compute groups
    groups = group_banks(all_metrics)

    selected_peers: list[dict] = []
    peer_label = ""

    if group_mode == "Asset Size":
        with c2:
            tier_options = list(groups["by_size"].keys())
            if tier_options:
                selected_tier = st.selectbox(
                    "Asset-size tier",
                    tier_options,
                    key="peer_size_tier",
                )
                selected_peers = groups["by_size"].get(selected_tier, [])
                peer_label = selected_tier
            else:
                st.info("No asset-size groups computed yet — load more banks.")

    elif group_mode == "Business Mix":
        with c2:
            mix_options = list(groups["by_mix"].keys())
            if mix_options:
                selected_mix = st.selectbox(
                    "Business-mix group",
                    mix_options,
                    key="peer_mix_group",
                )
                selected_peers = groups["by_mix"].get(selected_mix, [])
                peer_label = selected_mix

    else:  # Manual
        with c2:
            available = sorted({m["ticker"] for m in all_metrics})
            default_selection = available[:5] if len(available) >= 5 else available
            picked = st.multiselect(
                "Select banks to compare (2-10)",
                available,
                default=default_selection[:5],
                key="peer_manual_pick",
                max_selections=10,
            )
            selected_peers = [m for m in all_metrics if m.get("ticker") in picked]
            peer_label = f"Manual ({len(picked)} banks)"

    with c3:
        category = st.selectbox(
            "Metric category",
            list(CATEGORY_METRICS.keys()),
            key="peer_category",
        )

    if not selected_peers:
        st.info("No peers selected. Pick a group above.")
        return

    if len(selected_peers) < 2:
        st.warning(f"Only {len(selected_peers)} bank in this group — add more banks to your watchlist for comparison.")

    st.caption(f"**Peer group:** {peer_label} · {len(selected_peers)} banks · Category: **{category}**")

    # ── View tabs ──────────────────────────────────────────────────────
    view_tab, scatter_tab, radar_tab = st.tabs([
        "📊 Metrics Table",
        "📉 Scatter Plots",
        "🕸 Radar Chart",
    ])

    with view_tab:
        _render_metrics_table(selected_peers, category)

    with scatter_tab:
        _render_peer_scatters(selected_peers)

    with radar_tab:
        _render_peer_radar(selected_peers)

    # ── Summary stats ──────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📊 Peer group composition"):
        comp_rows = []
        for m in selected_peers:
            assets = m.get("total_assets")
            if assets and assets < 1e9:
                assets = assets * 1000
            comp_rows.append({
                "Ticker": m["ticker"],
                "Bank": get_name(m["ticker"]),
                "Assets": format_value(assets, "dollars_auto", 2),
                "Size Tier": asset_size_tier(assets) or "—",
                "Business Mix": business_mix_tier(m),
            })
        comp_df = pd.DataFrame(comp_rows)
        st.dataframe(comp_df, use_container_width=True, hide_index=True)


def _render_metrics_table(selected_peers: list[dict], category: str):
    """Extract the existing metrics table into a dedicated function for the tab."""
    metric_keys = CATEGORY_METRICS.get(category, [])

    # Rows: metrics, Columns: banks + statistics
    tickers = [m["ticker"] for m in selected_peers]
    rows = []
    style_map = {}  # (metric_key, ticker) → color

    for mkey in metric_keys:
        m_def = METRICS_BY_KEY.get(mkey)
        if not m_def:
            continue

        # Raw values
        values = [b.get(mkey) for b in selected_peers]
        numeric = [v for v in values if isinstance(v, (int, float)) and v is not None]
        if not numeric:
            continue

        # Peer median (for color-coding)
        peer_median = pd.Series(numeric).median()
        higher_better = m_def.get("color_rule") == "higher_better"
        lower_better = m_def.get("color_rule") == "lower_better"

        row = {"Metric": m_def["label"]}
        fmt = m_def.get("format", "number")
        dec = m_def.get("decimals", 2)

        for t, v in zip(tickers, values):
            row[t] = format_value(v, fmt, dec) if v is not None else "—"
            # Compute percentile vs peer group
            pct = compute_peer_percentile(v, numeric)
            if higher_better:
                style_map[(mkey, t)] = _percentile_color(pct, True)
            elif lower_better:
                style_map[(mkey, t)] = _percentile_color(pct, False)
            else:
                style_map[(mkey, t)] = ""

        # Add peer median column
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

    # ── Legend ─────────────────────────────────────────────────────────
    legend_html = """
    <div style="display:flex; gap:12px; margin-top:8px; flex-wrap:wrap; font-size:0.8rem;">
        <div style="background:#c8e6c9; padding:4px 10px; border-radius:4px; color:#1b5e20;"><b>Best 20%</b></div>
        <div style="background:#e8f5e9; padding:4px 10px; border-radius:4px;">Above median</div>
        <div style="background:#fff3e0; padding:4px 10px; border-radius:4px;">Below median</div>
        <div style="background:#ffebee; padding:4px 10px; border-radius:4px;">Bottom 40%</div>
        <div style="background:#ffcdd2; padding:4px 10px; border-radius:4px; color:#b71c1c;"><b>Worst 20%</b></div>
    </div>
    """
    st.markdown(legend_html, unsafe_allow_html=True)


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
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("Install plotly to view scatter plots.")
        return

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
    try:
        import plotly.graph_objects as go
        from utils.chart_style import apply_standard_layout
    except ImportError:
        return None

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
        textfont=dict(size=10, color="#1a1a1a"),
        marker=dict(
            size=sizes,
            color="#1a73e8",
            opacity=0.65,
            line=dict(color="#0d47a1", width=1),
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
    fig.add_vline(x=x_med, line_color="#666", line_width=1, line_dash="dot")
    fig.add_hline(y=y_med, line_color="#666", line_width=1, line_dash="dot")

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
    ("roatce", "ROATCE", True),              # higher better
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
    try:
        import plotly.graph_objects as go
        from utils.chart_style import apply_standard_layout
    except ImportError:
        st.warning("Install plotly to view radar chart.")
        return

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
    colors = ["#2563eb", "#059669", "#d97706", "#dc2626", "#9333ea"]
    fill_colors = [
        "rgba(37, 99, 235, 0.14)",
        "rgba(5, 150, 105, 0.14)",
        "rgba(217, 119, 6, 0.14)",
        "rgba(220, 38, 38, 0.14)",
        "rgba(147, 51, 234, 0.14)",
    ]

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

    fig.update_layout(
        title=dict(
            text="Peer Radar — Percentile Rank (0–100)",
            font=dict(color="#0f172a", size=13),
        ),
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 100],
                tickfont=dict(size=10, color="#475569"),
                gridcolor="rgba(15,23,42,0.08)",
                linecolor="rgba(15,23,42,0.14)",
            ),
            angularaxis=dict(
                tickfont=dict(size=11, color="#475569"),
                gridcolor="rgba(15,23,42,0.06)",
                linecolor="rgba(15,23,42,0.12)",
            ),
            bgcolor="#f8fafc",
        ),
        height=520,
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.08,
            font=dict(color="#0f172a"),
        ),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="Inter, system-ui, sans-serif", color="#0f172a"),
        showlegend=True,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Each axis = percentile rank of that metric within the full peer group "
        "(higher = better for this metric; lower-is-better metrics inverted). "
        "Bigger polygon = stronger overall."
    )
