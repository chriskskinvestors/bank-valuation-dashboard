"""
FDIC National Rates and Rate Caps — deposit pricing for the Market & Macro
"Funding & Deposits" section (docs/HOME-MACRO-PLAN.md §3): national average
deposit rates (savings, checking, MMDA, CD tenors) plus the Section 337.7
national rate caps, against which deposit betas and funding pressure read.

Source (verified live 2026-06-11):
  • The structured FDIC data API (https://api.fdic.gov/banks/, formerly
    banks.data.fdic.gov) does NOT expose national rates — its swagger lists
    only institutions/locations/summary/failures/history/financials/
    demographics/sod. So the source is the FDIC website publication.
  • https://www.fdic.gov/national-rates-and-rate-caps publishes the current
    table as HTML; the "Previous Rates" page links one stable workbook,
      https://www.fdic.gov/resources/bankers/national-rates/documents/
      archive-revised-rule.xlsx
    containing every revised-rule observation (April 2021 → current month,
    one "YYYY Archive" sheet per year). The newest column of the archive
    matched the live HTML page exactly when verified, so ONE request serves
    both current rates and full history.

Cadence — monthly, not weekly: under the December 2020 Final Rule
(effective April 1, 2021) the FDIC publishes national rates MONTHLY on the
third Monday of each month. (The pre-2021 series was weekly; that older
methodology lives in a separate archive.xlsx and is not comparable, so it
is deliberately not stitched on.) "History" here therefore means one
observation per month.

Workbook layout (per "YYYY Archive" sheet): a date row where each month
spans five columns headed National Rate / National Rate plus 75 bps /
Treasury Yield / Treasury Yield - Rate Cap Adjusted / National Rate Cap;
product names down column 0 (Savings, Interest Checking, Money Market
<100M, "N month CD <100M"). We keep National Rate (rate_pct) and National
Rate Cap (cap_pct).

Functions:
  get_national_rates()              — latest month, dict or None
  get_national_rate_history(weeks)  — monthly observations within the
                                      lookback window, ascending; [] on failure

Cache: the parsed archive is cached in data.cache under
``national_rates:revised_rule`` for 24h via the shared freshness check —
the series only changes once a month, so a daily refetch is generous.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from io import BytesIO

ARCHIVE_URL = ("https://www.fdic.gov/resources/bankers/national-rates/"
               "documents/archive-revised-rule.xlsx")
CACHE_KEY = "national_rates:revised_rule"
CACHE_TTL_SECONDS = 86400  # 24h; FDIC publishes monthly (third Monday)

# Workbook product label (normalized: lowercased, "<100M" qualifier and
# extra whitespace stripped) → our field name.
PRODUCT_FIELDS = {
    "savings": "savings",
    "interest checking": "interest_checking",
    "money market": "mmda",
    "1 month cd": "cd_1mo",
    "3 month cd": "cd_3mo",
    "6 month cd": "cd_6mo",
    "12 month cd": "cd_12mo",
    "24 month cd": "cd_24mo",
    "36 month cd": "cd_36mo",
    "48 month cd": "cd_48mo",
    "60 month cd": "cd_60mo",
}

RATE_HEADER = "national rate"
CAP_HEADER = "national rate cap"


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _norm(label) -> str:
    """Normalize a workbook cell label: lowercase, drop the '<100M' deposit-
    size qualifier, collapse whitespace."""
    if not isinstance(label, str):
        return ""
    return re.sub(r"\s+", " ", label.lower().replace("<100m", "")).strip()


def _pct(raw) -> float | None:
    """One rate cell → rounded percent, or None (never a guess)."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(v, 2)


def _parse_sheet(df, records: dict) -> None:
    """Parse one 'YYYY Archive' sheet (read with header=None) into
    ``records`` keyed by asof date string. Layout is located by content —
    the header row is the first row containing a 'National Rate' cell, the
    date row sits directly above it — so row/column drift doesn't break us."""
    import pandas as pd

    grid = df.values.tolist()
    header_idx = None
    for i, row in enumerate(grid):
        if any(_norm(c) == RATE_HEADER for c in row):
            header_idx = i
            break
    if header_idx is None or header_idx == 0:
        return
    headers = [_norm(c) for c in grid[header_idx]]
    date_row = grid[header_idx - 1]

    # Product name (col 0) → row index, for rows below the header row.
    product_rows = {}
    for i in range(header_idx + 1, len(grid)):
        field = PRODUCT_FIELDS.get(_norm(grid[i][0]))
        if field:
            product_rows[field] = i

    # Each month block starts at a 'National Rate' column; its cap column is
    # the next 'National Rate Cap' within the 5-column block.
    for col, h in enumerate(headers):
        if h != RATE_HEADER:
            continue
        asof = pd.to_datetime(date_row[col], errors="coerce")
        if pd.isna(asof):
            continue  # placeholder block for a future month
        cap_col = next((c for c in range(col + 1, min(col + 6, len(headers)))
                        if headers[c] == CAP_HEADER), None)

        rec = {"asof": asof.date().isoformat()}
        for field, ridx in product_rows.items():
            rec[field] = {
                "rate_pct": _pct(grid[ridx][col]),
                "cap_pct": _pct(grid[ridx][cap_col]) if cap_col is not None else None,
            }
        if any(rec[f]["rate_pct"] is not None for f in product_rows):
            records[rec["asof"]] = rec


def _fetch_archive() -> list[dict] | None:
    """Download + parse the archive workbook (or serve the cached parse).
    Returns records sorted newest-first, or None on any failure."""
    from data import cache

    cached = cache.get(CACHE_KEY)
    if _is_fresh(cached) and cached.get("records"):
        return cached["records"]

    try:
        from data.http import get_with_retry
        resp = get_with_retry(ARCHIVE_URL, timeout=30)
        if resp is None:
            print("[national_rates] archive fetch: retries exhausted (429)")
            return None
        import pandas as pd
        sheets = pd.read_excel(BytesIO(resp.content), sheet_name=None, header=None)
    except Exception as e:
        print(f"[national_rates] archive fetch/parse error: {type(e).__name__}: {e}")
        return None

    records: dict[str, dict] = {}
    for df in sheets.values():
        _parse_sheet(df, records)
    if not records:
        print("[national_rates] archive parsed but no rate observations found "
              "— layout may have changed")
        return None

    out = [records[k] for k in sorted(records, reverse=True)]
    cache.put(CACHE_KEY, {"cached_at": datetime.now().isoformat(), "records": out})
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_national_rates() -> dict | None:
    """Latest FDIC national deposit rates + rate caps (monthly series).

    Returns {asof, savings, interest_checking, mmda, cd_1mo, cd_3mo, cd_6mo,
    cd_12mo, cd_24mo, cd_36mo, cd_48mo, cd_60mo} where each product is
    {rate_pct, cap_pct} (percent, or None when unpublished) — or None on
    any failure."""
    records = _fetch_archive()
    if not records:
        return None
    return records[0]


def get_national_rate_history(weeks: int = 104) -> list[dict]:
    """All FDIC national-rate observations within the last ``weeks`` weeks,
    oldest first — same per-observation shape as get_national_rates().

    Note: the FDIC publishes this series MONTHLY (third Monday), so a
    104-week window yields ~24 observations, not 104. The full revised-rule
    series starts April 2021. Returns [] on any failure."""
    records = _fetch_archive()
    if not records:
        return []
    cutoff = (datetime.now() - timedelta(weeks=weeks)).date().isoformat()
    return sorted((r for r in records if r["asof"] >= cutoff),
                  key=lambda r: r["asof"])
