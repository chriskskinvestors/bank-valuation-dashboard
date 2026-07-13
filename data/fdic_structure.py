"""
FDIC structure-change history client — mergers, absorptions, charter events.

Powers the Transactions section's Detailed M&A History (docs/SNL-BUILD-PLAN.md
§14). SNL lists a bank's completed acquisitions at the holding-company level;
FDIC records the same deals at the bank-subsidiary level as structure-change
events, so names differ (SNL "Starbuck Bancshares" = FDIC "AmericanWest Bank")
but dates and counts line up.

Endpoint: https://api.fdic.gov/banks/history
  (banks.data.fdic.gov/api/history 301-redirects here — use the new host
  directly). Each record is one structure/office event. CHANGECODE taxonomy,
  verified live against Banner Bank (cert 28489) on 2026-06-12:

    1xx  charter events (new charter, cert change)
    2xx  terminations — absorbed / failed / liquidated (on the dying cert)
    4xx  charter/class/insurance/org-type changes
    5xx  office name/location changes              ← branch noise, excluded
    7xx  branch events — excluded HERE; data/ma_history.py consumes
         712 (Branch Purchased: one header row per deal on the BUYER's cert,
         OUT_* = seller) and 722 (Branch Sold: per-office rows, also recorded
         on the buyer's cert). 713 rows are the branch-level echo of a
         whole-bank 810/811 absorption; 711/721 are organic open/close noise.
         Sellers record NOTHING — a bank's branch sales are found by the
         reverse query OUT_CERT:{cert} AND CHANGECODE:712.
         (Verified live 2026-07-13: Umpqua 17266, Banner 28489 / the 2014
         six-branch Umpqua-divestiture purchase, Meadows Bank 58722.)
    810  Participated in Absorbtion/Consolidation/Merger (on the survivor)
    811/812  FDIC-assisted / RTC-assisted merger (on the survivor)
    820  Phantom (Interim) Corporate Reorganization

  Merger records carry three institution roles:
    OUT_* — the absorbed (disappearing) institution
    ACQ_* — the acquirer at deal time
    SUR_* — the surviving institution
  On the survivor's cert an 810 row has ACQ_CERT == SUR_CERT == cert and
  OUT_* identifies the target; on the absorbed cert a 2xx row has
  OUT_CERT == cert and SUR_* identifies who it merged into.

Cache: ``fdic_structure:{cert}`` for 7 days via the shared freshness check —
structure changes are rare. (The cache backend's own global TTL, 24h, expires
entries first locally; worst case is a daily refetch.)

Failures return [] with one ``[fdic_structure]`` log line — never raise,
never a plausible-wrong list.
"""

from __future__ import annotations

from datetime import datetime

FDIC_HISTORY_URL = "https://api.fdic.gov/banks/history"
CACHE_TTL_SECONDS = 7 * 86400

# Institution-level structure events only; 5xx/7xx office rows excluded
# server-side (Banner: 416 raw history rows -> 19 structure events).
STRUCTURE_CODES_FILTER = "(CHANGECODE:[100 TO 499] OR CHANGECODE:[800 TO 899])"
_FIELDS = ",".join([
    "CERT", "INSTNAME", "EFFDATE", "CHANGECODE", "CHANGECODE_DESC",
    "OUT_CERT", "OUT_INSTNAME", "ACQ_CERT", "ACQ_INSTNAME",
    "SUR_CERT", "SUR_INSTNAME",
])
_PAGE_SIZE = 1000


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


