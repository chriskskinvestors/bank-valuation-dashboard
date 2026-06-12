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

Cache:
  Quotes are cached for 60 seconds in Postgres so a Streamlit page reload
  doesn't re-hit FMP. History is cached for 1 hour. Falls back to the same
  shape as get_empty_price() so views render gracefully without a key.
"""

from __future__ import annotations
import json
import os
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
        print(f"[FMP] {path} error: {type(e).__name__}: {e}")
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
# Public API — historical price chart
# ──────────────────────────────────────────────────────────────────────────

# Period → FMP endpoint mapping. Different time horizons use different
# series-of-data endpoints; intraday for short windows, daily otherwise.
_PERIOD_TO_ENDPOINT = {
    "1W":  ("historical-chart/15min", 7),       # last 7 days, 15-min bars
    "1M":  ("historical-chart/1hour", 30),      # last 30 days, 1-hour bars
    "3M":  ("historical-price-eod/full", 90),
    "1Y":  ("historical-price-eod/full", 365),
    "5Y":  ("historical-price-eod/full", 1826),
}


def get_history(ticker: str, period: str = "1Y") -> pd.DataFrame:
    """
    Return a DataFrame of (date, close, open, high, low, volume) for `ticker`
    over `period`. Period: "1W" | "1M" | "3M" | "1Y" | "5Y".
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
