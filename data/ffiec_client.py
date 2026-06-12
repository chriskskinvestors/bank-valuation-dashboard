"""
FFIEC Call Report client.

Pulls per-bank Schedule RC-B Memorandum 2 (debt securities by remaining
maturity / repricing date) and Schedule RC-K (loan repricing buckets)
via the `ffiec-data-connect` package against FFIEC's CDR REST API.

Auth: 90-day JWT bearer token, generated from the user's free FFIEC CDR
account (Account Details tab). Token must be available as the env var
FFIEC_JWT_TOKEN (mounted from Google Secret Manager in Cloud Run) plus
the username as FFIEC_USERNAME.

If the token is missing or expired, all functions degrade gracefully —
the rate-sensitivity model falls back to its generic per-year repricing
assumptions.

Maturity buckets we extract (Call Report Schedule RC-B Memorandum 2):
  RCFD/RCONA549 — debt securities, remaining maturity ≤ 3 months
  RCFD/RCONA550 — > 3 months to 1 year
  RCFD/RCONA551 — > 1 year to 3 years
  RCFD/RCONA552 — > 3 years to 5 years
  RCFD/RCONA553 — > 5 years to 15 years
  RCFD/RCONA554 — > 15 years

Loan repricing (Schedule RC-K — closed-end first lien residential
mortgages, plus aggregates for all loans):
  Total loan repricing buckets aren't a single line — derived from the
  weighted average of LN* repricing fields. For now we extract:
  RCFD/RCON5369 — loans with floating rate (rate-sensitive within 1yr)
  to refine the floating_loan_share estimate.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import pandas as pd

# Module-level cache of the credentials object — building it is cheap but
# avoids re-reading env each call.
_creds_cache = None
_creds_cache_err: str | None = None


def _username() -> str:
    return (os.environ.get("FFIEC_USERNAME") or "").strip()


def _jwt_token() -> str:
    return (os.environ.get("FFIEC_JWT_TOKEN") or "").strip()


def is_configured() -> bool:
    """True if both username + JWT are present. Doesn't validate them."""
    return bool(_username() and _jwt_token())


def _get_creds():
    """
    Build an OAuth2Credentials once and cache it.

    Returns None if not configured or the package fails to import (e.g.
    in a local dev env where ffiec-data-connect isn't installed yet).
    """
    global _creds_cache, _creds_cache_err
    if _creds_cache is not None:
        return _creds_cache
    if _creds_cache_err:
        return None

    if not is_configured():
        _creds_cache_err = "FFIEC_USERNAME or FFIEC_JWT_TOKEN not set"
        return None

    try:
        from ffiec_data_connect import OAuth2Credentials
        _creds_cache = OAuth2Credentials(
            username=_username(),
            bearer_token=_jwt_token(),
        )
        return _creds_cache
    except ImportError as e:
        _creds_cache_err = f"ffiec-data-connect not installed: {e}"
        return None
    except Exception as e:
        _creds_cache_err = f"FFIEC creds init failed: {e}"
        return None


def health_check() -> dict:
    """For the Data Quality tab — confirm FFIEC wiring is healthy."""
    if not _username():
        return {"ok": False, "reason": "FFIEC_USERNAME not set"}
    if not _jwt_token():
        return {"ok": False, "reason": "FFIEC_JWT_TOKEN not set"}
    creds = _get_creds()
    if creds is None:
        return {"ok": False, "reason": _creds_cache_err or "creds init failed"}
    # JWT token's `exp` claim is auto-detected by the package. Surface
    # remaining days so we can warn before the 90-day expiry.
    try:
        import base64, json
        token = _jwt_token()
        parts = token.split(".")
        if len(parts) >= 2:
            # Add padding back
            payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get("exp")
            if exp:
                days_left = (exp - time.time()) / 86400
                return {
                    "ok": days_left > 0,
                    "username": _username(),
                    "days_until_expiry": round(days_left, 1),
                    "warning": "token expires in < 14 days"
                    if 0 < days_left < 14 else None,
                }
    except Exception:
        pass
    return {"ok": True, "username": _username()}


# ─────────────────────────────────────────────────────────────────────
# Reporting period helpers
# ─────────────────────────────────────────────────────────────────────

