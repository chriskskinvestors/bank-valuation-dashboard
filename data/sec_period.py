"""As-reported holding-company fundamentals for ONE specific reporting period.

The consensus-vs-actual comparison needs the figure a sell-side model is actually
estimating: the consolidated HOLDING COMPANY (HoldCo), as it reported it in its
SEC filing, for the SAME period as the estimate — a quarter like 2026Q2 or a full
year like 2026. This is the same basis as Financials → "Company Reported": SEC
companyfacts XBRL, which refreshes as the company's filings are processed (so the
actuals update when the filing hits, within the standard fundamentals cache).

Two traps this module is built around:

  * **YTD vs single quarter.** Income-statement values in XBRL are cumulative
    from the fiscal-year start: a 10-Q tags H1 (Jan–Jun), not Q2 alone. A naive
    "value ending 2026-06-30" picks the YTD figure and a one-quarter estimate gets
    scored against a half-year actual (the +284%-"beat" class of error). We take
    the standalone quarter by DIFFERENCING the cumulative legs: Q = YTD(q) −
    YTD(q−1). This is also the only way to get Q4, which companies never tag
    directly — it is FY − 9M.

  * **Not filed yet.** If the cumulative legs for the period aren't on file, the
    metric is absent; if nothing is, the whole period returns None and the UI
    shows n/a — never a guess (the cardinal rule).

Dollar values are raw dollars (companyfacts USD); eps is $/share.
"""
from __future__ import annotations

import re
from datetime import date

# us-gaap concept fallback chains — first concept with a usable value wins.
_NET_INCOME = ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
               "ProfitLoss")
_NONINT_INCOME = ("NoninterestIncome", "NoninterestIncomeLoss")
_NONINT_EXPENSE = ("NoninterestExpense",)
_PROVISION = ("ProvisionForCreditLosses", "ProvisionForLoanAndLeaseLosses",
              "ProvisionForLoanLeaseAndOtherLosses",
              "ProvisionForCreditLossExpenseReversal")
_EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
_NII_DIRECT = ("InterestIncomeExpenseNet",)
_INT_INCOME = ("InterestAndDividendIncomeOperating", "InterestIncome")
_INT_EXPENSE = ("InterestExpense",)

# Income-statement flows, keyed to the consensus-actual key space.
_FLOW = {
    "net_income": _NET_INCOME,
    "nonint_income": _NONINT_INCOME,
    "nonint_expense": _NONINT_EXPENSE,
    "provision": _PROVISION,
}
# Point-in-time balance-sheet stocks.
_INSTANT = {
    "total_deposits": ("Deposits", "DepositsDomestic"),
    "total_loans": ("LoansAndLeasesReceivableNetReportedAmount",
                    "LoansReceivableHeldForInvestmentNet", "NotesReceivableNet"),
    "total_assets": ("Assets",),
}

_Q_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
# 52/53-week fiscal calendars land period ends a few days off the calendar date;
# quarters are ~90 days apart, so this window can never match the wrong period.
_END_TOL_DAYS = 14
# A cumulative (YTD) leg starts at the fiscal-year start (~Jan 1); a standalone
# quarter starts mid-year. This separates the two.
_FYSTART_TOL_DAYS = 20
_FILINGS = ("10-K", "10-Q")


