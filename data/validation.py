"""
Data quality validation for the portal.

Runs after data is fetched but before it's displayed. Catches:
  1. Range violations (e.g., CET1 > 30% or < 4% is suspicious)
  2. Staleness (e.g., an XBRL concept whose latest filing is > 2 years old)
  3. Cross-source reconciliation (SEC HoldCo vs FDIC sub-bank values
     where they should relate in known ways)
  4. Internal consistency (e.g., loans_to_deposits > 0 and < 2)
  5. Missing critical fields

Output: list of Finding objects with severity and explanation.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Finding:
    severity: str            # "error" | "warning" | "info"
    field: str               # metric key or concept name
    message: str             # human-readable explanation
    value: object = None     # the problematic value
    source: str = ""         # data source (e.g., "SEC", "FDIC")


# ───── RANGE RULES ─────────────────────────────────────────────────────
# Sanity bounds for key metrics. Anything outside (min, max) raises a warning.
# These are intentionally wide — we want to catch outliers, not stifle real
# banks with unusual profiles.
RANGE_RULES = {
    # Capital ratios. Some small banks/thrifts run 25-35% capital ratios
    # (very over-capitalized), and some FDIC rows report 0 for missing fields
    # rather than null — so we skip 0 (let the UI show "—") and widen bounds.
    "cet1_ratio":        {"min": 3.0,   "max": 40.0, "unit": "%", "skip_if_zero": True},
    "leverage_ratio":    {"min": 3.0,   "max": 25.0, "unit": "%", "skip_if_zero": True},
    "total_capital_ratio": {"min": 5.0, "max": 40.0, "unit": "%", "skip_if_zero": True},

    # Profitability. Best-in-class community banks like FFIN can hit
    # 35-40% ROATCE legitimately (low TCE + strong NI).
    "roatce":            {"min": -30.0, "max": 45.0, "unit": "%"},
    "roatce_holdco":     {"min": -30.0, "max": 45.0, "unit": "%"},
    "roatce_4q":         {"min": -30.0, "max": 45.0, "unit": "%"},
    "roaa":              {"min": -3.0,  "max": 3.5,  "unit": "%"},
    "nim":               {"min": 0.5,   "max": 7.0,  "unit": "%"},
    "efficiency_ratio":  {"min": 20.0,  "max": 100.0, "unit": "%"},

    # Credit
    "npl_ratio":         {"min": 0.0,   "max": 10.0, "unit": "%"},
    "nco_ratio":         {"min": -0.5,  "max": 5.0,  "unit": "%"},

    # Deposits
    "uninsured_pct":     {"min": 5.0,   "max": 75.0, "unit": "%"},
    "nonint_dep_pct":    {"min": 0.0,   "max": 60.0, "unit": "%"},
    "brokered_pct":      {"min": 0.0,   "max": 50.0, "unit": "%"},

    # Valuation
    "pe_ratio":          {"min": 0.0,   "max": 50.0, "unit": "x"},
    "ptbv_ratio":        {"min": 0.1,   "max": 8.0,  "unit": "x"},
    "fair_ptbv":         {"min": 0.0,   "max": 6.0,  "unit": "x"},
    "dividend_yield":    {"min": 0.0,   "max": 12.0, "unit": "%"},
    "shareholder_yield": {"min": 0.0,   "max": 25.0, "unit": "%"},

    # Size sanity
    "market_cap":        {"min": 1e7,   "max": 1e13, "unit": "$"},   # $10M to $10T
    "total_assets":      {"min": 1e7,   "max": 1e13, "unit": "$"},
    "total_deposits":    {"min": 1e7,   "max": 1e13, "unit": "$"},
    "total_equity":      {"min": 1e6,   "max": 1e12, "unit": "$"},
    "total_loans":       {"min": 0,     "max": 1e13, "unit": "$"},

    # Shares
    # 1M to 15B is a realistic band for public US bank share counts
    # (smallest thrift at ~1M shares, JPM/BAC/WFC at 2.7-7.5B)
}


def check_range(key: str, value) -> Finding | None:
    """Range check a value against its rule, return a Finding if violated."""
    rule = RANGE_RULES.get(key)
    if not rule or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Some FDIC fields report 0 for "not reported". Skip validation on those.
    if rule.get("skip_if_zero") and v == 0:
        return None
    if v < rule["min"] or v > rule["max"]:
        return Finding(
            severity="warning",
            field=key,
            message=(
                f"{key} = {v:.2f} is outside expected range "
                f"[{rule['min']}, {rule['max']}] {rule['unit']}. "
                "Verify against primary source."
            ),
            value=v,
        )
    return None


# ───── CROSS-SOURCE RECONCILIATION ────────────────────────────────────

def cross_check_equity(sec_holdco_equity: float | None,
                        fdic_sub_equity: float | None,
                        ticker: str = "") -> list[Finding]:
    """
    Verify SEC HoldCo vs FDIC sub-bank equity relationship.

    HoldCo equity can legitimately be BELOW sub-bank equity because:
    - HoldCo issues subordinated debt / trust preferred securities
    - Treasury stock from HoldCo buybacks reduces HoldCo equity
    - Minority-interest treatment differences
    These are normal for bank holding companies, especially ones with
    capital-markets activity.

    Thresholds (tuned against real Q4 2025 data across ~380 US bank holdcos):
    - HoldCo within ±5% of sub-bank: fully expected
    - HoldCo 5-30% below sub: legit (holdco debt + buybacks), no flag
    - HoldCo 30-60% below sub: unusual but possible, WARNING
    - HoldCo >60% below sub: very likely wrong CIK mapping, ERROR
    - HoldCo >100% above sub: flag for manual review (diversified HoldCo)
    """
    findings = []
    if sec_holdco_equity is None or fdic_sub_equity is None:
        return findings
    if sec_holdco_equity <= 0 or fdic_sub_equity <= 0:
        return findings
    gap_pct = (sec_holdco_equity - fdic_sub_equity) / fdic_sub_equity * 100

    if gap_pct < -60:
        findings.append(Finding(
            severity="error",
            field="equity_reconciliation",
            message=(
                f"HoldCo equity (${sec_holdco_equity/1e9:.2f}B) is "
                f"{abs(gap_pct):.0f}% below sub-bank equity (${fdic_sub_equity/1e9:.2f}B). "
                "Gap this large is almost certainly a wrong CIK mapping — verify entity."
            ),
        ))
    elif gap_pct < -30:
        findings.append(Finding(
            severity="warning",
            field="equity_reconciliation",
            message=(
                f"HoldCo equity (${sec_holdco_equity/1e9:.2f}B) is "
                f"{abs(gap_pct):.0f}% below sub-bank equity. Unusual — possible "
                "large holdco debt, buybacks, or stale reporting. Verify."
            ),
        ))
    elif gap_pct > 100:
        findings.append(Finding(
            severity="warning",
            field="equity_reconciliation",
            message=(
                f"HoldCo equity (${sec_holdco_equity/1e9:.2f}B) is "
                f"{gap_pct:.0f}% above sub-bank equity. Likely diversified "
                "holding company; verify non-bank operations."
            ),
        ))
    return findings


def cross_check_net_income(sec_ni: float | None, fdic_ni_ytd: float | None,
                             quarter: int | None) -> list[Finding]:
    """
    Verify SEC HoldCo NI vs FDIC sub-bank NI. Both should be positive and
    related (HoldCo includes sub + non-bank). If signs disagree, flag.
    """
    findings = []
    if sec_ni is None or fdic_ni_ytd is None:
        return findings
    # Annualize FDIC YTD
    if quarter and quarter > 0:
        fdic_ni_annualized = fdic_ni_ytd * (4 / quarter)
    else:
        fdic_ni_annualized = fdic_ni_ytd

    # Sign check
    if sec_ni > 0 and fdic_ni_annualized < -abs(sec_ni) * 0.3:
        findings.append(Finding(
            severity="warning",
            field="ni_reconciliation",
            message=(
                "Sub-bank NI is significantly negative while HoldCo NI is positive. "
                "Check for non-bank segment gains offsetting bank losses."
            ),
        ))
    return findings


def cross_check_assets(sec_holdco_assets: float | None,
                        fdic_sub_assets: float | None) -> list[Finding]:
    """
    Verify SEC HoldCo total assets vs FDIC sub-bank total assets.

    A bank holding company consolidates the bank, so HoldCo assets should be
    within a modest band of sub-bank assets for most BHCs (the vast majority
    are ~100% bank). A large mismatch is almost always one of:
      - a unit error (thousands vs dollars → ~99% or ~10,000% gap)
      - a wrong CIK mapping (HoldCo assets a fraction of the bank's)
    Both are exactly what we want to catch.

    Inputs must be in the SAME units (raw USD). Caller scales FDIC ×1000.
    """
    findings: list[Finding] = []
    if not sec_holdco_assets or not fdic_sub_assets:
        return findings
    if sec_holdco_assets <= 0 or fdic_sub_assets <= 0:
        return findings
    gap_pct = (sec_holdco_assets - fdic_sub_assets) / fdic_sub_assets * 100

    if gap_pct < -50:
        findings.append(Finding(
            severity="error",
            field="assets_reconciliation",
            message=(
                f"HoldCo assets (${sec_holdco_assets/1e9:.1f}B) are "
                f"{abs(gap_pct):.0f}% below sub-bank assets (${fdic_sub_assets/1e9:.1f}B). "
                "A gap this large points to a unit error or wrong CIK mapping."
            ),
        ))
    elif abs(gap_pct) > 35:
        findings.append(Finding(
            severity="warning",
            field="assets_reconciliation",
            message=(
                f"HoldCo assets (${sec_holdco_assets/1e9:.1f}B) differ "
                f"{gap_pct:+.0f}% from sub-bank assets (${fdic_sub_assets/1e9:.1f}B). "
                "Verify entity mapping / material non-bank assets."
            ),
        ))
    return findings


def check_loan_composition(fdic_data: dict) -> list[Finding]:
    """
    Internal consistency on the loan book: no top-level segment can exceed
    total loans, and the mutually-exclusive top-level categories shouldn't
    sum to materially more than gross loans. A unit error in one segment
    field blows its ratio past 1.0 — a strong tripwire.

    Uses raw FDIC values (thousands); all comparisons are ratios so the
    common scale cancels out.
    """
    findings: list[Finding] = []
    gross = fdic_data.get("LNLSGR") or fdic_data.get("LNLSNET")
    if not gross or gross <= 0:
        return findings

    # Mutually-exclusive top-level FDIC loan categories.
    segments = {
        "real estate (LNRE)": fdic_data.get("LNRE"),
        "C&I (LNCI)": fdic_data.get("LNCI"),
        "consumer (LNCON)": fdic_data.get("LNCON"),
        "agricultural (LNAG)": fdic_data.get("LNAG"),
    }
    for label, v in segments.items():
        if v is not None and v > gross * 1.02:
            findings.append(Finding(
                severity="error",
                field="loan_composition",
                message=(
                    f"Loan segment {label} exceeds gross loans "
                    f"({v/max(gross,1)*100:.0f}% of total). Almost certainly a "
                    "unit mismatch in this field."
                ),
                value=v,
            ))
    present = [v for v in segments.values() if v is not None]
    if present:
        s = sum(present)
        if s > gross * 1.05:
            findings.append(Finding(
                severity="warning",
                field="loan_composition",
                message=(
                    f"Top-level loan segments sum to {s/gross*100:.0f}% of gross "
                    "loans (>105%). Possible double-count or unit error."
                ),
            ))
    return findings


def check_deposit_composition(fdic_data: dict) -> list[Finding]:
    """
    Internal consistency on deposits: insured+uninsured should ≈ total, and
    interest-bearing + non-interest-bearing (domestic) should be ≤ total.
    A unit error in one component blows the sum past total deposits.

    Uses raw FDIC values (thousands); comparisons are ratios.
    """
    findings: list[Finding] = []
    total = fdic_data.get("DEP")
    if not total or total <= 0:
        return findings

    pairs = [
        ("insured+uninsured", fdic_data.get("DEPINS"), fdic_data.get("DEPUNINS"), True),
        ("IB+NIB (domestic)", fdic_data.get("DEPIDOM"), fdic_data.get("DEPNIDOM"), False),
    ]
    for label, a, b, check_low in pairs:
        if a is None or b is None:
            continue
        ratio = (a + b) / total
        if ratio > 1.05:
            findings.append(Finding(
                severity="warning",
                field="deposit_composition",
                message=(
                    f"Deposit split '{label}' sums to {ratio*100:.0f}% of total "
                    "deposits (>105%). Possible unit error or double-count."
                ),
            ))
        elif check_low and ratio < 0.90:
            # Domestic IB+NIB can legitimately run below total DEP (foreign
            # deposits), so only the insured+uninsured pair triggers the low side.
            findings.append(Finding(
                severity="warning",
                field="deposit_composition",
                message=(
                    f"Insured+uninsured deposits sum to only {ratio*100:.0f}% of "
                    "total deposits (<90%). Check for a missing component field."
                ),
            ))
    return findings


def _coerce_date_str(d) -> str | None:
    """Normalize a date-like value (datetime, 'YYYY-MM-DD', 'YYYYMMDD') to
    'YYYY-MM-DD' for check_staleness. Returns None if unparseable."""
    if d is None:
        return None
    if hasattr(d, "strftime"):
        try:
            return d.strftime("%Y-%m-%d")
        except Exception:
            return None
    s = str(d).strip()
    if len(s) >= 10 and s[4:5] == "-":
        return s[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


# ───── STALENESS ──────────────────────────────────────────────────────

def check_staleness(as_of: str | None, max_age_days: int,
                      field_name: str = "") -> Finding | None:
    """Flag a data point as stale if older than max_age_days."""
    if not as_of:
        return None
    try:
        d = datetime.strptime(as_of, "%Y-%m-%d")
        age = (datetime.now() - d).days
    except Exception:
        # An unparseable date means the staleness check is being skipped —
        # the validator itself failing open. Log it so it's visible.
        print(f"[validation] unparseable as_of date {as_of!r} for "
              f"{field_name or 'unknown field'} — staleness check skipped")
        return None

    if age > max_age_days:
        return Finding(
            severity="warning" if age < max_age_days * 2 else "error",
            field=field_name,
            message=f"Data is {age} days old (max expected: {max_age_days}).",
        )
    return None


# ───── FULL VALIDATION ─────────────────────────────────────────────────

def validate_bank_metrics(metrics: dict, sec_data: dict | None = None,
                            fdic_data: dict | None = None) -> list[Finding]:
    """Run all validation checks on a bank's computed metrics."""
    findings = []

    # Range checks on every known metric
    for key, value in metrics.items():
        finding = check_range(key, value)
        if finding:
            findings.append(finding)

    # Cross-source: equity reconciliation
    if sec_data and fdic_data:
        sec_eq = sec_data.get("book_value_total")
        fdic_eq = fdic_data.get("EQTOT")
        if fdic_eq:
            fdic_eq = fdic_eq * 1000  # thousands to dollars
        findings.extend(cross_check_equity(sec_eq, fdic_eq))

        # NI cross-check
        sec_ni = sec_data.get("net_income")
        fdic_ni = fdic_data.get("NETINC")
        if fdic_ni:
            fdic_ni = fdic_ni * 1000

        # Infer quarter from REPDTE
        quarter = None
        repdte = fdic_data.get("REPDTE")
        if repdte:
            try:
                if hasattr(repdte, "month"):
                    quarter = (repdte.month - 1) // 3 + 1
                else:
                    s = str(repdte)
                    m = int(s.split("-")[1]) if "-" in s else int(s[4:6])
                    quarter = (m - 1) // 3 + 1
            except Exception:
                pass
        findings.extend(cross_check_net_income(sec_ni, fdic_ni, quarter))

        # Cross-source: total-assets reconciliation (catches unit errors and
        # wrong CIK mappings — a unit mismatch shows up as a ~99% / ~10,000% gap)
        sec_assets = sec_data.get("total_assets_sec")
        fdic_assets = fdic_data.get("ASSET")
        if fdic_assets:
            fdic_assets = fdic_assets * 1000  # thousands to dollars
        findings.extend(cross_check_assets(sec_assets, fdic_assets))

    # Internal consistency: loan & deposit composition must sum sensibly.
    # These run off raw FDIC fields (ratios, so scale-independent) and are
    # strong tripwires for a single mis-scaled field.
    if fdic_data:
        findings.extend(check_loan_composition(fdic_data))
        findings.extend(check_deposit_composition(fdic_data))

    # Staleness — flag data that's fallen behind. FDIC Call Reports are
    # quarterly and post ~45 days after quarter-end, so >135 days means we've
    # missed a full quarter. SEC filings: >200 days (missed a 10-Q cycle).
    if fdic_data:
        f = check_staleness(
            _coerce_date_str(fdic_data.get("REPDTE")),
            max_age_days=135, field_name="fdic_call_report")
        if f:
            findings.append(f)
    if sec_data:
        f = check_staleness(
            _coerce_date_str(sec_data.get("sec_as_of")),
            max_age_days=200, field_name="sec_filings")
        if f:
            findings.append(f)

    # Internal consistency
    ltd = metrics.get("loans_to_deposits")
    if ltd is not None and (ltd < 20 or ltd > 180):
        findings.append(Finding(
            severity="warning",
            field="loans_to_deposits",
            message=f"Loans/Deposits = {ltd:.0f}% is unusual. Expected 40-150% for most banks.",
            value=ltd,
        ))

    # Share count sanity (most impactful since TBV depends on it).
    # Lower bound loosened to 100K to accommodate very small thrifts and
    # recently-IPO'd community banks.
    shares = metrics.get("shares_outstanding") or (sec_data or {}).get("shares_outstanding")
    if shares is not None:
        if shares < 1e5:
            findings.append(Finding(
                severity="error",
                field="shares_outstanding",
                message=f"Share count = {shares:,.0f} is suspiciously low. Verify XBRL concept.",
                value=shares,
            ))
        elif shares < 5e5:
            findings.append(Finding(
                severity="warning",
                field="shares_outstanding",
                message=(
                    f"Share count = {shares:,.0f} is unusually small. "
                    "Verify — may be a thrift / recent IPO."
                ),
                value=shares,
            ))
        elif shares > 2e10:
            findings.append(Finding(
                severity="error",
                field="shares_outstanding",
                message=(
                    f"Share count = {shares:,.0f} is suspiciously high (>20B). "
                    "Possible stale XBRL concept (e.g., pre-reverse-split data)."
                ),
                value=shares,
            ))

    return findings


def summary(findings: list[Finding]) -> dict:
    """Quick summary: {errors, warnings, info, total}."""
    by_severity = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
    return {
        "errors": by_severity["error"],
        "warnings": by_severity["warning"],
        "info": by_severity["info"],
        "total": len(findings),
    }