def latest_reporting_period(as_of: pd.Timestamp | None = None) -> str:
    """
    Return the most-recent Call Report period end-date that's likely
    available. Call Reports are due ~30 days after quarter-end, so:
      • Q1 (3/31) typically available by mid-May
      • Q2 (6/30) by mid-August
      • Q3 (9/30) by mid-November
      • Q4 (12/31) by mid-February

    Returns date string in MM/DD/YYYY format expected by the package.
    """
    # Strip tz so the arithmetic with tz-naive Timestamp(year,month,day)
    # below doesn't blow up — we only care about calendar dates here, not
    # wall-clock precision. pandas 2.x returns tz-aware from utcnow().
    now = as_of if as_of is not None else pd.Timestamp.utcnow()
    if getattr(now, "tzinfo", None) is not None:
        now = now.tz_localize(None)
    # Walk back through every quarter-end for the last 2 calendar years
    # so we never silently jump a year if the per-year list misses.
    candidates: list[tuple[int, int, int]] = []
    for y in (now.year, now.year - 1, now.year - 2):
        for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
            candidates.append((y, m, d))
    for y, m, d in candidates:
        # Allow 45-day filing window after quarter-end
        quarter_end = pd.Timestamp(year=y, month=m, day=d)
        if (now - quarter_end).days >= 45:
            return f"{m:02d}/{d:02d}/{y}"
    # Should never reach here, but defensive: oldest candidate
    y, m, d = candidates[-1]
    return f"{m:02d}/{d:02d}/{y}"


# ─────────────────────────────────────────────────────────────────────
# Per-bank data fetch
# ─────────────────────────────────────────────────────────────────────

# FFIEC Call Report dollar fields (RCFD/RCON) are reported in THOUSANDS of
# dollars. Multiply raw values by this to expose actual USD in *_usd fields.
# (Fractions/shares are ratios and unaffected by the scale.)
FFIEC_DOLLAR_SCALE = 1_000

# RCON codes for Schedule RC-B Memorandum 2.
# Banks with foreign offices use RCFD (consolidated) instead of RCON (domestic).
# We try both and use the larger value (consolidated wins for global banks).
SECURITIES_MATURITY_BUCKETS = [
    ("le_3mo",   "A549", 0.0,  0.25,  "≤ 3 months"),
    ("3mo_1y",   "A550", 0.25, 1.0,   "3 months – 1 year"),
    ("1y_3y",    "A551", 1.0,  3.0,   "1 – 3 years"),
    ("3y_5y",    "A552", 3.0,  5.0,   "3 – 5 years"),
    ("5y_15y",   "A553", 5.0,  15.0,  "5 – 15 years"),
    ("gt_15y",   "A554", 15.0, 30.0,  "> 15 years"),
]


# One-shot schema log so we can see what columns ffiec-data-connect
# actually returns. Helps debug when every bank silently produces
# no_data because our _lookup_concept column-name guessing is off.
_schema_logged = False


def _previous_quarter(period: str) -> str:
    """Step back one quarter. Accepts 'MM/DD/YYYY' and returns same format."""
    m, d, y = period.split("/")
    m, d, y = int(m), int(d), int(y)
    # Map current quarter-end → previous quarter-end
    if (m, d) == (3, 31):
        return f"12/31/{y - 1}"
    if (m, d) == (6, 30):
        return f"03/31/{y}"
    if (m, d) == (9, 30):
        return f"06/30/{y}"
    if (m, d) == (12, 31):
        return f"09/30/{y}"
    # Defensive — return as-is
    return period


def _call_collect_data(creds, rssd_id: int, period: str) -> pd.DataFrame:
    """Single attempt. Returns empty DF on any failure."""
    try:
        from ffiec_data_connect.methods import collect_data
        df = collect_data(
            creds,
            reporting_period=period,
            rssd_id=str(rssd_id),
            series="call",
            output_type="pandas",
        )
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        msg = str(e)
        # 204 No Content = bank hasn't filed for this quarter yet. Caller
        # decides whether to retry against the previous quarter.
        if "204" in msg:
            return pd.DataFrame()
        print(f"[FFIEC] fetch_call_report({rssd_id}, {period}) failed: {e}",
              flush=True)
        return pd.DataFrame()


