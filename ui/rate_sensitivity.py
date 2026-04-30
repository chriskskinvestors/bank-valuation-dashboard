"""
Rate Sensitivity UI — NIM scenario analysis per bank.

Primary view: 3M × 5Y curve shifts (bank-appropriate — short end drives
funding costs, 5Y drives asset yields).

Three tabs:
  1. Named Curve Scenarios — preset steepener/flattener/parallel combos
  2. 2D Curve Matrix — heat-map of ΔNIM across Δ3M × Δ5Y grid
  3. Parallel Shift (legacy) — simple +/- rate scenarios
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert
from data.cache import get as cache_get, put as cache_put
from data import fdic_client
from analysis.rate_sensitivity import (
    run_rate_sensitivity, run_curve_sensitivity, run_curve_matrix,
    DEFAULT_SCENARIOS_BPS, TEXTBOOK_INT_BEARING_BETA, NAMED_SCENARIOS,
)
from utils.formatting import fmt_dollars
from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT


def _load_hist(ticker: str) -> list[dict]:
    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= 8:
        return hist
    cert = get_fdic_cert(ticker)
    if not cert:
        return hist or []
    df = fdic_client.fetch_financials(cert, limit=20)
    if df.empty:
        return hist or []
    records = df.to_dict("records")
    cache_put(f"fdic_hist:{ticker}", records)
    return records


def _nim_color(delta_bps: float | None) -> str:
    if delta_bps is None:
        return "#999"
    if delta_bps > 20:
        return "#1b5e20"
    if delta_bps > 0:
        return "#558b2f"
    if delta_bps < -20:
        return "#b71c1c"
    if delta_bps < 0:
        return "#e65100"
    return "#666"


def _pick_nii_scale(max_abs: float) -> tuple[float, str]:
    if max_abs >= 1e9: return 1e9, "B"
    if max_abs >= 1e6: return 1e6, "M"
    return 1e3, "K"


def render_rate_sensitivity(ticker: str):
    """Render the NIM Sensitivity panel for a bank."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for rate-sensitivity analysis.")
        return

    latest = hist[0]

    st.subheader("📈 NIM Rate Sensitivity")
    st.caption(
        "Curve-based NIM scenarios: **3M rate** drives funding costs, "
        "**5Y rate** drives earning-asset yields. Steepening curve widens NIM; "
        "flattening compresses. Asset-side assumes 100% pass-through to 5Y movements."
    )

    # ── Current curve context from FRED ───────────────────────────────
    try:
        from data.fred_client import latest_value
        ff = latest_value("FEDFUNDS")
        t3m = latest_value("DGS3MO")
        t5 = latest_value("DGS5")
        curve_5y_3m = (t5 - t3m) if (t5 is not None and t3m is not None) else None
    except Exception:
        ff, t3m, t5, curve_5y_3m = None, None, None, None

    cc1, cc2, cc3, cc4 = st.columns(4)
    with cc1:
        st.metric("Fed Funds", f"{ff:.2f}%" if ff is not None else "—")
    with cc2:
        st.metric("3M Treasury", f"{t3m:.2f}%" if t3m is not None else "—")
    with cc3:
        st.metric("5Y Treasury", f"{t5:.2f}%" if t5 is not None else "—")
    with cc4:
        slope_label = "Steep" if (curve_5y_3m and curve_5y_3m > 0.5) else (
            "Flat" if (curve_5y_3m and abs(curve_5y_3m) <= 0.5) else "Inverted"
        )
        st.metric(
            "5Y − 3M Slope",
            f"{curve_5y_3m:+.2f}pp" if curve_5y_3m is not None else "—",
            delta=slope_label, delta_color="off",
        )

    st.markdown("---")

    # ── Beta selector (shared across tabs) ─────────────────────────────
    bc1, bc2 = st.columns([2, 3])
    with bc1:
        beta_mode = st.radio(
            "Deposit beta model",
            ["Historical (measured)", "Textbook (50%)", "Custom"],
            key=f"beta_mode_{ticker}",
            horizontal=False,
        )
    custom_beta = None
    with bc2:
        if beta_mode == "Custom":
            custom_beta = st.slider(
                "Deposit beta (interest-bearing)",
                min_value=0.0, max_value=1.0, value=0.50, step=0.05,
                key=f"custom_beta_{ticker}",
            )
        elif beta_mode == "Historical (measured)":
            st.caption("Uses the bank's measured cycle beta from Deposit Dynamics.")
        else:
            st.caption(f"Industry-standard {TEXTBOOK_INT_BEARING_BETA*100:.0f}% pass-through.")

        asset_beta = st.slider(
            "Asset repricing speed (5Y pass-through to yields)",
            min_value=0.3, max_value=1.0, value=1.0, step=0.05,
            key=f"asset_beta_{ticker}",
            help="1.0 = full 5Y rate change flows to asset yields. Lower for banks with long fixed-rate books.",
        )

    mode_key = (
        "historical" if beta_mode.startswith("Historical")
        else "textbook" if beta_mode.startswith("Textbook")
        else "custom"
    )

    st.markdown("---")

    # ── Tabs ────────────────────────────────────────────────────────────
    tab_named, tab_matrix, tab_parallel = st.tabs([
        "🎯 Named Curve Scenarios",
        "🔥 Curve Matrix (3M × 5Y)",
        "Parallel Shift (legacy)",
    ])

    with tab_named:
        _render_named_scenarios(latest, hist, mode_key, custom_beta, asset_beta)

    with tab_matrix:
        _render_curve_matrix(latest, hist, mode_key, custom_beta, asset_beta)

    with tab_parallel:
        _render_parallel_legacy(latest, hist, mode_key, custom_beta)

    # ── Methodology ─────────────────────────────────────────────────────
    with st.expander("📐 Methodology"):
        st.markdown("""
        **Why 3M × 5Y?** Banks are structurally short-funded, long-invested:
        - **3M Treasury** ≈ cost of funds anchor (Fed funds, CD rates, money-market rates)
        - **5Y Treasury** ≈ asset-yield anchor (typical duration of loan + securities book)

        A **steepening curve** (5Y up more than 3M) is supportive for NIM — assets reprice
        higher faster than funding costs. **Flattening** is the opposite.

        **Named scenarios:**
        - *Parallel*: all points move together (pure rate level change)
        - *Bull steepener*: Fed cuts → short down more than long (typical early-cycle response)
        - *Bear steepener*: growth/inflation → long up more than short
        - *Bull flattener*: long rates fall on recession fears
        - *Bear flattener*: Fed hikes → short up more than long
        - *Curve inversion / normalization*: explicit sign change in curve slope

        **Beta modes:**
        - *Historical*: bank's measured cycle beta from Deposit Dynamics tab
        - *Textbook*: 50% pass-through to interest-bearing deposits
        - *Custom*: your slider

        **Excluded:** securities mark-to-market, prepayment acceleration, deposit outflows
        under stress. These are first-order annualized NIM/NII impacts — directionally
        correct for ranking but not a replacement for a full ALM model.
        """)


