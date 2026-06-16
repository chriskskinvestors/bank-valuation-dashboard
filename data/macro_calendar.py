"""
Macro-print calendar — upcoming US economic release dates for Home's
"Today's Agenda" and the Market & Macro "Economy & Calendar" section
(docs/HOME-MACRO-PLAN.md): which market-moving prints (CPI, jobs, GDP,
PCE, ...) land on which day, merged client-side with the earnings half
of the calendar.

Source — the FRED releases API (requires FRED_API_KEY):
  https://api.stlouisfed.org/fred/release/dates?release_id=N
  with include_release_dates_with_no_data=true, which is what makes FRED
  return the *scheduled future* dates (announced, no data yet) and not
  just the historical ones. realtime_end defaults to 9999-12-31, so
  scheduled dates across a year boundary are included.

Release ids (verified live 2026-06-12 against the public release pages,
https://fred.stlouisfed.org/release?rid=N — no key needed there):

  10  Consumer Price Index
  54  Personal Income and Outlays            (PCE)
  50  Employment Situation                   (NFP / jobs report)
  53  Gross Domestic Product
   9  Advance Monthly Sales for Retail and Food Services (Retail Sales)
  46  Producer Price Index
 180  Unemployment Insurance Weekly Claims Report (jobless claims)

ISM is deliberately ABSENT: the Institute for Supply Management pulled
its data from FRED in 2016 (the old NAPM series page now redirects to
"Institute for Supply Management Data To Be Removed from FRED"), so
there is no FRED release to poll. If ISM is ever wanted it needs a
different source.

FOMC meetings are NOT a FRED release either — the official calendar is
hardcoded below (see FOMC_DECISION_DATES) and merged in with
kind="fomc".

Entry shape (shared by both public functions, designed to merge with
ui/home._af_calendar_pane's earnings entries which also carry an
ISO "date"):

  {date: "YYYY-MM-DD", name: str, release_id: int | None,
   kind: "print" | "fomc", importance: "high" | "medium"}

Importance: CPI / FOMC / Employment Situation / PCE / GDP are "high";
Retail Sales / PPI / jobless claims are "medium".

Functions:
  get_upcoming_prints(days=7)  — entries in [today, today+days], sorted
                                 by date then importance; [] on failure
  get_prints_for_date(date)    — same shape for one day; [] on failure

Cache: the per-release date lists are cached in data.cache under
``macro_calendar:release_dates`` for 24h via the shared freshness check
— release schedules are announced months ahead, so daily is generous.
On any fetch failure (no key, HTTP error, bad payload) both functions
return [] after one [macro_calendar] log line.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

FRED_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
CACHE_KEY = "macro_calendar:release_dates"
CACHE_TTL_SECONDS = 86400  # 24h; schedules are announced months ahead

# FRED release id → display name + importance tier.
TRACKED_RELEASES = [
    {"release_id": 10,  "name": "CPI",                        "importance": "high"},
    {"release_id": 54,  "name": "PCE (Personal Income & Outlays)", "importance": "high"},
    {"release_id": 50,  "name": "Employment Situation (NFP)", "importance": "high"},
    {"release_id": 53,  "name": "GDP",                        "importance": "high"},
    {"release_id": 9,   "name": "Retail Sales",               "importance": "medium"},
    {"release_id": 46,  "name": "PPI",                        "importance": "medium"},
    {"release_id": 180, "name": "Jobless Claims",             "importance": "medium"},
]

# Official FOMC meeting calendar — NOT available via FRED, hardcoded from
# https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# (verified live 2026-06-12). Each entry is the DECISION day — the second
# meeting day, when the statement drops at 2pm ET — which is the date the
# agenda cares about. REFRESH ANNUALLY: the Fed publishes the next year's
# schedule around June; append the new year's dates when they appear.
FOMC_DECISION_DATES = [
    # 2026 (meetings: Jan 27-28, Mar 17-18, Apr 28-29, Jun 16-17,
    #       Jul 28-29, Sep 15-16, Oct 27-28, Dec 8-9)
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

_IMPORTANCE_ORDER = {"high": 0, "medium": 1}


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def _parse_date(raw) -> date | None:
    """One 'YYYY-MM-DD' string → date, or None (never a guess)."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _fetch_release_dates() -> dict[str, list[str]] | None:
    """Scheduled dates per tracked release (or the cached copy).

    Returns {str(release_id): ["YYYY-MM-DD", ...]} or None on any failure
    — missing key, HTTP error, retries exhausted, unparseable payload."""
    from data import cache

    cached = cache.get(CACHE_KEY)
    if _is_fresh(cached) and cached.get("by_release"):
        return cached["by_release"]

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        print("[macro_calendar] FRED_API_KEY not set — macro calendar unavailable")
        return None

    by_release: dict[str, list[str]] = {}
    try:
        from data.http import get_with_retry
        for spec in TRACKED_RELEASES:
            resp = get_with_retry(FRED_DATES_URL, params={
                "release_id": spec["release_id"],
                "api_key": api_key,
                "file_type": "json",
                # The flag that makes FRED include announced FUTURE dates.
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
            })
            if resp is None:
                print("[macro_calendar] release dates fetch: retries exhausted (429)")
                return None
            rows = resp.json().get("release_dates", [])
            by_release[str(spec["release_id"])] = [
                r["date"] for r in rows
                if isinstance(r, dict) and _parse_date(r.get("date")) is not None
            ]
    except Exception as e:
        print(f"[macro_calendar] release dates fetch error: {type(e).__name__}: {e}")
        return None

    cache.put(CACHE_KEY, {"cached_at": datetime.now().isoformat(),
                          "by_release": by_release})
    return by_release


