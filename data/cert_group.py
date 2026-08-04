"""Multi-charter holding companies: one ticker → MANY FDIC certs.

The platform's mapping is one ticker → one cert, which silently represents a
multi-bank holdco by its LEAD charter alone. Measured 2026-08-02 across the
universe, 11 banks are affected and the understatement is severe:

    WTFC  12.8%   $9.3B of $72.4B   16 charters
    QCRH  30.6%   $3.1B of $10.1B    4
    ATLO  50.7%   $1.1B of  $2.2B    6
    IBOC  57.2%   $9.9B of $17.3B    5
    MS    61.8%   $391B of  $633B    2
    GCBC  69.3%   $3.2B of  $4.6B    2
    BPOP  80.2%  $60.6B of $75.6B    2
    GOVB  84.3%   $199M of  $236M    2
    BANF  85.6%  $12.8B of $14.9B    3
    MBWM  91.8%   $6.4B of  $6.9B    2
    BNY   93.5%   $467B of  $500B    4

Every FDIC-sourced level (assets, deposits, loans, equity, income) was that
fraction of the real banking operation, and every FDIC ratio was the lead
charter's rather than the consolidated bank's.

WHAT AGGREGATES, AND WHAT DELIBERATELY DOES NOT
-----------------------------------------------
Levels (stocks and flows) sum: a group's assets ARE the sum of its charters'
assets. Ratios do not, and this module refuses to fake them:

  * RECOMPUTED from summed components — the formula is a pure ratio of two
    summed levels, so it is exact:
        EEFFR     efficiency  = NONIX / (net interest income + NONII)
        RBCRWAJ   total RBC   = RBC    / RWAJ
        RBC1RWAJ  tier 1 RBC  = RBCT1J / RWAJ
  * n/a — FDIC computes these against AVERAGE balances over the period, which
    period-end levels cannot reconstruct. Carrying the lead charter's figure
    would be a plausible-wrong number on a consolidated label, so they are
    dropped and flagged instead:
        ROA ROE NIMY RBCT1JR IDT1CER and their single-quarter *Q variants

A future pass can restore the averaged ratios by aggregating each charter's
HISTORY and forming 2-point averages, the way ui/financials_statements already
does for a single bank. That is deliberately not attempted here: it needs
per-charter history for all 16 of Wintrust's banks, and a wrong NIM is worse
than an absent one.

Single-charter banks (the other ~350) are untouched: get_cert_group returns
[cert] and aggregation is a no-op passthrough.
"""
from __future__ import annotations

# Fields FDIC computes against AVERAGE balances — unreconstructable from
# period-end levels, so they are dropped for a group rather than guessed.
AVERAGE_BASED_RATIOS = frozenset({
    "ROA", "ROE", "NIMY", "RBCT1JR", "IDT1CER", "INTEXPY", "INTINCY",
    "NONIIAY", "NONIXAY", "ROAPTX", "NCLNLSR", "NTLNLSR", "LNATRESR",
    "NPERFV", "ROAQ", "ROEQ", "NIMYQ", "EEFFQR", "NTLNLSQR",
})

# Identity/metadata — carried from the LEAD (largest) charter, never summed.
_IDENTITY = frozenset({"CERT", "REPNM", "REPDTE", "NAME", "STALP", "CITY",
                       "RSSDHCR", "FED_RSSD", "ID"})

_GROUP_TTL_S = 30 * 86400          # bank structure changes rarely

# ONE cache row holding {str(cert): [group certs largest-first]} for every
# cert under a holdco with 2+ active charters, built from the universe
# snapshot's full-institutions walk (data/bank_universe._fetch_fdic_banks).
# Why bulk: the per-cert path costs 2 FDIC API calls per bank, and inside the
# nightly's 8-worker burst those get 429-throttled — get_with_retry returns
# None, and the group silently degraded to the lead charter for EVERY bank
# (2026-08-04: a "successful" run wrote lead-charter data universe-wide).
# Absence from the map means single-charter; the API path is only a fallback
# for an environment where the map has never been built.
_GROUP_MAP_KEY = "cert_groups:v2"