# ── Named Curve Scenarios ─────────────────────────────────────────────

def _render_named_scenarios(latest, hist, mode_key, custom_beta, asset_beta):
    result = run_curve_sensitivity(
        latest, hist, beta_mode=mode_key,
        custom_deposit_beta=custom_beta, asset_beta=asset_beta,
    )
    scenarios = result["scenarios"]
    inputs = result["inputs"]

    # Headline row
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "Current NIM",
            f"{inputs.get('current_nim_pct'):.2f}%" if inputs.get('current_nim_pct') else "—",
        )
    with c2:
        st.metric("Earning Assets", fmt_dollars(inputs.get("earning_assets_usd"), 2))
    with c3:
        st.metric(
            "Deposit Beta",
            f"{result['beta_used']:.2f}",
            delta=result["beta_mode"].replace("_", " ").title(),
            delta_color="off",
        )

    # Scenario table
    rows = []
    for s in scenarios:
        rows.append({
            "Scenario": s["name"],
            "Δ 3M": f"{s['short_change_bps']:+d} bps",
            "Δ 5Y": f"{s['long_change_bps']:+d} bps",
            "New NIM": f"{s['nim_new_pct']:.2f}%",
            "Δ NIM": f"{s['nim_delta_bps']:+.0f} bps",
            "Δ NII (annual)": fmt_dollars(s.get("nii_delta_usd"), 2),
            "Description": s.get("description", ""),
        })
    df = pd.DataFrame(rows)

    def _style_row(row):
        label = row["Δ NIM"]
        try:
            bps = float(label.replace(" bps", "").replace("+", ""))
        except Exception:
            return [""] * len(row)
        if bps > 25:
            return ["background-color: #c8e6c9; color: #1b5e20;"] * len(row)
        elif bps > 5:
            return ["background-color: #e8f5e9;"] * len(row)
        elif bps < -25:
            return ["background-color: #ffcdd2; color: #b71c1c;"] * len(row)
        elif bps < -5:
            return ["background-color: #ffebee;"] * len(row)
        return ["background-color: #f5f5f5;"] * len(row)

    styled = df.style.apply(_style_row, axis=1).set_properties(
        **{"font-size": "0.82rem", "padding": "4px 8px"}
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=40 + 35 * len(df))

    # Bar chart
    try:
        import plotly.graph_objects as go
        scenario_names = [s["name"] for s in scenarios]
        nim_deltas = [s["nim_delta_bps"] for s in scenarios]
        nii_deltas = [s["nii_delta_usd"] for s in scenarios]
        nii_scale, nii_unit = _pick_nii_scale(max(abs(d) for d in nii_deltas) if nii_deltas else 0)
        nii_scaled = [d / nii_scale for d in nii_deltas]

        cc1, cc2 = st.columns(2)

        fig1 = go.Figure()
        fig1.add_trace(go.Bar(
            x=scenario_names, y=nim_deltas,
            marker_color=[_nim_color(d) for d in nim_deltas],
            text=[f"{d:+.0f}" for d in nim_deltas],
            textposition="outside",
        ))
        fig1.add_hline(y=0, line_color="#666", line_width=1)
        apply_standard_layout(
            fig1, title="Δ NIM by Scenario",
            height=CHART_HEIGHT_COMPACT,
            yaxis_title="Δ NIM (bps)",
            show_legend=False, hovermode="x",
        )
        fig1.update_xaxes(tickangle=30)
        with cc1:
            st.plotly_chart(fig1, use_container_width=True)

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=scenario_names, y=nii_scaled,
            marker_color=[_nim_color(d) for d in nim_deltas],
            text=[f"${v:+,.1f}{nii_unit}" for v in nii_scaled],
            textposition="outside",
        ))
        fig2.add_hline(y=0, line_color="#666", line_width=1)
        apply_standard_layout(
            fig2, title="Δ Net Interest Income (Annualized)",
            height=CHART_HEIGHT_COMPACT,
            yaxis_title=f"Δ NII ($ {nii_unit})",
            show_legend=False, hovermode="x",
            wide_left_margin=True,
        )
        fig2.update_xaxes(tickangle=30)
        with cc2:
            st.plotly_chart(fig2, use_container_width=True)

    except ImportError:
        pass

    # ── Historical NIM-vs-slope scatter ────────────────────────────────
    st.markdown("")
    _render_historical_nim_scatter(hist)


