"""
Data-provenance freshness badges shown above the screening table.

(The legacy screening-table renderer that used to live here —
render_overview_table / render_column_selector — was dead code with zero
callers and was removed; app.py builds the screener itself and imports only
render_data_freshness from this module.)
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st


def price_badge_state(fmp_ok: bool, max_updated_at: datetime | None,
                      now: datetime) -> tuple[str, str]:
    """Pure badge decision (label, css class) from actual price-data age —
    key presence alone used to render "FMP LIVE" over week-old prices when
    the refresh-prices job broke.

      no key            → PRICES OFFLINE                    (danger)
      age < 1h          → FMP LIVE                          (live)
      1h–76h            → PRICES AS OF <HH:MM ET | date>    (warn)
      > 76h             → PRICES STALE — <date>             (danger)

    max_updated_at is the newest price_cache stamp = the last successful
    refresh-prices job write. The job idles outside market hours, so the
    stamp legitimately ages ~65h over a normal weekend (Fri evening → Mon
    open) and ~89h over a holiday one; 76h keeps normal weekends in warn
    styling (an honest "as of Friday", not a red alarm) while still
    catching the documented 2026-06-09..12 incident (refresh-prices dead
    3 days, found only via a stale on-screen timestamp — see the invoker
    gate comment in .github/workflows/deploy.yml) by day 3+. None with a
    key = no cached rows: served prices are live FMP fetches, so LIVE is
    honest.
    """
    if not fmp_ok:
        return ("PRICES OFFLINE", "freshness-stale")
    if max_updated_at is None:
        return ("FMP LIVE", "freshness-live")
    age_s = (now - max_updated_at).total_seconds()
    if age_s < 3600:
        return ("FMP LIVE", "freshness-live")
    et = max_updated_at.astimezone(ZoneInfo("America/New_York"))
    asof = (f"{et.strftime('%H:%M')} ET" if age_s <= 86400
            else et.strftime("%Y-%m-%d"))
    if age_s <= 76 * 3600:
        return (f"PRICES AS OF {asof}", "freshness-cached")
    return (f"PRICES STALE — {et.strftime('%Y-%m-%d')}", "freshness-stale")


def render_data_freshness(fdic_ages: dict, sec_ages: dict, ibkr_connected: bool):
    """One compact, left-aligned row of data-provenance freshness badges.

    Was ``st.columns(3)``, which pinned each badge to the left edge of its own
    third of the page — three tiny pills flung across the full width with big
    empty gaps. A single flex row keeps them tight together, which is also how
    the design system treats pill groups.
    """
    badges: list[tuple[str, str]] = []

    # Price source: IBKR when connected (local), else FMP (cloud) — show real status.
    if ibkr_connected:
        badges.append(("IBKR LIVE", "freshness-live"))
    else:
        try:
            from data.fmp_client import _has_key
            fmp_ok = _has_key()
        except Exception:
            fmp_ok = False
        max_ts = None
        if fmp_ok:
            try:
                from data.price_cache_store import get_max_updated_at
                max_ts = get_max_updated_at()
            except Exception:
                max_ts = None
        badges.append(price_badge_state(
            fmp_ok, max_ts, datetime.now(timezone.utc)))

    # FDIC + SEC fundamentals: average age across the sampled tickers.
    for src, ages_map in (("FDIC", fdic_ages), ("SEC", sec_ages)):
        ages = [a for a in ages_map.values() if a is not None]
        if ages:
            avg_hours = sum(ages) / len(ages) / 3600
            cls = "freshness-live" if avg_hours < 24 else "freshness-cached"
            badges.append((f"{src} {avg_hours:.0f}h ago", cls))
        else:
            badges.append((f"{src} NO DATA", "freshness-stale"))

    spans = "".join(
        f'<span class="freshness-badge {cls}">{label}</span>'
        for label, cls in badges)
    st.markdown(
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
        f'margin:0 0 8px;">{spans}</div>',
        unsafe_allow_html=True,
    )
