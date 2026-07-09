"""
Peer grouping logic — by asset size and by business mix.

Asset size tiers (standard analyst framework):
  - Community:     <$10B total assets
  - Regional:      $10B - $100B
  - Large Regional: $100B - $1T
  - Money-Center:  >$1T

Business-mix groups (based on dominant balance-sheet exposure):
  - CRE-heavy:     CRE/capital >= 300%
  - Retail-heavy:  non-int-bearing deposits > 30%, consumer loans > 20%
  - C&I-focused:   C&I loans > 30% of loans
  - Mortgage-heavy: residential RE > 40% of loans
  - Diversified:   everything else
"""

from __future__ import annotations

import pandas as pd


# Asset size thresholds (raw $)
COMMUNITY_CAP = 10e9
REGIONAL_CAP = 100e9
LARGE_REGIONAL_CAP = 1e12


def asset_size_tier(total_assets_usd: float | None) -> str | None:
    """Classify a bank by asset-size tier."""
    if total_assets_usd is None or total_assets_usd <= 0:
        return None
    if total_assets_usd < COMMUNITY_CAP:
        return "Community (<$10B)"
    elif total_assets_usd < REGIONAL_CAP:
        return "Regional ($10-100B)"
    elif total_assets_usd < LARGE_REGIONAL_CAP:
        return "Large Regional ($100B-$1T)"
    else:
        return "Money-Center (>$1T)"


# Finer asset bands for the Screen/Compare "Asset-size tier" SCOPE picker only.
# These are deliberately separate from asset_size_tier above: that 4-tier framework
# feeds the peer-percentile cohorts (Peer Rank / Valuation verdict) and must stay
# fixed, whereas the scope wants a tighter selectable band (e.g. $1-10B).
_ASSET_BANDS = [
    ("< $1B",     0.0,     1e9),
    ("$1-10B",    1e9,     10e9),
    ("$10-50B",   10e9,    50e9),
    ("$50-250B",  50e9,    250e9),
    ("> $250B",   250e9,   float("inf")),
]


def asset_size_band(total_assets_usd: float | None) -> str | None:
    """Classify a bank into a finer scope-only asset band (raw $). None for
    missing / non-positive assets, so a bank with no value is simply unbucketed
    rather than guessed into a band."""
    if total_assets_usd is None or total_assets_usd <= 0:
        return None
    for label, lo, hi in _ASSET_BANDS:
        if lo <= total_assets_usd < hi:
            return label
    return None


def asset_size_bands(all_metrics: list[dict]) -> dict:
    """Banks bucketed into the finer scope-only asset bands, ascending, empties
    dropped. Distinct from group_banks()['by_size'] (the coarse peer tiers)."""
    buckets: dict[str, list] = {}
    for m in all_metrics:
        band = asset_size_band(m.get("total_assets"))
        if band:
            buckets.setdefault(band, []).append(m)
    return {label: buckets[label] for label, _, _ in _ASSET_BANDS if label in buckets}


def business_mix_tier(metrics: dict) -> str:
    """Classify a bank by dominant business mix."""
    cre_cap = metrics.get("cre_to_capital")
    nonint_dep_pct = metrics.get("nonint_dep_pct")
    ln_consumer_pct = metrics.get("ln_consumer_pct")
    ln_ci_pct = metrics.get("ln_ci_pct")
    ln_resi_pct = metrics.get("ln_resi_pct")

    try:
        if cre_cap is not None and cre_cap >= 300:
            return "CRE-heavy"
        if ln_resi_pct is not None and ln_resi_pct >= 40:
            return "Mortgage-heavy"
        if ln_ci_pct is not None and ln_ci_pct >= 30:
            return "C&I-focused"
        if (nonint_dep_pct is not None and nonint_dep_pct >= 30
                and ln_consumer_pct is not None and ln_consumer_pct >= 20):
            return "Retail-heavy"
    except (TypeError, ValueError):
        pass
    return "Diversified"


def group_banks(all_metrics: list[dict]) -> dict:
    """
    Group banks by asset size and business mix.

    Returns:
        {
            "by_size": {"Community (<$10B)": [metrics...], "Regional": [...], ...},
            "by_mix":  {"CRE-heavy": [...], "Diversified": [...], ...},
        }
    """
    by_size = {}
    by_mix = {}

    for m in all_metrics:
        # total_assets is ALWAYS raw dollars (analysis/metrics.py converts FDIC
        # $thousands at the boundary). The old "< 1e9 → ×1000" guess here
        # double-converted genuine sub-$1B banks into trillion-dollar tiers.
        assets = m.get("total_assets")
        tier = asset_size_tier(assets)
        if tier:
            by_size.setdefault(tier, []).append(m)

        mix = business_mix_tier(m)
        by_mix.setdefault(mix, []).append(m)

    return {"by_size": by_size, "by_mix": by_mix}