def get_cert_group(ticker: str, cert: int | None = None) -> list[int]:
    """Every ACTIVE FDIC cert under `ticker`'s holding company, largest first.

    Returns [cert] for a single-charter bank (the overwhelming majority) and
    [] when the ticker has no cert at all. Falls back to [cert] on any lookup
    failure — never fewer charters than we already had, so a bad FDIC day
    degrades to today's behaviour rather than losing the bank entirely.
    """
    from data.bank_mapping import get_fdic_cert
    if cert is None:
        try:
            cert = get_fdic_cert(ticker)
        except Exception:
            cert = None
    if not cert:
        return []
    cert = int(cert)

    from data import cache
    # Bulk map first: no API call, and a cert absent from the map IS the
    # single-charter answer (the map only lists multi-charter groups).
    try:
        m = cache.get(_GROUP_MAP_KEY, max_age_s=_GROUP_TTL_S)
    except Exception:
        m = None
    if isinstance(m, dict):
        g = m.get(str(cert))
        return [int(c) for c in g] if g else [cert]

    # No bulk map yet (fresh environment): legacy per-cert resolution.
    key = f"cert_group:v1:{cert}"
    try:
        hit = cache.get(key, max_age_s=_GROUP_TTL_S)
        if hit and isinstance(hit.get("certs"), list) and hit["certs"]:
            return [int(c) for c in hit["certs"]]
    except Exception:
        pass

    certs = _resolve_group(cert)
    if not certs:
        return [cert]
    try:
        cache.put(key, {"certs": certs})
    except Exception:
        pass
    return certs


def build_group_map(institutions: list[dict]) -> dict[str, list[int]]:
    """{str(cert): [group certs largest-first]} for every cert whose holdco
    (rssdhcr) has 2+ active charters. Single-charter certs are OMITTED —
    absence from the map means [cert]. Keys are strings because the map
    round-trips through JSON in the cache.

    `institutions` rows need cert / rssdhcr / asset (lowercase, as
    data/bank_universe._fetch_fdic_banks builds them). RSSDHCR of 0/None/""
    means no high holder."""
    by_hc: dict[int, list[dict]] = {}
    for b in institutions:
        try:
            hc = int(b.get("rssdhcr") or 0)
            cert = int(b.get("cert") or 0)
        except (TypeError, ValueError):
            continue
        if not hc or not cert:
            continue
        by_hc.setdefault(hc, []).append(b)
    out: dict[str, list[int]] = {}
    for members in by_hc.values():
        if len(members) < 2:
            continue
        certs = [int(mm["cert"]) for mm in
                 sorted(members, key=lambda mm: -(float(mm.get("asset") or 0)))]
        for c in certs:
            out[str(c)] = certs
    return out


def warm_group_map(institutions: list[dict]) -> int:
    """Build and persist the bulk group map. Returns the number of certs that
    belong to a multi-charter group. Called from the universe snapshot build,
    which already walked every ACTIVE institution — so this costs zero extra
    FDIC API calls."""
    m = build_group_map(institutions)
    from data import cache
    cache.put(_GROUP_MAP_KEY, m)
    return len(m)


def _resolve_group(cert: int) -> list[int]:
    """Live FDIC lookup: the cert's holdco RSSD, then every active cert under
    it, ordered by assets descending. [] on any failure (caller falls back)."""
    from data.http import get_with_retry
    from data.fdic_client import FDIC_INSTITUTIONS_URL

    try:
        r = get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"CERT:{int(cert)}",
            "fields": "CERT,RSSDHCR,ASSET", "limit": 1, "format": "json",
        }, timeout=30)
        if r is None:
            # get_with_retry returns None ONLY when every attempt ate a 429.
            # This must be loud: the silent version of this line let a
            # rate-limited nightly degrade every bank to its lead charter
            # with nothing in the logs (2026-08-04).
            print(f"[cert_group] cert {cert}: institutions lookup "
                  f"rate-limited past retries — degrading to single cert")
            return []
        rows = r.json().get("data", [])
        if not rows:
            return []
        hc = (rows[0].get("data") or {}).get("RSSDHCR")
        if not hc:
            return [int(cert)]              # no holding company: itself only
        r2 = get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"RSSDHCR:{int(hc)} AND ACTIVE:1",
            "fields": "CERT,ASSET", "limit": 100, "format": "json",
            "sort_by": "ASSET", "sort_order": "DESC",
        }, timeout=30)
        if r2 is None:
            print(f"[cert_group] cert {cert}: group lookup rate-limited "
                  f"past retries — degrading to single cert")
            return []
        out = []
        for d in r2.json().get("data", []):
            c = (d.get("data") or {}).get("CERT")
            if c is not None:
                out.append(int(c))
        if int(cert) not in out:            # mapped cert must always be present
            out.insert(0, int(cert))
        return out
    except Exception as e:
        print(f"[cert_group] resolve failed for cert {cert}: "
              f"{type(e).__name__}: {e}")
        return []


