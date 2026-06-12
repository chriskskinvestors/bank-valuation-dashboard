"""
Company Analysis navigation as data: sections → sub-tabs → renderers.

One registry drives both the nav radios in app.py and the dispatch, so a
sub-tab cannot exist without a renderer (or a renderer without a nav entry)
— the A17 bug class (two tabs silently rendering the same view, or a tab
rendering nothing) is structurally impossible and pinned by a test.

Renderer signature: fn(ticker, ctx). ctx carries the few app.py-scope
dependencies (see render_company_subtab). All heavy imports stay lazy
inside the renderers, matching the old inline-elif behavior — importing
this module is cheap and safe for tests.
"""
from __future__ import annotations

COMPANY_NAV = {
    "Overview": ["Corporate Profile"],
    "Financials": ["Financial Highlights", "Income Statement", "Balance Sheet",
                   "Performance Analysis", "Capital Adequacy", "Asset Quality Detail",
                   "Asset Quality by Loan Type", "Deposit/Loan Composition", "Deposit Trends",
                   "Interest Rate Risk", "Fair Value Analysis", "Portfolio Analysis",
                   "Capital Structure Details"],
    "Valuation": ["Valuation Model", "Peer Rank", "Price & Trends"],
    "Estimates / Earnings": ["Earnings"],
    "News & Filings": ["Filings & Reports", "Key Exhibits", "Press Releases",
                       "Transcripts & Presentations", "Events Calendar"],
    "Market Analysis": ["Market Share & Branches"],
    "Ownership": ["Institutional (13F)", "Insider Activity"],
}
# Flat list of every leaf sub-tab (for deep-link validation, etc.).
COMPANY_LEAVES = [leaf for subs in COMPANY_NAV.values() for leaf in subs]
# Which section a given leaf lives under.
COMPANY_SECTION_OF = {leaf: sec for sec, subs in COMPANY_NAV.items() for leaf in subs}


# ── Renderers ────────────────────────────────────────────────────────────

def _corporate_profile(t, ctx):
    import pandas as pd
    from ui.bank_detail import render_corporate_profile
    render_corporate_profile(t, pd.DataFrame([ctx["load_metrics"](t)]))


def _price_trends(t, ctx):
    from ui.bank_detail import render_price_trends
    render_price_trends(t)


def _financial_highlights(t, ctx):
    import streamlit as st
    from ui.financial_highlights import render_financial_highlights
    from ui.historicals import render_historicals
    render_financial_highlights(t)
    with st.expander("Trend charts", expanded=False):
        render_historicals(t)


def _capital_adequacy(t, ctx):
    from ui.capital_dynamics import render_capital_dynamics
    render_capital_dynamics(t, ctx["watchlist"])


def _asset_quality(t, ctx):
    from ui.credit_dynamics import render_credit_dynamics
    render_credit_dynamics(t, ctx["watchlist"])


def _asset_quality_by_loan_type(t, ctx):
    from ui.credit_dynamics import render_credit_dynamics
    render_credit_dynamics(t, ctx["watchlist"], view="by_loan_type")


def _deposit_loan_composition(t, ctx):
    from ui.deposit_lookup import render_deposits_for_ticker
    render_deposits_for_ticker(t)


def _interest_rate_risk(t, ctx):
    from ui.rate_sensitivity import render_rate_sensitivity
    render_rate_sensitivity(t)


def _income_statement(t, ctx):
    from ui.financials_statements import render_income_statement
    render_income_statement(t)


def _balance_sheet(t, ctx):
    from ui.financials_statements import render_balance_sheet
    render_balance_sheet(t)


def _performance_analysis(t, ctx):
    from ui.financials_statements import render_performance_analysis
    render_performance_analysis(t)


def _fair_value(t, ctx):
    from ui.financials_statements import render_fair_value
    render_fair_value(t)


def _portfolio(t, ctx):
    from ui.financials_statements import render_portfolio
    render_portfolio(t)


def _capital_structure(t, ctx):
    from ui.financials_statements import render_capital_structure
    render_capital_structure(t)


def _valuation_model(t, ctx):
    from ui.valuation_model import render_valuation_model
    render_valuation_model(t)


def _peer_rank(t, ctx):
    from ui.peer_rank import render_peer_rank
    render_peer_rank(t, ctx["peer_cohort"]())


def _earnings(t, ctx):
    from ui.earnings import render_earnings_consensus
    render_earnings_consensus(t, ctx["load_metrics"](t))


def _filings(t, ctx):
    from ui.filings import render_filings_for_ticker
    render_filings_for_ticker(t)


def _key_exhibits(t, ctx):
    from ui.key_exhibits import render_key_exhibits
    render_key_exhibits(t)


def _press_releases(t, ctx):
    # The events feed (SEC 8-K + wire services) largely IS press/wire items —
    # the old "Activity" sub-tab content lives here now.
    from ui.recent_activity import render_recent_activity
    render_recent_activity(t, title="Press Releases & News")


def _transcripts(t, ctx):
    from ui.transcripts import render_transcripts
    render_transcripts(t)


def _events_calendar(t, ctx):
    from ui.recent_activity import render_events_calendar
    render_events_calendar(t)


def _deposit_trends(t, ctx):
    from ui.deposit_dynamics import render_deposit_dynamics
    render_deposit_dynamics(t)


def _market_share(t, ctx):
    from ui.deposit_lookup import render_market_share_for_ticker
    render_market_share_for_ticker(t)


def _ownership_13f(t, ctx):
    from ui.ownership import render_ownership
    render_ownership(t)


def _insider_activity(t, ctx):
    from ui.insider_activity import render_insider_activity
    render_insider_activity(t)


_RENDERERS = {
    "Corporate Profile": _corporate_profile,
    "Price & Trends": _price_trends,
    "Financial Highlights": _financial_highlights,
    "Capital Adequacy": _capital_adequacy,
    "Asset Quality Detail": _asset_quality,
    "Asset Quality by Loan Type": _asset_quality_by_loan_type,
    "Deposit/Loan Composition": _deposit_loan_composition,
    "Interest Rate Risk": _interest_rate_risk,
    "Income Statement": _income_statement,
    "Balance Sheet": _balance_sheet,
    "Performance Analysis": _performance_analysis,
    "Fair Value Analysis": _fair_value,
    "Portfolio Analysis": _portfolio,
    "Capital Structure Details": _capital_structure,
    "Valuation Model": _valuation_model,
    "Peer Rank": _peer_rank,
    "Earnings": _earnings,
    "Filings & Reports": _filings,
    "Key Exhibits": _key_exhibits,
    "Press Releases": _press_releases,
    "Transcripts & Presentations": _transcripts,
    "Events Calendar": _events_calendar,
    "Deposit Trends": _deposit_trends,
    "Market Share & Branches": _market_share,
    "Institutional (13F)": _ownership_13f,
    "Insider Activity": _insider_activity,
}


def render_company_subtab(subtab: str, ticker: str, ctx: dict) -> bool:
    """Dispatch a sub-tab to its renderer. Returns False when no renderer is
    wired (app.py shows an explicit error — never a silent blank page).

    ctx keys: watchlist (list[str]), load_metrics (ticker -> dict),
    peer_cohort (() -> list[dict])."""
    renderer = _RENDERERS.get(subtab)
    if renderer is None:
        return False
    renderer(ticker, ctx)
    return True
