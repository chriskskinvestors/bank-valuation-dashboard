"""
Peer comparison: percentile rankings and z-scores across banks.
"""

import pandas as pd
import numpy as np
from config import METRICS


def compute_percentile_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each numeric metric column, compute the percentile rank (0-100)
    across all banks in the DataFrame.

    Returns a new DataFrame with the same index/columns but values are percentiles.
    """
    metric_keys = [m["key"] for m in METRICS if m["key"] in df.columns]
    ranks = pd.DataFrame(index=df.index)
    ranks["ticker"] = df["ticker"]

    for key in metric_keys:
        col = df[key]
        if col.notna().sum() < 2:
            ranks[key] = None
            continue
        m = next((x for x in METRICS if x["key"] == key), None)
        ascending = True
        if m and m.get("color_rule") == "lower_better":
            ascending = False  # Lower is better → invert rank
        ranks[key] = col.rank(pct=True, ascending=ascending, na_option="keep") * 100

    return ranks


def compute_z_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Compute z-scores for each metric across all banks."""
    metric_keys = [m["key"] for m in METRICS if m["key"] in df.columns]
    z = pd.DataFrame(index=df.index)
    z["ticker"] = df["ticker"]

    for key in metric_keys:
        col = df[key]
        mean = col.mean()
        std = col.std()
        if std and std > 0:
            z[key] = (col - mean) / std
        else:
            z[key] = None

    return z


def get_peer_group_by_asset_size(df: pd.DataFrame, ticker: str, n: int = 5) -> list[str]:
    """
    Find the n closest peers by total assets.
    Returns list of peer tickers (excluding the target).
    """
    if "total_assets" not in df.columns or ticker not in df["ticker"].values:
        return df["ticker"].tolist()

    target_row = df[df["ticker"] == ticker]
    if target_row.empty or pd.isna(target_row.iloc[0]["total_assets"]):
        return df["ticker"].tolist()

    target_assets = target_row.iloc[0]["total_assets"]
    others = df[df["ticker"] != ticker].copy()
    others["asset_diff"] = (others["total_assets"] - target_assets).abs()
    others = others.sort_values("asset_diff")
    return others.head(n)["ticker"].tolist()


def build_radar_data(df: pd.DataFrame, tickers: list[str], metrics_keys: list[str]) -> dict:
    """
    Build data structure for radar/spider chart comparing banks on selected metrics.

    Returns {"categories": [...], "series": [{"name": ticker, "values": [...]}, ...]}
    """
    ranks = compute_percentile_ranks(df)
    categories = []
    for key in metrics_keys:
        m = next((x for x in METRICS if x["key"] == key), None)
        categories.append(m["label"] if m else key)

    series = []
    for ticker in tickers:
        row = ranks[ranks["ticker"] == ticker]
        if row.empty:
            continue
        values = [row.iloc[0].get(k) for k in metrics_keys]
        series.append({"name": ticker, "values": values})

    return {"categories": categories, "series": series}
