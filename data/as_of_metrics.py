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
        m["_fdic_cert"] = c     # lets the table link defunct banks to FDIC BankFind
        out.append(m)

    cache.put(key, {"metrics": out, "cached_at": q.isoformat()})
    return out


# Curated FDIC-resolvable fundamentals offered in the Trends view (key, label).
# All are built in ONE engine pass per bank-quarter, so the cached series serves
# every metric — the heavy all-banks build is pre-warmed by jobs/refresh_trends.
TREND_METRICS = [
    ("roaa", "ROAA"), ("roaa_4q", "ROAA (4Q avg)"),
    ("roatce", "ROATCE"), ("roatce_4q", "ROATCE (4Q)"),
    ("nim", "NIM"), ("nim_4q", "NIM (4Q)"), ("efficiency_ratio", "Efficiency"),
    ("npl_ratio", "NPL ratio"), ("nco_ratio", "NCO ratio"),
    ("allowance_loans", "ALL / Loans"), ("reserve_to_loans", "Reserves / Loans"),
    ("cet1_ratio", "CET1"), ("total_capital_ratio", "Total capital"),
    ("leverage_ratio", "T1 leverage"), ("equity_to_assets", "Equity / Assets"),
    ("total_assets", "Total assets ($)"), ("total_loans", "Loans ($)"),
    ("total_deposits", "Deposits ($)"), ("total_equity", "Total equity ($)"),
    ("uninsured_pct", "Uninsured deposits %"), ("brokered_pct", "Brokered %"),
    ("loans_to_deposits", "Loans / Deposits"), ("cost_of_funds", "Cost of funds"),
    ("dep_qoq_growth", "Deposit QoQ %"),
]
TREND_KEYS = [k for k, _ in TREND_METRICS]
# Pre-warmed all-banks grids are stable day-to-day; allow a generous TTL so the
# Trends view stays instant between nightly refreshes even if one is missed.
_GRID_TTL_S = 36 * 3600


def _cohort_key(certs) -> str:
    import hashlib
    return hashlib.md5(",".join(str(c) for c in sorted(certs)).encode()).hexdigest()[:12]


def quarterly_series(cert_to_id: dict, n_quarters: int = _WINDOW, *,
                     build_if_missing: bool = True, trail_buffer: int = 4,
                     scope_id: str | None = None):
    """Full per-bank quarterly series for all TREND_METRICS over the last
    ``n_quarters`` quarter-ends. ONE engine pass per bank-quarter computes every
    metric, so the cached payload serves the whole Trends view.

    Returns ``{"labels": [...], "rows": [{"ticker","_fdic_cert","series":{key:[v…]}}]}``
    — or ``None`` when ``build_if_missing`` is False and nothing is cached. The
    all-banks build recomputes the engine ~7.5k times (~300s), exceeding the 300s
    request timeout, so it is NEVER built in a live page request: jobs/refresh_trends
    pre-warms it (within the 900s job timeout) into the cross-instance cache, and the
    Trends view reads it. Scoped cohorts (≤ a few hundred banks) build live.
    """
    from data import cache, fdic_client
    from analysis.metrics import build_bank_metrics
    from data.freshness import is_fresh

    n = max(int(n_quarters), 1)
    certs = list(cert_to_id.keys())
    # A caller-supplied scope_id (e.g. "ALLBANKS") gives a STABLE key the pre-warm
    # job and the live view agree on; otherwise hash the exact cohort (scoped sets).
    key = f"trend_series:{scope_id or _cohort_key(certs)}:{n}"
    cached = cache.get(key)
    if is_fresh(cached, _GRID_TTL_S) and isinstance(cached.get("rows"), list):
        return cached
    if not build_if_missing:
        return None

    # n display quarters + a trailing buffer so the oldest column's TTM/4Q metrics
    # still have their look-back window.
    all_ends = recent_quarter_ends(n + trail_buffer)        # newest first
    repdtes = [e.strftime("%Y%m%d") for e in all_ends]
    by_quarter = {rd: fdic_client.fetch_quarter_financials(rd, certs=certs)
                  for rd in repdtes}
    labels = [quarter_label(e) for e in all_ends[:n]]

    rows: list[dict] = []
    for cert, id_ in cert_to_id.items():
        c = int(cert)
        series = {k: [] for k in TREND_KEYS}
        any_val = False
        for qi in range(n):
            anchor = by_quarter[repdtes[qi]].get(c)
            if anchor is None:
                for k in TREND_KEYS:
                    series[k].append(None)
                continue
            hist = [by_quarter[r][c] for r in repdtes[qi:] if c in by_quarter[r]]
            # ticker=None skips the per-call SEC companyfacts fetch (capital-return) —
            # FDIC-only here, and that fetch ×N quarters ×all banks was a ~5x slowdown.
            m = build_bank_metrics(None, anchor, {}, {}, hist)
            for k in TREND_KEYS:
                v = m.get(k)
                series[k].append(v)
                any_val = any_val or v is not None
        if any_val:
            rows.append({"ticker": id_, "_fdic_cert": c, "series": series})

    payload = {"labels": labels, "rows": rows,
               "cached_at": pd.Timestamp.today().isoformat()}
    cache.put(key, payload)
    return payload


def metric_grid(metric_key: str, cert_to_id: dict, n_quarters: int = _WINDOW,
                *, build_if_missing: bool = True, scope_id: str | None = None):
    """One metric extracted from the cached full series → ``(labels, rows)`` with
    rows ``[{"ticker","_fdic_cert", <label>: value, …}]``. ``(None, None)`` when the
    series isn't available (not pre-warmed and ``build_if_missing`` is False)."""
    data = quarterly_series(cert_to_id, n_quarters, build_if_missing=build_if_missing,
                            scope_id=scope_id)
    if data is None:
        return None, None
    labels = data["labels"]
    out = []
    for r in data["rows"]:
        ser = r.get("series", {}).get(metric_key) or []
        row = {"ticker": r["ticker"], "_fdic_cert": r["_fdic_cert"]}
        row.update({lb: (ser[i] if i < len(ser) else None) for i, lb in enumerate(labels)})
        out.append(row)
    return labels, out
