"""
Release-anchored composite TTM diluted EPS (release-first increment 2).

OWNER DECISION (2026-08-19): the site's EPS stays TTM-basis (a single
release quarter served as "EPS" makes P/E ~4x too high — the WAL trap), but
between a bank's earnings release and its 10-Q the XBRL TTM is one quarter
behind. The composite closes that window:

    composite TTM = release quarter's diluted EPS (data/release_metrics —
                    the ONLY release extractor with PERIOD PROOF: qend from
                    the filing date, table columns identified by period
                    header, ambiguity refused)
                  + the prior THREE discrete single quarters from the bank's
                    own Company-Reported income statements
                    (data/sec_statements.as_reported_statement_multiquarter).

THE FISCAL-Q4 COMPONENT: one of any three consecutive prior quarters is a
fiscal Q4, and the CR stitch deliberately leaves per-share Q4 cells blank
(differencing FY − 9M per-share values is refused for DISPLAY — the PGC
lesson). Inside a TTM sum, though, the same-start YTD difference is the
established house construction: data/sec_client._extract_ttm_value builds
the XBRL TTM EPS exactly that way (FY − 9M = Q4). So a prior quarter the CR
statement cannot supply is filled from the XBRL quarterly construction
(_xbrl_quarterly_eps below) — the SAME 10-Q/10-K filings via companyfacts,
per-component provenance recorded. Without this fill the composite could
only ever assemble for fiscal-Q4 releases (one window in four).

ALL FOUR components must exist — a 3-quarter sum labeled TTM is exactly the
A21 defect class (a plausible-wrong number), so any missing component means
(None, None, None), never a partial sum. A release whose quarter-end is not
the expected most-recent completed quarter is STALE (the bank hasn't
reported yet, or stopped reporting) and is refused the same way.

Deliberately NOT data/sec_earnings_8k.latest_earnings_8k_figures: that
extractor takes nums[0] with NO period proof — a Q2/Q3 release leading with
the YTD column would silently supply YTD EPS (documented trap).

The resolution (release-first vs XBRL TTM, ±15% band, eps_conflict) lives in
analysis/valuation._resolve_eps; this module only assembles the composite.
"""

from __future__ import annotations

import re

# Income-statement diluted-EPS label alternatives — keep in sync with the
# derivation in ui/financials_statements._cr_highlights_by_year (the proven
# Company-Reported per-quarter EPS lookup; duplicated here because analysis
# must never import ui — that would pull streamlit into the Cloud Run jobs).
_EPS_LABELS = (
    "diluted earnings per common share (in dollars per share)",
    "diluted earnings per share (in dollars per share)",
    "diluted earnings per common share (in usd per share)",
    "diluted earnings per share (in usd per share)",
    "diluted earnings per common share",
    "diluted earnings per share",
    "earnings per common share - diluted (in dollars per share)",
    "earnings per common share - diluted (in usd per share)",
)

# In-process memo: the inputs (release_metrics, the multiquarter stitch) are
# already cache-backed, so this only avoids re-assembling within one build.
# Keyed by (release accession, expected quarter-end) — a new release or a
# date rollover invalidates. Cleared by tests.
_MEMO: dict[int, tuple] = {}


