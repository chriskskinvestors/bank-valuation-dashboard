"""
Capital Dynamics UI — renders the capital adequacy & buyback capacity panel
in the Company Analysis > Capital tab.
"""

import streamlit as st
import pandas as pd

from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data.cache import get as cache_get, put as cache_put
from data import fdic_client, sec_client
from analysis.capital_dynamics import (
    summarize_bank_capital,
    CET1_REG_MIN, CET1_BUFFER_FLOOR,
)
from utils.formatting import fmt_dollars_from_thousands


from utils.chart_style import (ALERT_STYLE as _SEVERITY_STYLE,
                               COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_DANGER,
                               CATEGORICAL_PALETTE)
from ui.chrome import ledger, title_bar


def _pick_scale(max_abs_dollars: float) -> tuple[float, str]:
    """
    Pick an appropriate scale for a chart axis given the max absolute $ value.

    Returns (divisor, unit_suffix) — e.g., (1e9, "B"), (1e6, "M"), (1e3, "K").
    """
    if max_abs_dollars is None:
        return 1.0, ""
    abs_val = abs(max_abs_dollars)
    if abs_val >= 1e9:
        return 1e9, "B"
    elif abs_val >= 1e6:
        return 1e6, "M"
    elif abs_val >= 1e3:
        return 1e3, "K"
    return 1.0, ""


# Shared loader (data/loaders) — was a verbatim copy in five tab modules.
from data.loaders import load_fdic_hist as _load_hist


def _load_shares(ticker: str) -> float | None:
    cik = get_cik(ticker)
    if not cik:
        return None
    cached = cache_get(f"sec:{ticker}")
    if cached and cached.get("shares_outstanding"):
        return cached["shares_outstanding"]
    sec = sec_client.get_latest_fundamentals(cik)
    if sec:
        cache_put(f"sec:{ticker}", sec)
        return sec.get("shares_outstanding")
    return None


def _load_peer_cet1_median(watchlist: list[str]) -> float | None:
    cet1s = []
    for t in watchlist:
        hist = cache_get(f"fdic_hist:{t}")
        if not hist:
            continue
        c = hist[0].get("IDT1CER")
        if c is not None and c > 0:
            cet1s.append(c)
    if not cet1s:
        return None
    return float(pd.Series(cet1s).median())


def _fmt_usd(amount_k: float | None) -> str:
    """Format thousands of dollars with auto T/B/M/K scaling."""
    return fmt_dollars_from_thousands(amount_k)


