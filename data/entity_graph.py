"""
Entity graph + as-of universe reconstruction for point-in-time screening
(docs/POINT-IN-TIME-RECONSTRUCTION.md).

Institutions disappear (acquired) or fail, so "the universe as of Q2 2023" is not
today's survivors projected backward. Membership at a past quarter is answered from
the FDIC *institutions* endpoint's charter dates: a cert was OPEN at quarter Q iff
``ESTYMD <= Q <= ENDEFYMD`` (active banks carry ``ENDEFYMD = 12/31/9999``). Lineage
(who absorbed whom) comes from the structure-change graph (``data/fdic_structure``).

The point-in-time **public** universe is reconstructed from today's coverage: today's
public banks that were open at Q, PLUS the banks they have since absorbed that were
open at Q (lineage), PLUS tracked public failures that were open at Q. Defunct or
predecessor certs that were never public equity are out of scope — no historical
ticker↔cert map exists, so we never *guess* a defunct bank into the public universe
(cardinal rule). This is the lineage reconstruction the spec chose, clearly labeled,
never a claim of the complete historical FDIC universe.
"""
from __future__ import annotations

from datetime import date, datetime

FDIC_INSTITUTIONS_URL = "https://api.fdic.gov/banks/institutions"
_LIFESPAN_TTL_S = 30 * 86400  # charter dates basically never change
_FAR_FUTURE = date(9999, 12, 31)

# Tracked public-equity failures/closures (FDIC cert → name). These had public
# stock and left the universe via failure rather than absorption into a current
# public survivor, so the lineage walk alone would miss them. Add as confirmed.
KNOWN_PUBLIC_FAILURES: dict[int, str] = {
    24735: "Silicon Valley Bank",
    57053: "Signature Bank",
    59017: "First Republic Bank",
    27330: "Silvergate Bank",
    58978: "Heartland Tri-State Bank",
    35095: "Republic First Bank (Republic Bank)",
}


def _parse_fdic_date(raw) -> date | None:
    """FDIC institution dates are MM/DD/YYYY. 12/31/9999 → far-future sentinel."""
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _as_date(q) -> date | None:
    if isinstance(q, date):
        return q
    return _parse_fdic_date(q)


def cert_lifespan(cert: int) -> tuple[date | None, date | None]:
    """(established, ended) for a cert from the FDIC institutions endpoint.
    ``ended`` is None for active banks. Cached ~30d. (None, None) if unknown."""
    if not cert:
        return (None, None)
    cert = int(cert)
    from data import cache
    from data.freshness import is_fresh

    key = f"entity_lifespan:{cert}"
    cached = cache.get(key)
    if is_fresh(cached, _LIFESPAN_TTL_S) and "est" in (cached or {}):
        return (_parse_fdic_date(cached.get("est")), _parse_fdic_date(cached.get("end")))

    from data.http import get_with_retry
    est = end = None
    try:
        resp = get_with_retry(FDIC_INSTITUTIONS_URL, {
            "filters": f"CERT:{cert}", "fields": "CERT,ESTYMD,ENDEFYMD,ACTIVE", "limit": 1,
        }, timeout=30)
        if resp is not None:
            data = resp.json().get("data", [])
            if data:
                d = data[0].get("data", {})
                est = _parse_fdic_date(d.get("ESTYMD"))
                _end = _parse_fdic_date(d.get("ENDEFYMD"))
                # active sentinel (12/31/9999) or active flag → still open
                if int(d.get("ACTIVE") or 0) == 1 or _end == _FAR_FUTURE:
                    end = None
                else:
                    end = _end
                cache.put(key, {"est": d.get("ESTYMD"), "end": d.get("ENDEFYMD"),
                                "cached_at": datetime.now().isoformat()})
    except Exception as e:
        print(f"[entity_graph] cert {cert} lifespan error: {type(e).__name__}: {e}")
    return (est, end)


def was_open(cert: int, quarter) -> bool:
    """Was this cert an open institution at quarter-end date ``quarter``?"""
    q = _as_date(quarter)
    if q is None:
        return False
    est, end = cert_lifespan(cert)
    if est is None:
        return False
    return est <= q and (end is None or end >= q)


def predecessors(cert: int) -> list[int]:
    """Certs this institution has absorbed (one level), from the structure graph."""
    from data.fdic_structure import get_acquisition_history
    out = []
    for a in get_acquisition_history(cert):
        tc = a.get("target_cert")
        if tc:
            out.append(int(tc))
    return out


def public_universe_as_of(base_certs, quarter, *, with_lineage: bool = True) -> dict:
    """Reconstruct the public screening universe as of ``quarter``.

    Returns ``{cert: {"name": str, "source": "base"|"lineage"|"failure"}}`` for the
    certs open at Q, drawn from today's public banks (``base_certs``) + their absorbed
    predecessors + tracked public failures. A bank chartered AFTER Q is dropped; a
    bank that exited AFTER Q is re-added. Membership uses charter dates only — it
    never fabricates one.
    """
    q = _as_date(quarter)
    if q is None:
        return {}
    base = {int(c) for c in base_certs if c}

    candidates: dict[int, str] = {c: "base" for c in base}
    for c, name in KNOWN_PUBLIC_FAILURES.items():
        candidates.setdefault(c, "failure")
    if with_lineage:
        for c in base:
            for p in predecessors(c):
                candidates.setdefault(p, "lineage")

    out: dict[int, dict] = {}
    for c, source in candidates.items():
        if was_open(c, q):
            out[c] = {"name": KNOWN_PUBLIC_FAILURES.get(c, ""), "source": source}
    return out
