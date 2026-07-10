"""
Auto-populated consensus estimates and earnings calendar.

Uses yfinance for earnings dates and analyst estimates.
Caches results to avoid excessive API calls.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from data.cloud_storage import save_json, load_json

ESTIMATES_CACHE_DIR = Path(__file__).parent.parent / "estimates_cache"
ESTIMATES_CACHE_DIR.mkdir(exist_ok=True)

ESTIMATES_PREFIX = "estimates_cache"

# Cache TTL: 6 hours
CACHE_TTL_SECONDS = 21600


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh_data(data: dict | None) -> bool:
    from data.freshness import is_fresh
    # Failures are never fresh: an {"error": ...} payload persisted by an older
    # build (before failures stopped being cached) must not suppress a real retry.
    if not data or data.get("error"):
        return False
    return is_fresh(data, CACHE_TTL_SECONDS)


def fetch_estimates(ticker: str) -> dict:
    """
    Fetch consensus estimates and earnings calendar for a ticker.

    Returns:
    {
        "ticker": "JPM",
        "next_earnings_date": "2026-04-15",
        "eps_estimate": 4.12,
        "eps_actual_last": 3.95,
        "revenue_estimate": 42500000000,
        "analyst_count": 18,
        "eps_trend": {"current_qtr": 4.12, "next_qtr": 4.25, ...},
        "earnings_history": [
            {"date": "2026-01-15", "eps_estimate": 3.90, "eps_actual": 3.95, "surprise_pct": 1.28},
            ...
        ],
        "cached_at": "2026-04-08T10:00:00"
    }
    """
    filename = f"{ticker.upper()}.json"

    # Check cache (GCS + local)
    cached = load_json(ESTIMATES_PREFIX, filename)
    if _is_fresh_data(cached):
        return cached

    result = _fetch_from_yfinance(ticker)

    # Failures are NEVER cached (data-layer honesty). A throttled/failed yfinance
    # call returns an {"error": ...} payload; persisting it would stamp it fresh
    # for CACHE_TTL_SECONDS and silently drop the ticker from the earnings
    # calendar until expiry. Return it un-persisted so the next call retries.
    if result.get("error"):
        return result

    result["cached_at"] = datetime.now().isoformat()

    try:
        save_json(ESTIMATES_PREFIX, filename, result)
    except Exception:
        pass

    return result


def _fetch_from_yfinance(ticker: str) -> dict:
    """Fetch data from yfinance."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        info = t.info or {}

        result = {
            "ticker": ticker.upper(),
            "next_earnings_date": None,
            "eps_estimate": None,
            "eps_actual_last": None,
            "revenue_estimate": None,
            "analyst_count": None,
            "recommendation": None,
            "target_price": None,
            "target_high": None,
            "target_low": None,
            "eps_trend": {},
            "earnings_history": [],
        }

        # Next earnings date
        try:
            cal = t.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed:
                        if isinstance(ed, list) and len(ed) > 0:
                            result["next_earnings_date"] = str(ed[0].date()) if hasattr(ed[0], 'date') else str(ed[0])
                        elif hasattr(ed, 'date'):
                            result["next_earnings_date"] = str(ed.date())
        except Exception:
            pass

        # EPS estimates (annual forward)
        result["eps_fwd_annual"] = info.get("epsForward") or info.get("epsCurrentYear")
        result["eps_actual_last"] = info.get("trailingEps")
        # Quarterly EPS estimate comes from earnings_history (populated below)
        result["eps_estimate"] = None
        result["analyst_count"] = info.get("numberOfAnalystOpinions")
        result["recommendation"] = info.get("recommendationKey")
        result["target_price"] = info.get("targetMeanPrice")
        result["target_high"] = info.get("targetHighPrice")
        result["target_low"] = info.get("targetLowPrice")
        result["revenue_estimate"] = info.get("revenueEstimate")

        # Earnings history (past surprises) — uses get_earnings_dates for full history
        try:
            eh = t.get_earnings_dates(limit=12)
            if eh is not None and not eh.empty:
                history = []
                for idx, row in eh.iterrows():
                    entry = {
                        "date": str(idx.date()) if hasattr(idx, 'date') else str(idx),
                        "eps_estimate": _safe_float(row.get("EPS Estimate")),
                        "eps_actual": _safe_float(row.get("Reported EPS")),
                        "surprise_pct": _safe_float(row.get("Surprise(%)")),
                    }
                    history.append(entry)
                result["earnings_history"] = history

                # Quarterly EPS estimate + next earnings date come from the
                # NEAREST upcoming unreported row (eps_actual None, dated
                # today or later). get_earnings_dates lists newest-first, so
                # history[0] is the FARTHEST future quarter when several
                # future rows exist — the old first-entry pick showed a date
                # (and estimate) two-plus quarters out instead of the next
                # report. A stale past-dated unreported row is never a
                # candidate: it can't be the "next" earnings date.
                today = datetime.now().date().isoformat()
                upcoming = [h for h in history
                            if h.get("eps_actual") is None
                            and (h.get("date") or "")[:10] >= today]
                if upcoming:
                    nearest = min(upcoming, key=lambda h: (h.get("date") or "")[:10])
                    result["eps_estimate"] = nearest.get("eps_estimate")
                    if not result["next_earnings_date"]:
                        result["next_earnings_date"] = nearest["date"]
        except Exception:
            pass

        return result

    except ImportError:
        return {
            "ticker": ticker.upper(),
            "error": "yfinance not installed",
            "next_earnings_date": None,
            "eps_estimate": None,
            "eps_actual_last": None,
            "revenue_estimate": None,
            "analyst_count": None,
            "eps_trend": {},
            "earnings_history": [],
        }
    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "error": str(e),
            "next_earnings_date": None,
            "eps_estimate": None,
            "eps_actual_last": None,
            "revenue_estimate": None,
            "analyst_count": None,
            "eps_trend": {},
            "earnings_history": [],
        }