def _render_capital_headline(ticker, hist, summary, timeline, peer_cet1):
    """Capital headline cards — click-to-source. Reported capital ratios (CET1,
    total capital, leverage) link to the Call Report; model metrics (TBV CAGR,
    free capital, payout) show their formula + the FDIC inputs feeding them."""
    from ui.source_trace import render_traceable_cards, fdic_calc, make_calc
    from ui.financial_highlights import _fdic_doc, _disp_date, _thou, _num
    from data.bank_mapping import get_name

    cert = get_fdic_cert(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    rec = hist[0]
    latest = summary["latest"]
    cr_doc = _fdic_doc(cert, rec.get("REPDTE")) if cert else None
    asof = _disp_date(rec.get("REPDTE"))

    def pct(x, dp=2):
        return f"{x:.{dp}f}%" if x is not None else "—"

    cet1 = latest.get("cet1_pct"); total_cap = latest.get("total_cap_pct")
    leverage = latest.get("leverage_pct"); cet1_qoq = latest.get("cet1_qoq_pp")
    bb = summary.get("buyback_capacity") or {}
    free = bb.get("free_capital"); organic = bb.get("organic_need"); retained = bb.get("retained")
    ni = _num(latest.get("net_income_k_qtr")); cap_ret = _num(latest.get("capital_returned_k"))
    tbv_cagr = summary.get("tbv_cagr_1y"); tbv_cagr2 = summary.get("tbv_cagr_2y")
    tbv_k = _num(latest.get("tbv_k"))

    cet1_disp = pct(cet1)
    if cet1_qoq is not None:
        col = "var(--success)" if cet1_qoq >= 0 else "var(--danger)"
        cet1_disp += (f" <span style='font-size:var(--fs-xs); color:{col}; "
                      f"font-weight:600;'>{cet1_qoq:+.2f}pp</span>")

    retention_4q = timeline["retention_ratio"].tail(4).dropna() if "retention_ratio" in timeline else []
    payout = max(0.0, (1 - retention_4q.mean()) * 100) if len(retention_4q) else None

    cards = [
        {"label": "CET1 Ratio", "value": cet1_disp,
         "calc": fdic_calc("CET1 ratio", "IDT1CER", rec, cert, unit="%", entity=entity,
                           value=pct(cet1), reported=True,
                           definition="Common equity tier 1 capital to risk-weighted assets "
                                       "(bank-level)." + (f" Peer median {peer_cet1:.2f}%."
                                                          if peer_cet1 else ""))},
        {"label": "TCE CAGR (1Y)",
         "value": (f"{tbv_cagr:.1f}%" if tbv_cagr is not None else "—"),
         "calc": make_calc("Tangible common equity CAGR (1-year)",
                           (f"{tbv_cagr:.1f}%" if tbv_cagr is not None else "—"), entity=entity,
                           source="Model — trailing tangible book", asof=asof, unit="%",
                           ref="(TCE now ÷ TCE 1yr ago) − 1",
                           definition="Growth in AGGREGATE tangible common equity over the "
                                       "trailing year. Note: not per-share — buybacks aren't "
                                       "credited (per-share CAGR would need historical share "
                                       "counts)."
                                       + (f" 2-year CAGR {tbv_cagr2:.1f}%." if tbv_cagr2 is not None else ""),
                           terms=[{"label": "Tangible common equity ($000)", "val": _thou(tbv_k),
                                   "doc": cr_doc,
                                   "sub": "equity − goodwill − other intangibles (FDIC)"}],
                           op="(TCE now ÷ TCE 1 year ago − 1) × 100")},
        {"label": "Free Capital (Q)", "value": _fmt_usd(free),
         "calc": make_calc("Free capital (quarter)", _fmt_usd(free), entity=entity,
                           source="Model — capital generation", asof=asof, unit="$",
                           ref="retained earnings − organic loan-growth need",
                           definition="Capital generated this quarter beyond what's needed to "
                                       "support loan growth at target CET1 — the buffer available "
                                       "for buybacks/special dividends.",
                           terms=[{"label": "Retained earnings", "val": _fmt_usd(retained), "doc": cr_doc,
                                   "sub": "net income − dividends/buybacks (FDIC)"},
                                  {"label": "Organic loan-growth need", "val": _fmt_usd(organic)}],
                           op="Retained earnings − organic loan-growth capital need")},
        {"label": "Payout Ratio (4Q)",
         "value": (f"{payout:.0f}%" if payout is not None else "—"),
         "calc": make_calc("Payout ratio (4-quarter)",
                           (f"{payout:.0f}%" if payout is not None else "—"), entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="Computed from Call Report",
                           definition="Share of earnings returned to shareholders (dividends + "
                                       "buybacks) over the trailing four quarters.",
                           terms=[{"label": "Capital returned (Q, $000)", "val": _thou(cap_ret), "doc": cr_doc},
                                  {"label": "Net income (Q, $000)", "val": _thou(ni), "doc": cr_doc}],
                           op="≈ capital returned ÷ net income (4-quarter avg) × 100", reported=False,
                           link=(cr_doc or {}).get("url"))},
        {"label": "Total Cap / Leverage",
         "value": f"{pct(total_cap)} / {pct(leverage)}",
         "calc": make_calc("Total capital & leverage ratios",
                           f"{pct(total_cap)} / {pct(leverage)}", entity=entity,
                           source="FDIC Call Report", asof=asof, unit="%",
                           ref="FDIC fields RBCRWAJ / RBCT1JR",
                           definition="Total risk-based capital ratio and tier-1 leverage ratio "
                                       "(bank-level).",
                           terms=[{"label": "Total capital ratio", "val": pct(total_cap), "doc": cr_doc},
                                  {"label": "Tier-1 leverage ratio", "val": pct(leverage), "doc": cr_doc}],
                           reported=True, link=(cr_doc or {}).get("url"))},
    ]
    render_traceable_cards(cards, key=f"capital_{ticker}", columns=5)


def render_capital_dynamics(ticker: str, watchlist: list[str] | None = None):
    """Render the Capital Adequacy & Buyback Capacity panel."""
    hist = _load_hist(ticker)
    if not hist:
        st.info("No FDIC history available for capital analysis.")
        return

    shares = _load_shares(ticker)
    peer_cet1 = _load_peer_cet1_median(watchlist or [])
    summary = summarize_bank_capital(hist, shares_outstanding=shares, peer_cet1_median=peer_cet1)
    timeline = summary["timeline"]

    if timeline.empty:
        st.info("Insufficient data for capital analysis.")
        return

    title_bar(f"{get_name(ticker)} ({ticker})", "Capital Adequacy")
    st.subheader("Capital Adequacy & Buyback Capacity")

    # ── Alerts ─────────────────────────────────────────────────────────
    alerts = summary["alerts"]
    if alerts:
        for a in alerts:
            style = _SEVERITY_STYLE.get(a["severity"], _SEVERITY_STYLE["medium"])
            st.markdown(
                f'<div style="{style}"><strong>{a["message"]}</strong></div>',
                unsafe_allow_html=True,
            )
        st.markdown("")
    else:
        st.markdown(
            f'<div style="{_SEVERITY_STYLE["ok"]}"><strong>Capital position healthy — no alerts</strong></div>',
            unsafe_allow_html=True,
        )

    # ── Headline metrics ───────────────────────────────────────────────
    latest = summary["latest"]
    _render_capital_headline(ticker, hist, summary, timeline, peer_cet1)

    # Buyback capacity explainer
    bb = summary["buyback_capacity"]
    if bb.get("free_capital") is not None:
        ni = latest.get("net_income_k_qtr")
        returned = latest.get("capital_returned_k")
        if ni is not None and returned is not None:
            explainer = (
                f"**Buyback capacity:** Quarterly NI {_fmt_usd(ni)} − "
                f"capital returned {_fmt_usd(returned)} − "
                f"loan-growth capital {_fmt_usd(bb.get('organic_need'))} "
                f"= **{_fmt_usd(bb.get('free_capital'))}** free for incremental buybacks."
            )
            if bb.get("free_capital") < 0:
                explainer += " *(Negative = already returning more than earnings support at current loan-growth pace.)*"
            # Escape $ so Streamlit doesn't parse "$56M ... $774K" as LaTeX math.
            st.caption(explainer.replace("$", "\\$"))

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    # Chart 1: CET1 with regulatory floor lines
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=timeline["date"], y=timeline["cet1_pct"],
        name="CET1", mode="lines+markers",
        line=dict(color=COLOR_PRIMARY, width=2.5),
        marker=dict(size=7),
    ))
    fig1.add_hline(y=CET1_REG_MIN, line_color=COLOR_DANGER, line_width=1, line_dash="dash",
                    annotation_text=f"{CET1_REG_MIN}% reg min + buffer",
                    annotation_position="bottom right")
    fig1.add_hline(y=CET1_BUFFER_FLOOR, line_color=COLOR_WARNING, line_width=1, line_dash="dot",
                    annotation_text=f"{CET1_BUFFER_FLOOR}% comfort floor",
                    annotation_position="top right")
    if peer_cet1:
        fig1.add_hline(y=peer_cet1, line_color=COLOR_SUCCESS, line_width=1, line_dash="dashdot",
                        annotation_text=f"Peer median {peer_cet1:.2f}%",
                        annotation_position="top left")
    from utils.chart_style import (apply_standard_layout, tighten_yaxis,
                                   CHART_HEIGHT_FULL, CHART_HEIGHT_COMPACT)

    apply_standard_layout(
        fig1, title="CET1 Ratio Trend",
        height=CHART_HEIGHT_COMPACT, yaxis_title="CET1",
        show_legend=False, hovermode="x",
    )
    # Zoom to the data + regulatory floors so the trend reads, instead of a
    # flat line pinned to the top of a 0-13% axis.
    _c = [v for v in timeline["cet1_pct"].tolist() if v is not None]
    _refs = [CET1_REG_MIN, CET1_BUFFER_FLOOR] + ([peer_cet1] if peer_cet1 else [])
    tighten_yaxis(fig1, _c + _refs, floor_zero=True, ticksuffix="%", pad_frac=0.20)

    # Chart 2: TBV/share trend
    fig2 = None
    if "tbv_per_share" in timeline.columns and timeline["tbv_per_share"].notna().any():
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=timeline["date"], y=timeline["tbv_per_share"],
            name="TBV / Share", mode="lines+markers",
            line=dict(color=COLOR_SUCCESS, width=2.5),
            marker=dict(size=6),
        ))
        apply_standard_layout(
            fig2, title="Tangible Book Value Per Share",
            height=CHART_HEIGHT_COMPACT, yaxis_title="TBV/Share",
            show_legend=False, hovermode="x",
            wide_left_margin=True,
        )
        tighten_yaxis(fig2, timeline["tbv_per_share"].dropna().tolist(), tickprefix="$")

    # Chart 3: Capital return mix — auto-scaled
    # Coerce to numeric first: columns may contain None from stale/missing FDIC
    # rows (e.g., banks right after their cert becomes active).
    _ni = pd.to_numeric(timeline["net_income_k_qtr"], errors="coerce")
    _cr = pd.to_numeric(timeline["capital_returned_k"], errors="coerce")
    max_val = max(_ni.abs().max() or 0, _cr.abs().max() or 0)
    scale, unit = _pick_scale(max_val * 1000)
    ni_scaled = timeline["net_income_k_qtr"] * 1000 / scale
    cr_scaled = timeline["capital_returned_k"] * 1000 / scale

    fig3 = go.Figure()
    fig3.add_trace(go.Bar(
        x=timeline["date"], y=ni_scaled,
        name="Net Income", marker_color=COLOR_PRIMARY, opacity=0.85,
    ))
    fig3.add_trace(go.Bar(
        x=timeline["date"], y=cr_scaled,
        name="Capital Returned", marker_color=COLOR_DANGER, opacity=0.85,
    ))
    apply_standard_layout(
        fig3, title="Net Income vs Capital Returned",
        height=CHART_HEIGHT_COMPACT, yaxis_title=f"$ {unit}",
        show_legend=True, wide_left_margin=True,
    )
    fig3.update_layout(barmode="group")

    # Chart 4: Capital Generation Waterfall (last quarter)
    #
    # Note: "Capital Returned" is DERIVED as NI − ΔEquity, so it captures
    # dividends + buybacks + AOCI + any other equity adjustments together.
    # We can't separate them without pulling from SEC 10-Q AOCI components.
    # The waterfall shows Starting Equity + NI − (NI − ΔEquity) = Ending Equity,
    # which by construction sums exactly — no residual.
    fig4 = None
    prior_eq = timeline["equity_k"].iloc[-2] if len(timeline) >= 2 else None
    curr_eq = latest.get("equity_k")
    ni = latest.get("net_income_k_qtr")
    if (prior_eq is not None and curr_eq is not None and ni is not None):
        cap_returned = latest.get("capital_returned_k") or 0

        scale, unit = _pick_scale(curr_eq * 1000)
        wf_scaled = [
            prior_eq * 1000 / scale,
            ni * 1000 / scale,
            -cap_returned * 1000 / scale,
            curr_eq * 1000 / scale,
        ]

        waterfall_labels = [
            "Starting<br>Equity",
            "+ Net<br>Income",
            "- Capital Returned<br>(Divs + Buybacks + AOCI)",
            "Ending<br>Equity",
        ]

        fig4 = go.Figure()
        fig4.add_trace(go.Waterfall(
            x=waterfall_labels,
            measure=["absolute", "relative", "relative", "total"],
            y=wf_scaled,
            text=[f"${v:,.1f}{unit}" for v in wf_scaled],
            textposition="outside",
            connector={"line": {"color": "rgb(150,150,150)"}},
            increasing={"marker": {"color": COLOR_SUCCESS}},
            decreasing={"marker": {"color": COLOR_DANGER}},
            totals={"marker": {"color": COLOR_PRIMARY}},
        ))
        latest_ts = latest.get("date")
        if latest_ts is not None and hasattr(latest_ts, "month"):
            q = (latest_ts.month - 1) // 3 + 1
            period_label = f"{latest_ts.year}-Q{q}"
        else:
            period_label = ""
        apply_standard_layout(
            fig4, title=f"Capital Generation — {period_label}",
            height=CHART_HEIGHT_COMPACT, yaxis_title=f"$ {unit}",
            show_legend=False, wide_left_margin=True,
        )
        # A 0-based axis makes the ±flows invisible against the ~$2B equity
        # bars. Zoom to the level of the bridge so NI and capital-returned
        # actually read as steps.
        _running, _levels = 0.0, []
        for _m, _v in zip(["absolute", "relative", "relative", "total"], wf_scaled):
            _running = _v if _m in ("absolute", "total") else _running + _v
            _levels.append(_running)
        _lo, _hi = min(_levels), max(_levels)
        _pad = max((_hi - _lo) * 0.6, 0.03 * max(abs(x) for x in _levels), 0.02)
        fig4.update_yaxes(range=[_lo - _pad, _hi + _pad])

    # Dense 2×2 grid — no full-width single charts.
    _g1 = st.columns(2)
    with _g1[0]:
        st.plotly_chart(fig1, use_container_width=True)
    if fig2 is not None:
        with _g1[1]:
            st.plotly_chart(fig2, use_container_width=True)
    _g2 = st.columns(2)
    with _g2[0]:
        st.plotly_chart(fig3, use_container_width=True)
    if fig4 is not None:
        with _g2[1]:
            st.plotly_chart(fig4, use_container_width=True)


    # ── Holding-company regulatory capital (SEC 10-K/10-Q — SNL basis) ──
    st.markdown("---")
    _render_holdco_capital(ticker)

    # ── RC-R Part I capital walk (SNL Capital Adequacy table) ──────────
    st.markdown("---")
    _render_rcr_capital_walk(ticker)

    # ── Capital Return Attribution (SEC-sourced) ────────────────────────
    st.markdown("---")
    _render_capital_return_attribution(ticker)


