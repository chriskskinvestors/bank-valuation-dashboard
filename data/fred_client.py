"""
FRED (Federal Reserve Economic Data) client.

Uses the free `fredapi`-equivalent CSV endpoint. If FRED_API_KEY is set,
uses the JSON API for better error handling + metadata. Falls back to
scraping the public CSV download (no auth required).

Data is cached locally + in GCS (if configured) for 24 hours.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from io import StringIO

import pandas as pd
import streamlit as st

from data.cloud_storage import save_json, load_json

FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_CACHE_PREFIX = "macro_cache"
CACHE_TTL_SECONDS = 86400  # 24 hours


# Curated FRED series IDs
SERIES = {
    "FEDFUNDS": {"name": "Fed Funds Effective Rate", "units": "%", "category": "rates"},
    "DFF": {"name": "Fed Funds (Daily)", "units": "%", "category": "rates"},
    "DGS3MO": {"name": "3-Month Treasury", "units": "%", "category": "rates"},
    "DGS2": {"name": "2-Year Treasury", "units": "%", "category": "rates"},
    "DGS5": {"name": "5-Year Treasury", "units": "%", "category": "rates"},
    "DGS10": {"name": "10-Year Treasury", "units": "%", "category": "rates"},
    "DGS30": {"name": "30-Year Treasury", "units": "%", "category": "rates"},
    "T10Y2Y": {"name": "10Y - 2Y Spread", "units": "%", "category": "curve"},
    "T10Y3M": {"name": "10Y - 3M Spread", "units": "%", "category": "curve"},
    "MORTGAGE30US": {"name": "30-Year Mortgage", "units": "%", "category": "rates"},
    "UNRATE": {"name": "Unemployment", "units": "%", "category": "econ"},
    "CPIAUCSL": {"name": "CPI (All Urban)", "units": "Index", "category": "econ"},
    "GDP": {"name": "Real GDP", "units": "$B", "category": "econ"},
    "BAMLH0A0HYM2": {"name": "High Yield Spread", "units": "%", "category": "credit"},
    "DCOILWTICO": {"name": "WTI Oil", "units": "$/bbl", "category": "econ"},
    "DXY": {"name": "Dollar Index", "units": "Index", "category": "econ"},
}


def _is_fresh(cached: dict | None) -> bool:
    if not cached:
        return False
    ts = cached.get("cached_at", "")
    if not ts:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
        return age < CACHE_TTL_SECONDS
    except Exception:
        return False


def _fetch_csv(series_id: str) -> pd.DataFrame:
    """Fetch FRED data via CSV download (no API key needed)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        # FRED CSV has columns [DATE, SERIES_ID] — normalize
        if len(df.columns) >= 2:
            df.columns = ["date", "value"]
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["date"])
        return df
    except Exception as e:
        print(f"[FRED] CSV fetch error for {series_id}: {e}")
        return pd.DataFrame()


def _fetch_api(series_id: str) -> pd.DataFrame:
    """Fetch FRED data via JSON API (requires FRED_API_KEY)."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if not obs:
            return pd.DataFrame()
        df = pd.DataFrame(obs)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        return df
    except Exception as e:
        print(f"[FRED] API error for {series_id}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_series(series_id: str, years: int = 5) -> pd.DataFrame:
    """
    Fetch a FRED series with caching. Returns DataFrame with columns (date, value).
    """
    # Check cloud cache first
    cached = load_json(FRED_CACHE_PREFIX, f"{series_id}.json")
    if _is_fresh(cached) and cached.get("records"):
        df = pd.DataFrame(cached["records"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        cutoff = datetime.now() - timedelta(days=365 * years)
        return df[df["date"] >= cutoff].reset_index(drop=True)

    # Fetch fresh
    if FRED_API_KEY:
        df = _fetch_api(series_id)
    else:
        df = _fetch_csv(series_id)

    if df.empty:
        return df

    # Cache
    try:
        save_json(FRED_CACHE_PREFIX, f"{series_id}.json", {
            "series_id": series_id,
            "cached_at": datetime.now().isoformat(),
            "records": [
                {"date": d.strftime("%Y-%m-%d"), "value": float(v) if pd.notna(v) else None}
                for d, v in zip(df["date"], df["value"])
            ],
        })
    except Exception:
        pass

    cutoff = datetime.now() - timedelta(days=365 * years)
    return df[df["date"] >= cutoff].reset_index(drop=True)


def latest_value(series_id: str) -> float | None:
    """Return the most recent non-null value for a series."""
    df = fetch_series(series_id, years=1)
    if df.empty:
        return None
    valid = df.dropna(subset=["value"])
    if valid.empty:
        return None
    return float(valid["value"].iloc[-1])


def latest_date(series_id: str) -> datetime | None:
    df = fetch_series(series_id, years=1)
    if df.empty:
        return None
    valid = df.dropna(subset=["value"])
    if valid.empty:
        return None
    return valid["date"].iloc[-1]


def recession_probability() -> dict:
    """
    Rough recession probability signal based on:
    - 10Y-2Y yield curve inversion (historically 100% predictive with ~1Y lag)
    - 10Y-3M spread (NY Fed's preferred indicator)
    - Unemployment trend (Sahm rule proxy)

    Returns {"level": "low"|"medium"|"high", "score": 0-100, "factors": [...]}
    """
    factors = []
    score = 0  # out of 100

    # 10Y-2Y spread
    spread_2y = latest_value("T10Y2Y")
    if spread_2y is not None:
        if spread_2y < -0.50:
            score += 40
            factors.append(f"10Y-2Y inverted {spread_2y:.2f}pp — strong recession signal")
        elif spread_2y < 0:
            score += 20
            factors.append(f"10Y-2Y inverted {spread_2y:.2f}pp — moderate signal")
        elif spread_2y < 0.50:
            score += 5
            factors.append(f"10Y-2Y narrow at {spread_2y:.2f}pp")

    # 10Y-3M spread (NY Fed's favorite)
    spread_3m = latest_value("T10Y3M")
    if spread_3m is not None:
        if spread_3m < -0.50:
            score += 30
            factors.append(f"10Y-3M inverted {spread_3m:.2f}pp — NY Fed indicator")
        elif spread_3m < 0:
            score += 15
            factors.append(f"10Y-3M inverted {spread_3m:.2f}pp")

    # Unemployment trend (Sahm rule: 3mo avg up 0.5pp from 12mo low)
    unrate_df = fetch_series("UNRATE", years=2)
    if not unrate_df.empty and len(unrate_df) >= 12:
        u_recent = unrate_df["value"].tail(3).mean()
        u_low = unrate_df["value"].tail(12).min()
        if u_recent - u_low > 0.50:
            score += 30
            factors.append(f"Sahm rule triggered: unemployment up {u_recent - u_low:.2f}pp")
        elif u_recent - u_low > 0.30:
            score += 15
            factors.append(f"Unemployment rising ({u_recent - u_low:.2f}pp from low)")

    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    return {"level": level, "score": score, "factors": factors}


def get_macro_snapshot() -> dict:
    """Get a snapshot of key macro indicators with latest values + date."""
    result = {}
    for sid in ["FEDFUNDS", "DGS2", "DGS10", "DGS30", "T10Y2Y", "T10Y3M",
                "MORTGAGE30US", "UNRATE", "BAMLH0A0HYM2"]:
        val = latest_value(sid)
        date = latest_date(sid)
        result[sid] = {
            "name": SERIES.get(sid, {}).get("name", sid),
            "value": val,
            "date": date.strftime("%Y-%m-%d") if date is not None else None,
            "units": SERIES.get(sid, {}).get("units", ""),
        }
    return result
