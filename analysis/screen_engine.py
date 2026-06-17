"""
Screening engine — composable filter primitives over a metrics snapshot.

A screen is a list of filter SPECS (plain dicts, so they round-trip through
Streamlit session-state and saved screens). All specs in a screen are
AND-combined. Each primitive returns one of three verdicts per bank:

    True   — passes this filter
    False  — fails this filter
    None   — no data for this filter's metric → the bank is EXCLUDED and counted
             as "no data", never silently scored as a failure (cardinal rule:
             n/a is not the same as fails-the-screen).

Primitive kinds:
    absolute       {"kind":"absolute","metric":k,"op":op,"value":v}
    peer_relative  {"kind":"peer_relative","metric":k,"band":"Top"|"Bottom","pct":p}
                   percentile of the RAW value within the active scope (Top = high
                   values, Bottom = low values); the caller labels good/bad by metric.

Change/trend primitives (QoQ/YoY, N consecutive quarters) land here next and key
off an optional history provider — see docs/SCREEN-COMPARE-OVERHAUL.md (B5).
"""
from __future__ import annotations

from analysis.peer_groups import compute_peer_percentile

OPS = ("<", "≤", ">", "≥", "=")


def _cmp(v: float, op: str, fv: float) -> bool:
    if op == "<":
        return v < fv
    if op == "≤":
        return v <= fv
    if op == ">":
        return v > fv
    if op == "≥":
        return v >= fv
    if op == "=":
        return abs(v - fv) < 0.005
    return False


def _as_float(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _passes(bank: dict, spec: dict, pct_lookup: dict) -> bool | None:
    """Verdict for one bank against one spec: True / False / None (no data)."""
    kind = spec.get("kind")
    mk = spec.get("metric")

    if kind == "absolute":
        v = _as_float(bank.get(mk))
        if v is None:
            return None
        return _cmp(v, spec.get("op", "<"), float(spec.get("value", 0.0)))

    if kind == "peer_relative":
        pct = pct_lookup.get(mk, {}).get(bank.get("ticker"))
        if pct is None:
            return None
        p = float(spec.get("pct", 25.0))
        if spec.get("band", "Top") == "Top":
            return pct >= (100.0 - p)
        return pct <= p

    # Unknown / not-yet-implemented kind → treat as no-data so it can't silently
    # pass everything.
    return None


def _peer_percentiles(metrics: list[dict], metric_keys: set) -> dict:
    """For each peer-relative metric, the percentile rank of every bank's raw
    value within the active scope. {metric_key: {ticker: percentile|None}}."""
    out: dict[str, dict] = {}
    for mk in metric_keys:
        numeric = [v for v in (_as_float(m.get(mk)) for m in metrics) if v is not None]
        col: dict = {}
        for m in metrics:
            v = _as_float(m.get(mk))
            col[m.get("ticker")] = compute_peer_percentile(v, numeric) if v is not None else None
        out[mk] = col
    return out


def evaluate(metrics: list[dict], specs: list[dict]) -> tuple[list[dict], int]:
    """Apply AND-combined filter specs to the active scope.

    Returns (kept, n_excluded_nodata). A bank missing data for ANY active spec's
    metric is excluded as no-data and counted — never scored as a failure.
    Peer-relative percentiles resolve against `metrics` (the active scope) — peer
    membership IS the scope the caller passed in.
    """
    if not specs:
        return list(metrics), 0

    pr_metrics = {s.get("metric") for s in specs if s.get("kind") == "peer_relative"}
    pct_lookup = _peer_percentiles(metrics, pr_metrics) if pr_metrics else {}

    kept: list[dict] = []
    n_excluded_nodata = 0
    for m in metrics:
        missing = False
        fails = False
        for s in specs:
            verdict = _passes(m, s, pct_lookup)
            if verdict is None:
                missing = True
                break
            if not verdict:
                fails = True
                break
        if missing:
            n_excluded_nodata += 1
        elif not fails:
            kept.append(m)
    return kept, n_excluded_nodata
