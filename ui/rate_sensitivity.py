"""
Rate Sensitivity UI — NIM scenario analysis per bank.

Primary view: 3M × 5Y curve shifts (bank-appropriate — short end drives
funding costs, 5Y drives asset yields).

Tabs: Multi-Year Impact (phased) · Named Curve Scenarios · Curve Matrix
(3M × 5Y) · Historical Fit. The old "Parallel Shift (legacy)" tab was
removed — it used a deposit-beta convention the phased model documents as
wrong (divided the cycle beta by ib_weight, ~1.4× inflated).
"""

import html

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_name
from data.cache import get as cache_get, put as cache_put
from data import fdic_client
from analysis.rate_sensitivity import (
    run_curve_sensitivity, run_curve_matrix,
    run_rate_sensitivity_phased,
    DEFAULT_SCENARIOS_BPS, TEXTBOOK_INT_BEARING_BETA, NAMED_SCENARIOS,
)
from utils.formatting import fmt_dollars
from utils.chart_style import (
    apply_standard_layout, CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT,
    COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_PRIMARY,
)
from ui.chrome import ledger, title_bar, lazy_tabs


# Shared loader (data/loaders) — was a verbatim copy in five tab modules.
from data.loaders import load_fdic_hist as _load_hist


def _kg_rows(rows):
    """Render a list of uniform dicts as a design-system .ksk-grid table
    (header = keys, one body row per dict). '$' is neutralised for the
    Streamlit LaTeX guard. Used for the plain scenario tables; the colour-coded
    Δ-NIM / Δ-NII heatmap matrices stay as styled dataframes (colour = signal)."""
    if not rows:
        return
    def esc(s):
        return html.escape(str(s)).replace("$", "&#36;")
    cols = list(rows[0].keys())
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(r.get(c, '—'))}</td>" for c in cols) + "</tr>"
        for r in rows)
    st.markdown(f'<div class="ksk-grid"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>", unsafe_allow_html=True)


def _nim_color(delta_bps: float | None) -> str:
    if delta_bps is None:
        return "#999"
    if delta_bps > 20:
        return COLOR_SUCCESS
    if delta_bps > 0:
        return "#558b2f"
    if delta_bps < -20:
        return COLOR_DANGER
    if delta_bps < 0:
        return COLOR_WARNING
    return "#666"


def _pick_nii_scale(max_abs: float) -> tuple[float, str]:
    if max_abs >= 1e9: return 1e9, "B"
    if max_abs >= 1e6: return 1e6, "M"
    return 1e3, "K"


def _render_phased_inputs(ticker, latest, inputs, tax_rate):
    """The phased-model input strip — every value click-to-source. NIM and
    earning assets are reported FDIC fields; the securities/loan mix is computed
    from the Call Report; the tax rate is a model assumption."""
    from ui.source_trace import render_traceable_cards, fdic_calc, make_calc
    from ui.financial_highlights import _fdic_doc, _disp_date, _num, _thou
    from data.bank_mapping import get_fdic_cert, get_name

    cert = get_fdic_cert(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    cr_doc = _fdic_doc(cert, latest.get("REPDTE")) if (cert and latest.get("REPDTE")) else None
    asof = _disp_date(latest.get("REPDTE")) if latest.get("REPDTE") else "latest"
    ern = _num(latest.get("ERNAST")); sc = _num(latest.get("SC")); ln = _num(latest.get("LNLSNET"))
    nim = inputs.get("current_nim_pct", 0); ea = inputs.get("earning_assets_usd")
    sec_sh = inputs.get("securities_share", 0); ln_sh = inputs.get("loans_share", 0)

    cards = [
        {"label": "Current NIM", "value": f"{nim:.2f}%",
         "calc": fdic_calc("Current net interest margin", "NIMY", latest, cert, unit="%",
                           entity=entity, value=f"{nim:.2f}%", reported=True,
                           definition="The bank's latest reported net interest margin — the "
                                       "starting point the rate scenarios shock.")},
        {"label": "Earning Assets", "value": fmt_dollars(ea),
         "calc": fdic_calc("Total earning assets", "ERNAST", latest, cert, unit="$ in thousands",
                           entity=entity, value=fmt_dollars(ea), reported=True,
                           definition="Total interest-earning assets — the base the NIM is "
                                       "applied to.")},
        {"label": "Securities", "value": f"{sec_sh*100:.0f}%",
         "calc": make_calc("Securities share of earning assets", f"{sec_sh*100:.0f}%", entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Investment securities as a share of earning assets — the "
                                       "slower-repricing bucket in the model.",
                           terms=[{"label": "Securities ($000)", "val": _thou(sc), "doc": cr_doc},
                                  {"label": "Earning assets ($000)", "val": _thou(ern), "doc": cr_doc}],
                           op="Securities ÷ earning assets × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        {"label": "Loans", "value": f"{ln_sh*100:.0f}%",
         "calc": make_calc("Loan share of earning assets", f"{ln_sh*100:.0f}%", entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Net loans as a share of earning assets — the faster-"
                                       "repricing bucket (especially floating-rate loans).",
                           terms=[{"label": "Net loans ($000)", "val": _thou(ln), "doc": cr_doc},
                                  {"label": "Earning assets ($000)", "val": _thou(ern), "doc": cr_doc}],
                           op="Net loans ÷ earning assets × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        {"label": "Tax Rate", "value": f"{tax_rate*100:.0f}%",
         "calc": make_calc("Effective tax rate", f"{tax_rate*100:.0f}%", entity=entity,
                           source="Model assumption", asof="model", unit="%",
                           ref="model input",
                           definition="Effective tax rate used to convert pre-tax NII changes into "
                                       "EPS impact (a model assumption, editable in the inputs).",
                           terms=[{"label": "Effective tax rate", "val": f"{tax_rate*100:.0f}%"}])},
    ]
    render_traceable_cards(cards, key=f"nim_phased_inputs_{ticker}", columns=5)


def _slope_regime(curve_3m_5y):
    """Regime label for the 3M−5Y curve slope, or None when unavailable.

    House spread convention (#36): shorter tenor FIRST (3M − 5Y), so an
    upward-sloping curve reads NEGATIVE (steep) and an inverted one POSITIVE —
    matching Home and the Macro tab.

    Explicit `is not None` semantics (AUDIT-2026-07-02 #16): the old
    truthiness chain sent BOTH a FRED-outage None AND an exactly-flat
    0.00pp slope to "Inverted". n/a-over-guess: no number → no label.
    """
    if curve_3m_5y is None:
        return None
    if curve_3m_5y < -0.5:
        return "Steep"
    if abs(curve_3m_5y) <= 0.5:
        return "Flat"
    return "Inverted"


def _render_rate_context(ff, t3m, t5, curve_3m_5y):
    """Macro rate strip — values from FRED (daily). Click for source."""
    from ui.source_trace import render_traceable_cards, make_calc
    FRED = "FRED — Federal Reserve daily data"

    def rate_card(label, value, definition, series):
        return {"label": label, "value": value,
                "calc": make_calc(label, value, entity="US Treasury / Fed", source=FRED,
                                  asof="latest daily", unit="%", ref=f"FRED series {series}",
                                  definition=definition, terms=[{"label": label, "val": value}],
                                  reported=True, link="https://fred.stlouisfed.org/series/" + series)}

    slope_label = _slope_regime(curve_3m_5y)
    slope_val = (f"{curve_3m_5y:+.2f}pp" if curve_3m_5y is not None else "—")
    slope_display = (f"{slope_val}  ({slope_label})" if slope_label is not None
                     else slope_val)
    cards = [
        rate_card("Fed Funds", (f"{ff:.2f}%" if ff is not None else "—"),
                  "Effective federal funds rate — the overnight policy rate.", "DFF"),
        rate_card("3M Treasury", (f"{t3m:.2f}%" if t3m is not None else "—"),
                  "3-month Treasury bill yield — drives short-term funding costs.", "DGS3MO"),
        rate_card("5Y Treasury", (f"{t5:.2f}%" if t5 is not None else "—"),
                  "5-year Treasury yield — a proxy for asset-reinvestment rates.", "DGS5"),
        {"label": "3M − 5Y Slope", "value": slope_display,
         "calc": make_calc("3M − 5Y curve slope", slope_val, entity="US Treasury",
                           source="FRED — Federal Reserve daily data", asof="latest daily", unit="pp",
                           ref="DGS3MO − DGS5",
                           definition="Yield-curve slope between the 3-month and 5-year points "
                                       "(shorter tenor first, the house spread convention). "
                                       "Negative = steep/upward-sloping, positive = inverted.",
                           terms=[{"label": "3M Treasury (DGS3MO)", "val": (f"{t3m:.2f}%" if t3m is not None else "—")},
                                  {"label": "5Y Treasury (DGS5)", "val": (f"{t5:.2f}%" if t5 is not None else "—")}],
                           op="3M yield − 5Y yield")},
    ]
    render_traceable_cards(cards, key="nim_rate_context", columns=4)


@st.fragment
def render_rate_sensitivity(ticker: str):
    """Render the NIM Sensitivity panel for a bank.

    @st.fragment: this page is a wall of beta/duration sliders and scenario
    toggles — each one used to rerun the ENTIRE Company page (~2.9s) just to
    recompute the NIM model. As a fragment, a knob change reruns only this
    panel. Fragments render fully on a full rerun (bank switch), so no
    regression."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for rate-sensitivity analysis.")
        return

    latest = hist[0]

    title_bar(f"{get_name(ticker)} ({ticker})", "Interest Rate Risk")

    # Every scenario anchors on the current net interest margin (FDIC NIMY):
    # the NII base is earning assets × NIM and each scenario is NIM ± a modeled
    # delta. A missing NIMY would _safe() to 0.0 — a "0.00%" current NIM and a
    # $0 NII base (AUDIT-2026-07-02 #29) — making every scenario meaningless.
    # n/a over a fabricated zero (cardinal rule).
    if latest.get("NIMY") is None:
        st.warning(
            "Current net interest margin (FDIC NIMY) is unavailable for the "
            "latest reporting period, so the rate-sensitivity scenarios — which "
            "anchor on it — can't be computed for this bank."
        )
        return

    st.markdown('<div class="ksk-sec">NIM Rate Sensitivity</div>',
                unsafe_allow_html=True)
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
        # Shorter tenor first (house convention #36): 3M − 5Y.
        curve_3m_5y = (t3m - t5) if (t5 is not None and t3m is not None) else None
    except Exception:
        ff, t3m, t5, curve_3m_5y = None, None, None, None

    _render_rate_context(ff, t3m, t5, curve_3m_5y)

    # ── Beta selector (shared across tabs) ─────────────────────────────
    bc1, bc2 = st.columns([2, 3])
    with bc1:
        beta_mode = st.radio(
            "Deposit beta model",
            ["Historical (measured)", "Textbook (50%)", "Custom"],
            key=f"beta_mode_{ticker}",
            horizontal=True,
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

    # ── Tabs ────────────────────────────────────────────────────────────
    # The "Parallel Shift (legacy)" tab was removed: its model divided the
    # historical cycle beta by ib_weight — a convention _resolve_deposit_beta
    # explicitly documents as wrong (~1.4x inflated) — so the same screen
    # showed two different "historical" betas. The phased/curve tabs are the
    # supported model.
    _rs_tabs = [
        "Multi-Year Impact (phased)",
        "Named Curve Scenarios",
        "Curve Matrix (3M × 5Y)",
        "Historical Fit",
    ]
    _rs_sel = lazy_tabs(_rs_tabs, key="ratesens")

    if _rs_sel == _rs_tabs[0]:
        _render_phased_scenarios(ticker, latest, hist, mode_key, custom_beta)

    elif _rs_sel == _rs_tabs[1]:
        _render_named_scenarios(latest, hist, mode_key, custom_beta, asset_beta)

    elif _rs_sel == _rs_tabs[2]:
        _render_curve_matrix(latest, hist, mode_key, custom_beta, asset_beta)

    elif _rs_sel == _rs_tabs[3]:
        _render_backtest(ticker, hist, mode_key, custom_beta)

    # ── Methodology ─────────────────────────────────────────────────────
    with st.expander("Methodology"):
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


# ── Multi-Year Phased Impact (new model) ─────────────────────────────

def _render_assumptions_panel(ticker, latest, hist, securities_ladder,
                              floating_share, cert=None, saved=None):
    """
    "My Assumptions" override panel: lets the analyst set their own
    subcategory deposit betas (NIB / IB-core / brokered) and asset durations
    (securities + fixed-loan), persisted per-bank in user_nim_assumptions.

    Returns (subcategory_betas, asset_durations) — both None unless the user
    has ticked "Use my assumptions". When non-None they override the page's
    beta selector inside run_rate_sensitivity_phased.

    cert + saved may be passed in by the caller (already loaded) to avoid a
    duplicate DB read; both are resolved here if omitted.
    """
    from analysis.rate_sensitivity import (
        build_rate_sensitivity_inputs, deposit_subcategory_weights,
        blend_deposit_betas,
        DEFAULT_BETA_NIB, DEFAULT_BETA_IB_CORE, DEFAULT_BETA_BROKERED,
    )

    if cert is None:
        cert = get_fdic_cert(ticker)
    inputs_preview = build_rate_sensitivity_inputs(latest, hist)
    wt = deposit_subcategory_weights(inputs_preview)

    if saved is None and cert:
        try:
            from data.nim_assumptions_store import get_assumptions
            saved = get_assumptions(int(cert))
        except Exception:
            saved = None

    ladder_dur = (
        securities_ladder.get("weighted_avg_duration_years")
        if securities_ladder else None
    )

    def _sv(key, fallback):
        if saved and saved.get(key) is not None:
            return float(saved[key])
        return float(fallback)

    subcategory_betas = None
    asset_durations = None

    with st.expander("My Assumptions — override betas & durations",
                     expanded=bool(saved)):
        use_overrides = st.checkbox(
            "Use my assumptions (override the deposit-beta selector above)",
            value=bool(saved),
            key=f"nim_use_overrides_{ticker}",
            help="When on, the model blends your three subcategory deposit "
                 "betas using this bank's actual deposit mix, and uses your "
                 "asset durations for the repricing pace.",
        )
        st.caption(
            f"Deposit mix for this bank — non-int-bearing **{wt['nib']*100:.0f}%** · "
            f"IB core (sav/MMDA/NOW) **{wt['ib_core']*100:.0f}%** · "
            f"brokered/wholesale **{wt['brokered']*100:.0f}%** of total deposits."
        )

        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            beta_nib = st.slider(
                "β non-int-bearing", 0.0, 1.0,
                value=_sv("beta_nib", DEFAULT_BETA_NIB), step=0.05,
                key=f"beta_nib_{ticker}",
                help="Pass-through on non-interest-bearing deposits. Usually "
                     "~0 — they don't reprice with rates.",
            )
        with bcol2:
            beta_ib_core = st.slider(
                "β IB core (sav/MMDA/NOW)", 0.0, 1.0,
                value=_sv("beta_ib_core", DEFAULT_BETA_IB_CORE), step=0.05,
                key=f"beta_ibcore_{ticker}",
                help="Pass-through on core interest-bearing deposits. Sticky "
                     "retail franchises ~0.2-0.4; rate-sensitive books higher.",
            )
        with bcol3:
            beta_brokered = st.slider(
                "β brokered/wholesale", 0.0, 1.0,
                value=_sv("beta_brokered", DEFAULT_BETA_BROKERED), step=0.05,
                key=f"beta_brokered_{ticker}",
                help="Pass-through on brokered/wholesale funding. Reprices "
                     "~1:1 with the market (~0.9-1.0).",
            )

        bi, bn = blend_deposit_betas(
            inputs_preview, beta_nib, beta_ib_core, beta_brokered)
        st.caption(
            f"→ Blends to interest-bearing beta **{bi:.2f}**, non-int beta "
            f"**{bn:.2f}** (weighted by this bank's deposit mix)."
        )

        st.markdown("**Asset durations** (drive the repricing pace)")
        dcol1, dcol2, dcol3 = st.columns(3)
        with dcol1:
            sec_dur = st.number_input(
                "Securities duration (yrs)", min_value=0.1, max_value=20.0,
                value=_sv("sec_duration_yrs", ladder_dur if ladder_dur else 3.0),
                step=0.25, key=f"sec_dur_{ticker}",
                help="Avg duration of the securities book. Pre-filled from the "
                     "FFIEC RC-B ladder when available.",
            )
        with dcol2:
            fixed_dur = st.number_input(
                "Fixed-loan duration (yrs)", min_value=0.1, max_value=20.0,
                value=_sv("fixed_loan_duration_yrs", 4.0),
                step=0.25, key=f"fixed_dur_{ticker}",
                help="Avg duration of the fixed-rate loan book. Lower reprices "
                     "faster (shorter-dated consumer/auto); higher = mortgages.",
            )
        with dcol3:
            st.caption(
                f"Floating-loan share **{floating_share*100:.0f}%** — set with "
                "the slider above; saved together with these."
            )

        note = st.text_input(
            "Note (your rationale)",
            value=(saved.get("note") if saved and saved.get("note") else ""),
            key=f"nim_note_{ticker}",
            placeholder="e.g. 60% of deposits are sticky operating accounts; "
                        "securities ladder skews short.",
        )

        sv1, sv2, _sp = st.columns([1, 1, 3])
        with sv1:
            if st.button("Save assumptions", key=f"nim_save_{ticker}",
                         disabled=not cert, use_container_width=True):
                try:
                    from data.nim_assumptions_store import upsert_assumptions
                    upsert_assumptions(int(cert), {
                        "beta_nib": beta_nib,
                        "beta_ib_core": beta_ib_core,
                        "beta_brokered": beta_brokered,
                        "sec_duration_yrs": sec_dur,
                        "floating_loan_share": floating_share,
                        "fixed_loan_duration_yrs": fixed_dur,
                        "note": note,
                    }, updated_by="dashboard")
                    st.success("Saved — will persist across sessions.")
                except Exception as e:
                    st.error(f"Save failed: {e}")
        with sv2:
            if st.button("↺ Clear (revert to auto)", key=f"nim_clear_{ticker}",
                         disabled=not (cert and saved), use_container_width=True):
                try:
                    from data.nim_assumptions_store import delete_assumptions
                    delete_assumptions(int(cert))
                    st.success("Cleared — reverts to auto-defaults on rerun.")
                except Exception as e:
                    st.error(f"Clear failed: {e}")

        if saved and saved.get("updated_at"):
            st.caption(
                f"Last saved {saved['updated_at']} by "
                f"{saved.get('updated_by', 'dashboard')}."
            )

        if use_overrides:
            subcategory_betas = {
                "beta_nib": beta_nib,
                "beta_ib_core": beta_ib_core,
                "beta_brokered": beta_brokered,
            }
            asset_durations = {
                "sec_duration_yrs": sec_dur,
                "fixed_loan_duration_yrs": fixed_dur,
            }

    return subcategory_betas, asset_durations


def _render_phased_scenarios(ticker, latest, hist, mode_key, custom_beta):
    """
    Show per-year NIM, NII, and EPS impact of parallel rate scenarios with
    asset repricing pace + deposit mix-shift modeling.

    Securities-repricing pace uses the bank's actual FFIEC Call Report
    Schedule RC-B Memo 2 maturity ladder when available; otherwise falls
    back to a generic ~29%/yr industry average.
    """
    # Try to pull bank-specific maturity ladder from Postgres (populated
    # quarterly by jobs/refresh_ffiec.py).
    securities_ladder = None
    try:
        from data.call_report_store import get_latest_ladder
        cert = get_fdic_cert(ticker)
        if cert:
            securities_ladder = get_latest_ladder(int(cert))
    except Exception:
        securities_ladder = None

    if securities_ladder:
        st.markdown(
            f":green-badge[Bank-specific FFIEC ladder] &middot; "
            f"period **{securities_ladder.get('reporting_period','—')}** &middot; "
            f"weighted-avg duration **{securities_ladder.get('weighted_avg_duration_years',0):.1f} yrs**"
        )
    else:
        st.markdown(
            ":blue-badge[Generic industry assumption] &middot; "
            "no FFIEC ladder cached for this bank — using ~29%/yr securities."
        )

    st.markdown(
        "**Phased model**: securities pace from the bank's actual maturity "
        "ladder when available (FFIEC RC-B Memo 2), else industry default "
        "(~29%/yr). Fixed loans ~15%/yr, floating loans Q1. "
        "Deposit mix-shift applied to rate-up scenarios. EPS computed using "
        "TTM share count + effective tax rate."
    )

    # Pull SEC data for shares outstanding + effective tax fallback
    sec = None
    try:
        from data.bank_mapping import get_cik
        from data import sec_client
        cik = get_cik(ticker)
        if cik:
            sec = sec_client.get_latest_fundamentals(cik)
    except Exception:
        sec = None

    # Load any saved analyst overrides once, here, so both the floating-loan
    # slider and the "My Assumptions" panel below pre-fill consistently.
    cert = get_fdic_cert(ticker)
    saved_assumptions = None
    if cert:
        try:
            from data.nim_assumptions_store import get_assumptions
            saved_assumptions = get_assumptions(int(cert))
        except Exception:
            saved_assumptions = None

    # Floating-loan-share default priority:
    #   1. analyst's saved override
    #   2. FFIEC-derived (RC-C Memo 2 ≤3-month repricing share)
    #   3. generic 0.30
    ffiec_floating = (
        securities_ladder.get("floating_loan_share")
        if securities_ladder else None
    )
    if saved_assumptions and saved_assumptions.get("floating_loan_share") is not None:
        floating_default = float(saved_assumptions["floating_loan_share"])
        floating_src = "your saved override"
    elif ffiec_floating is not None:
        floating_default = float(ffiec_floating)
        floating_src = "FFIEC RC-C Memo 2 (actual)"
    else:
        floating_default = 0.30
        floating_src = "generic ~30% default"

    # Per-bank floating-loan-share slider — biggest single lever for the
    # accuracy of Yr1 numbers.
    col1, col2, col3, col4 = st.columns([1, 1.3, 1.6, 1.1])
    with col1:
        horizon = st.selectbox(
            "Horizon", [1, 2, 3, 4, 5], index=2,
            key=f"phased_horizon_{ticker}",
        )
    with col2:
        floating_share = st.slider(
            "Floating-rate loan share",
            min_value=0.0, max_value=1.0, value=floating_default, step=0.05,
            key=f"phased_floating_{ticker}",
            help="Share of loan book that re-prices within Q1 (floating rate). "
                 f"Pre-filled from {floating_src}. "
                 "Industry avg ~30%; commercial-heavy banks 40-55%; consumer/mortgage-"
                 "heavy banks 15-25%.",
        )
        st.caption(f"source: {floating_src}")
    with col3:
        apply_shift = st.checkbox(
            "Apply NIB → IB deposit mix-shift (rate-up scenarios)",
            value=True,
            key=f"phased_mixshift_{ticker}",
            help="In rate-up scenarios, NIB customers migrate to IB accounts, "
                  "raising effective funding cost. ~4pp of NIB shifts per +100bps.",
        )
    with col4:
        # Projects earning assets forward each year using the bank's historical
        # YoY growth, optionally damped by the rate scenario (higher rates →
        # lower loan demand).
        apply_volume = st.checkbox(
            "Apply volume effects",
            value=False,
            key=f"phased_volume_{ticker}",
            help="Projects earning assets forward using historical YoY growth, "
                  "damped by rate scenarios (loans -2pp / +100bps, deposits "
                  "-1pp / +100bps). Off = balance sheet held flat across horizon.",
        )
    if apply_volume:
        from analysis.rate_sensitivity import compute_historical_growth_rates
        ghist = compute_historical_growth_rates(hist) or {}
        ea_g = ghist.get("earning_assets_growth", 0.05) * 100
        st.caption(
            f"Historical YoY: loans **{ghist.get('loans_growth', 0)*100:+.1f}%** · "
            f"deposits **{ghist.get('deposits_growth', 0)*100:+.1f}%** · "
            f"earning assets **{ea_g:+.1f}%** · "
            f"securities **{ghist.get('securities_growth', 0)*100:+.1f}%**"
        )

    # ── My Assumptions: per-bank subcategory betas + asset durations ───
    # Lets the analyst inject their own deposit stickiness + asset-repricing
    # view (durations) instead of the noisy trailing-historical estimator.
    # When enabled, these override the beta selector at the top of the page.
    subcategory_betas, asset_durations = _render_assumptions_panel(
        ticker, latest, hist, securities_ladder, floating_share,
        cert=cert, saved=saved_assumptions,
    )

    result = run_rate_sensitivity_phased(
        latest, hist, sec_data=sec,
        beta_mode=mode_key, custom_deposit_beta=custom_beta,
        floating_loan_share=floating_share,
        apply_mix_shift=apply_shift,
        horizon_years=horizon,
        scenarios_bps=[-200, -100, -50, 50, 100, 200],
        securities_ladder=securities_ladder,
        apply_volume_effects=apply_volume,
        subcategory_betas=subcategory_betas,
        asset_durations=asset_durations,
    )

    if subcategory_betas:
        st.markdown(
            ":violet-badge[Using your saved assumptions] &middot; "
            f"blended IB beta **{result.get('beta_used', 0):.2f}** · "
            "subcategory betas + durations override the selector above."
        )

    inputs = result["inputs"]
    _render_phased_inputs(ticker, latest, inputs, result.get("tax_rate_used", 0))

    # Charts side-by-side: pace curve + (if available) the actual maturity ladder
    import plotly.express as px
    pace = result["repricing_pace"]

    if securities_ladder and securities_ladder.get("buckets"):
        # Two columns: pace line + ladder bar
        ch_left, ch_right = st.columns([1, 1])
        with ch_left:
            pace_df = pd.DataFrame({
                "year": list(pace.keys()),
                "cumulative_repriced_pct": [v * 100 for v in pace.values()],
            })
            fig_pace = px.line(
                pace_df, x="year", y="cumulative_repriced_pct",
                markers=True,
                labels={"year": "Year", "cumulative_repriced_pct": "Cumulative % repriced"},
            )
            apply_standard_layout(
                fig_pace, height=CHART_HEIGHT_COMPACT,
                title=f"Earning-asset repricing pace ({floating_share*100:.0f}% floating loans)")
            fig_pace.update_yaxes(ticksuffix="%", range=[0, 100])
            st.plotly_chart(fig_pace, use_container_width=True)
        with ch_right:
            bucket_labels = {
                "le_3mo": "≤ 3 mo", "3mo_1y": "3 mo – 1 yr",
                "1y_3y": "1 – 3 yr", "3y_5y": "3 – 5 yr",
                "5y_15y": "5 – 15 yr", "gt_15y": "> 15 yr",
            }
            buckets = securities_ladder["buckets"]
            ladder_df = pd.DataFrame([
                {"bucket": bucket_labels.get(k, k), "pct_of_securities": v * 100}
                for k, v in buckets.items()
            ])
            fig_ladder = px.bar(
                ladder_df, x="bucket", y="pct_of_securities",
                labels={"bucket": "", "pct_of_securities": "% of total debt securities"},
            )
            apply_standard_layout(fig_ladder, height=CHART_HEIGHT_COMPACT,
                                  title="Securities maturity ladder (FFIEC RC-B)")
            fig_ladder.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig_ladder, use_container_width=True)
    else:
        pace_df = pd.DataFrame({
            "year": list(pace.keys()),
            "cumulative_repriced_pct": [v * 100 for v in pace.values()],
        })
        fig_pace = px.line(
            pace_df, x="year", y="cumulative_repriced_pct",
            markers=True,
            labels={"year": "Year", "cumulative_repriced_pct": "Cumulative % repriced"},
        )
        apply_standard_layout(
            fig_pace, height=CHART_HEIGHT_COMPACT,
            title=f"Earning-asset repricing pace ({floating_share*100:.0f}% floating loans) — generic")
        fig_pace.update_yaxes(ticksuffix="%", range=[0, 100])
        st.plotly_chart(fig_pace, use_container_width=True)

    # Per-scenario table: rows = scenarios, columns = years
    st.markdown(f"### NIM / EPS impact by year — horizon: {horizon}Y")

    rows = []
    for s in result["scenarios"]:
        bps = s["rate_change_bps"]
        row = {"Scenario": f"{bps:+d}bps"}
        for y in s["years"]:
            yr = y["year"]
            nim_d = y["nim_delta_bps"]
            eps_d = y["eps_delta"]
            row[f"Yr{yr} ΔNIM"] = f"{nim_d:+.0f}bps"
            row[f"Yr{yr} ΔNII"] = fmt_dollars(y["nii_delta_usd"])
            row[f"Yr{yr} ΔEPS"] = f"${eps_d:+.2f}" if eps_d is not None else "—"
        rows.append(row)
    _kg_rows(rows)

    # Honest disclosure
    with st.expander("Model assumptions + known limitations"):
        shares = result.get("shares_outstanding")
        shares_str = f"{shares/1e6:,.0f}M" if shares else "missing"
        ladder_source = result.get("ladder_source", "generic")
        ladder_note = (
            f"FFIEC RC-B Memo 2 ({securities_ladder.get('reporting_period','—')})"
            if securities_ladder else "generic industry average"
        )
        st.markdown(f"""
**Inputs used:**
- Deposit beta: **{result['beta_used']:.2f}** ({result['beta_mode']})
- Floating-loan share: **{floating_share*100:.0f}%**
- Securities repricing source: **{ladder_note}**
- Repricing pace per yr: {', '.join(f'Yr{k}={v*100:.0f}%' for k, v in pace.items())}
- Mix-shift in rate-up scenarios: **{'ON' if apply_shift else 'OFF'}** (4pp NIB/100bps)
- Effective tax rate: **{result['tax_rate_used']*100:.0f}%** (from FDIC ITAX/PTAXNETINC)
- Shares outstanding (TTM): **{shares_str}** (from SEC)

**What's modeled:**
- Phased asset repricing — securities pace from the bank's actual FFIEC
  RC-B Memo 2 maturity ladder when available, else ~29%/yr default
- Fixed loans ~15%/yr, floating loans Q1
- Immediate deposit beta (cycle-measured or textbook)
- NIB→IB deposit shift in rate-up scenarios
- EPS impact: NII delta × (1 − tax rate) / shares

**Still NOT modeled:**
- Volume changes (loan demand, deposit outflows)
- Securities AOCI marks (capital impact, not NIM)
- Non-interest income/expense response
- Per-bank loan repricing buckets (uses generic 15%/yr fixed)

**Use as:** ranking tool for rate sensitivity across banks. **Don't use as:** the
only input for trade sizing — pair with the bank's own ALM disclosures.
""")


# ── Named Curve Scenarios ─────────────────────────────────────────────

# ── Historical Backtest ───────────────────────────────────────────────

def _render_backtest(ticker, hist, mode_key, custom_beta):
    """
    Replay the rate cycle through the phased model and compare predicted
    NIM to actual NIM quarter-by-quarter. Surfaces R², RMSE, bias, plus
    a directional correlation (does Δpredicted move with Δactual?).
    """
    st.markdown(
        "**Honest model self-test.** Walks forward through this bank's "
        "20 quarters of FDIC history, predicts NIM one year out from each "
        "baseline using the actual FedFunds rate change, then compares "
        "to the bank's reported NIM."
    )

    if not hist or len(hist) < 8:
        st.warning("Need at least 8 quarters of FDIC history to backtest. "
                   "This bank has fewer.")
        return

    from analysis.rate_sensitivity import backtest_bank

    # Pull the same ladder used elsewhere so the backtest uses the bank-
    # specific repricing pace when available
    securities_ladder = None
    try:
        from data.call_report_store import get_latest_ladder
        cert = get_fdic_cert(ticker)
        if cert:
            securities_ladder = get_latest_ladder(int(cert))
    except Exception:
        securities_ladder = None

    bt = backtest_bank(
        hist,
        beta_mode=mode_key,
        custom_deposit_beta=custom_beta,
        securities_ladder=securities_ladder,
    )
    if bt is None:
        st.warning("Backtest unavailable — insufficient quarter coverage "
                   "or missing FedFunds data.")
        return

    # ── Metric strip (boxless ledger; hover help preserved as tooltips) ──
    def _tip(val, tip):
        return f'<span title="{html.escape(tip)}">{val}</span>'

    r2 = bt.get("r_squared")
    dc = bt.get("directional_corr")
    ledger("Backtest Fit", [
        ("Quarters tested", f"{bt['n_quarters']}"),
        ("Levels R²", _tip(
            f"{r2:.2f}" if r2 is not None else "—",
            "1.0 = perfect fit to NIM levels. Negative = model fits worse than "
            "just predicting the mean. Levels are hard to predict because "
            "balance-sheet evolution isn't modeled — look at the directional "
            "metrics for cleaner model-skill signal.")),
        ("RMSE", _tip(
            f"{bt['rmse_bps']:.0f} bps",
            "Root mean squared error in NIM level (basis points). Lower = better.")),
        ("Bias", _tip(
            f"{bt['bias_bps']:+.0f} bps",
            "Mean (predicted − actual). Positive = model systematically "
            "over-predicts NIM. Near zero is good.")),
        ("Directional corr.", _tip(
            f"{dc:+.2f}" if dc is not None else "—",
            "Correlation between quarter-over-quarter ΔPredicted and ΔActual. "
            "+1 = perfect direction tracking. 0 = no signal. The cleanest "
            "measure of whether the model gets rate-driven NIM moves right.")),
    ])

    hr = bt.get("directional_hit_rate")
    if hr is not None:
        st.caption(
            f"**Directional hit rate**: model called the right direction "
            f"of quarter-over-quarter NIM moves **{hr*100:.0f}%** of the "
            f"time (random = 50%)."
        )

    # ── Time series chart ─────────────────────────────────────────────
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=bt["quarters"], y=bt["actual_nim_pct"],
        mode="lines+markers", name="Actual NIM",
        line=dict(width=3, color=COLOR_PRIMARY),
    ))
    fig.add_trace(go.Scatter(
        x=bt["quarters"], y=bt["predicted_nim_pct"],
        mode="lines+markers", name="Model prediction",
        line=dict(width=2, color=COLOR_WARNING, dash="dash"),
    ))
    apply_standard_layout(
        fig, title=f"{ticker} — predicted vs actual NIM, 1-year forward from each baseline",
        height=CHART_HEIGHT_FULL, xaxis_title="Quarter", yaxis_title="NIM (%)",
    )
    fig.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig, use_container_width=True)

    # ── Interpretation block ──────────────────────────────────────────
    with st.expander("How to read this backtest"):
        st.markdown("""
**What the backtest is doing:**
1. Takes each quarter t in the bank's 20-quarter history
2. Goes back 4 quarters (to t-4) as a baseline
3. Computes the actual FedFunds change from t-4 to t
4. Runs the phased model with that rate change, 1-year horizon
5. Compares predicted NIM to actual NIM at quarter t

**What it does NOT model** (so absolute-level fit will always be limited):
- Balance-sheet evolution (mix shifts, M&A, loan-loss provision moves)
- Fee income / non-interest expense changes
- Credit-cycle effects on yields
- Deposit competition / runoff during stress

**How to use the metrics:**
- **Levels R²** — if positive, the model captures the NIM level decently.
  Often negative for big banks because their NIM is driven by many
  non-rate factors. Read it as a strict sanity check.
- **Directional correlation + hit rate** — the cleaner question:
  given a rate move, does the model get the direction right? If hit rate
  is well above 50% and correlation > 0.3, the model has real signal.
  This is the metric to actually trust the forward predictions on.
- **Bias** — systematic over- or under-prediction. Near zero is good.

**Use the forward predictions** (Multi-Year Impact tab) **more confidently**
when this backtest shows high directional correlation. If it's flat or
negative, the model isn't picking up much rate-driven signal for this
bank — pair the forecast with the bank's own ALM disclosures before
sizing trades.
""")


def _render_named_scenarios(latest, hist, mode_key, custom_beta, asset_beta):
    result = run_curve_sensitivity(
        latest, hist, beta_mode=mode_key,
        custom_deposit_beta=custom_beta, asset_beta=asset_beta,
    )
    scenarios = result["scenarios"]
    inputs = result["inputs"]

    # Headline row (boxless ledger)
    _m = "color:var(--text-muted);font-size:var(--fs-xs)"
    ledger("Scenario Inputs", [
        ("Current NIM",
         f"{inputs.get('current_nim_pct'):.2f}%" if inputs.get('current_nim_pct') else "—"),
        ("Earning Assets", fmt_dollars(inputs.get("earning_assets_usd"), 2)),
        ("Deposit Beta",
         f"{result['beta_used']:.2f}"
         + f' <span style="{_m}">{result["beta_mode"].replace("_", " ").title()}</span>'),
    ])

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
            return ["background-color: #c8e6c9; color: #059669;"] * len(row)
        elif bps > 5:
            return ["background-color: rgba(5, 150, 105, 0.08);"] * len(row)
        elif bps < -25:
            return ["background-color: rgba(220, 38, 38, 0.24); color: #dc2626;"] * len(row)
        elif bps < -5:
            return ["background-color: rgba(220, 38, 38, 0.08);"] * len(row)
        return ["background-color: #f1f5f9;"] * len(row)

    styled = df.style.apply(_style_row, axis=1).set_properties(
        **{"font-size": "0.82rem", "padding": "4px 8px"}
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=40 + 35 * len(df))

    # Bar chart
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

    import plotly.graph_objects as go
    from data.fred_client import fetch_series

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
    current_slope = ((current_5y - current_3m)
                     if (current_5y is not None and current_3m is not None)
                     else None)

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
            line=dict(color=COLOR_DANGER, width=2, dash="dash"),
            name=f"Fit (β={slope_coef:+.2f}, R²={r_sq:.2f})",
        ))

    # Current slope marker (vertical line)
    if current_slope is not None:
        fig.add_vline(
            x=current_slope, line_color=COLOR_SUCCESS, line_width=2,
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

    st.markdown('<div class="ksk-sec">Historical NIM vs Curve Slope</div>',
                unsafe_allow_html=True)
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
            return "background-color: #f1f5f9;"
        if val > 50: return "background-color: #66bb6a; color: white; font-weight: 600;"
        if val > 20: return "background-color: #a5d6a7;"
        if val > 5: return "background-color: rgba(5, 150, 105, 0.08);"
        if val < -50: return "background-color: #ef5350; color: white; font-weight: 600;"
        if val < -20: return "background-color: #ef9a9a;"
        if val < -5: return "background-color: rgba(220, 38, 38, 0.08);"
        return "background-color: #f1f5f9;"

    st.markdown('<div class="ksk-sec">Δ NIM Matrix (bps)</div>',
                unsafe_allow_html=True)
    styled_nim = nim_df.style.map(_cell_color).format("{:+.0f}")
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
            return "background-color: #f1f5f9;"
        # Scale thresholds proportionally to max
        if max_abs == 0: return ""
        ratio = val * nii_scale / max_abs if max_abs else 0
        if ratio > 0.5: return "background-color: #66bb6a; color: white; font-weight: 600;"
        if ratio > 0.2: return "background-color: #a5d6a7;"
        if ratio > 0.05: return "background-color: rgba(5, 150, 105, 0.08);"
        if ratio < -0.5: return "background-color: #ef5350; color: white; font-weight: 600;"
        if ratio < -0.2: return "background-color: #ef9a9a;"
        if ratio < -0.05: return "background-color: rgba(220, 38, 38, 0.08);"
        return "background-color: #f1f5f9;"

    st.markdown(f"##### Δ NII Matrix (annualized, ${nii_unit})")
    styled_nii = nii_df.style.map(_nii_cell_color).format("${:+,.2f}")
    st.dataframe(styled_nii, use_container_width=True)

    # Plotly heat-map (optional visual)
    import plotly.graph_objects as go
    fig = go.Figure(data=go.Heatmap(
        z=nim_mat,
        x=[f"{l:+d}" for l in long_range],
        y=[f"{s:+d}" for s in short_range],
        text=[[f"{v:+.0f}" for v in row] for row in nim_mat],
        texttemplate="%{text}",
        textfont={"size": 11},
        colorscale=[[0, COLOR_DANGER], [0.5, "#f1f5f9"], [1, COLOR_SUCCESS]],
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


