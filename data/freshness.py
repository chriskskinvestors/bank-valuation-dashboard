"""
Shared cache-freshness check.

Four clients (form4, form13f, fred, estimates) carried verbatim copies of the
same "is this cached blob's `cached_at` younger than my TTL?" helper — one
implementation, with the TTL passed explicitly.
"""
from datetime import datetime


def is_fresh(cached: dict | None, ttl_seconds: float) -> bool:
    """True when ``cached`` has an ISO `cached_at` younger than ttl_seconds."""
    if not cached:
        return False
    ts = cached.get("cached_at", "")
    if not ts:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False
