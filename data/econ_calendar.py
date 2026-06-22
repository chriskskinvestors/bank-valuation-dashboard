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


# Marquee US macro releases (owner: focus the panels on core economic data,
# not commodity inventories, CFTC positioning, auctions, or nowcasts).
# An event qualifies if its name contains a MARQUEE term and no EXCLUDE term.
_MARQUEE_TERMS = (
    "cpi", "pce", "ppi",
    "nonfarm payroll", "non-farm payroll", "non farm payroll", "unemployment rate",
    "jobless claims", "adp employment", "jolts", "job openings", "hourly earnings",
    "gdp", "retail sales", "personal income", "personal spending", "durable goods",
    "factory orders", "industrial production", "capacity utilization",
    "ism ", "pmi", "empire state", "philadelphia fed", "philly fed", "chicago pmi",
    "dallas fed", "richmond fed", "kansas city fed",
    "housing starts", "building permits", "new home sales", "existing home sales",
    "pending home", "case-shiller", "case shiller", "house price", "nahb",
    "construction spending",
    "consumer confidence", "consumer sentiment", "michigan",
    "interest rate decision", "fomc", "fed interest rate", "fed press conference",
    "fed economic projection", "trade balance", "goods trade",
)
_EXCLUDE_TERMS = (
    "cftc", "api ", "crude oil", "eia ", "auction", "redbook", "gdpnow",
    "rig count", "baker hughes", "mba mortgage", "money supply",
    "import prices", "export prices", "fed balance sheet", "speculative net positions",
)


def is_marquee(event_name: str) -> bool:
    """True for core US macroeconomic releases; False for positioning,
    commodity inventories, auctions, nowcasts, etc. Pure / unit-tested."""
    n = (event_name or "").lower()
    if not n or any(x in n for x in _EXCLUDE_TERMS):
        return False
    return any(t in n for t in _MARQUEE_TERMS)


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
    # FMP's stable REST path is "economic-calendar" (singular); the docs page
    # slug / MCP name is "economics-calendar". Try the real path first and fall
    # back, so a naming change on either side can't silently break this.
    raw = None
    for path in ("economic-calendar", "economics-calendar"):
        resp = _get(path, params, timeout=20)
        if isinstance(resp, list) and resp:
            raw = resp
            break
    if not isinstance(raw, list):
        return []
    events = [pe for e in raw if e.get("country") == "US"
              for pe in (parse_event(e),) if pe is not None]
    cache.put(CACHE_KEY, {"cached_at": datetime.now().isoformat(), "events": events})
    return events


def get_recent_releases(days: int = 10, limit: int = 14) -> list[dict]:
    """Recently-released marquee US macro prints (actual present) within the
    lookback, newest first."""
    today = date.today()
    floor = (today - timedelta(days=days)).isoformat()
    rows = [e for e in get_us_calendar()
            if e["released"] and floor <= e["date"] <= today.isoformat()
            and is_marquee(e["event"])]
    rows.sort(key=lambda e: e["datetime"], reverse=True)
    return rows[:limit]


def get_upcoming_releases(days: int = 14, limit: int = 20) -> list[dict]:
    """Upcoming marquee US macro releases (no actual yet) within the forward
    window, soonest first."""
    today = date.today().isoformat()
    rows = [e for e in get_us_calendar()
            if not e["released"] and e["date"] >= today and is_marquee(e["event"])]
    rows.sort(key=lambda e: e["datetime"])
    return rows[:limit]


# ── Display helpers ─────────────────────────────────────────────────────────
# Plain-text formatters (no HTML) so any consumer can apply its own styling —
# the Market & Macro panel and the Home calendar pane both render these events.

def fmt_value(v, unit) -> str | None:
    """An econ value + its unit as plain text ('180K', '3.2%', '1.5 bps'), or
    None when there's no value. Pure / unit-tested."""
    if v is None:
        return None
    u = (unit or "").strip()
    if u in ("%", "M", "K", "B"):
        return f"{v:g}{u}"
    return f"{v:g}{(' ' + u) if u else ''}"


def et_time(dt_str) -> str:
    """FMP UTC datetime string → US/Eastern 'h:mm AM/PM ET'; '' for a midnight
    placeholder (FMP's no-scheduled-time marker) or any parse failure."""
    from datetime import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        utc = _dt.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("UTC"))
    except Exception:
        return ""
    if utc.hour == 0 and utc.minute == 0:
        return ""
    d = utc.astimezone(ZoneInfo("America/New_York"))
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} ET"