def to_cert(raw) -> int | None:
    """Coerce a cert field to int; 0/None/'' (FDIC's 'no institution') -> None."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v or None


def _classify(cert: int, out_cert, acq_cert, sur_cert) -> str:
    """
    Direction of a structure event relative to ``cert``:
      was_acquired — this cert is the OUT (absorbed) institution and a
                     different institution survived
      acquired     — a different institution was absorbed and this cert is
                     the acquirer/survivor
      other        — charter/class changes, phantom self-reorganizations
                     (OUT == SUR == cert), liquidations with no survivor
    """
    if out_cert == cert and sur_cert is not None and sur_cert != cert:
        return "was_acquired"
    if out_cert is not None and out_cert != cert and cert in (acq_cert, sur_cert):
        return "acquired"
    return "other"


def parse_event(cert: int, d: dict) -> dict | None:
    """One API record -> one event dict, or None if undated."""
    date = (d.get("EFFDATE") or "")[:10]
    if not date:
        return None
    out_cert = to_cert(d.get("OUT_CERT"))
    acq_cert = to_cert(d.get("ACQ_CERT"))
    sur_cert = to_cert(d.get("SUR_CERT"))
    direction = _classify(cert, out_cert, acq_cert, sur_cert)
    if direction == "acquired":
        other = {"name": d.get("OUT_INSTNAME") or "", "cert": out_cert}
    elif direction == "was_acquired":
        other = {"name": d.get("SUR_INSTNAME") or "", "cert": sur_cert}
    else:
        other = None
    return {
        "date": date,
        "event_type": d.get("CHANGECODE"),
        "description": d.get("CHANGECODE_DESC") or "",
        "other_institution": other,
        "direction": direction,
    }


def fetch_history_rows(filters: str, fields: str = _FIELDS,
                       log_tag: str = "fdic_structure") -> list[dict] | None:
    """
    All records matching an FDIC /banks/history filter expression, paginated.

    Returns raw record dicts (the ``data`` inner object per row), or None on
    fetch failure — callers distinguish "no rows" from "couldn't fetch" so a
    transient outage is never cached as an empty history.
    """
    from data.http import get_with_retry

    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "filters": filters,
            "fields": fields,
            "sort_by": "EFFDATE",
            "sort_order": "DESC",
            "limit": _PAGE_SIZE,
            "offset": offset,
        }
        try:
            resp = get_with_retry(FDIC_HISTORY_URL, params, timeout=30)
            if resp is None:
                print(f"[{log_tag}] {filters}: retries exhausted (429)")
                return None
            page = resp.json().get("data", [])
        except Exception as e:
            print(f"[{log_tag}] {filters} error: {type(e).__name__}: {e}")
            return None
        rows.extend(r.get("data", {}) for r in page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def get_structure_events(cert: int) -> list[dict]:
    """
    All institution-level structure events for an FDIC cert, newest-first.

    Returns [{date: 'YYYY-MM-DD', event_type: int FDIC change code,
              description, other_institution: {name, cert} | None,
              direction: 'acquired' | 'was_acquired' | 'other'}].
    Covers both directions: institutions absorbed BY this cert (810 rows)
    and this cert's own termination event (2xx rows), if any.
    """
    if not cert:
        return []
    cert = int(cert)
    from data import cache

    key = f"fdic_structure:{cert}"
    cached = cache.get(key)
    if _is_fresh(cached) and isinstance(cached.get("events"), list):
        return cached["events"]

    rows = fetch_history_rows(f"CERT:{cert} AND {STRUCTURE_CODES_FILTER}")
    if rows is None:
        return []

    events = [e for e in (parse_event(cert, d) for d in rows) if e]
    events.sort(key=lambda e: e["date"], reverse=True)

    cache.put(key, {"events": events, "cached_at": datetime.now().isoformat()})
    return events


def get_acquisition_history(cert: int) -> list[dict]:
    """
    Completed acquisitions (institutions absorbed by this cert), newest-first,
    shaped for the M&A table: [{date, target_name, target_cert, event_desc}].
    """
    return [
        {
            "date": e["date"],
            "target_name": e["other_institution"]["name"],
            "target_cert": e["other_institution"]["cert"],
            "event_desc": e["description"],
        }
        for e in get_structure_events(cert)
        if e["direction"] == "acquired" and e["other_institution"]
    ]


if __name__ == "__main__":
    # LIVE smoke: Banner Bank (cert 28489) vs the SNL completed-acquisitions
    # screenshot. SNL names the holding companies; FDIC names the bank subs.
    BANNER_CERT = 28489
    SNL_ROWS = [  # (SNL holdco name, SNL effective date, FDIC bank-name keyword)
        ("AltaPacific Bancorp",         "11/01/2019", "AltaPacific"),
        ("Skagit Bancorp, Inc.",        "11/01/2018", "Skagit"),
        ("Starbuck Bancshares, Inc.",   "10/01/2015", "AmericanWest"),
        ("Siuslaw Financial Group Inc", "03/06/2015", "Siuslaw"),
    ]

    acq = get_acquisition_history(BANNER_CERT)
    print(f"Banner Bank (cert {BANNER_CERT}) — {len(acq)} completed acquisitions per FDIC:")
    for a in acq:
        print(f"  {a['date']}  {a['target_name']}  (cert {a['target_cert']})")

    print("\nSNL screenshot vs FDIC structure history:")
    missing = []
    for snl_name, snl_date, kw in SNL_ROWS:
        hit = next((a for a in acq if kw.lower() in a["target_name"].lower()), None)
        if hit:
            print(f"  SNL: {snl_name:<30} {snl_date}  ->  FDIC: {hit['target_name']:<22} {hit['date']}")
        else:
            missing.append(snl_name)
            print(f"  SNL: {snl_name:<30} {snl_date}  ->  FDIC: NOT FOUND")
    if missing:
        raise SystemExit(f"SMOKE FAIL: missing {missing}")
    print("\nSMOKE OK: all four SNL acquisitions surfaced.")
