"""
As-of-quarter FDIC metrics for point-in-time screening (increment 2).

For a chosen quarter and the reconstructed cert set (data/entity_graph), fetch a
WINDOW of quarters up to Q (filtered to those certs — a handful of fast calls per
quarter, not a full-system sweep), assemble each bank's history newest-first, and
build metrics through the REAL engine (build_bank_metrics over that history) — never
a reimplementation. The window matches the live engine's depth, so 4-quarter
averages, trends, QoQ/YoY changes and the fair-value cascade compute exactly as the
dashboard would have shown them at Q.

As-of mode is FDIC-only: SEC/price/market-cap metrics are absent (None → n/a), never
guessed. Results are cached per (quarter, cohort size, window).
"""
from __future__ import annotations

import pandas as pd

_CACHE_TTL_S = 24 * 3600
# Quarters of history fed to the engine, matching the live build's effective depth
# (load_fdic_hist limit=20) so as-of values equal the live values computed at Q.
_WINDOW = 20


def quarter_label(ts) -> str:
    """Timestamp/'YYYY-MM-DD' → 'Qn YYYY'."""
    t = pd.Timestamp(ts)
    return f"Q{(t.month - 1) // 3 + 1} {t.year}"


def recent_quarter_ends(n: int = 12) -> list[pd.Timestamp]:
    """The last ``n`` completed calendar quarter-ends, newest first. Used to
    populate the 'As of' picker. Excludes the current incomplete quarter."""
    today = pd.Timestamp.today().normalize()
    q_end = (today - pd.offsets.QuarterEnd(startingMonth=12))
    return [q_end - pd.offsets.QuarterEnd(startingMonth=12) * i for i in range(n)]


def as_of_quarter_metrics(quarter, cert_to_id: dict, *, window: int = _WINDOW) -> list[dict]:
    """Build FDIC metrics as of ``quarter`` for the reconstructed certs.

    cert_to_id: {fdic_cert: display_id} — the live ticker for current banks or a
    synthetic id (e.g. name) for defunct ones. A bank is included only if it FILED
    at Q (its last filing on/before Q); the window back from Q gives the engine the
    history its multi-quarter metrics need. Cached per quarter.
    """
    from data import cache, fdic_client
    from analysis.metrics import build_bank_metrics
    from data.freshness import is_fresh

    q = pd.Timestamp(quarter)
    repdte = q.strftime("%Y%m%d")
    key = f"as_of_metrics:{repdte}:{len(cert_to_id)}:w{window}"
    cached = cache.get(key)
    if is_fresh(cached, _CACHE_TTL_S) and isinstance(cached.get("metrics"), list):
        return cached["metrics"]

    certs = list(cert_to_id.keys())
    # The window of quarter-ends [Q, Q-1, …], newest first; one filtered fetch each.
    repdtes = [(q - pd.offsets.QuarterEnd(startingMonth=12) * i).strftime("%Y%m%d")
               for i in range(window)]
    by_quarter = {rd: fdic_client.fetch_quarter_financials(rd, certs=certs) for rd in repdtes}

    out: list[dict] = []
    for cert, id_ in cert_to_id.items():
        c = int(cert)
        anchor = by_quarter[repdtes[0]].get(c)
        if anchor is None:
            continue  # didn't file at Q (chartered later / already exited)
        # newest-first history across the window (skip quarters it didn't file)
        hist = [by_quarter[rd][c] for rd in repdtes if c in by_quarter[rd]]
        m = build_bank_metrics(id_, anchor, {}, {}, hist)
        m["ticker"] = id_
        m["_as_of"] = repdte
        out.append(m)

    cache.put(key, {"metrics": out, "cached_at": q.isoformat()})
    return out
