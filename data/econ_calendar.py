"""
US economic-release calendar + consensus surprises for the Market & Macro
"Economic Data" section.

Source: FMP's economics-calendar endpoint (Premium) — each US event carries
previous / estimate (consensus) / actual / impact tier (High/Medium/Low) /
unit. This is the consensus+surprise data FRED does not provide; FRED stays
the authoritative source for the indicator time-series and charts, while this
module powers the "latest releases & surprises" panel and the upcoming
release calendar.

One windowed fetch ([today-back, today+fwd], 1h-cached) serves both halves:
events with a non-null actual are recent prints (with a beat/miss surprise);
events with a null actual are upcoming. Surprise is the raw actual−consensus
deviation — colored by direction (above/below consensus), NOT good/bad, since
"good" is indicator-specific (lower CPI is good, higher payrolls is good).

All-empty on missing key / plan denial / fetch failure → the renderer shows
an honest note, never fabricated prints.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from data.fmp_client import _get

CACHE_KEY = "econ_calendar_us:v1"
CACHE_TTL_SECONDS = 3600  # 1h — actuals print intraday

_IMPACT_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def parse_event(e: dict) -> dict | None:
    """One FMP economics-calendar row → normalized event, or None if unusable.

    Pure / unit-tested. surprise = actual − estimate (None unless both present);
    surprise_pct is relative to |estimate|. `released` flags a printed actual."""
    if not isinstance(e, dict):
        return None
    name = (e.get("event") or "").strip()
    raw = e.get("date")
    if not name or not raw:
        return None
    actual, est, prev = _num(e.get("actual")), _num(e.get("estimate")), _num(e.get("previous"))
    surprise = (actual - est) if (actual is not None and est is not None) else None
    surprise_pct = (surprise / abs(est) * 100.0) if (surprise is not None and est not in (None, 0)) else None
    impact = e.get("impact") if e.get("impact") in _IMPACT_RANK else "Low"
    return {
        "datetime": str(raw),
        "date": str(raw)[:10],
        "event": name,
        "actual": actual,
        "estimate": est,
        "previous": prev,
        "surprise": surprise,
        "surprise_pct": surprise_pct,
        "impact": impact,
        "unit": e.get("unit"),
        "released": actual is not None,
    }


def _impact_ok(ev: dict, min_impact: str) -> bool:
    return _IMPACT_RANK.get(ev["impact"], 9) <= _IMPACT_RANK.get(min_impact, 1)


def get_us_calendar(back_days: int = 10, fwd_days: int = 14) -> list[dict]:
    """Parsed US economic events in [today-back, today+fwd], 1h-cached.
    Empty list on no key / denial / failure."""
    from data import cache
    from data.freshness import is_fresh

    cached = cache.get(CACHE_KEY)
    if is_fresh(cached, CACHE_TTL_SECONDS) and cached.get("events") is not None:
        return cached["events"]

    today = date.today()
    params = {"from": (today - timedelta(days=back_days)).isoformat(),
              "to": (today + timedelta(days=fwd_days)).isoformat()}
    raw = _get("economics-calendar", params, timeout=20)
    if not isinstance(raw, list):
        return []
    events = [pe for e in raw if e.get("country") == "US"
              for pe in (parse_event(e),) if pe is not None]
    cache.put(CACHE_KEY, {"cached_at": datetime.now().isoformat(), "events": events})
    return events


def get_recent_releases(days: int = 7, min_impact: str = "Medium", limit: int = 14) -> list[dict]:
    """Recently-released US prints (actual present) within the lookback, at or
    above `min_impact`, newest first."""
    today = date.today()
    floor = (today - timedelta(days=days)).isoformat()
    rows = [e for e in get_us_calendar()
            if e["released"] and e["date"] >= floor and e["date"] <= today.isoformat()
            and _impact_ok(e, min_impact)]
    rows.sort(key=lambda e: e["datetime"], reverse=True)
    return rows[:limit]


def get_upcoming_releases(days: int = 10, min_impact: str = "Medium", limit: int = 20) -> list[dict]:
    """Upcoming US releases (no actual yet) within the forward window, at or
    above `min_impact`, soonest first."""
    today = date.today().isoformat()
    rows = [e for e in get_us_calendar()
            if not e["released"] and e["date"] >= today and _impact_ok(e, min_impact)]
    rows.sort(key=lambda e: e["datetime"])
    return rows[:limit]