def _norm(s: str) -> str:
    """Statement-label normalization — same convention as
    ui/financials_statements._cr_hl_norm (collapse whitespace, lowercase,
    fold curly apostrophes)."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower().replace(
        "’", "'").replace("‘", "'")


def _expected_release_qend() -> str | None:
    """The most-recent completed calendar quarter-end (ISO) as of today —
    the ONLY quarter a fresh earnings release can cover. Same helper the
    release pipeline uses to attribute a release to its quarter."""
    from datetime import date

    from data.ir_provider import _quarter_end_before
    return _quarter_end_before(date.today().isoformat())


_QE_DAY = {3: 31, 6: 30, 9: 30, 12: 31}


def _prior_quarters(qend_iso: str, n: int = 3) -> list[tuple[str, str]] | None:
    """The n quarters immediately BEFORE qend_iso, most recent first, as
    (statement column label, ISO quarter-end) pairs — the label follows the
    data/sec_statements _q_label convention (the CR stitch's column key);
    the ISO end keys the XBRL fill. None if qend_iso doesn't parse or a
    prior end month isn't a calendar quarter-end (off-cycle fiscal years
    can't be labeled honestly — refuse, never guess)."""
    from data.sec_statements import _minus_quarter, _q_label
    try:
        y, m = int(qend_iso[:4]), int(qend_iso[5:7])
    except (TypeError, ValueError):
        return None
    out, qe = [], (y, m)
    for _ in range(n):
        qe = _minus_quarter(qe)
        yy, mm = qe
        if mm not in _QE_DAY:
            return None
        out.append((_q_label(qe), f"{yy:04d}-{mm:02d}-{_QE_DAY[mm]:02d}"))
    return out


def _xbrl_quarterly_eps(cik_i: int) -> dict[str, float]:
    """Discrete quarterly diluted EPS keyed by ISO period-end, from SEC
    companyfacts: direct ~3-month facts plus same-start YTD differences
    (FY − 9M = Q4, 9M − H1 = Q3, H1 − Q1 = Q2) — the SAME construction
    data/sec_client._extract_ttm_value uses for the XBRL TTM this composite
    is cross-checked against. Direct facts always beat derived ones;
    restatements win by filing date (dedup by (start, end), latest filed).

    Mirrored rather than imported: sec_client keeps the quarters dict
    internal to its TTM extractor, and refactoring the golden-verified
    extractor for this fill would risk the anchor the composite is gated
    against."""
    from datetime import datetime

    from data.sec_client import fetch_company_facts
    facts = fetch_company_facts(cik_i) or {}
    units = facts.get("facts", {}).get("us-gaap", {}).get(
        "EarningsPerShareDiluted", {}).get("units", {})

    durations: dict[tuple[str, str], dict] = {}
    for e in units.get("USD/shares", []):
        if e.get("form") not in ("10-K", "10-Q"):
            continue
        start, end, val = e.get("start"), e.get("end"), e.get("val")
        if not start or not end or val is None:
            continue
        try:
            span = (datetime.fromisoformat(end)
                    - datetime.fromisoformat(start)).days
        except ValueError:
            continue
        prev = durations.get((start, end))
        if prev is None or e.get("filed", "") > prev["filed"]:
            durations[(start, end)] = {"val": val, "span": span,
                                       "filed": e.get("filed", "")}

    def _gap_days(a: str, b: str) -> int:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days

    quarters: dict[str, float] = {
        end: d["val"] for (start, end), d in durations.items()
        if 80 <= d["span"] <= 100
    }
    for (s1, e1), d1 in durations.items():
        if d1["span"] <= 100 or e1 in quarters:
            continue
        for (s2, e2), d2 in durations.items():
            if s2 != s1 or e2 >= e1:
                continue
            if 80 <= _gap_days(e2, e1) <= 100:
                quarters[e1] = d1["val"] - d2["val"]
                break
    return quarters


def composite_ttm_eps(cik) -> tuple[float | None, str | None, dict | None]:
    """(composite TTM diluted EPS, quarter-end it runs through, components)
    or (None, None, None) whenever any of the four quarters is missing.

    components = {"release": {"qend", "eps", "ni_common", "dil_shares",
                              "accession", "url"},
                  "quarters": {statement label: single-quarter diluted EPS}}
    — provenance for every input, so a served composite is auditable.
    """
    if not cik:
        return None, None, None
    try:
        cik_i = int(cik)
    except (TypeError, ValueError):
        return None, None, None

    from data.release_metrics import release_metrics
    rm = release_metrics(cik_i)
    if not rm:
        return None, None, None

    expected = _expected_release_qend()
    memo_key = (rm.get("accession"), expected)
    hit = _MEMO.get(cik_i)
    if hit and hit[0] == memo_key:
        return hit[1]

    result = _assemble(cik_i, rm, expected)
    _MEMO[cik_i] = (memo_key, result)
    return result


def _assemble(cik_i: int, rm: dict, expected: str | None):
    qend = rm.get("qend")
    rel_metrics = rm.get("metrics") or {}
    rel_eps = rel_metrics.get("eps_diluted")
    if rel_eps is None or not qend:
        return None, None, None
    # STALE-RELEASE GATE: the release must cover the expected most-recent
    # completed quarter. An older qend means the bank hasn't reported that
    # quarter (or stopped reporting) — a composite anchored on it would be
    # a mislabeled TTM, so refuse (the XBRL TTM is the honest fallback).
    if expected is None or qend != expected:
        return None, None, None

    priors = _prior_quarters(qend, 3)
    if not priors:
        return None, None, None

    from data.sec_statements import as_reported_statement_multiquarter
    # n_quarters=8 on purpose: the Company-Reported quarterly views stitch 8,
    # and the cache key includes n_quarters — reusing 8 shares their entry.
    mq = as_reported_statement_multiquarter(cik_i, "income", 8)
    stmt = (mq or {}).get("statement") or {}
    periods = list(stmt.get("periods") or [])
    series = {_norm(r["label"]): r["values"]
              for r in (stmt.get("rows") or []) if not r.get("header")}

    def _cr_eps_at(label: str) -> float | None:
        if label not in periods:
            return None
        ci = periods.index(label)
        for alt in _EPS_LABELS:
            vals = series.get(alt)
            if vals and ci < len(vals) and vals[ci] is not None:
                return vals[ci]
        return None

    # Company-Reported statement value first; the XBRL quarterly
    # construction fills only what the stitch structurally cannot supply
    # (the fiscal-Q4 per-share cell — see the module docstring).
    quarters: dict[str, float | None] = {}
    sources: dict[str, str] = {}
    for label, _iso in priors:
        v = _cr_eps_at(label)
        if v is not None:
            quarters[label], sources[label] = v, "company_reported"
        else:
            quarters[label] = None
    missing = [(lab, iso) for lab, iso in priors if quarters[lab] is None]
    if missing:
        xq = _xbrl_quarterly_eps(cik_i)
        for label, iso in missing:
            v = xq.get(iso)
            if v is not None:
                quarters[label], sources[label] = v, "xbrl"
    if any(v is None for v in quarters.values()):
        # A21 discipline: fewer than four quarters is NOT a TTM — n/a, never
        # a partial sum.
        return None, None, None

    value = rel_eps + sum(quarters.values())
    components = {
        "release": {"qend": qend, "eps": rel_eps,
                    # Tie-out inputs from the SAME release (may be None —
                    # analysis/valuation._release_eps_tie_out only speaks
                    # when both are present): NI applicable to common in
                    # raw dollars, weighted average diluted shares as the
                    # raw PRINTED count (unknown scale; the tie-out tries
                    # ×1/×1e3/×1e6).
                    "ni_common": rel_metrics.get("ni_common"),
                    "dil_shares": rel_metrics.get("dil_shares"),
                    "accession": rm.get("accession"), "url": rm.get("url")},
        "quarters": quarters,
        "quarter_sources": sources,
    }
    return value, qend, components