def _parse_period(period: str):
    """('Q', year, qtr) for a quarter, ('Y', year, None) for a year, else None.
    Accepts the canonical forms normalize_period() emits ('2026Q2', '2026')."""
    s = (period or "").strip().upper()
    m = re.fullmatch(r"(\d{4})Q([1-4])", s)
    if m:
        return ("Q", int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return ("Y", int(m.group(1)), None)
    return None


def _to_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _entries(facts: dict, concept: str, unit_types: tuple) -> list:
    ug = facts.get("facts", {}).get("us-gaap", {})
    units = (ug.get(concept) or {}).get("units", {}) or {}
    out = []
    for ut in unit_types:
        out.extend(units.get(ut, []) or [])
    return out


def _q_end(year: int, q: int) -> date:
    mm, dd = _Q_END[q]
    return date(year, mm, dd)


def _ytd(facts, concepts, year, q, unit_types):
    """Cumulative value from the fiscal-year start through quarter q's end — the
    YTD figure a 10-Q/10-K reports — or None. Identified by start ≈ Jan 1 of
    `year` and end at quarter q's end, most-recently-filed winning (restatements)."""
    end_target, fy_start = _q_end(year, q), date(year, 1, 1)
    for concept in concepts:
        cands = []
        for e in _entries(facts, concept, unit_types):
            if e.get("form") not in _FILINGS:
                continue
            end, start, val = _to_date(e.get("end")), _to_date(e.get("start")), e.get("val")
            if end is None or start is None or val is None:
                continue
            if abs((end - end_target).days) > _END_TOL_DAYS:
                continue
            if abs((start - fy_start).days) > _FYSTART_TOL_DAYS:
                continue            # keep only the cumulative (YTD) leg
            cands.append(e)
        if cands:
            cands.sort(key=lambda e: e.get("filed", ""), reverse=True)
            return cands[0].get("val")
    return None


def _standalone_quarter(facts, concepts, year, q, unit_types):
    """The directly-tagged standalone-quarter value (start mid-quarter, ~90-day
    span ending at q's end) — the company's own reported quarterly figure, when it
    tags one (typically Q1–Q3). None otherwise."""
    end_target = _q_end(year, q)
    for concept in concepts:
        cands = []
        for e in _entries(facts, concept, unit_types):
            if e.get("form") not in _FILINGS:
                continue
            end, start, val = _to_date(e.get("end")), _to_date(e.get("start")), e.get("val")
            if end is None or start is None or val is None:
                continue
            if abs((end - end_target).days) > _END_TOL_DAYS:
                continue
            if 80 <= (end - start).days <= 100:
                cands.append(e)
        if cands:
            cands.sort(key=lambda e: e.get("filed", ""), reverse=True)
            return cands[0].get("val")
    return None


def _quarter_by_diff(facts, concepts, year, q, unit_types):
    """Standalone quarter q via YTD differencing: YTD(q) − YTD(q−1), Q1 = YTD(1).
    The robust path — works for every quarter including Q4 (FY − 9M). None if a
    needed cumulative leg is missing."""
    cur = _ytd(facts, concepts, year, q, unit_types)
    if cur is None:
        return None
    if q == 1:
        return cur
    prev = _ytd(facts, concepts, year, q - 1, unit_types)
    if prev is None:
        return None
    return cur - prev


def _flow_for(facts, concepts, kind, year, q, unit_types=("USD",)):
    """Period value for an income-statement flow: the full-year cumulative for a
    year, else the standalone quarter (YTD-differenced, with the directly-tagged
    quarter as a fallback)."""
    if kind == "Y":
        return _ytd(facts, concepts, year, 4, unit_types)
    v = _quarter_by_diff(facts, concepts, year, q, unit_types)
    if v is None:
        v = _standalone_quarter(facts, concepts, year, q, unit_types)
    return v


def _eps_for(facts, kind, year, q):
    """Period diluted EPS. For a quarter, prefer the company's directly-tagged
    standalone EPS (Q1–Q3); Q4 / untagged falls back to FY − 9M differencing —
    the conventional back-out. For a year, the FY EPS."""
    ut = ("USD/shares",)
    if kind == "Y":
        return _ytd(facts, _EPS, year, 4, ut)
    v = _standalone_quarter(facts, _EPS, year, q, ut)
    if v is None:
        v = _quarter_by_diff(facts, _EPS, year, q, ut)
    return v


def _net_interest_income(facts, kind, year, q):
    """NII for the period: the directly-tagged net figure when present, else total
    interest income minus interest expense (the standard bank derivation), each
    over the same period. None if it can't be formed cleanly."""
    direct = _flow_for(facts, _NII_DIRECT, kind, year, q)
    if direct is not None:
        return direct
    income = _flow_for(facts, _INT_INCOME, kind, year, q)
    expense = _flow_for(facts, _INT_EXPENSE, kind, year, q)
    if income is not None and expense is not None:
        return income - expense
    return None


def _instant(facts, concepts, end_target, unit_types=("USD",)):
    """Most-recently-filed point-in-time value as of end_target (±tol)."""
    for concept in concepts:
        cands = []
        for e in _entries(facts, concept, unit_types):
            if e.get("form") not in _FILINGS:
                continue
            end, val = _to_date(e.get("end")), e.get("val")
            if end is None or val is None:
                continue
            if abs((end - end_target).days) > _END_TOL_DAYS:
                continue
            cands.append(e)
        if cands:
            cands.sort(key=lambda e: (e.get("end", ""), e.get("filed", "")), reverse=True)
            return cands[0].get("val")
    return None


def fundamentals_for_period(cik, period: str) -> dict | None:
    """As-reported HoldCo actuals for (cik, period) in the metrics key space, or
    None if the period has not been filed yet. $-amounts are raw dollars; eps is
    $/share. Covers net income, NII, fee income, expense, provision, EPS, deposits,
    loans, assets — each present only when the company tagged it for this exact
    period (absent ⇒ n/a, never a guess)."""
    parsed = _parse_period(period)
    if parsed is None or not cik:
        return None
    kind, year, q = parsed
    end_target = _q_end(year, q) if kind == "Q" else date(year, 12, 31)

    from data.sec_client import fetch_company_facts
    facts = fetch_company_facts(int(cik))
    if not facts:
        return None

    out: dict = {}
    for key, concepts in _FLOW.items():
        v = _flow_for(facts, concepts, kind, year, q)
        if v is not None:
            out[key] = v
    nii = _net_interest_income(facts, kind, year, q)
    if nii is not None:
        out["net_interest_income"] = nii
    eps = _eps_for(facts, kind, year, q)
    if eps is not None:
        out["eps"] = eps
    for key, concepts in _INSTANT.items():
        v = _instant(facts, concepts, end_target)
        if v is not None:
            out[key] = v
    return out or None
