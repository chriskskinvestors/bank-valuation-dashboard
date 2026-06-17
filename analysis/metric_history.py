"""
Faithful per-quarter metric history for the change/growth and trend/persistence
screening primitives.

Prior-quarter values are recomputed through the REAL metric engine
(``build_bank_metrics`` over cached FDIC history) — never a reimplementation — so a
metric's value "4 quarters ago" is exactly what the dashboard would have shown then.
Only FDIC-sourced metrics resolve (SEC/price inputs are omitted); SEC/price-derived
metrics come back None for history and so are reported as no-data by the engine,
never guessed.
"""
from __future__ import annotations

from data.loaders import load_fdic_hist
from analysis.metrics import build_bank_metrics


def metric_series(ticker: str, metric_keys, n_quarters: int) -> dict:
    """{metric_key: [v_latest, v_t-1, …, v_t-n]} — index 0 is the most recent
    quarter. Shorter lists when history is shorter than n_quarters+1."""
    keys = list(metric_keys)
    want = max(int(n_quarters) + 1, 2)
    hist = load_fdic_hist(ticker, min_quarters=min(want, 8))
    series: dict = {k: [] for k in keys}
    for q in range(min(want, len(hist))):
        # Each historical quarter's record IS hist[q]; hist[q:] is the window
        # available as-of that quarter for the engine's look-back computations.
        d = build_bank_metrics(ticker, hist[q], {}, {}, hist[q:])
        for k in keys:
            series[k].append(d.get(k))
    return series