def _render_historical_nim_scatter(hist: list[dict]):
    """
    Scatter plot of this bank's historical NIM vs the 5Y-3M Treasury slope
    at each historical quarter. Shows the bank's actually-observed rate
    sensitivity independent of any modeling assumptions.
    """
    if not hist or len(hist) < 8:
        return

    try:
        import plotly.graph_objects as go
        from data.fred_client import fetch_series
    except ImportError:
        return

    # Pull DGS5 and DGS3MO history
    try:
        dgs5 = fetch_series("DGS5", years=6)
        dgs3m = fetch_series("DGS3MO", years=6)
    except Exception:
        return

    if dgs5.empty or dgs3m.empty:
        return

    # Merge rates by date and compute slope
    rates = pd.merge(
        dgs5.rename(columns={"value": "y5"}),
        dgs3m.rename(columns={"value": "y3m"}),
        on="date", how="inner",
    )
    rates["slope"] = rates["y5"] - rates["y3m"]

    # Match each quarter's REPDTE to the Treasury rate on that date (or nearest prior)
    rows = []
    for r in hist:
        repdte = r.get("REPDTE")
        nim = r.get("NIMY")
        if repdte is None or nim is None:
            continue
        ts = pd.to_datetime(repdte, errors="coerce")
        if pd.isna(ts):
            continue
        # Find nearest earlier rate observation
        prior = rates[rates["date"] <= ts]
        if prior.empty:
            continue
        slope_at_date = prior["slope"].iloc[-1]
        rows.append({
            "date": ts,
            "nim": float(nim),
            "slope": float(slope_at_date),
        })

    if len(rows) < 6:
        return

    df = pd.DataFrame(rows).sort_values("date")

    # Color gradient by date (older = lighter, newer = darker)
    n = len(df)
    colors = [f"rgba(26,115,232,{0.3 + 0.7 * i / max(1, n - 1)})" for i in range(n)]

    # Regression line
    x = df["slope"].values
    y = df["nim"].values
    if len(x) >= 3 and x.std() > 0:
        slope_coef = ((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum()
        intercept = y.mean() - slope_coef * x.mean()
        x_line = [x.min(), x.max()]
        y_line = [intercept + slope_coef * xi for xi in x_line]
        # R²
        y_pred = intercept + slope_coef * x
        ss_res = ((y - y_pred) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    else:
        slope_coef = intercept = r_sq = None
        x_line = y_line = None

    # Current curve slope from latest FRED values
    from data.fred_client import latest_value
    current_5y = latest_value("DGS5")
    current_3m = latest_value("DGS3MO")
    current_slope = (current_5y - current_3m) if (current_5y and current_3m) else None

    fig = go.Figure()

    # Scatter points, sized by recency
    fig.add_trace(go.Scatter(
        x=df["slope"], y=df["nim"],
        mode="markers",
        marker=dict(
            size=[8 + 6 * i / max(1, n - 1) for i in range(n)],
            color=colors,
            line=dict(color="#1a1a1a", width=0.5),
        ),
        text=[d.strftime("%Y-Q%q") if False else f"{d.year}Q{(d.month-1)//3+1}"
              for d in df["date"]],
        hovertemplate="<b>%{text}</b><br>5Y-3M slope: %{x:+.2f}pp<br>NIM: %{y:.2f}%<extra></extra>",
        name="Historical Quarters",
    ))

    # Regression line
    if x_line and y_line:
        fig.add_trace(go.Scatter(
            x=x_line, y=y_line,
            mode="lines",
            line=dict(color="#b71c1c", width=2, dash="dash"),
            name=f"Fit (β={slope_coef:+.2f}, R²={r_sq:.2f})",
        ))

    # Current slope marker (vertical line)
    if current_slope is not None:
        fig.add_vline(
            x=current_slope, line_color="#1b5e20", line_width=2,
            annotation_text=f"Current slope: {current_slope:+.2f}pp",
            annotation_position="top right",
            annotation_font_size=11,
        )

    apply_standard_layout(
        fig, title="Historical NIM vs 5Y–3M Curve Slope",
        height=CHART_HEIGHT_FULL,
        xaxis_title="5Y − 3M Slope (pp)",
        yaxis_title="NIM (%)",
        show_legend=True, hovermode="closest",
    )
    fig.update_xaxes(ticksuffix="pp")
    fig.update_yaxes(ticksuffix="%")

    st.markdown("##### Historical NIM vs Curve Slope")
    st.caption(
        "Each dot = one quarter. Darker = more recent. The red line is the best-fit "
        "regression: a positive slope (β > 0) means the bank's NIM historically expanded "
        "when the curve was steeper, and compressed when flatter. R² shows how tightly NIM "
        "tracked slope. Green vertical line = current curve position."
    )
    st.plotly_chart(fig, use_container_width=True)

    # Interpretation
    if slope_coef is not None and r_sq is not None:
        r_label = "strong" if r_sq > 0.5 else ("moderate" if r_sq > 0.25 else "weak")
        direction = "widens NIM" if slope_coef > 0.1 else (
            "compresses NIM" if slope_coef < -0.1 else "has minimal effect on NIM"
        )
        st.caption(
            f"**Observed sensitivity:** A 100bps steepening historically {direction} by "
            f"~{abs(slope_coef)*100:.0f}bps. Relationship is **{r_label}** (R² = {r_sq:.2f})."
        )


# ── 2D Curve Matrix ────────────────────────────────────────────────────

def _render_curve_matrix(latest, hist, mode_key, custom_beta, asset_beta):
    """Render the 5x5 heat-map of NIM/NII deltas across 3M × 5Y."""

    # Range controls
    rc1, rc2 = st.columns(2)
    with rc1:
        max_short = st.select_slider(
            "3M range (±bps)", options=[50, 100, 150, 200], value=100,
            key="curve_max_short",
        )
    with rc2:
        max_long = st.select_slider(
            "5Y range (±bps)", options=[50, 100, 150, 200], value=100,
            key="curve_max_long",
        )

    short_range = [-max_short, -max_short // 2, 0, max_short // 2, max_short]
    long_range = [-max_long, -max_long // 2, 0, max_long // 2, max_long]

    matrix = run_curve_matrix(
        latest, hist,
        short_bps_range=short_range, long_bps_range=long_range,
        beta_mode=mode_key, custom_deposit_beta=custom_beta, asset_beta=asset_beta,
    )

    nim_mat = matrix["nim_delta_matrix_bps"]
    nii_mat = matrix["nii_delta_matrix_usd"]

    # Build DataFrames
    col_labels = [f"Δ5Y {l:+d}bps" for l in long_range]
    row_labels = [f"Δ3M {s:+d}bps" for s in short_range]

    nim_df = pd.DataFrame(nim_mat, index=row_labels, columns=col_labels)

    def _cell_color(val):
        if val is None or pd.isna(val):
            return "background-color: #f5f5f5;"
        if val > 50: return "background-color: #66bb6a; color: white; font-weight: 600;"
        if val > 20: return "background-color: #a5d6a7;"
        if val > 5: return "background-color: #e8f5e9;"
        if val < -50: return "background-color: #ef5350; color: white; font-weight: 600;"
        if val < -20: return "background-color: #ef9a9a;"
        if val < -5: return "background-color: #ffebee;"
        return "background-color: #f5f5f5;"

    st.markdown("##### Δ NIM Matrix (bps)")
    styled_nim = nim_df.style.applymap(_cell_color).format("{:+.0f}")
    st.dataframe(styled_nim, use_container_width=True)

    st.caption(
        "Rows = change in 3-Month Treasury (funding proxy). "
        "Columns = change in 5-Year Treasury (asset-yield proxy). "
        "Upper-right quadrant (steepening) is NIM-positive; lower-left (flattening) is NIM-negative."
    )

    st.markdown("")

    # NII matrix
    max_abs = max(abs(v) for row in nii_mat for v in row) if nii_mat else 0
    nii_scale, nii_unit = _pick_nii_scale(max_abs)
    nii_scaled_mat = [[v / nii_scale for v in row] for row in nii_mat]
    nii_df = pd.DataFrame(nii_scaled_mat, index=row_labels, columns=col_labels)

    def _nii_cell_color(val):
        if val is None or pd.isna(val):
            return "background-color: #f5f5f5;"
        # Scale thresholds proportionally to max
        if max_abs == 0: return ""
        ratio = val * nii_scale / max_abs if max_abs else 0
        if ratio > 0.5: return "background-color: #66bb6a; color: white; font-weight: 600;"
        if ratio > 0.2: return "background-color: #a5d6a7;"
        if ratio > 0.05: return "background-color: #e8f5e9;"
        if ratio < -0.5: return "background-color: #ef5350; color: white; font-weight: 600;"
        if ratio < -0.2: return "background-color: #ef9a9a;"
        if ratio < -0.05: return "background-color: #ffebee;"
        return "background-color: #f5f5f5;"

    st.markdown(f"##### Δ NII Matrix (annualized, ${nii_unit})")
    styled_nii = nii_df.style.applymap(_nii_cell_color).format("${:+,.2f}")
    st.dataframe(styled_nii, use_container_width=True)

    # Plotly heat-map (optional visual)
    try:
        import plotly.graph_objects as go
        fig = go.Figure(data=go.Heatmap(
            z=nim_mat,
            x=[f"{l:+d}" for l in long_range],
            y=[f"{s:+d}" for s in short_range],
            text=[[f"{v:+.0f}" for v in row] for row in nim_mat],
            texttemplate="%{text}",
            textfont={"size": 11},
            colorscale=[[0, "#b71c1c"], [0.5, "#f5f5f5"], [1, "#1b5e20"]],
            zmid=0,
            hovertemplate="Δ3M %{y} bps<br>Δ5Y %{x} bps<br>ΔNIM: %{z:+.0f} bps<extra></extra>",
        ))
        apply_standard_layout(
            fig, title="Δ NIM Heat-Map",
            height=CHART_HEIGHT_FULL,
            xaxis_title="Δ 5Y Treasury (bps)",
            yaxis_title="Δ 3M Treasury (bps)",
            show_legend=False, hovermode="closest",
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        pass


# ── Parallel Shift (Legacy) ───────────────────────────────────────────

def _render_parallel_legacy(latest, hist, mode_key, custom_beta):
    """Render the old parallel-shift scenarios."""
    st.caption(
        "Legacy view: assumes all rates move together in parallel. "
        "For more realistic NIM impact, see the Named Curve Scenarios or Curve Matrix tabs."
    )

    result = run_rate_sensitivity(
        latest, hist, beta_mode=mode_key, custom_deposit_beta=custom_beta,
    )
    inputs = result["inputs"]
    scenarios = result["scenarios"]

    rows = []
    for s in scenarios:
        rows.append({
            "Scenario": f"{s['rate_change_bps']:+d} bps" if s['rate_change_bps'] != 0 else "Flat",
            "EA Yield": f"{s.get('earning_asset_yield_new_pct'):.2f}%" if s.get("earning_asset_yield_new_pct") else "—",
            "Cost of IBL": f"{s.get('cost_of_funds_new_pct'):.2f}%" if s.get("cost_of_funds_new_pct") else "—",
            "NIM": f"{s.get('nim_new_pct'):.2f}%",
            "Δ NIM": f"{s.get('nim_delta_bps'):+.0f} bps",
            "Δ NII (annual)": fmt_dollars(s.get("nii_delta_usd"), 2),
        })
    df = pd.DataFrame(rows)

    def _style(row):
        label = row["Scenario"]
        if label == "Flat":
            return ["background-color: #f5f5f5; font-weight: 600;"] * len(row)
        try:
            bps = int(label.split(" ")[0])
        except Exception:
            return [""] * len(row)
        scen = next((s for s in scenarios if s["rate_change_bps"] == bps), None)
        if not scen:
            return [""] * len(row)
        d = scen["nim_delta_bps"]
        if d > 20: return ["background-color: #e8f5e9; color: #1b5e20;"] * len(row)
        if d > 0: return ["background-color: #f1f8e9;"] * len(row)
        if d < -20: return ["background-color: #ffebee; color: #b71c1c;"] * len(row)
        if d < 0: return ["background-color: #fff3e0;"] * len(row)
        return [""] * len(row)

    styled = df.style.apply(_style, axis=1).set_properties(
        **{"font-size": "0.85rem", "padding": "4px 8px"}
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=40 + 35 * len(df))

    asym = result.get("asymmetry_bps")
    if asym is not None:
        label = "Asset-sensitive (benefits from rate hikes)" if asym > 5 else (
            "Liability-sensitive (benefits from rate cuts)" if asym < -5 else "Symmetric"
        )
        st.caption(f"**Asymmetry: {asym:+.0f} bps** → {label}")
