"""
Financial Modeling Prep (FMP) price client.

Replaces IBKR for stock-price data in the cloud deployment (IBKR requires
a local TWS/Gateway process which doesn't run in Cloud Run).

Uses FMP's `stable` endpoint family — the legacy v3 endpoints were
deprecated 2025-08-31. Auth via FMP_API_KEY env var.

Functions:
  get_quote(ticker)              — single bank: {price, change, ...}
  get_quote_batch(tickers)       — bulk fetch for screening views
  get_eod_close_batch(tickers)   — bulk latest EOD close (quote fallback)
  get_history(ticker, period)    — historical price series (DataFrame)
  Analyst/comp family (own section below): price-target consensus/summary,
  grades, ratings snapshot, executive compensation, insider trading.

Cache:
  Quotes are cached for 60 seconds in Postgres so a Streamlit page reload
  doesn't re-hit FMP. History is cached for 1 hour. Falls back to the same
  shape as get_empty_price() so views render gracefully without a key.
"""

from __future__ import annotations
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd

FMP_BASE = "https://financialmodelingprep.com/stable"

# Override at the env-var level (Cloud Run mounts this from Secret Manager).
def _api_key() -> str:
    return (os.environ.get("FMP_API_KEY") or "").strip()


def _has_key() -> bool:
    return bool(_api_key())


# ──────────────────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────────────────

QUOTE_TTL_SECONDS = 60
HISTORY_TTL_SECONDS = 3600
EOD_CLOSE_TTL_SECONDS = 3600  # EOD bars only change once a day
FUNDAMENTALS_TTL_SECONDS = 21600  # 6h — TTM fundamentals change quarterly
ANALYST_TTL_SECONDS = 86400  # 24h — targets/ratings/comp move slowly
GRADES_TTL_SECONDS = 21600  # 6h — grade actions land intraday
INSIDER_TTL_SECONDS = 21600  # 6h — Form 4s land intraday too


def _cache_get(key: str, ttl: int) -> dict | None:
    """Tiny custom-TTL cache layered on the Postgres cache backend.

    Stores the timestamp inside the value so we can use a shorter TTL
    than the global cache.TTL_SECONDS (which is 24h) without changing
    the backend.
    """
    from data import cache as _cache
    cached = _cache.get(key)
    if not cached:
        return None
    ts = cached.get("_ts")
    if ts and time.time() - float(ts) > ttl:
        return None
    return cached.get("_v")


def _cache_put(key: str, value):
    from data import cache as _cache
    _cache.put(key, {"_ts": time.time(), "_v": value})


# ──────────────────────────────────────────────────────────────────────────
# Generic GET with retry
# ──────────────────────────────────────────────────────────────────────────

def _get(path: str, params: dict, timeout: int = 10) -> object | None:
    """GET against FMP via the shared retry policy. Returns parsed JSON or
    None on failure (logged)."""
    from data.http import get_with_retry
    if not _has_key():
        return None
    params = {**params, "apikey": _api_key()}
    try:
        resp = get_with_retry(f"{FMP_BASE}/{path}", params=params, timeout=timeout)
        return resp.json() if resp is not None else None
    except Exception as e:
        # NEVER log the raw exception text: requests' HTTPError embeds the
        # full URL including apikey=<secret>, which would leak the key into
        # Cloud Run logs on every failed call (found live 2026-06-12).
        msg = re.sub(r"apikey=[^&\s'\"]+", "apikey=***", str(e))
        print(f"[FMP] {path} error: {type(e).__name__}: {msg}")
        return None


# ──────────────────────────────────────────────────────────────────────────
# Public API — quote
# ──────────────────────────────────────────────────────────────────────────

# Shape compatible with data/ibkr_client.get_empty_price() so callers don't
# need to know which source the data came from.
def _empty_quote() -> dict:
    return {
        "price": None,
        "bid": None,
        "ask": None,
        "close": None,
        "open": None,
        "high": None,
        "low": None,
        "volume": None,
        "change": None,
        "change_pct": None,
    }


