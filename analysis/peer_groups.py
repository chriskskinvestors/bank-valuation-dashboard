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
        assets = m.get("total_assets")
        # total_assets may be stored in thousands or dollars depending on flow
        if assets and assets < 1e9:
            # likely in thousands — convert
            assets = assets * 1000
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
        assets = bank.get("total_assets")
        if assets and assets < 1e9:
            assets = assets * 1000
        my_tier = asset_size_tier(assets)
    else:
        my_tier = business_mix_tier(bank)

    return groups[key].get(my_tier, [])


def compute_peer_percentile(bank_value: float | None, peer_values: list[float]) -> float | None:
    """
    Return the bank's percentile rank within peer group (0-100).

    Uses the "average rank" convention for ties: if 3 banks tie for rank 5,
    they all get rank 5+6+7 / 3 = 6. This ensures tied peers get the same
    percentile instead of arbitrary ordering-based values.

    Formula: percentile = (n_below + 0.5 * n_equal) / n_total * 100
    This is the Hazen method — robust, commonly used in statistics packages.
    """
    if bank_value is None:
        return None
    valid = [v for v in peer_values if v is not None]
    if not valid:
        return None
    below = sum(1 for v in valid if v < bank_value)
    equal = sum(1 for v in valid if v == bank_value)
    total = len(valid)
    return ((below + 0.5 * equal) / total) * 100 if total > 0 else None