def fetch_call_report(rssd_id: int, reporting_period: str | None = None) -> pd.DataFrame:
    """
    Pull the entire Call Report for one bank in one HTTP call.

    Returns a DataFrame with one row per MDRM code (the package's standard
    schema). Empty DataFrame if FFIEC isn't configured, the call fails, or
    the bank didn't file for that period.

    Auto-fallback: if the requested period returns empty (typically a 204
    because the bank hasn't filed yet — common for small community banks
    a few weeks after quarter-end), retry the previous quarter once.
    """
    global _schema_logged
    creds = _get_creds()
    if creds is None:
        return pd.DataFrame()

    period = reporting_period or latest_reporting_period()
    df = _call_collect_data(creds, rssd_id, period)
    if df.empty:
        # Try one quarter back — covers slow filers without burning
        # extra API budget on the rest of the universe
        prior = _previous_quarter(period)
        df = _call_collect_data(creds, rssd_id, prior)

    # One-shot schema log so the first successful call surfaces actual
    # column names + first row shape in Cloud Run logs
    if not df.empty and not _schema_logged:
        try:
            print(f"[FFIEC] First call_report DF — rssd={rssd_id} "
                  f"rows={len(df)} cols={list(df.columns)}",
                  flush=True)
            print(f"[FFIEC] First row sample: {df.iloc[0].to_dict()}",
                  flush=True)
        except Exception:
            pass
        _schema_logged = True
    return df


def _lookup_concept(df: pd.DataFrame, code: str) -> float | None:
    """
    Pull a single concept's value from a Call Report DataFrame.

    ffiec-data-connect v3 returns a long-form DF with this schema:
      mdrm        — concept code (e.g. 'RCFDA549')
      rssd        — bank ID
      quarter     — reporting period
      data_type   — 'int' | 'float' | 'bool' | 'str'
      int_data    — populated when data_type=='int'
      float_data  — populated when data_type=='float'
      bool_data   — populated when data_type=='bool'
      str_data    — populated when data_type=='str'

    For our codes we want the consolidated (RCFD) value preferred over
    domestic-only (RCON), since global banks like JPM use RCFD.
    """
    if df is None or df.empty or "mdrm" not in df.columns:
        return None

    def _typed_value(row) -> float | None:
        """Pick the right typed column based on data_type."""
        dt = str(row.get("data_type", "")).lower()
        if dt == "float":
            v = row.get("float_data")
        elif dt == "int":
            v = row.get("int_data")
        else:
            # bool/str don't make sense for $ amounts
            return None
        if v is None:
            return None
        try:
            f = float(v)
            return f if not pd.isna(f) else None
        except (ValueError, TypeError):
            return None

    # RCFD beats RCON when both exist (RCFD = consolidated, RCON = domestic)
    best = None
    for prefix in ("RCFD", "RCON"):
        full_code = f"{prefix}{code}"
        match = df[df["mdrm"].astype(str).str.upper() == full_code]
        if not match.empty:
            v = _typed_value(match.iloc[0])
            if v is not None and (best is None or v > best):
                best = v
    return best