def _render_holdco_capital(ticker: str):
    """SNL-basis Capital Adequacy highlights for the HOLDING COMPANY, sourced
    from the company's own latest SEC 10-K/10-Q (timely; not delayed FR Y-9C).
    Values are scraped from the filing's inline XBRL and anchored to the bank's
    FDIC CET1; anything that can't be reconciled renders n/a. See
    docs/DATA-SOURCING-ARCHITECTURE.md."""
    cik = get_cik(ticker)
    if not cik:
        return

    # Freshest-source layer: when the latest earnings release reports a quarter
    # the 10-Q/10-K hasn't filed yet, surface its (preliminary) STANDARDIZED
    # capital ratios above the filed walk — ~2-3 weeks ahead of the periodic
    # filing. Each ratio is double-confirmed in the release (0-mismatch audited);
    # shown only when genuinely fresher (data.ir_provider.fresh_capital). Rendered
    # BEFORE the early-return below so it still appears for banks whose holdco
    # walk is n/a (reconcile-gated) — exactly where a fresh CET1 is most useful.
    try:
        from data.ir_provider import fresh_capital
        _fc = fresh_capital(cik)
    except Exception:
        _fc = None
    if _fc and _fc.get("ratios"):
        _q = _fc["quarter"]
        _ql = f"Q{(int(_q[5:7]) - 1) // 3 + 1} {_q[:4]}"
        _parts = [f"{lab} **{_fc['ratios'][k]:.2f}%**"
                  for lab, k in (("CET1", "cet1_ratio"), ("Tier 1", "t1_ratio"),
                                 ("Total", "total_ratio"), ("Leverage", "lev_ratio"))
                  if _fc["ratios"].get(k) is not None]
        if _parts:
            st.info(
                f"**Latest quarter (preliminary, {_ql}):** " + " · ".join(_parts)
                + f" — from the [earnings release filed {_fc['filed_date']}]"
                f"({_fc['url']}), ahead of the next 10-Q. Standardized basis, each "
                "ratio double-confirmed in the release; the filed figures supersede "
                "it once published.")

    try:
        from data.sec_filing_scraper import holdco_capital_for
        res = holdco_capital_for(cik, get_fdic_cert(ticker))
    except Exception:
        res = None
    if not res or not res.get("capital"):
        return
    meta, cap = res["meta"], res["capital"]
    periods = sorted(cap, reverse=True)[:5]
    if not periods:
        return

    def _plab(p):
        y, m = p[:4], p[5:7]
        return f"FY{y}" if m == "12" else f"Q{(int(m) - 1) // 3 + 1} '{y[2:]}"

    st.subheader("Capital Adequacy — holding company (SEC filing)")
    basis = cap[periods[0]].get("_basis")
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{meta['accession']}/{meta['doc']}")
    note = (" · bank-subsidiary basis (holdco not separately disclosed)"
            if basis == "bank" else "")
    st.caption(
        f"Source: SEC [{meta['form']} filed {meta['date']}]({src}) — holding-company "
        f"consolidated, anchored to the bank's FDIC CET1{note}. Updates as soon as "
        f"the company files (not the delayed FR Y-9C).")

    rows = [
        ("Common Equity Tier 1 ratio", "cet1_ratio", "pct"),
        ("Tier 1 capital ratio", "t1_ratio", "pct"),
        ("Total capital ratio", "total_ratio", "pct"),
        ("Tier 1 leverage ratio", "lev_ratio", "pct"),
        ("Common Equity Tier 1 capital", "cet1_cap", "usd"),
        ("Tier 1 capital", "t1_cap", "usd"),
        ("Tier 2 capital", "tier2_cap", "usd"),
        ("Total capital", "total_cap", "usd"),
        ("Risk-weighted assets", "rwa", "usd"),
    ]

    def _cell(v, kind):
        if v is None:
            return "n/a"
        return f"{v * 100:.2f}%" if kind == "pct" else f"${v / 1e9:,.2f}B"

    hdr = "| ($) | " + " | ".join(_plab(p) for p in periods) + " |"
    sep = "|---|" + "---|" * len(periods)
    body = [f"| {lab} | " + " | ".join(_cell(cap[p].get(k), kind) for p in periods) + " |"
            for lab, k, kind in rows]
    st.markdown("\n".join([hdr, sep] + body))
    st.caption("Liquidity Coverage Ratio · HQLA · Net cash outflows · Supplementary "
               "leverage: large-bank disclosures (FR 2052a) — not in this filing (n/a).")

    _render_holdco_walk(cap, periods, _plab)


