"""
Valuation Model UI — FCFE DCF + Warranted P/TBV + Scenarios + Sensitivity Grids.

Renders in Company Analysis > Valuation sub-tab.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data.cache import get as cache_get, put as cache_put
from data import fdic_client, sec_client
from analysis.dcf import (
    run_fcfe_dcf, dcf_sensitivity_grid, warranted_ptbv_grid,
    warranted_ptbv, run_scenarios,
)
from analysis.deposit_dynamics import summarize_bank_deposits  # reuse helpers
from data.consensus import list_consensus, load_consensus
from utils.formatting import fmt_dollars


def _load_hist(ticker: str) -> list[dict]:
    hist = cache_get(f"fdic_hist:{ticker}")
    if hist and len(hist) >= 4:
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


def _load_sec(ticker: str) -> dict:
    cached = cache_get(f"sec:{ticker}")
    if cached:
        return cached
    cik = get_cik(ticker)
    if not cik:
        return {}
    data = sec_client.get_latest_fundamentals(cik)
    if data:
        cache_put(f"sec:{ticker}", data)
    return data or {}


def _load_price(ticker: str) -> float | None:
    """Try to get current price from cache/IBKR."""
    from data.ibkr_client import get_ibkr_client
    try:
        ibkr = get_ibkr_client()
        p = ibkr.get_price(ticker)
        if p and p.get("price"):
            return p["price"]
    except Exception:
        pass
    return None


def _derive_defaults(ticker: str, hist: list[dict], sec: dict) -> dict:
    """Derive sensible starting inputs from historical data."""
    if not hist:
        return {}

    from analysis.valuation import compute_roatce
    latest = hist[0]
    # Trailing EPS
    base_eps = sec.get("eps") or 0.0
    # ROATCE (current, annualized) — use the canonical helper so we share
    # the annualization logic with screening / fair value screens.
    roatce_pct = compute_roatce(latest) or 12.0
    equity = latest.get("EQTOT") or 0
    goodwill = latest.get("INTANGW") or 0
    tce = equity - goodwill

    # Shares
    shares = sec.get("shares_outstanding") or 0
    # TBV per share
    tbv_usd = tce * 1000  # thousands → dollars
    tbvps = (tbv_usd / shares) if shares > 0 else None

    # Trailing loan growth (TTM)
    loans = [r.get("LNLSNET") for r in hist[:5] if r.get("LNLSNET") is not None]
    loan_growth_trailing = None
    if len(loans) >= 5 and loans[-1] > 0:
        loan_growth_trailing = (loans[0] / loans[-1] - 1) * 100

    # Dividend payout
    dps = sec.get("dividends_per_share")
    payout_ratio = 0.30  # default 30%
    if dps and base_eps and base_eps > 0:
        payout_ratio = max(0.0, min(0.95, dps / base_eps))

    # Starting loans per share
    loans_per_share = (loans[0] * 1000 / shares) if (loans and shares > 0) else 0

    return {
        "base_eps": base_eps if base_eps else 2.00,
        "roatce_pct": roatce_pct,
        "tbvps": tbvps,
        "loan_growth_trailing_pct": loan_growth_trailing,
        "payout_ratio": payout_ratio,
        "loans_per_share": loans_per_share,
        "shares": shares,
    }


def render_valuation_model(ticker: str):
    """Render the full valuation model panel."""
    hist = _load_hist(ticker)
    sec = _load_sec(ticker)
    price = _load_price(ticker)

    if not hist:
        st.info("No FDIC history available for valuation model.")
        return
    if not sec:
        st.warning("Limited SEC data — EPS/shares may be missing.")

    name = get_name(ticker)
    st.subheader(f"💎 Valuation Model — {ticker}")
    st.caption(
        f"{name}. FCFE DCF + Warranted P/TBV with scenario sensitivity. "
        "Defaults are derived from trailing data; override any input."
    )

    defaults = _derive_defaults(ticker, hist, sec)

    # ── Input: consensus override (upload) ─────────────────────────────
    with st.expander("📂 Use uploaded consensus data"):
        available = list_consensus(ticker)
        if not available:
            st.caption(
                "No consensus data uploaded for this bank. Go to the Earnings tab "
                "to upload a consensus file, then return here to select the period."
            )
        else:
            period_labels = [f"{p['period']} ({p['source']}, {p['metric_count']} metrics)" for p in available]
            sel_idx = st.selectbox(
                "Select consensus period",
                options=list(range(len(available))),
                format_func=lambda i: period_labels[i],
                key=f"dcf_consensus_period_{ticker}",
            )
            use_consensus = st.checkbox(
                "Pre-fill EPS / payout from this consensus period",
                key=f"dcf_use_consensus_{ticker}",
            )
            if use_consensus:
                consensus = load_consensus(ticker, available[sel_idx]["period"])
                if consensus:
                    for m in consensus.get("metrics", []):
                        if m.get("key") == "eps" and m.get("value"):
                            defaults["base_eps"] = float(m["value"]) * 4  # quarterly × 4
                        if m.get("key") == "dps" and m.get("value") and defaults.get("base_eps"):
                            defaults["payout_ratio"] = min(0.95, float(m["value"]) * 4 / defaults["base_eps"])

    # ── Input controls ────────────────────────────────────────────────
    with st.expander("⚙ Model inputs (click to edit)", expanded=False):
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Starting point**")
            base_eps = st.number_input(
                "Base EPS ($, annual)",
                value=float(defaults.get("base_eps") or 2.0),
                step=0.10, format="%.2f",
                key=f"dcf_base_eps_{ticker}",
            )
            tbvps = st.number_input(
                "TBV / Share ($)",
                value=float(defaults.get("tbvps") or 20.0),
                step=0.10, format="%.2f",
                key=f"dcf_tbvps_{ticker}",
            )
            loans_ps = st.number_input(
                "Starting loans / share ($)",
                value=float(defaults.get("loans_per_share") or 200.0),
                step=1.0, format="%.0f",
                key=f"dcf_loans_ps_{ticker}",
            )

        with col2:
            st.markdown("**Growth (5-year)**")
            eps_growth_avg = st.slider(
                "EPS growth rate (avg %)",
                min_value=-5.0, max_value=25.0, value=5.0, step=0.5,
                key=f"dcf_eps_g_{ticker}",
            )
            loan_growth_avg = st.slider(
                "Loan growth rate (avg %)",
                min_value=-5.0, max_value=25.0,
                value=float(defaults.get("loan_growth_trailing_pct") or 4.0),
                step=0.5,
                key=f"dcf_loan_g_{ticker}",
            )
            payout_ratio = st.slider(
                "Payout ratio",
                min_value=0.0, max_value=0.95,
                value=float(defaults.get("payout_ratio") or 0.30),
                step=0.05,
                key=f"dcf_payout_{ticker}",
            )

        with col3:
            st.markdown("**Terminal & discount**")
            cost_of_equity = st.slider(
                "Cost of equity (%)",
                min_value=6.0, max_value=16.0, value=10.0, step=0.25,
                key=f"dcf_coe_{ticker}",
            )
            terminal_growth = st.slider(
                "Terminal growth (%)",
                min_value=0.0, max_value=5.0, value=2.5, step=0.25,
                key=f"dcf_tg_{ticker}",
            )
            target_cet1 = st.slider(
                "Target CET1 (%)",
                min_value=7.0, max_value=14.0, value=10.0, step=0.5,
                key=f"dcf_cet1_{ticker}",
            )

    # ── Run base case DCF ──────────────────────────────────────────────
    eps_growth_rates = [eps_growth_avg / 100] * 5
    loan_growth_rates = [loan_growth_avg / 100] * 5

    base_params = {
        "base_eps": base_eps,
        "eps_growth_rates": eps_growth_rates,
        "payout_ratio": payout_ratio,
        "loan_growth_rates": loan_growth_rates,
        "starting_loans_per_share": loans_ps,
        "target_cet1_pct": target_cet1,
        "cost_of_equity_pct": cost_of_equity,
        "terminal_growth_pct": terminal_growth,
    }
    try:
        dcf = run_fcfe_dcf(**base_params)
    except Exception as e:
        st.error(f"DCF error: {e}")
        return

    # ── Warranted P/TBV ────────────────────────────────────────────────
    roatce_pct = defaults.get("roatce_pct") or 12.0
    w_ptbv = warranted_ptbv(roatce_pct, cost_of_equity, terminal_growth)
    w_fair_price = w_ptbv * tbvps if (w_ptbv is not None and tbvps) else None

    # ── Headline Metrics ──────────────────────────────────────────────
    dcf_fv = dcf.get("fair_value_per_share")
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.metric("Current Price", f"${price:.2f}" if price else "—")
    with c2:
        st.metric(
            "DCF Fair Value",
            f"${dcf_fv:.2f}" if dcf_fv else "—",
            delta=(
                f"{((dcf_fv / price) - 1) * 100:+.1f}% vs price"
                if (dcf_fv and price) else None
            ),
        )
    with c3:
        st.metric(
            "Warranted P/TBV",
            f"{w_ptbv:.2f}x" if w_ptbv else "—",
            delta=f"@ ROATCE {roatce_pct:.1f}%, CoE {cost_of_equity:.1f}%",
            delta_color="off",
        )
    with c4:
        st.metric(
            "Warranted Price",
            f"${w_fair_price:.2f}" if w_fair_price else "—",
            delta=(
                f"{((w_fair_price / price) - 1) * 100:+.1f}% vs price"
                if (w_fair_price and price) else None
            ),
        )
    with c5:
        if dcf_fv and w_fair_price:
            blended = (dcf_fv + w_fair_price) / 2
            st.metric(
                "Blended Fair Value",
                f"${blended:.2f}",
                delta=(
                    f"{((blended / price) - 1) * 100:+.1f}% vs price"
                    if price else None
                ),
            )
        else:
            st.metric("Blended Fair Value", "—")

    st.markdown("---")

    # ── DCF cash flow waterfall ────────────────────────────────────────
    st.markdown("#### Projected FCFE & Terminal Value")
    projected_eps = dcf.get("projected_eps", [])
    projected_fcfe = dcf.get("projected_fcfe", [])
    tv = dcf.get("terminal_value")
    pv_terminal = dcf.get("pv_terminal")
    pv_explicit = dcf.get("pv_explicit")

    years = [f"Y{i+1}" for i in range(len(projected_fcfe))]
    rows = []
    for i in range(len(projected_fcfe)):
        rows.append({
            "Year": years[i],
            "Projected EPS": f"${projected_eps[i]:.2f}",
            "FCFE / share": f"${projected_fcfe[i]:.2f}",
        })
    if tv is not None:
        rows.append({"Year": "Terminal", "Projected EPS": f"${dcf.get('terminal_eps', 0):.2f}", "FCFE / share": f"${tv:.2f}"})

    df_cf = pd.DataFrame(rows)
    st.dataframe(df_cf, hide_index=True, use_container_width=True)

    c_pv1, c_pv2, c_pv3 = st.columns(3)
    with c_pv1:
        st.metric("PV of 5-Year FCFE", f"${pv_explicit:.2f}" if pv_explicit else "—")
    with c_pv2:
        st.metric("PV of Terminal Value", f"${pv_terminal:.2f}" if pv_terminal else "—")
    with c_pv3:
        tv_pct = (pv_terminal / dcf_fv * 100) if (pv_terminal and dcf_fv) else None
        st.metric("Terminal / Total %", f"{tv_pct:.0f}%" if tv_pct else "—")

    st.markdown("---")

    # ── Sensitivity Tabs ───────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🔥 CoE × Terminal Growth",
        "🔥 ROATCE × CoE (Warranted P/TBV)",
        "🐂🐻 Bull / Base / Bear",
        "🌪 Tornado + Implied IRR",
        "🆚 Peer Warranted P/TBV",
    ])

    with tab1:
        st.markdown("**DCF Fair Value under different discount & growth assumptions**")
        coe_range = [cost_of_equity - 2, cost_of_equity - 1, cost_of_equity,
                     cost_of_equity + 1, cost_of_equity + 2]
        g_range = [max(0, terminal_growth - 1.5), max(0, terminal_growth - 0.75),
                   terminal_growth, terminal_growth + 0.75, terminal_growth + 1.5]

        grid1 = dcf_sensitivity_grid(base_params, coe_range, g_range)

        grid_df = pd.DataFrame(
            grid1,
            index=[f"CoE {c:.1f}%" for c in coe_range],
            columns=[f"g {g:.1f}%" for g in g_range],
        )
        # Format and color
        def _color_dcf(val):
            if val is None or pd.isna(val):
                return "background-color: #f5f5f5; color: #999;"
            if price is None:
                return ""
            upside = (val / price - 1) * 100
            if upside > 20:
                return "background-color: #c8e6c9;"
            elif upside > 5:
                return "background-color: #e8f5e9;"
            elif upside < -20:
                return "background-color: #ffcdd2;"
            elif upside < -5:
                return "background-color: #ffebee;"
            return "background-color: #fff3e0;"

        styled1 = grid_df.style.applymap(_color_dcf).format("${:.2f}", na_rep="—")
        st.dataframe(styled1, use_container_width=True)
        if price:
            st.caption(
                f"Green = upside vs market price ${price:.2f}; red = downside. "
                "Rows = cost of equity, columns = terminal growth."
            )

    with tab2:
        st.markdown("**Warranted P/TBV Fair Price under different ROATCE & CoE**")
        roatce_range = [roatce_pct - 4, roatce_pct - 2, roatce_pct,
                        roatce_pct + 2, roatce_pct + 4]
        coe_range2 = [cost_of_equity - 2, cost_of_equity - 1, cost_of_equity,
                      cost_of_equity + 1, cost_of_equity + 2]

        grid2 = warranted_ptbv_grid(roatce_range, coe_range2, terminal_growth, tbvps)
        grid2_df = pd.DataFrame(
            grid2,
            index=[f"ROATCE {r:.1f}%" for r in roatce_range],
            columns=[f"CoE {c:.1f}%" for c in coe_range2],
        )
        styled2 = grid2_df.style.applymap(_color_dcf).format("${:.2f}", na_rep="—")
        st.dataframe(styled2, use_container_width=True)
        if price:
            st.caption(
                f"Rows = ROATCE, columns = cost of equity. "
                f"Holding terminal growth at {terminal_growth:.2f}% and TBV/share at ${tbvps:.2f}."
            )

    with tab3:
        st.markdown("**Bull / Base / Bear Scenario Comparison**")
        sc_col1, sc_col2 = st.columns(2)

        with sc_col1:
            bull_eps_delta = st.slider("Bull EPS growth adjustment (pp)", 0.0, 5.0, 2.0, 0.5, key=f"bull_eps_{ticker}")
            bull_coe_delta = st.slider("Bull CoE adjustment (pp)", -2.0, 0.0, -1.0, 0.25, key=f"bull_coe_{ticker}")
        with sc_col2:
            bear_eps_delta = st.slider("Bear EPS growth adjustment (pp)", -5.0, 0.0, -2.0, 0.5, key=f"bear_eps_{ticker}")
            bear_coe_delta = st.slider("Bear CoE adjustment (pp)", 0.0, 3.0, 1.5, 0.25, key=f"bear_coe_{ticker}")

        bull_adj = {
            "eps_growth_rates": [bull_eps_delta / 100] * 5,
            "cost_of_equity_pct": bull_coe_delta,
        }
        bear_adj = {
            "eps_growth_rates": [bear_eps_delta / 100] * 5,
            "cost_of_equity_pct": bear_coe_delta,
        }

        scenarios = run_scenarios(base_params, bull_adj, bear_adj)

        scen_rows = []
        for name, color in [("bull", "#1b5e20"), ("base", "#666"), ("bear", "#b71c1c")]:
            s = scenarios[name]
            fv = s.get("fair_value_per_share")
            upside = ((fv / price - 1) * 100) if (fv and price) else None
            scen_rows.append({
                "Scenario": name.title(),
                "Fair Value": f"${fv:.2f}" if fv else "—",
                "Upside vs Price": f"{upside:+.1f}%" if upside is not None else "—",
                "PV Explicit": f"${s.get('pv_explicit', 0):.2f}",
                "PV Terminal": f"${s.get('pv_terminal', 0):.2f}" if s.get("pv_terminal") else "—",
            })

        scen_df = pd.DataFrame(scen_rows)

        def _color_scen(row):
            label = row["Scenario"]
            if label == "Bull":
                return ["background-color: #e8f5e9; color: #1b5e20;"] * len(row)
            elif label == "Bear":
                return ["background-color: #ffebee; color: #b71c1c;"] * len(row)
            return ["background-color: #f5f5f5;"] * len(row)

        styled3 = scen_df.style.apply(_color_scen, axis=1)
        st.dataframe(styled3, use_container_width=True, hide_index=True)

        # Bar chart
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            scen_names = ["Bear", "Base", "Bull"]
            scen_fvs = [scenarios["bear"].get("fair_value_per_share") or 0,
                        scenarios["base"].get("fair_value_per_share") or 0,
                        scenarios["bull"].get("fair_value_per_share") or 0]
            colors = ["#b71c1c", "#1a73e8", "#1b5e20"]
            fig.add_trace(go.Bar(
                x=scen_names, y=scen_fvs,
                marker_color=colors,
                text=[f"${v:.2f}" for v in scen_fvs],
                textposition="outside",
            ))
            if price:
                fig.add_hline(
                    y=price, line_color="#000", line_width=2, line_dash="dash",
                    annotation_text=f"Market ${price:.2f}",
                    annotation_position="top right",
                )
            from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL
            apply_standard_layout(
                fig, title="Fair Value by Scenario",
                height=CHART_HEIGHT_FULL, yaxis_title="Fair Value",
                show_legend=False, hovermode="x",
                wide_left_margin=True,
            )
            fig.update_yaxes(tickprefix="$")
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            pass

    # ── Tab 4: Tornado + Implied IRR ──────────────────────────────────
    with tab4:
        _render_tornado_and_irr(base_params, price)

    # ── Tab 5: Peer-Relative Warranted P/TBV ──────────────────────────
    with tab5:
        _render_peer_warranted(ticker, cost_of_equity, terminal_growth)

    # ── Consensus vs Model comparison (below tabs) ──────────────────
    st.markdown("---")
    _render_consensus_vs_model(ticker, projected_eps, hist[0])

    # ── Notes ──────────────────────────────────────────────────────────
    with st.expander("📐 Methodology"):
        st.markdown("""
        **FCFE DCF (5-year explicit + Gordon terminal)**
        - FCFE per share = Projected EPS − (Δ loans per share × target CET1 ratio)
        - Discounted at cost of equity
        - Terminal value = Terminal FCFE × (1 + g) / (CoE − g)

        **Warranted P/TBV (Gordon-equivalent for banks)**
        - P/TBV = (ROATCE − g) / (CoE − g)
        - Fair price = P/TBV × current TBV per share
        - Where ROATCE = CoE → P/TBV = 1.0
        - Where ROATCE > CoE → P/TBV > 1 (value creator)

        **Inputs default to derived values from FDIC history and SEC XBRL data.**
        You can override every input, or pre-fill from an uploaded consensus estimate
        (Earnings tab). All sensitivity grids recompute live as you change inputs.

        **Caveats:**
        - Loan growth is a proxy for overall balance sheet growth
        - No explicit treatment of securities portfolio mark-to-market
        - Doesn't model regulatory capital stress or management actions
        - Beta (systematic risk) is not separately modeled — you pick CoE directly
        """)


# ── Tornado + Implied IRR ────────────────────────────────────────────

def _render_tornado_and_irr(base_params: dict, price: float | None):
    """Tornado chart showing which inputs move fair value most + implied IRR."""
    from analysis.dcf import tornado_sensitivity, implied_irr

    st.markdown("**Which inputs matter most?**")
    st.caption(
        "Each bar shows how fair value changes when the input moves by ±1 unit in either direction. "
        "The longest bars are the biggest sensitivities — focus your judgement there."
    )

    # Implied IRR
    irr = implied_irr(price, base_params) if price else None
    base_fv = base_params  # (used in base case)

    ic1, ic2, ic3 = st.columns(3)
    with ic1:
        st.metric(
            "Implied IRR at current price",
            f"{irr:.2f}%" if irr is not None else "—",
            delta="What you earn if assumptions hold",
            delta_color="off",
        )
    with ic2:
        st.metric("Current CoE Assumption", f"{base_params.get('cost_of_equity_pct', 10):.2f}%")
    with ic3:
        if irr is not None:
            gap = irr - base_params.get("cost_of_equity_pct", 10)
            if gap > 2:
                label = "Undervalued"
                color = "#1b5e20"
            elif gap < -2:
                label = "Overvalued"
                color = "#b71c1c"
            else:
                label = "Fair"
                color = "#e65100"
            st.markdown(
                f"""<div style="padding:4px 0;">
                <div style="font-size:0.85rem; color:#666;">IRR vs CoE</div>
                <div style="font-size:1.75rem; font-weight:600; color:{color};">
                    {gap:+.2f}pp → {label}
                </div>
                <div style="font-size:0.75rem; color:#999;">Excess return vs required</div>
                </div>""",
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # Tornado
    tornado = tornado_sensitivity(base_params)
    if not tornado:
        st.info("Could not compute tornado sensitivity.")
        return

    # Friendly labels
    labels_map = {
        "eps_growth_rates": "EPS growth rate (±3pp)",
        "loan_growth_rates": "Loan growth rate (±3pp)",
        "payout_ratio": "Payout ratio (±15pp)",
        "cost_of_equity_pct": "Cost of equity (±1pp)",
        "terminal_growth_pct": "Terminal growth (±0.5pp)",
        "target_cet1_pct": "Target CET1 (±1pp)",
    }

    try:
        import plotly.graph_objects as go

        # Sort ascending so biggest bar appears at top
        tornado_sorted = sorted(tornado, key=lambda t: t["range"])
        labels = [labels_map.get(t["input"], t["input"]) for t in tornado_sorted]
        base_fv = tornado_sorted[0]["base_fv"]

        low_vals = [t["low_fv"] - base_fv for t in tornado_sorted]
        high_vals = [t["high_fv"] - base_fv for t in tornado_sorted]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=low_vals, y=labels, orientation="h",
            marker_color="#b71c1c", name="Low adj",
            base=base_fv,
            text=[f"${base_fv + v:.2f}" for v in low_vals],
            textposition="outside",
        ))
        fig.add_trace(go.Bar(
            x=high_vals, y=labels, orientation="h",
            marker_color="#1b5e20", name="High adj",
            base=base_fv,
            text=[f"${base_fv + v:.2f}" for v in high_vals],
            textposition="outside",
        ))
        fig.add_vline(x=base_fv, line_color="#000", line_width=2, line_dash="dash",
                      annotation_text=f"Base ${base_fv:.2f}", annotation_position="top")
        if price:
            fig.add_vline(x=price, line_color="#1a73e8", line_width=2,
                          annotation_text=f"Market ${price:.2f}",
                          annotation_position="bottom")

        from utils.chart_style import apply_standard_layout, CHART_HEIGHT_FULL
        apply_standard_layout(
            fig, title="Tornado: Input Sensitivity",
            height=CHART_HEIGHT_FULL,
            xaxis_title="Fair Value ($)", yaxis_title="",
            show_legend=True, hovermode="closest",
        )
        fig.update_layout(barmode="overlay")
        fig.update_xaxes(tickprefix="$")
        st.plotly_chart(fig, use_container_width=True)

        # Tornado table
        rows = []
        for t in tornado:
            rows.append({
                "Input": labels_map.get(t["input"], t["input"]),
                "Low-case FV": f"${t['low_fv']:.2f}",
                "Δ Low %": f"{t['low_delta_pct']:+.1f}%",
                "High-case FV": f"${t['high_fv']:.2f}",
                "Δ High %": f"{t['high_delta_pct']:+.1f}%",
                "Range ($)": f"${t['range']:.2f}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    except ImportError:
        pass


# ── Peer-Relative Warranted P/TBV ───────────────────────────────────

def _render_peer_warranted(ticker: str, coe_pct: float, terminal_g_pct: float):
    """Rank watchlist peers by warranted P/TBV upside at the same CoE/growth assumption."""
    from analysis.dcf import rank_peer_warranted_ptbv
    from data.bank_mapping import get_name

    # Use the watchlist metrics — load from session cache
    try:
        from data.cache import get as cache_get
        watchlist_metrics_cached = cache_get("watchlist_metrics_last")
        if not watchlist_metrics_cached:
            st.info(
                "Peer data not available. Visit the Screening or Peer Comparison section "
                "first to load watchlist data, then return here."
            )
            return
        peer_metrics = watchlist_metrics_cached
    except Exception:
        st.info("Peer data not loaded.")
        return

    st.markdown(
        f"**Peer-relative warranted P/TBV at CoE={coe_pct:.2f}%, g={terminal_g_pct:.2f}%**"
    )
    st.caption(
        "For each watchlist peer, applies the same discount-rate & terminal-growth "
        "assumption from your model inputs. Warranted P/TBV = (ROATCE − g) / (CoE − g). "
        "Banks with the largest positive upside have the highest implied value gap."
    )

    ranked = rank_peer_warranted_ptbv(peer_metrics, coe_pct, terminal_g_pct)
    if not ranked:
        st.info("Not enough peer data to rank (need ROATCE, TBV/share, price).")
        return

    rows = []
    for r in ranked:
        t = r["ticker"]
        is_self = (t == ticker)
        rows.append({
            "": "👉" if is_self else "",
            "Ticker": t,
            "Bank": get_name(t)[:40],
            "ROATCE": f"{r['roatce']:.1f}%" if r["roatce"] else "—",
            "Actual P/TBV": f"{r['ptbv_actual']:.2f}x" if r["ptbv_actual"] else "—",
            "Warranted P/TBV": f"{r['ptbv_warranted']:.2f}x",
            "Price": f"${r['price']:.2f}" if r["price"] else "—",
            "Fair Price": f"${r['fair_price']:.2f}",
            "Upside": f"{r['upside_pct']:+.1f}%" if r["upside_pct"] is not None else "—",
        })

    df = pd.DataFrame(rows)

    def _style_upside(row):
        label = row["Upside"]
        try:
            val = float(label.replace("%", "").replace("+", ""))
        except Exception:
            return [""] * len(row)
        base = [""] * len(row)
        if row.get(""):  # highlight current bank
            base = ["background-color: #e3f2fd; font-weight: 600;"] * len(row)
        elif val > 20:
            base = ["background-color: #c8e6c9; color: #1b5e20;"] * len(row)
        elif val > 5:
            base = ["background-color: #e8f5e9;"] * len(row)
        elif val < -20:
            base = ["background-color: #ffcdd2; color: #b71c1c;"] * len(row)
        elif val < -5:
            base = ["background-color: #ffebee;"] * len(row)
        return base

    styled = df.style.apply(_style_upside, axis=1).set_properties(
        **{"font-size": "0.82rem", "padding": "4px 8px"}
    )
    st.dataframe(styled, use_container_width=True, hide_index=True,
                 height=min(700, 50 + 32 * len(df)))

    st.caption(
        "**Reading the table:** Banks at top have the largest implied upside under "
        "your assumptions. If your current bank is near the top → undervalued relative to "
        "peers at the same CoE/growth. Near the bottom → richly valued."
    )


# ── Consensus vs Model Comparison ─────────────────────────────────────

def _render_consensus_vs_model(ticker: str, projected_eps: list[float], fdic_latest: dict):
    """
    Compare the model's EPS projection against uploaded consensus estimates.
    Shows where the model is above/below street for each metric.
    """
    from data.consensus import list_consensus, load_consensus

    available = list_consensus(ticker)
    if not available:
        st.markdown("##### 📋 Model vs Consensus")
        st.caption(
            "No consensus uploaded for this bank yet. Upload estimates in the "
            "Earnings tab to compare your model projection against street consensus."
        )
        return

    st.markdown("##### 📋 Model vs Consensus")

    col_p, _ = st.columns([1, 3])
    with col_p:
        period_labels = [f"{p['period']} ({p['metric_count']} metrics)" for p in available]
        sel_idx = st.selectbox(
            "Consensus period",
            options=list(range(len(available))),
            format_func=lambda i: period_labels[i],
            key=f"val_consensus_period_{ticker}",
        )

    consensus = load_consensus(ticker, available[sel_idx]["period"])
    if not consensus:
        st.warning("Could not load consensus data.")
        return

    # Compute trailing (actual) values from FDIC
    actual_nim = fdic_latest.get("NIMY")
    actual_eff = fdic_latest.get("EEFFR")
    actual_roa = fdic_latest.get("ROA")
    actual_roe = fdic_latest.get("ROE")
    actual_npl = fdic_latest.get("NCLNLSR")

    # Model's Year-1 quarterly EPS (if projected 5Y annual, split by 4)
    model_eps_annual_y1 = projected_eps[0] if projected_eps else None
    model_eps_quarterly = (model_eps_annual_y1 / 4) if model_eps_annual_y1 else None

    rows = []
    for m in consensus.get("metrics", []):
        key = m.get("key")
        name = m.get("name") or key
        consensus_val = m.get("value")
        unit = m.get("unit", "")
        if consensus_val is None:
            continue

        # Match model projection/actual for this metric
        model_val = None
        actual_val = None
        if key == "eps":
            model_val = model_eps_quarterly
            # Trailing EPS not readily available — skip actual
        elif key == "nim":
            model_val = None  # nim not in projection, use trailing
            actual_val = actual_nim
        elif key == "efficiency_ratio":
            actual_val = actual_eff
        elif key == "roaa":
            actual_val = actual_roa
        elif key == "roatce":
            actual_val = actual_roe
        elif key == "npl_ratio":
            actual_val = actual_npl

        if model_val is None and actual_val is None:
            continue

        comp_val = model_val if model_val is not None else actual_val
        delta = comp_val - consensus_val if (comp_val is not None and consensus_val is not None) else None
        delta_pct = (delta / abs(consensus_val) * 100) if (delta is not None and consensus_val != 0) else None

        def _fmt(v):
            if v is None:
                return "—"
            if unit == "$":
                return f"${v:.2f}"
            elif unit == "%":
                return f"{v:.2f}%"
            elif unit in ("$M", "$m"):
                return f"${v:,.1f}M"
            return f"{v:,.2f}"

        if delta is None:
            verdict = "—"
        elif abs(delta_pct or 0) < 2:
            verdict = "In line"
        elif delta > 0:
            verdict = "Above consensus"
        else:
            verdict = "Below consensus"

        rows.append({
            "Metric": name,
            "Consensus": _fmt(consensus_val),
            "Model / Actual": _fmt(comp_val),
            "Δ": _fmt(delta) if delta is not None else "—",
            "Δ %": f"{delta_pct:+.1f}%" if delta_pct is not None else "—",
            "Verdict": verdict,
        })

    if not rows:
        st.info("No comparable metrics between your model and the consensus upload.")
        return

    df = pd.DataFrame(rows)

    def _color(row):
        v = row["Verdict"]
        if v == "Above consensus":
            return ["background-color: #e8f5e9; color: #1b5e20;"] * len(row)
        elif v == "Below consensus":
            return ["background-color: #ffebee; color: #b71c1c;"] * len(row)
        elif v == "In line":
            return ["background-color: #fff8e1;"] * len(row)
        return [""] * len(row)

    styled = df.style.apply(_color, axis=1).set_properties(
        **{"font-size": "0.82rem", "padding": "4px 8px"}
    )
    st.dataframe(styled, use_container_width=True, hide_index=True,
                 height=min(450, 50 + 35 * len(df)))

    st.caption(
        "'Model / Actual' = your Year-1 EPS projection for earnings, or most recent "
        "trailing FDIC value for operating metrics. Bands: ±2% = in line."
    )
