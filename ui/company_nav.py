"""
Company Analysis navigation as data: sections → sub-tabs → renderers.

One registry drives both the nav radios in app.py and the dispatch, so a
sub-tab cannot exist without a renderer (or a renderer without a nav entry)
— the A17 bug class (two tabs silently rendering the same view, or a tab
rendering nothing) is structurally impossible and pinned by a test.

Financials is special: it has a basis layer — Company Reported (scraped from
the company's own filings, exactly how they report it) vs Templated (FDIC call
reports, uniform across banks). The SAME leaf name (e.g. "Income Statement")
exists under both bases with DIFFERENT renderers, so Financials dispatch is
basis-aware: Company Reported → _CR_RENDERERS, everything else → _RENDERERS.

Renderer signature: fn(ticker, ctx). ctx carries the few app.py-scope
dependencies (see render_company_subtab). All heavy imports stay lazy
inside the renderers, matching the old inline-elif behavior — importing
this module is cheap and safe for tests.
"""
from __future__ import annotations

COMPANY_NAV = {
    "Overview": ["Corporate Profile"],
    "Financials": {
        # FDIC call-report fields — uniform across every bank. Default basis (first):
        # fully built + fast, while Company Reported is still being filled in.
        "Templated": ["Financial Highlights", "Income Statement", "Balance Sheet",
                      "Performance Analysis", "Capital Adequacy", "Asset Quality Detail",
                      "Asset Quality by Loan Type", "Deposit/Loan Composition",
                      "Deposit Trends", "Portfolio Analysis", "Capital Structure Details",
                      "Interest Rate Risk"],
        # Scraped from the company's own filings — their labels, never n/a.
        "Company Reported": ["Financial Highlights", "Income Statement", "Balance Sheet",
                             "Performance Analysis", "Regulatory Capital",
                             "Credit Quality / Allowance", "Loan Composition",
                             "Deposit Composition", "Securities Portfolio", "Fair Value",
                             "Segment Reporting", "Interest Rate Risk"],
    },
    "Valuation": ["Valuation Model", "Peer Rank", "Price & Trends"],
    "Estimates / Earnings": ["Earnings"],
    "News & Filings": ["Filings & Reports", "Key Exhibits", "Press Releases",
                       "Transcripts & Presentations", "Events Calendar"],
    "Market Analysis": ["Market Share & Branches"],
    "Ownership": ["Institutional (13F)", "Insider Activity"],
}


def _all_leaves(val) -> list:
    """Leaves of a section value, whether it's a flat list or a basis dict."""
    if isinstance(val, dict):
        return [leaf for sub in val.values() for leaf in sub]
    return list(val)


# Flat list of every leaf (for deep-link validation, etc.) — may repeat a name
# that exists under both Financials bases; membership checks don't care.
COMPANY_LEAVES = [leaf for val in COMPANY_NAV.values() for leaf in _all_leaves(val)]
# Which section a leaf lives under (both Financials bases map to "Financials").
COMPANY_SECTION_OF = {leaf: sec for sec, val in COMPANY_NAV.items()
                      for leaf in _all_leaves(val)}


def resolve_url_bank(url_bank: str | None, applied: str | None) -> str | None:
    """Decide whether the URL's ?bank= should override the bank picker.

    Returns the bank to force into the picker, or None to leave the widget's
    value untouched.

    The URL wins ONLY on external navigation — a deep-link click, a shared
    link, or a refresh — which we detect as "the URL names a different bank
    than the one we last applied". On an ordinary widget-driven rerun the URL
    is briefly stale (app.py syncs URL <- widget only AFTER the picker renders),
    so forcing the picker to the URL value here would revert the user's fresh
    selection on every rerun and freeze the dropdown (the 2026-06-14 "can't
    change banks" bug). The caller MUST record the applied bank both here and
    when the widget drives a new selection, so a stale URL never re-clobbers.
    """
    if url_bank and url_bank != applied:
        return url_bank
    return None


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


# ── Company Reported renderers (Financials basis = "Company Reported") ──────
# Scraped from the company's own latest filing. The built ones render real data;
# the rest are placeholders until the company-reported pipeline lands. Each will
# source every value to a company document (never n/a) and the sub-tab will be
# hidden per-bank when the company doesn't disclose it.

def _cr_income(t, ctx):
    import streamlit as st
    from ui.financials_statements import _render_company_statement
    st.subheader("Income Statement — Company Reported")
    _render_company_statement(t, "income")


def _cr_balance(t, ctx):
    import streamlit as st
    from ui.financials_statements import _render_company_statement
    st.subheader("Balance Sheet — Company Reported")
    _render_company_statement(t, "balance")


def _cr_deposit(t, ctx):
    import streamlit as st
    from ui.financials_statements import _render_company_composition
    st.subheader("Deposit Composition — Company Reported")
    _render_company_composition(t, "deposit")


def _cr_loan(t, ctx):
    import streamlit as st
    from ui.financials_statements import _render_company_composition
    st.subheader("Loan Composition — Company Reported")
    _render_company_composition(t, "loan")


def _cr_fair_value(t, ctx):
    from ui.financials_statements import _render_fair_value_hierarchy
    _render_fair_value_hierarchy(t)


def _cr_securities(t, ctx):
    from ui.financials_statements import _render_securities_portfolio
    _render_securities_portfolio(t)


def _cr_credit(t, ctx):
    from ui.financials_statements import _render_credit_quality
    _render_credit_quality(t)


def _cr_performance(t, ctx):
    from ui.financials_statements import _render_performance
    _render_performance(t)


def _cr_highlights(t, ctx):
    from ui.financials_statements import _render_financial_highlights
    _render_financial_highlights(t)


def _cr_reg_capital(t, ctx):
    from ui.capital_dynamics import _render_holdco_capital
    _render_holdco_capital(t)


def _cr_todo(label):
    def _render(t, ctx):
        import streamlit as st
        st.info(f"**{label}** — Company-Reported view, sourced directly from the "
                f"company's own filings. Building now.")
    return _render


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

# Financials → Company Reported basis. Keyed by the same leaf names as the
# Templated list where they overlap, but pointing at the company-scrape views.
_CR_RENDERERS = {
    "Financial Highlights": _cr_highlights,
    "Income Statement": _cr_income,
    "Balance Sheet": _cr_balance,
    "Performance Analysis": _cr_performance,
    "Regulatory Capital": _cr_reg_capital,
    "Credit Quality / Allowance": _cr_credit,
    "Loan Composition": _cr_loan,
    "Deposit Composition": _cr_deposit,
    "Securities Portfolio": _cr_securities,
    "Fair Value": _cr_fair_value,
    "Segment Reporting": _cr_todo("Segment Reporting"),
    "Interest Rate Risk": _cr_todo("Interest Rate Risk"),
}


def render_company_subtab(subtab: str, ticker: str, ctx: dict, basis: str | None = None) -> bool:
    """Dispatch a sub-tab to its renderer. Returns False when no renderer is
    wired (app.py shows an explicit error — never a silent blank page).

    For Financials, `basis` is "Company Reported" or "Templated"; Company
    Reported dispatches through _CR_RENDERERS, everything else through
    _RENDERERS. ctx keys: watchlist (list[str]), load_metrics (ticker -> dict),
    peer_cohort (() -> list[dict])."""
    registry = _CR_RENDERERS if basis == "Company Reported" else _RENDERERS
    renderer = registry.get(subtab)
    if renderer is None:
        return False
    renderer(ticker, ctx)
    return True
