"""FMP executive compensation (data layer).

Separate module from data/fmp_client.py (often mid-edit by other sessions),
reusing its request + cache helpers. Endpoint verified IN-PLAN on the current
Premium key (2026-06-24): ``governance-executive-compensation?symbol=<ticker>``
returns per-named-executive, per-year rows from the proxy (DEF 14A) Summary
Compensation Table, each with a SEC filing link.

The raw feed reports the SAME (executive, fiscal year) across multiple proxy
filings (e.g. FY2023 appears in the 2024, 2025 and 2026 proxies); we de-dup to
ONE row per (name+position, year) keeping the most recently filed figure.
"""
from __future__ import annotations

from data.fmp_client import _get, _has_key, _cache_get, _cache_put

_TTL_SECONDS = 24 * 3600  # proxies file annually; a day's cache is plenty

_NUM_FIELDS = {
    "salary": "salary",
    "bonus": "bonus",
    "stock_award": "stockAward",
    "option_award": "optionAward",
    "incentive": "incentivePlanCompensation",
    "other": "allOtherCompensation",
    "total": "total",
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_executive_compensation(ticker: str) -> list[dict]:
    """Named-executive compensation, de-duped to one row per (name+position,
    year), newest fiscal year first. Each row:
      {name_position, year, salary, bonus, stock_award, option_award,
       incentive, other, total, filing_date, link}
    Returns [] on no coverage or failure (steady state for many small banks).
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    ck = f"fmp_exec_comp:v1:{ticker}"
    cached = _cache_get(ck, _TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("governance-executive-compensation", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        return []

    # De-dup by (name+position, year), keeping the most recently FILED record.
    best: dict[tuple, dict] = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        name = (r.get("nameAndPosition") or "").strip()
        yr = r.get("year")
        if not name or yr is None:
            continue
        try:
            yr = int(yr)
        except (TypeError, ValueError):
            continue
        filed = str(r.get("filingDate") or "")
        key = (name, yr)
        prev = best.get(key)
        if prev is not None and filed <= prev["_filed"]:
            continue
        row = {"name_position": name, "year": yr,
               "filing_date": filed or None,
               "link": r.get("link") or None, "_filed": filed}
        for out_k, src_k in _NUM_FIELDS.items():
            row[out_k] = _num(r.get(src_k))
        best[key] = row

    rows = sorted(best.values(),
                  key=lambda x: (x["year"], x.get("total") or 0), reverse=True)
    for x in rows:
        x.pop("_filed", None)
    _cache_put(ck, rows)
    return rows