def get_quote(ticker: str) -> dict:
    """Single-ticker quote. Returns dict with same keys as IBKR client."""
    if not _has_key():
        return _empty_quote()
    ticker = ticker.upper()
    cache_key = f"fmp_quote:{ticker}"
    cached = _cache_get(cache_key, QUOTE_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("quote", {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return _empty_quote()
    row = data[0]

    out = {
        "price": row.get("price"),
        "bid": None,  # not in stable/quote
        "ask": None,
        "close": row.get("previousClose"),
        "open": row.get("open"),
        "high": row.get("dayHigh"),
        "low": row.get("dayLow"),
        "volume": row.get("volume"),
        "change": row.get("change"),
        "change_pct": row.get("changePercentage"),
    }
    _cache_put(cache_key, out)
    return out


def get_quote_batch(tickers: Iterable[str],
                    max_per_min: int | None = None) -> dict[str, dict]:
    """
    Bulk-fetch quotes. FMP's stable endpoint accepts a single symbol per
    call; we fan out in a small thread pool and cache each response.

    max_per_min: when set, pace request submission so we stay under FMP's
    per-minute rate cap. A cold full-universe burst (~369 symbols) otherwise
    exceeds the ~300/min plan limit and ~13% of calls get throttled to empty.
    The warm-price job passes ~270 here; the live UI path leaves it None
    (it only ever fetches a handful of cache-miss tickers).

    Returns {ticker: quote_dict}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}
    if not _has_key():
        return {t: _empty_quote() for t in tickers}

    interval = (60.0 / max_per_min) if max_per_min else 0.0

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for t in tickers:
            futures[ex.submit(get_quote, t)] = t
            if interval:
                time.sleep(interval)  # spread submissions under the rate cap
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception as e:
                print(f"[FMP] {t} batch error: {e}")
                out[t] = _empty_quote()
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API — pre/post-market (aftermarket) quote
# ──────────────────────────────────────────────────────────────────────────

# Aftermarket bid/ask, for the Home ETF table's "Aft" column. On plan-denial
# (401/403) or any failure it returns the empty shape, and callers MUST treat
# missing data as "no print" (render —), never a guess — so the feature
# degrades to EOD cleanly if the key tier ever lacks it.
def _empty_aftermarket() -> dict:
    return {"bid": None, "ask": None, "volume": None, "timestamp": None}


def get_aftermarket_quote(ticker: str, cache_only: bool = False) -> dict:
    """Pre/post-market bid/ask via FMP's stable `aftermarket-quote`.
    Returns {bid, ask, volume, timestamp}; empty shape on denial/failure.
    cache_only=True: read the cache, return the empty shape on a miss, never do
    the live FMP fetch — for the Home render path so a cold cache can't block it."""
    if not _has_key():
        return _empty_aftermarket()
    ticker = ticker.upper()
    cache_key = f"fmp_aq:{ticker}"
    cached = _cache_get(cache_key, QUOTE_TTL_SECONDS)
    if cached is not None:
        return cached
    if cache_only:
        return _empty_aftermarket()
    data = _get("aftermarket-quote", {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return _empty_aftermarket()
    row = data[0]
    out = {
        "bid": row.get("bidPrice"),
        "ask": row.get("askPrice"),
        "volume": row.get("volume"),
        "timestamp": row.get("timestamp"),
    }
    _cache_put(cache_key, out)
    return out


def get_aftermarket_quote_batch(tickers: Iterable[str],
                                max_per_min: int | None = None,
                                cache_only: bool = False) -> dict[str, dict]:
    """Bulk aftermarket quotes — single-symbol fan-out + pacing, same shape
    discipline as get_quote_batch. Returns {ticker: aftermarket_dict}.
    cache_only=True: read the cache only (empty shape on a miss), never live-fetch
    — the Home ETF table passes this so a cold cache can't block the render on
    N live FMP calls; a background job keeps the cache warm."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}
    if not _has_key():
        return {t: _empty_aftermarket() for t in tickers}
    interval = (60.0 / max_per_min) if max_per_min else 0.0
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for t in tickers:
            futures[ex.submit(get_aftermarket_quote, t, cache_only)] = t
            if interval:
                time.sleep(interval)
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = _empty_aftermarket()
    return out


def aftermarket_move(bid, ask, last, max_rel_spread: float = 0.012):
    """Post-market % move from the regular-session `last` to the bid/ask
    MID — only when the spread is tight enough to be a real print (default
    ≤1.2% of mid). Wide / one-sided / non-positive / unparseable quotes
    return None so the UI shows '—' instead of a fabricated move."""
    try:
        bid = float(bid); ask = float(ask); last = float(last)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or last <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0 or (ask - bid) / mid > max_rel_spread:
        return None
    return (mid / last - 1.0) * 100.0


# ──────────────────────────────────────────────────────────────────────────
# Public API — latest EOD close (quote-endpoint fallback)
# ──────────────────────────────────────────────────────────────────────────

# The FMP Starter plan DENIES the /quote endpoints (403) but allows the
# chart/EOD history family. When quotes are plan-denied, the warm-price job
# falls back to the most recent daily close from the same endpoint
# get_history uses. The returned `date` is the REAL trading date of the
# close — cache writers must stamp rows with it, never now(), so the
# staleness badge stays honest (EOD data is yesterday's close).

def _empty_eod() -> dict:
    return {"price": None, "close": None, "date": None,
            "change": None, "change_pct": None, "volume": None}


def _get_eod_close(ticker: str) -> dict:
    """Latest EOD bar for `ticker` via historical-price-eod/full.

    Returns {price, close, date, change, change_pct, volume}: price = most
    recent daily close, close = prior session's close (prev_close), date =
    the trading date of `price` (YYYY-MM-DD). All-None shape on failure.
    Window is 7 calendar days so weekends + Monday holidays still contain a
    trading day and a prior session for the change calc.
    """
    if not _has_key():
        return _empty_eod()
    ticker = ticker.upper()
    cache_key = f"fmp_eod_close:{ticker}"
    cached = _cache_get(cache_key, EOD_CLOSE_TTL_SECONDS)
    if cached is not None:
        return cached

    from_d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_d = datetime.utcnow().strftime("%Y-%m-%d")
    data = _get("historical-price-eod/full",
                {"symbol": ticker, "from": from_d, "to": to_d}, timeout=15)
    if not data or not isinstance(data, list):
        return _empty_eod()
    rows = [r for r in data if isinstance(r, dict)
            and r.get("close") is not None and r.get("date")]
    if not rows:
        return _empty_eod()
    rows.sort(key=lambda r: str(r.get("date")))  # ISO dates sort lexically

    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    price = last.get("close")
    prev_close = prev.get("close") if prev else None
    change = change_pct = None
    try:
        if price is not None and prev_close:
            change = float(price) - float(prev_close)
            change_pct = change / float(prev_close) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        change = change_pct = None

    out = {
        "price": price,
        "close": prev_close,
        "date": str(last.get("date"))[:10],
        "change": change,
        "change_pct": change_pct,
        "volume": last.get("volume"),
    }
    _cache_put(cache_key, out)
    return out


def get_eod_close_batch(tickers: Iterable[str],
                        max_per_min: int | None = 270) -> dict[str, dict]:
    """
    Bulk latest-EOD-close — the warm-price job's fallback when the plan
    denies /quote. Same fan-out + pacing discipline as get_quote_batch
    (one symbol per call, paced under FMP's ~300/min cap by default since
    the only expected caller is the full-universe job).

    Returns {ticker: {price, close, date, change, change_pct, volume}};
    `date` is the real trading date of the close — stamp cache writes with
    it, never now().
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}
    if not _has_key():
        return {t: _empty_eod() for t in tickers}

    interval = (60.0 / max_per_min) if max_per_min else 0.0

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for t in tickers:
            futures[ex.submit(_get_eod_close, t)] = t
            if interval:
                time.sleep(interval)  # spread submissions under the rate cap
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                out[t] = fut.result()
            except Exception as e:
                print(f"[FMP] {t} eod batch error: {e}")
                out[t] = _empty_eod()
    return out


def get_fundamentals(ticker: str) -> dict:
    """
    FMP's pre-computed TTM HoldCo fundamentals — used as an INDEPENDENT
    cross-check against our SEC-derived values (not as the primary source;
    the dashboard keeps computing from filings so provenance is preserved).

    Returns a normalized dict (None for any field FMP doesn't provide):
      pe_ratio, pb_ratio, bvps, tbvps, dividend_yield (percent)

    These are exactly the SEC-derived ratios where our own derivation
    (TTM-EPS summation, XBRL tag choice, goodwill/intangible handling,
    share count) could drift — so disagreement flags a real gap.
    """
    if not _has_key():
        return {}
    ticker = ticker.upper()
    cache_key = f"fmp_fund:{ticker}"
    cached = _cache_get(cache_key, FUNDAMENTALS_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("ratios-ttm", {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return {}
    r = data[0]

    def _f(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    dy = _f(r.get("dividendYieldTTM"))
    out = {
        "pe_ratio": _f(r.get("priceToEarningsRatioTTM")),
        "pb_ratio": _f(r.get("priceToBookRatioTTM")),
        "bvps": _f(r.get("bookValuePerShareTTM")),
        "tbvps": _f(r.get("tangibleBookValuePerShareTTM")),
        "dividend_yield": (dy * 100) if dy is not None else None,
    }
    _cache_put(cache_key, out)
    return out


def get_fundamentals_batch(tickers: Iterable[str],
                           max_per_min: int | None = None) -> dict[str, dict]:
    """Bulk get_fundamentals (6h-cached). Paced like get_quote_batch so a cold
    cache doesn't burst past FMP's rate cap. Returns {ticker: fundamentals}."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = [t.upper() for t in tickers if t]
    if not tickers or not _has_key():
        return {}
    interval = (60.0 / max_per_min) if max_per_min else 0.0
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for t in tickers:
            futures[ex.submit(get_fundamentals, t)] = t
            if interval:
                time.sleep(interval)
        for fut, t in futures.items():
            try:
                out[t] = fut.result()
            except Exception:
                out[t] = {}
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API — security profile (share-class classification)
# ──────────────────────────────────────────────────────────────────────────

PROFILE_TTL_SECONDS = 604800  # 7d — a security's name/type effectively never changes


def get_company_name(ticker: str) -> str | None:
    """The FMP `profile` security name (e.g. 'Customers Bancorp, Inc 5.375% S'
    for a preferred vs 'Customers Bancorp, Inc.' for the common). Used by
    data.share_class to VERIFY the structural common/preferred split — never as
    the sole classifier (FMP omits the marker for most preferreds). None when
    FMP is unconfigured or the lookup fails."""
    if not _has_key():
        return None
    ticker = ticker.upper()
    cache_key = f"fmp_profile_name:{ticker}"
    cached = _cache_get(cache_key, PROFILE_TTL_SECONDS)
    if cached is not None:
        return cached or None
    data = _get("profile", {"symbol": ticker})
    row = _first_row(data)
    name = (row.get("companyName") if row else None) or ""
    _cache_put(cache_key, name)
    return name or None


# ──────────────────────────────────────────────────────────────────────────
# Public API — historical price chart
# ──────────────────────────────────────────────────────────────────────────

# Period → FMP endpoint mapping. Different time horizons use different
# series-of-data endpoints; intraday for short windows, daily otherwise.
_PERIOD_TO_ENDPOINT = {
    "1W":  ("historical-chart/15min", 7),       # last 7 days, 15-min bars
    "1M":  ("historical-chart/1hour", 30),      # last 30 days, 1-hour bars
    "3M":  ("historical-price-eod/full", 90),
    "1Y":  ("historical-price-eod/full", 365),
    "2Y":  ("historical-price-eod/full", 731),
    "5Y":  ("historical-price-eod/full", 1826),
}


def get_history(ticker: str, period: str = "1Y", cache_only: bool = False) -> pd.DataFrame:
    """
    Return a DataFrame of (date, close, open, high, low, volume) for `ticker`
    over `period`. Period: "1W" | "1M" | "3M" | "1Y" | "2Y" | "5Y".

    cache_only=True: read the persisted cache and return empty on a miss — NEVER
    do the live FMP fetch. Render paths (the Home overlay chart, looped over
    several ETFs) pass this so a cold/expired cache can't block the request
    thread on N×15s FMP calls; a background job (jobs/refresh_home_snapshot)
    keeps the cache warm.
    """
    if not _has_key():
        return pd.DataFrame()
    ticker = ticker.upper()
    cache_key = f"fmp_history:{ticker}:{period}"
    cached = _cache_get(cache_key, HISTORY_TTL_SECONDS)
    if cached is not None:
        try:
            return pd.DataFrame(cached)
        except Exception:
            pass
    if cache_only:
        return pd.DataFrame()

    endpoint, days = _PERIOD_TO_ENDPOINT.get(period, _PERIOD_TO_ENDPOINT["1Y"])

    from_d = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    to_d = datetime.utcnow().strftime("%Y-%m-%d")

    params = {"symbol": ticker, "from": from_d, "to": to_d}
    data = _get(endpoint, params, timeout=15)
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    if df.empty:
        return df

    # Stable's historical-chart returns: date, open, high, low, close, volume
    # Stable's historical-price-eod returns: date, open, high, low, close, volume, ...
    # Normalize column names.
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
    keep_cols = [c for c in ["date", "open", "high", "low", "close", "volume"]
                 if c in df.columns]
    df = df[keep_cols].reset_index(drop=True)

    # Cache the parsed records
    try:
        _cache_put(cache_key, df.to_dict("records"))
    except Exception:
        pass
    return df


def get_price_change(ticker: str) -> dict | None:
    """Period price-change percentages from stable/stock-price-change
    (1D / 5D / 1M / 3M / 6M / ytd / 1Y / ...). Returns the symbol's dict, or
    None on failure / no data.

    FMP computes the year-anchored `ytd` figure directly, so the Home Movers
    YTD column reads it from here rather than re-deriving from EOD history
    (which mishandles the Jan-1 anchor when history is short or gapped).
    """
    if not _has_key():
        return None
    data = _get("stock-price-change", {"symbol": ticker.upper()})
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def get_earnings_calendar(from_date: str, to_date: str) -> list[dict] | None:
    """FMP stable earnings-calendar for [from_date, to_date], all symbols. Each
    row: {symbol, date, time ('bmo'|'amc'|...), epsEstimated, revenueEstimated,
    periodEnding, confirmed}. None on failure.

    Source of reliable, universe-wide report timing (before/after market open)
    and the confirmed-date flag — neither of which the yfinance estimate carries.

    Cached 1h in the cross-instance Postgres cache (keyed on the date range): the
    Earnings page renders its 7 sub-tabs with st.tabs, which evaluates EVERY tab
    body on each load, so the Calls & Webcasts tab's whole-market 75-day fetch ran
    live on every Earnings open. Failures are never cached (house pattern), so a
    transient FMP error retries rather than poisoning the cache with None.
    """
    if not _has_key():
        return None
    key = f"fmp_earn_cal:{from_date}:{to_date}"
    cached = _cache_get(key, 3600)
    if cached is not None:
        return cached
    data = _get("earnings-calendar", {"from": from_date, "to": to_date}, timeout=15)
    out = data if isinstance(data, list) else None
    if out is not None:
        _cache_put(key, out)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API — analyst coverage & executive compensation
# ──────────────────────────────────────────────────────────────────────────

# All parsing is tolerant (.get() everything, never KeyError): FMP's field
# names vary across endpoint generations, and a missing field must surface
# as None — never a fabricated value. Failures return None/[] with one
# [FMP] log line; failures are never cached (house pattern).
#
# Every record carries `source_url` (the UI's click-through-to-source rule):
# the SEC filing link when FMP provides one (exec comp → DEF 14A, insider →
# Form 4), else the key-free FMP endpoint URL. Cache keys carry :v2 where
# the shape gained source_url — a 24h-cached pre-stamp record must never
# serve into the UI contract.

def _num(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _int(x) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _pick(row: dict, *keys):
    """First non-None value among row[keys] — `or`-chains would swallow
    legitimate 0 / "" values (comp rows really do have $0 bonuses)."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _first_row(data) -> dict | None:
    """The single-record FMP shape: a one-element list of one dict."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _source_url(path: str, ticker: str) -> str:
    """Key-free FMP endpoint URL — the source stamp for records whose
    underlying source is FMP itself (no SEC filing link to point at)."""
    return f"{FMP_BASE}/{path}?symbol={ticker}"


def get_price_target_consensus(ticker: str) -> dict | None:
    """Analyst price-target consensus: {consensus, high, low, median,
    source_url} (floats or None per field), or None on failure / no
    coverage."""
    if not _has_key():
        return None
    ticker = ticker.upper()
    cache_key = f"fmp_pt_consensus:v2:{ticker}"
    cached = _cache_get(cache_key, ANALYST_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("price-target-consensus", {"symbol": ticker})
    row = _first_row(data)
    if row is None:
        print(f"[FMP] price-target-consensus: no data for {ticker}")
        return None
    out = {
        "consensus": _num(row.get("targetConsensus")),
        "high": _num(row.get("targetHigh")),
        "low": _num(row.get("targetLow")),
        "median": _num(row.get("targetMedian")),
        "source_url": _source_url("price-target-consensus", ticker),
    }
    _cache_put(cache_key, out)
    return out


def get_price_target_summary(ticker: str) -> dict | None:
    """Price-target summary by window: counts + average targets for last
    month/quarter/year/all-time, plus the publisher list. None on failure.

    FMP returns `publishers` as a JSON-encoded string ('["Benzinga", ...]');
    normalized here to a real list ([] when absent/unparseable)."""
    if not _has_key():
        return None
    ticker = ticker.upper()
    cache_key = f"fmp_pt_summary:v2:{ticker}"
    cached = _cache_get(cache_key, ANALYST_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("price-target-summary", {"symbol": ticker})
    row = _first_row(data)
    if row is None:
        print(f"[FMP] price-target-summary: no data for {ticker}")
        return None

    publishers = row.get("publishers")
    if isinstance(publishers, str):
        try:
            publishers = json.loads(publishers)
        except (TypeError, ValueError):
            publishers = None
    if not isinstance(publishers, list):
        publishers = []

    out = {
        "last_month_count": _int(row.get("lastMonthCount")),
        "last_month_avg": _num(row.get("lastMonthAvgPriceTarget")),
        "last_quarter_count": _int(row.get("lastQuarterCount")),
        "last_quarter_avg": _num(row.get("lastQuarterAvgPriceTarget")),
        "last_year_count": _int(row.get("lastYearCount")),
        "last_year_avg": _num(row.get("lastYearAvgPriceTarget")),
        "all_time_count": _int(row.get("allTimeCount")),
        "all_time_avg": _num(row.get("allTimeAvgPriceTarget")),
        "publishers": publishers,
        "source_url": _source_url("price-target-summary", ticker),
    }
    _cache_put(cache_key, out)
    return out


def get_analyst_grades(ticker: str, limit: int = 50) -> list[dict]:
    """Recent analyst grade actions, newest first as FMP returns them:
    [{date, firm, action, from_grade, to_grade, source_url}]. [] on
    failure or no coverage (the steady state for most small banks).

    Field names vary across FMP generations — tolerant mapping:
    gradingCompany/company, previousGrade/fromGrade, newGrade/toGrade/grade.
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    cache_key = f"fmp_grades:v2:{ticker}:{limit}"
    cached = _cache_get(cache_key, GRADES_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("grades", {"symbol": ticker, "limit": limit})
    if not isinstance(data, list) or not data:
        print(f"[FMP] grades: no data for {ticker}")
        return []
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        date = r.get("date")
        out.append({
            "date": str(date)[:10] if date else None,
            "firm": _pick(r, "gradingCompany", "company", "analystCompany"),
            "action": r.get("action"),
            "from_grade": _pick(r, "previousGrade", "fromGrade"),
            "to_grade": _pick(r, "newGrade", "toGrade", "grade"),
            "source_url": _source_url("grades", ticker),
        })
    _cache_put(cache_key, out)
    return out


def get_ratings_snapshot(ticker: str) -> dict | None:
    """FMP's current composite rating + per-factor sub-scores:
    {rating, overall_score, dcf_score, roe_score, roa_score,
     debt_to_equity_score, pe_score, pb_score, source_url}.
    None on failure."""
    if not _has_key():
        return None
    ticker = ticker.upper()
    cache_key = f"fmp_ratings:v2:{ticker}"
    cached = _cache_get(cache_key, ANALYST_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("ratings-snapshot", {"symbol": ticker})
    row = _first_row(data)
    if row is None:
        print(f"[FMP] ratings-snapshot: no data for {ticker}")
        return None
    out = {
        "rating": row.get("rating"),
        "overall_score": _int(row.get("overallScore")),
        "dcf_score": _int(row.get("discountedCashFlowScore")),
        "roe_score": _int(row.get("returnOnEquityScore")),
        "roa_score": _int(row.get("returnOnAssetsScore")),
        "debt_to_equity_score": _int(row.get("debtToEquityScore")),
        "pe_score": _int(row.get("priceToEarningsScore")),
        "pb_score": _int(row.get("priceToBookScore")),
        "source_url": _source_url("ratings-snapshot", ticker),
    }
    _cache_put(cache_key, out)
    return out


def get_executive_compensation(ticker: str) -> list[dict]:
    """Named-executive compensation rows from DEF 14A proxies:
    [{name, title, year, salary, bonus, stock_awards, incentive, other,
      total, filing_url, source_url}]. [] on failure.

    PLAN STATUS (live-probed 2026-06-12, WAL): NOT available on Starter —
    stable/executive-compensation 404s, governance-executive-compensation
    402s (premium), legacy v4 403s (sunset). Returns [] until either the
    plan changes or the Compensation tab moves to DEF 14A parsing via
    EDGAR directly (preferred: primary source).

    `filing_url` is FMP's SEC archive link to the underlying proxy —
    kept per the provenance rule (every displayed row links its DEF 14A);
    `source_url` is the same link (endpoint URL only when FMP omits it).
    FMP often returns name+title combined in `nameAndPosition`; mapping is
    tolerant and `title` stays None when no separate field exists.
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    cache_key = f"fmp_exec_comp:v2:{ticker}"
    cached = _cache_get(cache_key, ANALYST_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("executive-compensation", {"symbol": ticker})
    if not isinstance(data, list) or not data:
        print(f"[FMP] executive-compensation: no data for {ticker}")
        return []
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        filing = _pick(r, "link", "url", "filingUrl")
        out.append({
            "name": _pick(r, "name", "nameAndPosition"),
            "title": _pick(r, "position", "title"),
            "year": _int(r.get("year")),
            "salary": _num(r.get("salary")),
            "bonus": _num(r.get("bonus")),
            "stock_awards": _num(_pick(r, "stockAward", "stockAwards")),
            "incentive": _num(_pick(r, "incentivePlanCompensation",
                                    "incentive")),
            "other": _num(_pick(r, "allOtherCompensation", "other")),
            "total": _num(r.get("total")),
            "filing_url": filing,
            "source_url": filing or _source_url("executive-compensation",
                                                ticker),
        })
    _cache_put(cache_key, out)
    return out


def get_insider_trading(ticker: str, limit: int = 50) -> list[dict]:
    """Latest insider (Form 4) trades, newest first as FMP returns them:
    [{filing_date, transaction_date, insider, relationship,
      transaction_type, acquisition_or_disposition, shares, price,
      shares_owned, security, form_type, source_url}]. [] on failure or
    no recent filings.

    `source_url` is the SEC EDGAR link to the underlying Form 4 when FMP
    provides it (stable: `url`, legacy v4: `link`), else the endpoint URL.
    The PRIMARY insider pipeline stays data/form4_client.py (EDGAR
    direct); this is the independent FMP view for cross-checking.
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    cache_key = f"fmp_insider:{ticker}:{limit}"
    cached = _cache_get(cache_key, INSIDER_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("insider-trading/search",
                {"symbol": ticker, "page": 0, "limit": limit})
    if not isinstance(data, list) or not data:
        print(f"[FMP] insider-trading: no data for {ticker}")
        return []
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        f_date = r.get("filingDate")
        t_date = r.get("transactionDate")
        sec_link = _pick(r, "url", "link")
        out.append({
            "filing_date": str(f_date)[:10] if f_date else None,
            "transaction_date": str(t_date)[:10] if t_date else None,
            "insider": _pick(r, "reportingName", "name"),
            "relationship": _pick(r, "typeOfOwner", "relationship"),
            "transaction_type": r.get("transactionType"),
            "acquisition_or_disposition":
                r.get("acquisitionOrDisposition"),
            "shares": _num(r.get("securitiesTransacted")),
            "price": _num(r.get("price")),
            "shares_owned": _num(r.get("securitiesOwned")),
            "security": r.get("securityName"),
            "form_type": r.get("formType"),
            "source_url": sec_link or _source_url("insider-trading/search",
                                                  ticker),
        })
    _cache_put(cache_key, out)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Public API — company press releases (news/events feed)
# ──────────────────────────────────────────────────────────────────────────

PRESS_RELEASE_TTL_SECONDS = 1800  # 30m — matches the poll cadence; a release's
#                                   text never changes once published.


def get_press_releases(ticker: str, limit: int = 25) -> list[dict]:
    """Recent FIRST-PARTY company press releases for `ticker` from FMP's
    press-release index (which aggregates Business Wire / PR Newswire / etc.),
    newest first:
        [{published_at, title, publisher, url, text}]

    `url` is the PRIMARY source (businesswire.com / prnewswire.com / the IR
    site) — FMP is only the discovery index here, never the displayed source,
    so this stays consistent with the primary-sources rule. `published_at` is
    the raw FMP timestamp string ('YYYY-MM-DD HH:MM:SS'); the caller parses it.

    [] on failure / no key (failures never cached — house pattern). Uses the
    shared _get() so the apikey is redacted from any error log.
    """
    if not _has_key():
        return []
    ticker = ticker.upper()
    cache_key = f"fmp_press:{ticker}:{limit}"
    cached = _cache_get(cache_key, PRESS_RELEASE_TTL_SECONDS)
    if cached is not None:
        return cached

    data = _get("news/press-releases", {"symbols": ticker, "limit": limit})
    if not isinstance(data, list) or not data:
        return []
    out: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "publisher": (r.get("publisher") or r.get("site") or "").strip(),
            "url": (r.get("url") or "").strip(),
            "published_at": str(r.get("publishedDate") or "").strip(),
            "text": (r.get("text") or "").strip(),
        })
    _cache_put(cache_key, out)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Diagnostic
# ──────────────────────────────────────────────────────────────────────────

def status() -> dict:
    """For the Data Quality tab — confirm FMP wiring is healthy."""
    if not _has_key():
        return {"ok": False, "reason": "FMP_API_KEY not set"}
    try:
        q = get_quote("JPM")
        return {"ok": q.get("price") is not None,
                "sample_price_jpm": q.get("price"),
                "key_prefix": _api_key()[:6] + "...",
                "endpoint": FMP_BASE}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
