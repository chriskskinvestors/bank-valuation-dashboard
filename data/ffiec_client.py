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
    now = as_of or pd.Timestamp.utcnow()
    candidates = [
        (now.year, 12, 31),
        (now.year, 9, 30),
        (now.year, 6, 30),
        (now.year, 3, 31),
        (now.year - 1, 12, 31),
    ]
    for y, m, d in candidates:
        # Allow 45-day filing window after quarter-end
        quarter_end = pd.Timestamp(year=y, month=m, day=d)
        if (now - quarter_end).days >= 45:
            return f"{m:02d}/{d:02d}/{y}"
    # Fall back to last year's Q4
    return f"12/31/{now.year - 2}"


# ─────────────────────────────────────────────────────────────────────
# Per-bank data fetch
# ─────────────────────────────────────────────────────────────────────

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


def fetch_call_report(rssd_id: int, reporting_period: str | None = None) -> pd.DataFrame:
    """
    Pull the entire Call Report for one bank in one HTTP call.

    Returns a DataFrame with one row per MDRM code (the package's standard
    schema). Empty DataFrame if FFIEC isn't configured, the call fails, or
    the bank didn't file for that period.
    """
    creds = _get_creds()
    if creds is None:
        return pd.DataFrame()

    period = reporting_period or latest_reporting_period()
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
        print(f"[FFIEC] fetch_call_report({rssd_id}, {period}) failed: {e}")
        return pd.DataFrame()


def _lookup_concept(df: pd.DataFrame, code: str) -> float | None:
    """
    Pull a single concept's value from a Call Report DataFrame.

    The ffiec-data-connect package returns a long-form DF with columns
    that include the MDRM code (e.g. "mdrm" or "concept") and a value
    column. Try several column name patterns since v3 schema isn't
    pinned in docs.

    For our codes we want the consolidated (RCFD) value preferred over
    domestic-only (RCON), since global banks like JPM use RCFD.
    """
    if df is None or df.empty:
        return None

    # Find the code-identifier column
    code_col = None
    for candidate in ("mdrm", "concept", "code", "id"):
        if candidate in df.columns:
            code_col = candidate
            break
    if code_col is None:
        return None

    # Find the value column
    val_col = None
    for candidate in ("value", "data_value", "amount", "val"):
        if candidate in df.columns:
            val_col = candidate
            break
    if val_col is None:
        return None

    # RCFD beats RCON when both exist
    best = None
    for prefix in ("RCFD", "RCON"):
        full_code = f"{prefix}{code}"
        match = df[df[code_col].astype(str).str.upper() == full_code]
        if not match.empty:
            try:
                v = float(match.iloc[0][val_col])
                if best is None or v > best:
                    best = v
            except (ValueError, TypeError):
                continue
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
        "amounts_usd": {...},      # raw dollar amounts
        "total_usd": 821_000_000,
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
        "amounts_usd": amounts,
        "total_usd": total,
        "weighted_avg_duration_years": round(weighted_dur, 2),
    }


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