def get_securities_maturity_ladder(
    rssd_id: int,
    reporting_period: str | None = None,
    call_report_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Return the bank's securities maturity ladder as fractions of total
    debt securities.

    Returns:
      {
        "reporting_period": "12/31/2025",
        "buckets": {
            "le_3mo": 0.12, "3mo_1y": 0.18, "1y_3y": 0.25,
            "3y_5y": 0.20, "5y_15y": 0.20, "gt_15y": 0.05,
        },
        "amounts_usd": {...},      # actual USD (FFIEC thousands × 1,000)
        "total_usd": 821_000_000_000,
        "weighted_avg_duration_years": 4.2,  # midpoint-weighted
      }
    or None if the data isn't available.

    Pass call_report_df to avoid re-fetching when caller already has it.
    """
    if call_report_df is None:
        df = fetch_call_report(rssd_id, reporting_period)
    else:
        df = call_report_df
    if df is None or df.empty:
        return None

    amounts: dict[str, float] = {}
    for key, code, _, _, _ in SECURITIES_MATURITY_BUCKETS:
        v = _lookup_concept(df, code)
        if v is not None:
            amounts[key] = v

    if not amounts:
        return None

    total = sum(amounts.values())
    if total <= 0:
        return None

    fractions = {k: v / total for k, v in amounts.items()}

    # Weighted-average duration using bucket midpoints (rough).
    weighted_dur = 0.0
    for key, _code, lo, hi, _label in SECURITIES_MATURITY_BUCKETS:
        midpoint = (lo + hi) / 2
        weighted_dur += fractions.get(key, 0.0) * midpoint

    return {
        "reporting_period": reporting_period or latest_reporting_period(),
        "buckets": fractions,
        # FFIEC reports in $thousands — scale to actual USD.
        "amounts_usd": {k: v * FFIEC_DOLLAR_SCALE for k, v in amounts.items()},
        "total_usd": total * FFIEC_DOLLAR_SCALE,
        "weighted_avg_duration_years": round(weighted_dur, 2),
    }


# Schedule RC-C Part I, Memorandum item 2 — "Loans and leases with a
# remaining maturity or next repricing date of…". Two sub-schedules summed:
#   2.a  Closed-end loans secured by 1st liens on 1-4 family residential
#   2.b  All other loans and all leases
# A loan in the ≤3-month bucket either floats (reprices each quarter off
# prime/SOFR) or matures within the quarter — both behave as floating for
# NIM repricing. So floating_loan_share ≈ the ≤3-month fraction.
LOAN_REPRICING_BUCKETS = [
    # key,       [2.a code, 2.b code], lo_yr, hi_yr, label
    ("le_3mo",  ["A564", "A570"], 0.0,  0.25, "≤ 3 months"),
    ("3mo_1y",  ["A565", "A571"], 0.25, 1.0,  "3 months – 1 year"),
    ("1y_3y",   ["A566", "A572"], 1.0,  3.0,  "1 – 3 years"),
    ("3y_5y",   ["A567", "A573"], 3.0,  5.0,  "3 – 5 years"),
    ("5y_15y",  ["A568", "A574"], 5.0,  15.0, "5 – 15 years"),
    ("gt_15y",  ["A569", "A575"], 15.0, 30.0, "> 15 years"),
]


def get_loan_repricing(
    rssd_id: int,
    reporting_period: str | None = None,
    call_report_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Return the bank's loan repricing/maturity ladder (Schedule RC-C Part I
    Memorandum 2) as fractions of total reported loans, plus a derived
    floating_loan_share (the ≤3-month repricing fraction) for the NIM model.

    Returns:
      {
        "reporting_period": "12/31/2025",
        "buckets": {"le_3mo": 0.34, "3mo_1y": 0.12, ...},
        "amounts_usd": {...},               # actual USD (FFIEC thousands × 1,000)
        "total_usd": 3_200_000_000,
        "floating_loan_share": 0.34,        # reprices within Q1
        "reprice_within_1y_share": 0.46,    # reprices within a year
        "weighted_avg_duration_years": 3.8, # midpoint-weighted
      }
    or None if the data isn't available.

    Pass call_report_df to reuse a Call Report the caller already fetched
    (the quarterly refresh job pulls it once for securities + loans).
    """
    if call_report_df is None:
        df = fetch_call_report(rssd_id, reporting_period)
    else:
        df = call_report_df
    if df is None or df.empty:
        return None

    amounts: dict[str, float] = {}
    for key, codes, _lo, _hi, _label in LOAN_REPRICING_BUCKETS:
        bucket_sum = 0.0
        found = False
        for code in codes:
            v = _lookup_concept(df, code)
            if v is not None:
                bucket_sum += v
                found = True
        if found:
            amounts[key] = bucket_sum

    if not amounts:
        return None

    total = sum(amounts.values())
    if total <= 0:
        return None

    fractions = {k: v / total for k, v in amounts.items()}

    floating_share = fractions.get("le_3mo", 0.0)
    within_1y = fractions.get("le_3mo", 0.0) + fractions.get("3mo_1y", 0.0)

    weighted_dur = 0.0
    for key, _codes, lo, hi, _label in LOAN_REPRICING_BUCKETS:
        weighted_dur += fractions.get(key, 0.0) * ((lo + hi) / 2)

    return {
        "reporting_period": reporting_period or latest_reporting_period(),
        "buckets": fractions,
        # FFIEC reports in $thousands — scale to actual USD.
        "amounts_usd": {k: v * FFIEC_DOLLAR_SCALE for k, v in amounts.items()},
        "total_usd": total * FFIEC_DOLLAR_SCALE,
        "floating_loan_share": round(floating_share, 4),
        "reprice_within_1y_share": round(within_1y, 4),
        "weighted_avg_duration_years": round(weighted_dur, 2),
    }


def _lookup_riad(df: pd.DataFrame, code: str) -> float | None:
    """Pull an income-statement (RIAD) concept from a Call Report DataFrame.
    Like _lookup_concept but for the single RIAD prefix (income/expense items
    don't have the RCFD/RCON consolidated-vs-domestic split)."""
    if df is None or df.empty or "mdrm" not in df.columns:
        return None
    full = f"RIAD{code}".upper()
    match = df[df["mdrm"].astype(str).str.upper() == full]
    if match.empty:
        return None
    row = match.iloc[0]
    dt = str(row.get("data_type", "")).lower()
    v = row.get("float_data") if dt == "float" else row.get("int_data") if dt == "int" else None
    try:
        f = float(v)
        return f if not pd.isna(f) else None
    except (ValueError, TypeError):
        return None


# ── Deposit interest-by-type (Schedule RI 2.a) ───────────────────────────────
# MDRM codes confirmed against the FFIEC 031/041 Schedule RI instructions
# (interest on deposits, item 2.a):
#   RIAD4508 transaction accounts · RIAD0093 savings (incl MMDAs) ·
#   RIADHK03 time deposits ≤ $250k · RIADHK04 time deposits > $250k.
#   CDs = HK03 + HK04 ; other deposits = 4508 + 0093.
# Time-deposit *balances* come from the FDIC feed (NTRTIME), so only the
# interest numerator needs FFIEC. The FFIEC webservice is JWT-gated (server
# side only); values populate through the refresh-ffiec pipeline, not local dev.
_DEP_COST_CODES = {
    "int_transaction": "4508",   # interest on transaction accounts
    "int_savings": "0093",       # interest on savings deposits (incl MMDAs)
    "int_time_le250": "HK03",    # interest on time deposits ≤ $250k
    "int_time_gt250": "HK04",    # interest on time deposits > $250k
}


def get_deposit_cost_detail(
    rssd_id: int,
    reporting_period: str | None = None,
    call_report_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Cost of CDs (time deposits) vs. other (transaction + savings) deposits —
    the SNL 'Int Cost: CDs' / 'Int Cost: Other Deposits' split that isn't in
    the FDIC financials feed.

    Returns the YTD interest-expense components ($000). Combine with FDIC time-
    deposit balances (NTRTIME) to get cost of CDs = interest on time deposits ÷
    avg time-deposit balance. Returns None in local dev (FFIEC unconfigured);
    the data flows through the refresh-ffiec pipeline on the server.
    """
    df = call_report_df
    if df is None:
        df = fetch_call_report(rssd_id, reporting_period)
    if df is None or df.empty:
        return None
    c = _DEP_COST_CODES

    def _sum_or_none(*codes):
        """Sum the components, but only when at least one was actually
        reported — a bank with a true $0 must not be conflated with
        'field missing from the filing' (the old `or None` did exactly that)."""
        vals = [_lookup_riad(df, code) for code in codes]
        present = [v for v in vals if v is not None]
        return sum(present) if present else None

    return {
        "reporting_period": reporting_period or latest_reporting_period(),
        "rssd_id": int(rssd_id),
        "int_time_deposits_000": _sum_or_none(c["int_time_le250"], c["int_time_gt250"]),
        "int_other_deposits_000": _sum_or_none(c["int_transaction"], c["int_savings"]),
    }


# ── Schedule RI income-statement detail ──────────────────────────────────────
# MDRM codes CONFIRMED by value-matching Banner Bank's 12/31/2025 call report
# against the SNL FY-2025 screenshot (docs/SNL-BUILD-PLAN.md, "IS tab";
# tools\probe_ri_codes.py). Notes from that probe:
#   • RIAD5416 (gain on sale of loans) — STRUCTURAL holdco-vs-sub gap
#     (bank-sub 11,491 vs holdco 9,108); label provenance accordingly.
#   • RIAD4313/4507 (tax-exempt loan/securities income) probed at 15,532 /
#     14,865 — direction still to be verified for the FTE adjustment.
#   • RIADC232 = amortization of intangibles & GW impairment combined;
#     RIADC216 = goodwill impairment alone.
_RI_INCOME_CODES = {
    "boli_income": "C014",            # earnings on bank-owned life insurance
    "gain_on_sale_loans": "5416",     # net gains on sales of loans & leases
    "provision_loans": "4230",        # provision for loan & lease losses
    "provision_total": "JJ33",        # provision for credit losses, total
    "inv_banking_fees": "C886",       # investment banking/advisory fees
    "brokerage_fees": "C888",         # securities brokerage fees
    "insurance_income": "C887",       # insurance commissions & fees
    "service_charges": "4080",        # service charges on deposit accounts
    "comp_benefits": "4135",          # salaries & employee benefits
    "amort_intangibles": "C232",      # amortization of intangibles (incl GW impair)
    "goodwill_impairment": "C216",    # goodwill impairment losses
    "data_processing": "C017",        # data processing expense
    "trading_revenue": "A220",        # trading revenue
    "tax_exempt_loan_income": "4313", # tax-exempt income on loans (probed 15,532)
    "tax_exempt_sec_income": "4507",  # tax-exempt income on securities (probed 14,865)
    "occupancy": "4217",              # premises & fixed-asset expense
    "other_opex": "4092",             # other noninterest expense
    "total_int_income": "4107",       # total interest income
    "total_int_expense": "4073",      # total interest expense
    "net_income": "4340",             # net income
}


def get_ri_income_detail(
    rssd_id: int,
    reporting_period: str | None = None,
    call_report_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Schedule RI (income statement) detail lines that aren't in the FDIC
    financials feed — the SNL IS-tab components (BOLI income, provision
    split, fee income lines, opex breakdown, tax-exempt income).

    Quarter semantics: RI is YTD within the calendar year — Q1 covers 3
    months, Q4 the full year. Callers needing discrete quarters must diff
    consecutive periods themselves.

    Returns one key per _RI_INCOME_CODES entry (raw $thousands as reported)
    plus a matching *_usd key scaled by FFIEC_DOLLAR_SCALE, and a derived
    provision_unfunded = provision_total − provision_loans (None unless both
    were reported). A true $0 stays 0.0 — only codes absent from the filing
    map to None. Returns None in local dev (FFIEC unconfigured); the data
    flows through the refresh-ffiec pipeline on the server.
    """
    df = call_report_df
    if df is None:
        df = fetch_call_report(rssd_id, reporting_period)
    if df is None or df.empty:
        return None

    out: dict = {
        "reporting_period": reporting_period or latest_reporting_period(),
        "rssd_id": int(rssd_id),
    }
    for key, code in _RI_INCOME_CODES.items():
        out[key] = _lookup_riad(df, code)

    # Unfunded-commitment provision = total credit-loss provision minus the
    # loan provision — only derivable when BOTH components were reported
    # (a missing component must not silently read as $0).
    pt, pl = out["provision_total"], out["provision_loans"]
    out["provision_unfunded"] = pt - pl if (pt is not None and pl is not None) else None

    for key in list(_RI_INCOME_CODES) + ["provision_unfunded"]:
        v = out[key]
        out[f"{key}_usd"] = v * FFIEC_DOLLAR_SCALE if v is not None else None
    return out


# ── Schedule RC-N past-due & nonaccrual loans by category ────────────────────
# Column A = 30-89 days past due (still accruing), column B = 90+ days past
# due (still accruing), column C = nonaccrual. Balance-sheet codes carry the
# RCFD/RCON prefix split (_lookup_concept resolves it; domestic filers like
# Banner report RCON only).
#
# MDRM codes VERIFIED by value-matching Banner Bank's (RSSD 352772)
# 12/31/2025 call report against the SNL FY-2025 Asset Quality screenshot
# (docs/SNL-BUILD-PLAN.md tab 5):
#   • Item 9 totals RCON1406 / 1407 / 1403 = 26,767 / 4,114 / 41,525 ($000)
#     — exact match to all three SNL totals.
#   • The disjoint categories below + residual "other" reconcile to those
#     totals to the dollar in every column (678 / 0 / 0 residual = loans to
#     depository institutions + leases + all-other-loans net of agricultural).
#   • Agricultural (1594/1597/1583) rides INSIDE "all other loans"
#     (5459-series) on the FFIEC 041 — Banner's other-loans nonaccrual 1,491
#     equals its agricultural 1,491 exactly. That double-count is why "other"
#     is derived as a residual from the item-9 totals rather than summed from
#     the 5459-series codes.
#
# Each category maps to (col A codes, col B codes, col C codes); multi-code
# tuples are sub-items summed.
_RCN_CATEGORIES: dict[str, tuple[tuple[str, ...], ...]] = {
    # 1.a construction & land development = 1-4 family residential
    # construction (F172/F174/F176) + other construction and all land
    # development (F173/F175/F177)
    "construction": (("F172", "F173"), ("F174", "F175"), ("F176", "F177")),
    # 1.b secured by farmland
    "farmland": (("3493",), ("3494",), ("3495",)),
    # 1.c.(1) revolving open-end 1-4 family (home equity lines)
    "heloc": (("5398",), ("5399",), ("5400",)),
    # 1.c.(2) closed-end 1-4 family = first liens (C236/C237/C229)
    # + junior liens (C238/C239/C230); HELOCs are their own row above
    "resi_1to4": (("C236", "C238"), ("C237", "C239"), ("C229", "C230")),
    # 1.d multifamily (5+) residential
    "multifamily": (("3499",), ("3500",), ("3501",)),
    # 1.e nonfarm nonresidential (CRE) = owner-occupied (F178/F180/F182)
    # + other nonfarm nonresidential (F179/F181/F183)
    "nonfarm_nonres": (("F178", "F179"), ("F180", "F181"), ("F182", "F183")),
    # 4. commercial & industrial
    "ci": (("1606",), ("1607",), ("1608",)),
    # loans to finance agricultural production (see header note: subset of
    # "all other loans" on the 041, hence excluded from the residual)
    "agricultural": (("1594",), ("1597",), ("1583",)),
    # 5.a credit cards
    "credit_cards": (("B575",), ("B576",), ("B577",)),
    # 5.b automobile (K213/K214/K215) + 5.c other consumer (K216/K217/K218)
    "other_consumer": (("K213", "K216"), ("K214", "K217"), ("K215", "K218")),
}

# Schedule RC-N item 9 totals (columns A/B/C) — the SNL "Total" row.
_RCN_TOTAL_CODES = {
    "total_pd30_89": "1406",
    "total_pd90_plus": "1407",
    "total_nonaccrual": "1403",
}

# Column keys, in (A, B, C) order matching _RCN_CATEGORIES tuples.
_RCN_COLS = ("pd30_89", "pd90_plus", "nonaccrual")


def get_rcn_detail(
    rssd_id: int,
    reporting_period: str | None = None,
    call_report_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Schedule RC-N: past-due 30-89 / past-due 90+ / nonaccrual loans by loan
    category — the full asset-quality matrix SNL shows as NA.

    Returns ($000 as reported, *_usd scaled by FFIEC_DOLLAR_SCALE):
      {
        "reporting_period": "12/31/2025",
        "rssd_id": 352772,
        "categories": {cat: {"pd30_89": v, "pd90_plus": v, "nonaccrual": v}},
        "categories_usd": {... same shape, scaled ...},
        "total_pd30_89": v, "total_pd90_plus": v, "total_nonaccrual": v,
        "total_pd30_89_usd": ..., ...
      }

    Categories are the disjoint _RCN_CATEGORIES plus a derived "other" =
    item-9 total minus the sum of reported named categories (depository
    institutions, leases, foreign governments, all-other net of agricultural).
    "other" is None when the total is unreported, or when the residual goes
    negative (a mapping violation must surface as n/a, never a plausible-wrong
    number). A true $0 stays 0.0 — only codes absent from the filing map to
    None. Returns None when the report has no RC-N content at all (or in
    local dev where FFIEC is unconfigured).
    """
    df = call_report_df
    if df is None:
        df = fetch_call_report(rssd_id, reporting_period)
    if df is None or df.empty:
        return None

    def _sum_or_none(codes: tuple[str, ...]) -> float | None:
        """Sum sub-items, but only when at least one was actually reported —
        a true $0 must not be conflated with 'absent from the filing'."""
        vals = [_lookup_concept(df, c) for c in codes]
        present = [v for v in vals if v is not None]
        return sum(present) if present else None

    categories: dict[str, dict[str, float | None]] = {}
    for cat, col_codes in _RCN_CATEGORIES.items():
        categories[cat] = {
            col: _sum_or_none(codes)
            for col, codes in zip(_RCN_COLS, col_codes)
        }

    totals = {key: _lookup_concept(df, code)
              for key, code in _RCN_TOTAL_CODES.items()}

    # Bank filed no RC-N content (e.g. a thrift filer or parse mismatch) —
    # don't return a matrix of all-Nones as if it were real data.
    if (all(v is None for cols in categories.values() for v in cols.values())
            and all(v is None for v in totals.values())):
        return None

    # Residual "other": filed total minus the disjoint named categories.
    # Absent categories contribute nothing to the filed total (blank == $0
    # in the filing), so summing only the present values is exact.
    other: dict[str, float | None] = {}
    for col, total_key in zip(_RCN_COLS, _RCN_TOTAL_CODES):
        total = totals[total_key]
        if total is None:
            other[col] = None
            continue
        named = sum(v for cols in categories.values()
                    if (v := cols[col]) is not None)
        residual = total - named
        # Negative residual = the disjointness assumption broke for this
        # filing — render n/a, never a negative balance.
        other[col] = residual if residual >= 0 else None
    categories["other"] = other

    out: dict = {
        "reporting_period": reporting_period or latest_reporting_period(),
        "rssd_id": int(rssd_id),
        "categories": categories,
        "categories_usd": {
            cat: {col: (v * FFIEC_DOLLAR_SCALE if v is not None else None)
                  for col, v in cols.items()}
            for cat, cols in categories.items()
        },
    }
    for key, v in totals.items():
        out[key] = v
        out[f"{key}_usd"] = v * FFIEC_DOLLAR_SCALE if v is not None else None
    return out


def maturity_ladder_to_yearly_pace(ladder: dict) -> dict[int, float]:
    """
    Convert a 6-bucket maturity ladder to cumulative repricing fractions
    by end of year N (N=1..5).

    Linear amortization within each bucket: securities maturing between
    1 and 3 years amortize equally across years 2 and 3, etc.

    Example: a ladder with 30% ≤3mo, 20% 3mo–1y, 20% 1–3y, ...
      Year 1 cumulative = 0.30 + 0.20 + 0   = 0.50 (everything maturing within 1y)
      Year 2 cumulative = 0.50 + (0.20 × 1/2)  = 0.60 (half of the 1-3y bucket)
      Year 3 cumulative = 0.60 + (0.20 × 1/2)  = 0.70
      ...
    """
    if not ladder or "buckets" not in ladder:
        return {}

    b = ladder["buckets"]
    # Already-repriced fraction by year-end:
    #   Y1: everything ≤ 1y (both first buckets fully done)
    #   Y2: + 1/2 of (1-3y bucket)
    #   Y3: + 1/2 of (1-3y) + 1/2 of (3-5y)  →  actually let's do linear amort
    #
    # Linear amortization model:
    #   Bucket "1-3y" (range = 2 years) amortizes 50% in Y2, 50% in Y3
    #   Bucket "3-5y" amortizes 50% in Y4, 50% in Y5
    #   Bucket "5-15y" amortizes 10%/yr from Y6..Y15 — none in years 1-5
    #   Bucket ">15y" — none in years 1-5
    incremental = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    incremental[1] += b.get("le_3mo", 0.0) + b.get("3mo_1y", 0.0)
    # 1-3y bucket spans Y2..Y3, 50% each
    incremental[2] += b.get("1y_3y", 0.0) * 0.5
    incremental[3] += b.get("1y_3y", 0.0) * 0.5
    # 3-5y bucket spans Y4..Y5, 50% each
    incremental[4] += b.get("3y_5y", 0.0) * 0.5
    incremental[5] += b.get("3y_5y", 0.0) * 0.5
    # 5-15y bucket: 10%/yr starting Y6 — irrelevant for Y1..5
    # (Y6 = 5-15y * 0.10, etc.)

    cumulative = {}
    running = 0.0
    for year in range(1, 6):
        running += incremental[year]
        cumulative[year] = round(min(1.0, running), 4)
    return cumulative