def _entries_in_window(start: date, end: date) -> list[dict] | None:
    """All calendar entries with start <= date <= end (FRED prints + FOMC),
    sorted by date, then importance (high first), then name. None when the
    FRED fetch failed — callers translate that to []."""
    by_release = _fetch_release_dates()
    if by_release is None:
        return None

    entries = []
    for spec in TRACKED_RELEASES:
        for raw in by_release.get(str(spec["release_id"]), []):
            d = _parse_date(raw)
            if d is not None and start <= d <= end:
                entries.append({
                    "date": d.isoformat(),
                    "name": spec["name"],
                    "release_id": spec["release_id"],
                    "kind": "print",
                    "importance": spec["importance"],
                    # Every tracked FRED release (BLS/BEA/Census) drops at
                    # 8:30 AM ET. A known scheduled time, not a guessed value.
                    "time": "8:30 ET",
                })
    for raw in FOMC_DECISION_DATES:
        d = _parse_date(raw)
        if d is not None and start <= d <= end:
            entries.append({
                "date": d.isoformat(),
                "name": "FOMC Rate Decision",
                "release_id": None,
                "kind": "fomc",
                "importance": "high",
                "time": "2:00 ET",  # FOMC statement
            })

    entries.sort(key=lambda e: (e["date"],
                                _IMPORTANCE_ORDER.get(e["importance"], 9),
                                e["name"]))
    return entries


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_upcoming_prints(days: int = 7) -> list[dict]:
    """Macro prints + FOMC decisions scheduled in [today, today + days],
    inclusive on both ends, sorted by date then importance.

    Each entry: {date: "YYYY-MM-DD", name, release_id: int|None,
    kind: "print"|"fomc", importance: "high"|"medium"}.
    Returns [] on any failure (already logged by the fetch)."""
    today = date.today()
    entries = _entries_in_window(today, today + timedelta(days=days))
    return entries if entries is not None else []


def get_prints_for_date(day) -> list[dict]:
    """Macro prints + FOMC decisions on one day — same entry shape as
    get_upcoming_prints. ``day`` is a datetime.date, datetime, or
    'YYYY-MM-DD' string. Returns [] on failure or an unparseable day."""
    if isinstance(day, datetime):
        day = day.date()
    elif isinstance(day, str):
        day = _parse_date(day)
    if not isinstance(day, date):
        return []
    entries = _entries_in_window(day, day)
    return entries if entries is not None else []
