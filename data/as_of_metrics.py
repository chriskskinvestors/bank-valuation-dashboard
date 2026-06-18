"""
As-of-quarter FDIC metrics for point-in-time screening (increment 2).

For a chosen quarter and the reconstructed cert set (data/entity_graph), fetch each
cert's FDIC quarterly history (the existing parallel fetcher), pick the chosen
quarter's record, and build metrics through the REAL engine (build_bank_metrics over
the as-of history window) — never a reimplementation, so a bank's Q1-2023 numbers are
exactly what FDIC filed then, including for since-failed banks.

As-of mode is FDIC-only: SEC/price/market-cap metrics are absent (None → n/a),
never guessed. Results are cached per quarter.
"""
from __future__ import annotations

import pandas as pd

_CACHE_TTL_S = 24 * 3600

# Metrics that need multiple quarters of history (4-quarter averages, trends, QoQ/
# YoY changes, and the fair-value cascade built on blended/normalized ROATCE).
# In single-quarter as-of mode they would be computed from one quarter and come
# out distorted, so they are forced to n/a — never a plausible-wrong number.
_HISTORY_DEPENDENT_CATEGORIES = {
    "Fair Value", "Capital Dynamics", "Credit Dynamics",
    "Deposit Dynamics", "Capital Return",
}
_HISTORY_DEPENDENT_EXTRA = {"roaa_4q", "roatce_4q", "nim_4q"}


def _history_dependent_keys() -> set:
    from config import METRICS
    keys = {m["key"] for m in METRICS
            if m.get("category") in _HISTORY_DEPENDENT_CATEGORIES}
    return keys | _HISTORY_DEPENDENT_EXTRA


_NA_KEYS = None


def quarter_label(ts) -> str:
    """Timestamp/'YYYY-MM-DD' → 'Qn YYYY'."""
    t = pd.Timestamp(ts)
    return f"Q{(t.month - 1) // 3 + 1} {t.year}"


def recent_quarter_ends(n: int = 12) -> list[pd.Timestamp]:
    """The last ``n`` completed calendar quarter-ends, newest first. Used to
    populate the 'As of' picker. Excludes the current incomplete quarter."""
    today = pd.Timestamp.today().normalize()
    # most recent quarter-end strictly before today
    q_end = (today - pd.offsets.QuarterEnd(startingMonth=12))
    return [q_end - pd.offsets.QuarterEnd(startingMonth=12) * i for i in range(n)]


def as_of_quarter_metrics(quarter, cert_to_id: dict) -> list[dict]:
    """Build FDIC metrics as of ``quarter`` for the reconstructed certs.

    cert_to_id: {fdic_cert: display_id} — display_id is the live ticker for current
    banks or a synthetic id (e.g. name) for defunct ones. Returns a list of metric
    dicts (the same shape the live screen uses), each tagged with ``_as_of`` and
    ``_as_of_source``. Cached per quarter.
    """
    from data import cache, fdic_client
    from analysis.metrics import build_bank_metrics
    from data.freshness import is_fresh

    q = pd.Timestamp(quarter)
    repdte = q.strftime("%Y%m%d")
    key = f"as_of_metrics:{repdte}:{len(cert_to_id)}"
    cached = cache.get(key)
    if is_fresh(cached, _CACHE_TTL_S) and isinstance(cached.get("metrics"), list):
        return cached["metrics"]

    # ONE quarter's financials for the whole banking system (≈5 paginated calls),
    # not a per-cert history sweep (hundreds of calls) — the only way this is
    # interactive. As-of is single-quarter: _4q / trend metrics resolve None (n/a),
    # never guessed.
    quarter_recs = fdic_client.fetch_quarter_financials(repdte)

    global _NA_KEYS
    if _NA_KEYS is None:
        _NA_KEYS = _history_dependent_keys()

    out: list[dict] = []
    for cert, id_ in cert_to_id.items():
        rec = quarter_recs.get(int(cert))
        if not rec:
            continue  # didn't file this quarter (chartered later / already exited)
        m = build_bank_metrics(id_, rec, {}, {}, [rec])
        for k in _NA_KEYS:           # multi-quarter metrics → n/a in single-quarter mode
            m[k] = None
        m["ticker"] = id_
        m["_as_of"] = repdte
        out.append(m)

    cache.put(key, {"metrics": out, "cached_at": q.isoformat()})
    return out
