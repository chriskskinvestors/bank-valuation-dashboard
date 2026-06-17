"""
Ticker → HQ-state / region resolver for geography-based peer groups.

HQ state is the FDIC bank subsidiary's state (``STALP``), which lives only on the
FDIC *institutions* endpoint — not on the per-bank financials record. We resolve it
once from ``fdic_client.list_all_active_institutions`` (the same source the universe
build uses), cache the cert→state map for a day, and map ticker→cert→state on demand.

This is the authoritative HQ state (cardinal rule: never guess geography). A bank
with no FDIC cert or an unmapped cert resolves to "" (unknown) and is grouped under
"Unknown", never silently placed in a state it isn't in.
"""
from __future__ import annotations

import time

from data import cache
from data.bank_mapping import get_fdic_cert

_MAP_KEY = "geo:cert_state_map"
_TTL_S = 86400  # one day

# US Census Bureau four-region grouping (+ territories → "Other"). Static and
# well-defined; used only to bucket states into regions for coarser peer groups.
_STATE_REGION = {
    # Northeast
    "CT": "Northeast", "ME": "Northeast", "MA": "Northeast", "NH": "Northeast",
    "RI": "Northeast", "VT": "Northeast", "NJ": "Northeast", "NY": "Northeast",
    "PA": "Northeast",
    # Midwest
    "IL": "Midwest", "IN": "Midwest", "MI": "Midwest", "OH": "Midwest",
    "WI": "Midwest", "IA": "Midwest", "KS": "Midwest", "MN": "Midwest",
    "MO": "Midwest", "NE": "Midwest", "ND": "Midwest", "SD": "Midwest",
    # South
    "DE": "South", "FL": "South", "GA": "South", "MD": "South", "NC": "South",
    "SC": "South", "VA": "South", "DC": "South", "WV": "South", "AL": "South",
    "KY": "South", "MS": "South", "TN": "South", "AR": "South", "LA": "South",
    "OK": "South", "TX": "South",
    # West
    "AZ": "West", "CO": "West", "ID": "West", "MT": "West", "NV": "West",
    "NM": "West", "UT": "West", "WY": "West", "AK": "West", "CA": "West",
    "HI": "West", "OR": "West", "WA": "West",
}


def region_for_state(state: str) -> str:
    """Census region for a 2-letter state code; territories/unknown → 'Other'."""
    if not state:
        return "Unknown"
    return _STATE_REGION.get(state.strip().upper(), "Other")


def _cert_state_map() -> dict:
    """{str(cert): state} for all active FDIC institutions, cached for a day."""
    cached = cache.get(_MAP_KEY)
    if cached and (time.time() - float(cached.get("_ts", 0)) < _TTL_S):
        return cached.get("map", {})
    from data import fdic_client
    insts = fdic_client.list_all_active_institutions()
    m = {str(i["cert"]): (i.get("state") or "").strip().upper()
         for i in insts if i.get("cert")}
    if m:  # only overwrite the cache on a successful fetch
        cache.put(_MAP_KEY, {"_ts": time.time(), "map": m})
        return m
    return cached.get("map", {}) if cached else {}


def get_states_for(tickers) -> dict:
    """ticker → 2-letter HQ state ('' when the cert is unknown/unmapped)."""
    cm = _cert_state_map()
    out = {}
    for t in tickers:
        cert = get_fdic_cert(t)
        out[t] = cm.get(str(cert), "") if cert else ""
    return out