def _render_holdco_walk(cap: dict, periods: list, _plab) -> None:
    """SNL "Regulatory Capital ($000)" walk for the holding company. Each step
    is sourced from the filing's inline XBRL and the section renders ONLY where
    the CET1 build reconciles to the extracted CET1 capital — banks fold
    CECL-transition, DTA/MSR-threshold and AOCI opt-out adjustments into CET1
    that the filing doesn't tag separately, so n/a is the honest result for a
    filing that doesn't disclose a reconcilable walk (see
    data.sec_filing_scraper._build_capital_walk). Component steps are exact
    filing tags; the bridge lines (= CET1/Tier 1/Tier 2/Total) reuse the
    already-extracted, FDIC-anchored capital amounts."""
    # Gate: only show the walk when the latest shown period actually reconciles
    # — never an all-n/a walk, and never a non-reconciling one.
    if not any(cap[p].get("_walk_reconciles") for p in periods):
        st.caption("Regulatory-capital walk: this filing does not tag a "
                   "machine-readable CET1 reconciliation that ties to the "
                   "reported CET1 capital — shown as n/a rather than a guess.")
        return

    st.markdown("###### Regulatory capital walk — holding company")

    def usd(v):
        return "n/a" if v is None else f"${v / 1e9:,.2f}B"

    def comp(p, key, negate=False):
        """A WALK component cell — only for periods that reconcile."""
        d = cap[p]
        if not d.get("_walk_reconciles"):
            return "n/a"
        v = (d.get("_walk") or {}).get(key)
        if v is None:
            return "n/a"
        return usd(-v if negate else v)

    def aoci_removed(p):
        d = cap[p]
        if not d.get("_walk_reconciles"):
            return "n/a"
        w = d.get("_walk") or {}
        # AOCI is a CET1 step only for opt-out (excluded) banks; for opt-in the
        # AOCI already sits in CET1, so there's no walk adjustment.
        if w.get("aoci_treatment") != "excluded":
            return "— (in CET1)"
        return usd(-(w.get("aoci") or 0.0))

    def bridge(p, key):
        """A bridge total from the extracted (anchored) capital amounts."""
        return usd(cap[p].get(key))

    def at1(p):
        d = cap[p]
        t1, cet1 = d.get("t1_cap"), d.get("cet1_cap")
        return usd(t1 - cet1) if (t1 is not None and cet1 is not None) else "n/a"

    def t2_other(p):
        """Tier 2 ex-sub-debt (allowance + adjustments) = Tier 2 − sub-debt,
        shown only when sub-debt is tagged for a reconciling period."""
        d = cap[p]
        if not d.get("_walk_reconciles"):
            return "n/a"
        sub = (d.get("_walk") or {}).get("subordinated_debt")
        t2 = d.get("tier2_cap")
        if sub is None or t2 is None:
            return "n/a"
        return usd(t2 - sub)

    # (label, fn) — fn(period) -> formatted cell.
    walk_rows = [
        ("Total common equity", lambda p: comp(p, "common_equity")),
        ("Less: goodwill", lambda p: comp(p, "goodwill", negate=True)),
        ("Less: other intangibles", lambda p: comp(p, "other_intangibles", negate=True)),
        ("Less: AOCI removed (opt-out)", aoci_removed),
        ("**= Common Equity Tier 1 capital**", lambda p: bridge(p, "cet1_cap")),
        ("Additional Tier 1 (qualifying preferred)", at1),
        ("**= Tier 1 capital**", lambda p: bridge(p, "t1_cap")),
        ("Subordinated debt & qualifying Tier 2", lambda p: comp(p, "subordinated_debt")),
        ("Other Tier 2 (allowance & adjustments)", t2_other),
        ("**= Tier 2 capital**", lambda p: bridge(p, "tier2_cap")),
        ("**= Total capital**", lambda p: bridge(p, "total_cap")),
    ]
    hdr = "| Walk ($) | " + " | ".join(_plab(p) for p in periods) + " |"
    sep = "|---|" + "---|" * len(periods)
    body = [f"| {lab} | " + " | ".join(fn(p) for p in periods) + " |"
            for lab, fn in walk_rows]
    st.markdown("\n".join([hdr, sep] + body))
    st.caption("CET1 = common equity − intangibles ± AOCI − deductions; "
               "Tier 1 = CET1 + qualifying preferred; Tier 2 = sub-debt + "
               "allowance; Total = Tier 1 + Tier 2. Component steps are inline-XBRL "
               "tags; bridge totals are the FDIC-anchored extracted amounts. "
               "A step the filing doesn't tag is n/a.")