def aggregate_records(records: list[dict]) -> dict:
    """Consolidate one FDIC financials record per charter into one record.

    Levels are summed; average-based ratios are dropped (see module docstring);
    the three exactly-recomputable ratios are rebuilt from the sums. Identity
    fields come from the LEAD charter — records must be largest-first.

    A single record passes through unchanged, so single-charter banks are
    bit-for-bit unaffected.
    """
    records = [r for r in records if r]
    if not records:
        return {}
    if len(records) == 1:
        return dict(records[0])

    lead = records[0]
    out: dict = {k: lead.get(k) for k in _IDENTITY if k in lead}

    keys: set = set()
    for r in records:
        keys.update(r.keys())

    for k in keys:
        if k in _IDENTITY or k in AVERAGE_BASED_RATIOS:
            continue
        total, seen = 0.0, False
        for r in records:
            v = r.get(k)
            if v is None:
                continue
            try:
                total += float(v)
                seen = True
            except (TypeError, ValueError):
                seen = False
                break
        out[k] = total if seen else None

    # Explicitly n/a rather than absent, so a consumer reading the key gets
    # None (render n/a) instead of a KeyError or a stale lead-charter value.
    for k in AVERAGE_BASED_RATIOS:
        if k in keys:
            out[k] = None

    _recompute_exact_ratios(out)
    out["_charter_count"] = len(records)
    out["_aggregated"] = True
    return out


def fetch_group_history(ticker: str, limit: int = 20,
                        cert: int | None = None) -> list[dict]:
    """`limit` quarters of FDIC financials for the ticker's WHOLE banking
    operation, newest first — one consolidated record per REPDTE.

    This is the single seam the wiring goes through. Every producer of the
    `fdic_hist:{ticker}` cache entry calls it, so every consumer (the metrics
    build, the statement/credit/capital/deposit/rate tabs, the trends grid)
    becomes correct without touching a line of their code.

    Aggregating the HISTORY rather than one record is what keeps the averaged
    ratios alive: downstream code already derives ROA/NIM/ROATCE from levels
    plus a prior quarter (ui/financials_statements._avg,
    analysis.valuation.compute_roatce_4q), so with a correct per-quarter series
    those come out right for a group too. Only the FDIC-REPORTED ratio columns
    are dropped, because those specific numbers are the lead charter's.

    Quarters are aggregated over whatever charters reported them. A bank that
    acquired a charter mid-history therefore steps up at the acquisition — that
    IS what happened, and each record carries _charter_count so a consumer can
    see the composition.

    Single-charter banks take the same path as before (one cert, no
    aggregation), so the ~350 of them are unaffected.
    """
    from data import fdic_client

    certs = get_cert_group(ticker, cert=cert)
    if not certs:
        return []
    if len(certs) == 1:
        df = fdic_client.fetch_financials(certs[0], limit=limit)
        return [] if df is None or df.empty else df.to_dict("records")

    by_period: dict[str, list[dict]] = {}
    for c in certs:
        try:
            df = fdic_client.fetch_financials(c, limit=limit)
        except Exception as e:
            print(f"[cert_group] {ticker}: cert {c} history failed: "
                  f"{type(e).__name__}: {e}")
            continue
        if df is None or df.empty:
            continue
        for rec in df.to_dict("records"):
            period = str(rec.get("REPDTE") or "")
            if period:
                by_period.setdefault(period, []).append(rec)

    out = []
    for period in sorted(by_period, reverse=True):
        recs = sorted(by_period[period],
                      key=lambda r: -(float(r.get("ASSET") or 0)))
        out.append(aggregate_records(recs))
    return out[:limit]


def _recompute_exact_ratios(out: dict) -> None:
    """Rebuild the ratios that ARE a pure quotient of summed levels."""
    def _n(k):
        v = out.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    intinc, eintexp = _n("INTINC"), _n("EINTEXP")
    nonii, nonix = _n("NONII"), _n("NONIX")
    if None not in (intinc, eintexp, nonii, nonix):
        revenue = (intinc - eintexp) + nonii
        out["EEFFR"] = (nonix / revenue * 100) if revenue > 0 else None

    rwaj = _n("RWAJ")
    if rwaj and rwaj > 0:
        rbc, t1 = _n("RBC"), _n("RBCT1J")
        out["RBCRWAJ"] = (rbc / rwaj * 100) if rbc is not None else None
        out["RBC1RWAJ"] = (t1 / rwaj * 100) if t1 is not None else None