def get_peer_group_for_bank(ticker: str, all_metrics: list[dict], mode: str = "size") -> list[dict]:
    """Return peer group (as metrics list) for a given ticker."""
    groups = group_banks(all_metrics)
    key = "by_size" if mode == "size" else "by_mix"

    bank = next((m for m in all_metrics if m.get("ticker") == ticker), None)
    if not bank:
        return []

    if mode == "size":
        my_tier = asset_size_tier(bank.get("total_assets"))
    else:
        my_tier = business_mix_tier(bank)

    return groups[key].get(my_tier, [])


# Curated headline metrics for peer-context badges — strong FDIC coverage so
# the cohort is well-populated. ROATCE uses the normalized (winsorized) figure.
CONTEXT_METRIC_KEYS = ["roaa", "roatce_normalized", "nim", "efficiency_ratio",
                       "npl_ratio", "nco_ratio", "cet1_ratio"]


def _higher_is_better(key: str) -> bool:
    from config import METRICS_BY_KEY
    return METRICS_BY_KEY.get(key, {}).get("color_rule") != "lower_better"


def metric_percentile_context(ticker: str, all_metrics: list[dict],
                              metric_keys: list[str] | None = None,
                              mode: str = "size", min_peers: int = 5) -> dict:
    """Where a bank sits vs its same-tier peers on each headline metric.

    Returns {metric_key: {value, percentile, raw, median, n, higher_better, label}}
    plus a "_meta" entry {tier, cohort_size, mode}. ``percentile`` is the
    *goodness* percentile (higher = better; inverted for lower-is-better metrics
    like efficiency / NPL / NCO) so the same colour scale reads intuitively.
    Metrics with fewer than ``min_peers`` populated peers are omitted.
    """
    import statistics
    from config import METRICS_BY_KEY

    self_m = next((m for m in all_metrics if m.get("ticker") == ticker), None)
    if not self_m:
        return {}
    cohort = get_peer_group_for_bank(ticker, all_metrics, mode=mode)
    if mode == "size":
        tier = asset_size_tier(self_m.get("total_assets"))
    else:
        tier = business_mix_tier(self_m)
    out = {"_meta": {"tier": tier, "cohort_size": len(cohort), "mode": mode}}
    for k in (metric_keys or CONTEXT_METRIC_KEYS):
        v = self_m.get(k)
        # NaN is missing data, not a value — a pandas round-trip turns None
        # into NaN, which passes `is not None` and would otherwise rank as
        # 0th percentile (every comparison against NaN is False).
        if v is None or pd.isna(v):
            continue
        peer_vals = [m.get(k) for m in cohort
                     if m.get(k) is not None and not pd.isna(m.get(k))]
        if len(peer_vals) < min_peers:
            continue
        raw = compute_peer_percentile(v, peer_vals)
        if raw is None:
            continue
        hib = _higher_is_better(k)
        # Rank position (1 = best). Ties share the better rank.
        better = sum(1 for x in peer_vals if (x > v if hib else x < v))
        out[k] = {
            "value": v,
            "percentile": raw if hib else (100 - raw),
            "raw": raw,
            "median": statistics.median(peer_vals),
            "n": len(peer_vals),
            "rank": better + 1,
            "out_of": len(peer_vals),
            "higher_better": hib,
            "label": METRICS_BY_KEY.get(k, {}).get("label", k),
        }
    return out


def compute_peer_percentile(bank_value: float | None, peer_values: list[float]) -> float | None:
    """
    Return the bank's percentile rank within peer group (0-100).

    Uses the "average rank" convention for ties: if 3 banks tie for rank 5,
    they all get rank 5+6+7 / 3 = 6. This ensures tied peers get the same
    percentile instead of arbitrary ordering-based values.

    Formula: percentile = (n_below + 0.5 * n_equal) / n_total * 100
    This is the Hazen method — robust, commonly used in statistics packages.
    """
    if bank_value is None or pd.isna(bank_value):
        return None  # NaN = missing data → no percentile, never 0th
    valid = [v for v in peer_values if v is not None and not pd.isna(v)]
    if not valid:
        return None
    below = sum(1 for v in valid if v < bank_value)
    equal = sum(1 for v in valid if v == bank_value)
    total = len(valid)
    return ((below + 0.5 * equal) / total) * 100 if total > 0 else None