def _render_rcr_capital_walk(ticker: str):
    """
    SNL-style regulatory-capital component walk from Schedule RC-R Part I
    (stored call-report detail, data/call_report_store.get_stored_rcr_detail).

    PROVENANCE: these are BANK SUBSIDIARY values — structurally different
    from SNL's holdco (FR Y-9C) figures (e.g. TruPS/sub debt issued at the
    holding company never appear in the bank's AT1; see the reconciliation
    block above data/ffiec_client._RCR_CAPITAL_CODES). Every display carries
    the "bank subsidiary (call report)" label.

    Walk semantics (from get_rcr_capital_detail): values are $thousands as
    filed; None = absent from the filing (rendered n/a), 0.0 = filed zero.
    "Less" lines are filed as positive deduction amounts; the AOCI
    adjustment is the items 9.a–9.e "LESS" lines negated, so positive =
    capital added back. Ratios are computed (component ÷ RWA) and shown
    only when both terms are present.
    """
    import streamlit.components.v1 as components
    from datetime import datetime
    from data.call_report_store import get_stored_rcr_detail
    from data.bank_mapping import get_name
    from ui.financial_highlights import _build_component, _fdic_doc
    from utils.formatting import (num as _n, thou as _thou,
                                  usd_compact_from_thousands as _usd)

    st.subheader("Regulatory Capital Walk — bank subsidiary (call report)")

    cert = get_fdic_cert(ticker)
    details = get_stored_rcr_detail(cert, quarters=8) if cert else []
    if not details:
        st.info("RC-R capital walk unavailable — call report not yet ingested.")
        return

    st.caption(
        "Schedule RC-R Part I of the FFIEC Call Report — BANK SUBSIDIARY "
        "values, not holding-company: holdco figures (FR Y-9C / SEC) differ "
        "structurally (e.g. TruPS or sub debt issued at the holding company "
        "never appear in the bank's additional tier 1). Click any number for "
        "its RC-R item / MDRM code or formula."
    )

    # Newest-first in the store → oldest-left / newest-right, matching the
    # statement-tab column convention (ui/financials_statements).
    details = list(reversed(details))

    def _qlabel(p):
        try:
            m, _, y = str(p).split("/")
            return f"Q{(int(m) - 1) // 3 + 1} '{y[2:]}"
        except Exception:
            return str(p)

    def _doc(p):
        try:
            return _fdic_doc(cert, datetime.strptime(str(p), "%m/%d/%Y"))
        except Exception:
            return None

    name = get_name(ticker) or ticker
    entity = f"{name} ({ticker}) — bank subsidiary (call report)"
    fdic_link = f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"

    def _calc(metric, v, asof, ref, terms, op, reported, doc):
        return {"metric": metric, "entity": entity,
                "source": "FFIEC Call Report — Schedule RC-R Part I",
                "asof": asof, "unit": "", "ref": ref, "definition": "",
                "terms": terms, "op": op, "reported": reported,
                "link": (doc or {}).get("url") or fdic_link}

    def _t(v):
        """$000 term value; absent stays honest — never rendered as $0."""
        return _thou(v) + " ($000)" if v is not None else "n/a — not in this filing"

    # Row builders: each returns (display value, click-through calc dict).
    # Item numbers are cited only where verified (RC-R Part I items 1–9, per
    # the code map in data/ffiec_client); other lines cite the MDRM code.
    def _rep(label, key, ref):
        def b(det, asof, doc):
            raw = _n(det.get(key))
            v = _usd(raw)
            return v, _calc(label, v, asof, ref,
                            [{"label": label, "val": _t(raw), "doc": doc}],
                            None, True, doc)
        return b

    def _intangibles(det, asof, doc):
        label = "Less: intangibles (goodwill + other)"
        total = _n(det.get("intangibles_deduction"))
        gw = _n(det.get("goodwill_deduction"))
        oth = _n(det.get("other_intangibles_deduction"))
        v = _usd(total)
        terms = [{"label": "Goodwill, net of DTLs (item 6, MDRM P841)",
                  "val": _t(gw), "doc": doc},
                 {"label": "Other intangibles ex-MSAs, net of DTLs (item 7, MDRM P842)",
                  "val": _t(oth), "doc": doc}]
        return v, _calc(label, v, asof, "Schedule RC-R Part I items 6 + 7",
                        terms, "Goodwill deduction + other-intangibles deduction",
                        False, doc)

    def _aoci(det, asof, doc):
        label = "AOCI adjustment (positive = added back)"
        total = _n(det.get("aoci_adjustment"))
        v = _usd(total)
        comps = [("Unrealized gains (losses) on AFS (item 9.a, MDRM P844)",
                  "aoci_adj_unrealized_afs"),
                 ("Unrealized loss on AFS preferred/equity (item 9.b, MDRM P845)",
                  "aoci_adj_afs_preferred"),
                 ("Accumulated gains (losses) on CF hedges (item 9.c, MDRM P846)",
                  "aoci_adj_cash_flow_hedges"),
                 ("DB postretirement plan amounts (item 9.d, MDRM P847)",
                  "aoci_adj_pension"),
                 ("Unrealized gains (losses) on HTM (item 9.e, MDRM P848)",
                  "aoci_adj_htm")]
        terms = [{"label": lb, "val": _t(_n(det.get(k))), "doc": doc}
                 for lb, k in comps]
        return v, _calc(label, v, asof,
                        "Schedule RC-R Part I items 9.a–9.e (AOCI opt-out banks)",
                        terms,
                        "−(sum of items 9.a–9.e \"LESS\" lines) — positive = "
                        "unrealized losses removed from regulatory capital",
                        False, doc)

    def _other_adj(det, asof, doc):
        label = "Other CET1 adjustments (residual)"
        total = _n(det.get("other_cet1_adjustments"))
        v = _usd(total)
        terms = [{"label": "CET1 (MDRM P859)", "val": _t(_n(det.get("cet1"))), "doc": doc},
                 {"label": "CET1 before adjustments (item 5, MDRM P840)",
                  "val": _t(_n(det.get("cet1_before_adjustments"))), "doc": doc},
                 {"label": "Intangibles deduction (items 6+7)",
                  "val": _t(_n(det.get("intangibles_deduction")))},
                 {"label": "DTA deduction (item 8, MDRM P843)",
                  "val": _t(_n(det.get("dta_deduction"))), "doc": doc},
                 {"label": "AOCI adjustment (items 9.a–9.e, negated)",
                  "val": _t(_n(det.get("aoci_adjustment")))}]
        return v, _calc(label, v, asof, "Residual — computed, not a filed line",
                        terms,
                        "CET1 − (CET1 before adj − intangibles − DTA + AOCI adj): "
                        "residual catching threshold deductions and all other "
                        "\"LESS\" items", False, doc)

    def _t2_other(det, asof, doc):
        label = "Other tier 2 components"
        nq = _n(det.get("t2_nonqualifying_instruments"))
        mi = _n(det.get("t2_minority_interest"))
        res = _n(det.get("t2_other"))
        present = [x for x in (nq, mi, res) if x is not None]
        total = sum(present) if present else None
        v = _usd(total)
        terms = [{"label": "Non-qualifying capital instruments (MDRM P867)",
                  "val": _t(nq), "doc": doc},
                 {"label": "Total-capital minority interest (MDRM P868)",
                  "val": _t(mi), "doc": doc},
                 {"label": "Residual (Tier 2 − named components)", "val": _t(res)}]
        return v, _calc(label, v, asof,
                        "MDRM P867 + P868 + residual (computed)", terms,
                        "Non-qualifying instruments + minority interest + "
                        "residual, so instruments + allowance + other = Tier 2",
                        False, doc)

    def _ratio(label, num_key, num_label, num_code):
        def b(det, asof, doc):
            a, r = _n(det.get(num_key)), _n(det.get("rwa"))
            v = f"{a / r * 100:.2f}%" if (a is not None and r) else "—"
            terms = [{"label": f"{num_label} (MDRM {num_code})", "val": _t(a), "doc": doc},
                     {"label": "Risk-weighted assets (MDRM A223)", "val": _t(r), "doc": doc}]
            return v, _calc(label, v, asof, "Computed from Schedule RC-R Part I",
                            terms, f"{num_label} ÷ risk-weighted assets × 100",
                            False, doc)
        return b

    spec = [
        ("Common Equity Tier 1", [
            ("CET1 before adjustments & deductions",
             _rep("CET1 before adjustments & deductions", "cet1_before_adjustments",
                  "Schedule RC-R Part I item 5 (MDRM P840)")),
            ("Less: intangibles (goodwill + other)", _intangibles),
            ("Less: DTAs from carryforwards",
             _rep("Less: DTAs from carryforwards", "dta_deduction",
                  "Schedule RC-R Part I item 8 (MDRM P843)")),
            ("AOCI adjustment (positive = added back)", _aoci),
            ("Other CET1 adjustments (residual)", _other_adj),
            ("Common equity tier 1 capital",
             _rep("Common equity tier 1 capital", "cet1",
                  "Schedule RC-R Part I (MDRM P859)")),
        ]),
        ("Tier 1", [
            ("Additional tier 1 capital",
             _rep("Additional tier 1 capital", "additional_tier1",
                  "Schedule RC-R Part I (MDRM P865)")),
            ("Tier 1 capital",
             _rep("Tier 1 capital", "tier1",
                  "Schedule RC-R Part I (MDRM 8274)")),
        ]),
        ("Tier 2 & Total Capital", [
            ("Tier 2 instruments + surplus",
             _rep("Tier 2 instruments + surplus", "t2_instruments",
                  "Schedule RC-R Part I (MDRM P866)")),
            ("Allowance includable in tier 2",
             _rep("Allowance includable in tier 2", "t2_allowance",
                  "Schedule RC-R Part I (MDRM 5310)")),
            ("Other tier 2 components", _t2_other),
            ("Tier 2 capital",
             _rep("Tier 2 capital", "tier2",
                  "Schedule RC-R Part I (MDRM 5311)")),
            ("Total capital",
             _rep("Total capital", "total_capital",
                  "Schedule RC-R Part I (MDRM 3792)")),
        ]),
        ("Risk-Weighted Assets & Ratios", [
            ("Total risk-weighted assets",
             _rep("Total risk-weighted assets", "rwa",
                  "Schedule RC-R Part I (MDRM A223)")),
            ("CET1 ratio", _ratio("CET1 ratio", "cet1", "CET1 capital", "P859")),
            ("Tier 1 ratio", _ratio("Tier 1 ratio", "tier1", "Tier 1 capital", "8274")),
            ("Total capital ratio",
             _ratio("Total capital ratio", "total_capital", "Total capital", "3792")),
        ]),
    ]

    labels = [_qlabel(d.get("reporting_period")) for d in details]
    asofs = [str(d.get("reporting_period")) for d in details]
    docs = [_doc(d.get("reporting_period")) for d in details]

    cells, rows_html, ri = {}, [], 0
    cell_errors: list[str] = []
    ncol = len(details)
    for sec_name, rows in spec:
        rows_html.append(f'<tr><td class="sec" colspan="{ncol + 1}">{sec_name}</td></tr>')
        for label, builder in rows:
            tds = [f'<td class="lbl">{label}</td>']
            for ci, det in enumerate(details):
                try:
                    v, c = builder(det, asofs[ci], docs[ci])
                except Exception as e:
                    # A computation bug must not be indistinguishable from
                    # "not reported" — collect and log once per render.
                    cell_errors.append(f"{label}[{ci}]: {type(e).__name__}: {e}")
                    v, c = "—", None
                cid = f"rcr_{ri}_{ci}"
                if c:
                    cells[cid] = c
                    tds.append(f'<td class="val" data-cid="{cid}">{v}</td>')
                else:
                    tds.append(f'<td class="val dead">{v}</td>')
            zebra = ' class="zebra"' if ri % 2 == 1 else ""
            rows_html.append(f'<tr{zebra}>{"".join(tds)}</tr>')
            ri += 1

    if cell_errors:
        print(f"[capital walk] {ticker}: {len(cell_errors)} cell(s) failed "
              f"to compute — {'; '.join(cell_errors[:5])}")

    head = ('<th class="lblh">($ in thousands unless noted)</th>'
            + "".join(f'<th class="colh">{lb}</th>' for lb in labels))
    height = 96 + 23 * (ri + len(spec) + 1)
    html = _build_component(head, "".join(rows_html), cells, entity,
                            fdic_link, fdic_link)
    components.html(html, height=height, scrolling=False)
    st.caption(f"Latest: FFIEC Call Report {asofs[-1]} · stored RC-R Part I "
               "detail (refreshed quarterly by the refresh-ffiec job).")


