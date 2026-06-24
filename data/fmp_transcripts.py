"""FMP earnings-call transcripts (data layer).

A separate module from data/fmp_client.py (which is frequently mid-edit by
other sessions) that reuses fmp_client's shared request + cache helpers.

Endpoints verified IN-PLAN on the current Premium key (2026-06-24) — the older
"transcripts need FMP Ultimate" note is stale:
  earning-call-transcript-dates  -> [{quarter, fiscalYear, date}]
  earning-call-transcript        -> [{symbol, period, year, date, content}]

Returns plain dicts; never raises to the caller (UI shows n/a on empties).
"""
from __future__ import annotations

from data.fmp_client import _get, _has_key, _cache_get, _cache_put

# A published transcript's text never changes; its availability list does (a new
# call is added each quarter), so the two get different TTLs.
_DATES_TTL_SECONDS = 12 * 3600          # availability list — a couple refreshes/day
_CONTENT_TTL_SECONDS = 30 * 24 * 3600   # the transcript body, effectively immutable


def get_transcript_dates(ticker: str) -> list[dict]:
    """Available earnings calls, newest first:
    ``[{"quarter": int, "year": int, "date": "YYYY-MM-DD"|None}]``.
    ``[]`` on no coverage or failure (the steady state for many small banks).
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    ck = f"fmp_tx_dates:v1:{ticker}"
    cached = _cache_get(ck, _DATES_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("earning-call-transcript-dates", {"symbol": ticker})
    out: list[dict] = []
    if isinstance(data, list):
        for r in data:
            if not isinstance(r, dict):
                continue
            q = r.get("quarter")
            y = r.get("fiscalYear", r.get("year"))
            if q is None or y is None:
                continue
            try:
                q, y = int(q), int(y)
            except (TypeError, ValueError):
                continue
            d = r.get("date")
            out.append({"quarter": q, "year": y,
                        "date": str(d)[:10] if d else None})
    out.sort(key=lambda r: (r["year"], r["quarter"]), reverse=True)
    _cache_put(ck, out)
    return out


def get_transcript(ticker: str, year: int, quarter: int) -> dict | None:
    """Full transcript for one call:
    ``{"quarter", "year", "date", "content"}`` or ``None`` if unavailable.
    """
    if not _has_key():
        return None
    ticker = ticker.upper()
    try:
        year, quarter = int(year), int(quarter)
    except (TypeError, ValueError):
        return None
    ck = f"fmp_tx:v1:{ticker}:{year}:{quarter}"
    cached = _cache_get(ck, _CONTENT_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("earning-call-transcript",
                {"symbol": ticker, "year": year, "quarter": quarter})
    rec = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        r = data[0]
        content = r.get("content")
        if content and str(content).strip():
            rec = {"quarter": quarter, "year": year,
                   "date": str(r.get("date") or "")[:10] or None,
                   "content": str(content)}
    # Only cache a real hit — a miss stays re-fetchable (coverage may appear).
    if rec is not None:
        _cache_put(ck, rec)
    return rec