def _safe_float(val) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_estimates_cached(ticker: str) -> dict:
    """Cached wrapper for fetch_estimates."""
    return fetch_estimates(ticker)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_earnings_calendar(tickers: tuple) -> list[dict]:
    """Upcoming earnings dates, served from the cross-instance snapshot and
    NEVER rebuilt on the interactive path.

    Regression 2026-06-13: the previous version rebuilt live whenever the
    snapshot was stale (6h TTL warmed only by the 6am nightly job, so stale
    every afternoon) or its ticker-count guard drifted (439<->440 from the
    universe fix). That rebuild is ~440 yfinance calls which, under Yahoo
    throttling, took *minutes* and blocked Home's Alert Inbox.

    Fix: serve the persisted snapshot WHATEVER its age — next-14-day earnings
    dates don't move intraday, so a morning copy is correct in the afternoon,
    and count drift no longer matters because we don't gate serving on it.
    When no snapshot exists yet, return [] (the inbox degrades to "no
    upcoming earnings" rather than hanging). ALL rebuilds happen in the
    background via refresh_earnings_calendar_snapshot(), called by the
    nightly refresh-universe job. The 30-min @st.cache_data ttl just bounds
    the per-instance Postgres round-trip; it never triggers a live build."""
    from data import cache as _cache
    try:
        snap = _cache.get("earnings_calendar_snap")
    except Exception:
        snap = None
    if snap and isinstance(snap.get("value"), list):
        return snap["value"]
    return []


def earnings_calendar_available() -> bool:
    """Whether an earnings-calendar snapshot has been built at all.

    fetch_earnings_calendar() returns [] BOTH when the snapshot is genuinely
    empty (no upcoming earnings) AND when it is missing/unreadable, so an empty
    result alone can't tell a real "0 reporting" from an unbuilt/failed
    snapshot. Callers that must show "unavailable" instead of a confident
    "0 reporting" (AUDIT-2026-07-02 #34) check this: True = a snapshot exists
    (an empty list is then genuine), False = no snapshot yet (feed unavailable).
    Never rebuilds — a pure snapshot-presence read, same non-blocking contract
    as fetch_earnings_calendar."""
    from data import cache as _cache
    try:
        snap = _cache.get("earnings_calendar_snap")
    except Exception:
        return False
    return bool(snap and isinstance(snap.get("value"), list))


def refresh_earnings_calendar_snapshot(tickers: tuple) -> list[dict]:
    """Build the earnings calendar live and persist the cross-instance
    snapshot. Background-only (nightly refresh-universe job) — NEVER call
    this on a user request: the live build is hundreds of yfinance calls
    and can block for minutes under Yahoo throttling."""
    from datetime import datetime
    from data import cache as _cache
    cal = _fetch_earnings_calendar_live(tickers)
    try:
        _cache.put("earnings_calendar_snap", {
            "cached_at": datetime.now().isoformat(),
            "guard": len(tickers),
            "value": cal,
        })
    except Exception as e:
        print("[estimates] could not persist earnings snapshot: "
              f"{type(e).__name__}: {e}")
    return cal


def _fetch_earnings_calendar_live(tickers: tuple) -> list[dict]:
    """Fetch earnings dates for multiple tickers IN PARALLEL."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    calendar = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_estimates_cached, ticker): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                est = future.result()
                if est and est.get("next_earnings_date"):
                    calendar.append({
                        "ticker": ticker,
                        "next_earnings_date": est["next_earnings_date"],
                        "eps_estimate": est.get("eps_estimate"),
                        "eps_fwd_annual": est.get("eps_fwd_annual"),
                        "eps_actual_last": est.get("eps_actual_last"),
                        "analyst_count": est.get("analyst_count"),
                        "target_price": est.get("target_price"),
                        "recommendation": est.get("recommendation"),
                    })
            except Exception as e:
                # Otherwise the bank silently vanishes from the calendar.
                print(f"[estimates] calendar entry failed for {ticker}: "
                      f"{type(e).__name__}: {e}")

    calendar.sort(key=lambda x: x.get("next_earnings_date", "9999"))
    return calendar


@st.cache_data(ttl=21600, show_spinner="Loading analyst estimates...")
def fetch_all_estimates(tickers: tuple) -> dict:
    """Fetch estimates for multiple tickers IN PARALLEL. Returns {ticker: estimates_dict}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_estimates_cached, ticker): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result() or {}
            except Exception:
                results[ticker] = {}

    return results