def _render_capital_return_attribution(ticker: str):
    """
    Show SEC-sourced dividend and buyback breakdown + total shareholder yield.
    """
    from data.bank_mapping import get_cik
    from analysis.capital_return import summarize_capital_return
    from utils.formatting import fmt_dollars

    cik = get_cik(ticker)
    if not cik:
        return

    # Try to get market cap from cached metrics
    market_cap = None
    try:
        from data.cache import get as cache_get
        metrics = cache_get("watchlist_metrics_last")
        if metrics:
            for m in metrics:
                if m.get("ticker") == ticker:
                    market_cap = m.get("market_cap")
                    break
    except Exception:
        pass

    with st.spinner("Loading SEC capital return data..."):
        result = summarize_capital_return(cik, market_cap=market_cap, lookback_quarters=20)

    timeline = result.get("timeline")
    if timeline is None or timeline.empty:
        return

    ttm = result.get("ttm", {})
    growth = result.get("growth", {})
    yld = result.get("yield", {})

    st.subheader("Capital Return Attribution")

    div_source = result.get("dividend_source", "unknown")
    source_note = {
        "common-specific": "Common dividends (pure, excludes preferred).",
        "total minus preferred": "Common dividends (derived = total − preferred).",
        "total (includes preferred)": "Total dividends only (includes preferred; may overstate common by ~3-8% for banks with meaningful preferred stock).",
        "unavailable": "Dividend data not available in SEC filings.",
    }.get(div_source, "")

    st.caption(
        "Data from SEC XBRL (holding-company 10-K / 10-Q cash flow statement). "
        f"{source_note} "
        "Buybacks are common-stock only. Total shareholder yield = (TTM dividends + buybacks) / current market cap."
    )

    # Headline row: Total Return Ratio, Payout Ratio, Buyback Ratio, Share Reduction, Shareholder Yield
    _m = "color:var(--text-muted);font-size:var(--fs-xs)"

    def _mut(s):
        return f' <span style="{_m}">{s}</span>' if s else ""

    tr_ratio = (ttm.get("total_return_ratio_ttm") or 0) * 100
    p_ratio = (ttm.get("payout_ratio_ttm") or 0) * 100
    bb_ratio = (ttm.get("buyback_ratio_ttm") or 0) * 100
    sc_change = ttm.get("share_change_pct_ttm")
    sy = yld.get("total_shareholder_yield_pct")
    ledger("Capital Return — TTM", [
        ("Total Return Ratio",
         (f"{tr_ratio:.0f}%" if ttm.get("total_return_ratio_ttm") is not None else "—")
         + _mut("of TTM net income")),
        ("Dividend Payout",
         (f"{p_ratio:.0f}%" if ttm.get("payout_ratio_ttm") is not None else "—")
         + _mut((fmt_dollars(ttm.get("dividends_ttm"), 2) + " TTM") if ttm.get("dividends_ttm") else "")),
        ("Buyback Ratio",
         (f"{bb_ratio:.0f}%" if ttm.get("buyback_ratio_ttm") is not None else "—")
         + _mut((fmt_dollars(ttm.get("buybacks_ttm"), 2) + " TTM") if ttm.get("buybacks_ttm") else "")),
        ("Share Reduction",
         (f"{sc_change:+.2f}%" if sc_change is not None else "—")
         + _mut("TTM" if sc_change is not None else "")),
        ("Shareholder Yield",
         (f"{sy:.2f}%" if sy is not None else "—")
         + _mut((f"{yld.get('dividend_yield_pct',0):.1f}% div + {yld.get('buyback_yield_pct',0):.1f}% bb")
                if sy is not None else "")),
    ])

    # ── Growth row ─────────────────────────────────────────────────────
    if any(growth.get(k) is not None for k in ["dividends_yoy_pct", "buybacks_yoy_pct", "dps_yoy_pct"]):
        dps_g = growth.get("dps_yoy_pct")
        dg = growth.get("dividends_yoy_pct")
        bg = growth.get("buybacks_yoy_pct")
        tg = growth.get("total_return_yoy_pct")
        ledger("Year-over-Year Growth", [
            ("DPS YoY Growth", (f"{dps_g:+.1f}%" if dps_g is not None else "—") + _mut("$/share declared")),
            ("Dividends YoY", (f"{dg:+.1f}%" if dg is not None else "—") + _mut("$ paid")),
            ("Buybacks YoY", (f"{bg:+.1f}%" if bg is not None else "—") + _mut("$ paid")),
            ("Total Return YoY", (f"{tg:+.1f}%" if tg is not None else "—") + _mut("combined")),
        ])

    # ── Quarterly trend chart ──────────────────────────────────────────
    import plotly.graph_objects as go
    from utils.chart_style import apply_standard_layout, CHART_HEIGHT_COMPACT

    # Only show quarters with actual data
    df = timeline.dropna(subset=["dividends_q", "buybacks_q"], how="all")
    if not df.empty:
        # Coerce columns to numeric — any may contain None from sparse quarters
        _div = pd.to_numeric(df["dividends_q"], errors="coerce")
        _bb = pd.to_numeric(df["buybacks_q"], errors="coerce")
        _ni = pd.to_numeric(df["net_income_q"], errors="coerce")
        # Pick scale
        max_abs = max(
            _div.abs().max() or 0,
            _bb.abs().max() or 0,
            _ni.abs().max() or 0,
        )
        if max_abs >= 1e9:
            scale, unit = 1e9, "B"
        elif max_abs >= 1e6:
            scale, unit = 1e6, "M"
        else:
            scale, unit = 1e3, "K"

        cc1, cc2 = st.columns(2)

        # Chart 1: Stacked bar NI vs Div+BB
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(
            x=df["date"], y=df["net_income_q"] / scale,
            name="Net Income", marker_color=COLOR_PRIMARY,
            opacity=0.45,
        ))
        fig1.add_trace(go.Bar(
            x=df["date"], y=df["dividends_q"].fillna(0) / scale,
            name="Dividends", marker_color=COLOR_SUCCESS,
        ))
        fig1.add_trace(go.Bar(
            x=df["date"], y=df["buybacks_q"].fillna(0) / scale,
            name="Buybacks", marker_color=COLOR_WARNING,
        ))
        apply_standard_layout(
            fig1, title="Net Income vs Capital Returned (Quarterly)",
            height=CHART_HEIGHT_COMPACT,
            yaxis_title=f"$ {unit}",
            show_legend=True, wide_left_margin=True,
        )
        fig1.update_layout(barmode="group")
        with cc1:
            st.plotly_chart(fig1, use_container_width=True)

        # Chart 2: Total return ratio % trend
        fig2 = go.Figure()
        ratio_pct = df["total_return_ratio_q"] * 100
        fig2.add_trace(go.Scatter(
            x=df["date"], y=ratio_pct,
            mode="lines+markers",
            line=dict(color=COLOR_PRIMARY, width=2.5),
            marker=dict(size=6),
            name="Total Return Ratio",
        ))
        fig2.add_hline(y=100, line_color=COLOR_DANGER, line_width=1, line_dash="dash",
                       annotation_text="100% (returning all NI)",
                       annotation_position="top right", annotation_font_size=10)
        apply_standard_layout(
            fig2, title="Total Return Ratio (Divs+BB / NI)",
            height=CHART_HEIGHT_COMPACT,
            yaxis_title="%", show_legend=False,
        )
        fig2.update_yaxes(ticksuffix="%")
        with cc2:
            st.plotly_chart(fig2, use_container_width=True)

        # Chart 3: Share count trend
        if df["shares_outstanding"].notna().any():
            fig3 = go.Figure()
            fig3.add_trace(go.Scatter(
                x=df["date"], y=df["shares_outstanding"] / 1e6,
                mode="lines+markers",
                line=dict(color=CATEGORICAL_PALETTE[4], width=2.5),
                marker=dict(size=6),
                fill="tozeroy",
                fillcolor="rgba(147, 51, 234, 0.08)",
                name="Shares Outstanding",
            ))
            apply_standard_layout(
                fig3, title="Shares Outstanding (M) — declining = buybacks working",
                height=CHART_HEIGHT_COMPACT,
                yaxis_title="Shares (M)", show_legend=False,
            )
            st.plotly_chart(fig3, use_container_width=True)


    # ── Quarterly detail table ─────────────────────────────────────────
    with st.expander("Quarterly detail (last 8 quarters)"):
        df_disp = timeline.tail(8).copy()

        def _fmt_d(v):
            if pd.isna(v) or v is None:
                return "—"
            return fmt_dollars(v, 2)

        def _fmt_pct(v):
            if pd.isna(v) or v is None:
                return "—"
            return f"{v*100:.1f}%"

        rows = []
        for _, r in df_disp.iterrows():
            rows.append({
                "Quarter": (
                    f"{int(r.get('year', 0))}Q{int(r.get('quarter', 0))}"
                    if pd.notna(r.get('year')) and pd.notna(r.get('quarter'))
                    else str(r['date'].date()) if r.get('date') is not None else "—"
                ),
                "Net Income": _fmt_d(r.get("net_income_q")),
                "Dividends": _fmt_d(r.get("dividends_q")),
                "Buybacks": _fmt_d(r.get("buybacks_q")),
                "Total Returned": _fmt_d(r.get("total_returned_q")),
                "Payout": _fmt_pct(r.get("payout_ratio_q")),
                "Buyback %": _fmt_pct(r.get("buyback_ratio_q")),
                "Total Ret %": _fmt_pct(r.get("total_return_ratio_q")),
                "Share Chg": (
                    f"{r.get('share_change_pct'):+.2f}%"
                    if r.get("share_change_pct") is not None and not pd.isna(r.get("share_change_pct"))
                    else "—"
                ),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
