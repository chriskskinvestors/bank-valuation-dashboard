"""Period-matched reported actuals for the consensus-vs-actual comparison.

A consensus estimate is for a SPECIFIC period — a quarter like 2026Q2 or a full
year like 2026. Comparing it to the bank's latest trailing/annualized snapshot is
a category error: a single-quarter EPS estimate held up against a trailing-twelve-
month actual reads as a +284% "beat", and the snapshot exists even for a quarter
that hasn't happened yet, so a future period showed actuals at all. Both are
cardinal-rule violations (a plausible-wrong number).

`period_actuals(ticker, period)` returns the company's AS-REPORTED figures for THE
SAME period, sourced from SEC companyfacts — the consolidated HOLDING COMPANY
basis that sell-side models estimate and that Financials → "Company Reported"
shows, so the actuals match the estimate's entity and refresh as the company's
filings are processed. None when the period has not been filed yet ⇒ the UI shows
n/a instead of a guess.

Source rationale: the broker estimates the public HoldCo, so the actual must be
the HoldCo too — NOT the FDIC bank-subsidiary call report (a different entity, the
old mismatch). We deliberately do NOT fall back to FDIC for a missing concept:
mixing entities is exactly how a wrong-but-plausible number sneaks in. A metric
the company didn't tag for the period is simply n/a.
"""
from __future__ import annotations

from data.bank_mapping import get_bank_info
from data.sec_period import fundamentals_for_period


def period_actuals(ticker: str, period: str) -> dict | None:
    """As-reported HoldCo actuals for (ticker, period) in the metrics key space, or
    None if the period has not been filed yet. $-amounts are raw dollars and eps is
    $/share — the basis compare_consensus_to_actual expects."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        return None
    return fundamentals_for_period(cik, period)
