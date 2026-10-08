"""Pay-versus-performance (SEC Item 402(v)) from proxy inline XBRL.

Since fiscal-2022 proxies, DEF 14As tag the PvP table in the `ecd` taxonomy;
those facts flow through companyfacts and are kept by the slim projection in
data/sec_client. This module reshapes them into the as-disclosed table:
one row per fiscal year with PEO (principal executive officer) summary-comp
total vs "compensation actually paid", the non-PEO NEO averages, TSR of a
fixed $100 investment (company + disclosed peer group), the net income the
issuer tagged IN THE PROXY, and the company-selected measure value.

Faithful-extraction rules:
- Values are the issuer's own tagged facts — reshaped, never computed.
- Successive proxies restate overlapping years; the newest filing wins per year.
- Multiple PEOs in one year (CEO transition) produce multiple facts for the
  same period in the same filing. The flat companyfacts API drops the
  executive dimension, so the values can't be attributed to a named officer —
  ALL values are kept (rendered together) rather than guessing one.
- CEO pay ratio (Item 402(u)) is NOT XBRL-tagged by the ecd taxonomy, so it
  is deliberately absent here — a text parse would be per-bank fragile.
"""
from __future__ import annotations

# ecd tag → output field. TSR amounts are the value of a fixed $100
# investment at each fiscal year end (as mandated), not a return %.
_TAGS = {
    "PeoTotalCompAmt": "peo_total",
    "PeoActuallyPaidCompAmt": "peo_paid",
    "NonPeoNeoAvgTotalCompAmt": "non_peo_avg_total",
    "NonPeoNeoAvgCompActuallyPaidAmt": "non_peo_avg_paid",
    "TotalShareholderRtnAmt": "tsr",
    "PeerGroupTotalShareholderRtnAmt": "peer_tsr",
    "CoSelectedMeasureAmt": "co_selected",
}


def _filing_url(cik: int, accn: str) -> str | None:
    if not (cik and accn):
        return None
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accn.replace('-', '')}/{accn}-index.htm")


def _facts_for(ns: dict, tag: str) -> list[dict]:
    """All unit entries for a tag, unit-agnostic (CoSelectedMeasureAmt may be
    'pure' or 'USD' depending on the measure the issuer picked)."""
    out = []
    for entries in (ns.get(tag, {}).get("units", {}) or {}).values():
        out.extend(e for e in entries if isinstance(e, dict))
    return out


def _pick_per_year(entries: list[dict]) -> dict[str, dict]:
    """{fy_end: {"values": [..], "accn", "filed"}} — newest filing wins each
    fiscal year; within that filing, all distinct values are kept (multiple
    PEOs are real disclosure, not duplicates)."""
    by_year: dict[str, dict] = {}
    for e in entries:
        end, val, filed = e.get("end"), e.get("val"), e.get("filed") or ""
        if not end or val is None:
            continue
        cur = by_year.get(end)
        if cur is None or filed > cur["filed"]:
            by_year[end] = {"values": [val], "accn": e.get("accn"), "filed": filed}
        elif filed == cur["filed"] and val not in cur["values"]:
            cur["values"].append(val)
    return by_year


_PROXY_CKEY_V = "v1"
# companyfacts PvP older than this (newest ecd fact filed > ~13 months ago)
# means SEC missed at least one annual proxy — read the latest DEF 14A.
_ECD_STALE_DAYS = 400
_NI_TAGS = ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
            "ProfitLoss")


def _newest_filed(ns: dict) -> str:
    return max((e.get("filed") or "" for tag in _TAGS for e in _facts_for(ns, tag)),
               default="")


def _proxy_namespaces(cik: int) -> tuple[dict, dict]:
    """(ecd, us-gaap) in companyfacts' shape, read from the latest DEF 14A's
    OWN inline XBRL — for issuers whose PvP facts SEC's companyfacts never
    ingested. AUB (2026-10-08): every proxy since 2023 tags the full Item
    402(v) table (FY2025 PEO total $5,066,713), yet companyfacts carries no
    `ecd` namespace for the CIK at all, so the Compensation tab hid every
    year as "cannot be verified". Same SEC-side class as the companyfacts
    10-Q lag (data/sec_facts_overlay).

    Undimensioned facts plus per-PEO facts (ecd:IndividualAxis only — a CEO
    transition year) are kept; adjustment rows (AdjToCompAmt and anything
    under another axis) are not. Cached immutably per accession; a fetch or
    parse failure returns empty namespaces uncached (the caller then shows
    what companyfacts has, as before)."""
    from data import cache
    from data.sec_filing_scraper import instance_facts, latest_filing
    try:
        meta = latest_filing(cik, forms=("DEF 14A",))
    except Exception as e:
        print(f"[pvp] proxy lookup failed for CIK {cik}: {type(e).__name__}: {e}")
        return {}, {}
    if not meta:
        return {}, {}
    acc = meta["accession"]
    ckey = f"pvp_proxy:{_PROXY_CKEY_V}:{acc}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return hit.get("ecd") or {}, hit.get("us-gaap") or {}
    try:
        facts = instance_facts({"cik": int(cik), "accession": acc, "doc": meta["doc"]})
    except Exception as e:
        print(f"[pvp] proxy parse failed for CIK {cik} {acc}: {type(e).__name__}: {e}")
        return {}, {}
    accn = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}" if len(acc) == 18 else acc
    ns = {"ecd": {}, "us-gaap": {}}
    for f in facts:
        prefix, _, name = f.concept.partition(":")
        keep = ((prefix == "ecd" and name in _TAGS)
                or (prefix == "us-gaap" and name in _NI_TAGS))
        if not keep or not f.period_end:
            continue
        if f.members and set(f.members) - {"ecd:IndividualAxis"}:
            continue
        entry = {"end": f.period_end, "val": f.value, "accn": accn,
                 "filed": meta.get("date"), "form": "DEF 14A"}
        if f.period_start:
            entry["start"] = f.period_start
        ns[prefix].setdefault(name, {"units": {"proxy": []}})["units"]["proxy"].append(entry)
    try:
        cache.put(ckey, ns)
    except Exception:
        pass
    return ns["ecd"], ns["us-gaap"]


def _merge(ns: dict, extra: dict) -> dict:
    """companyfacts namespace + proxy-read facts for the same tags (newest
    filing still wins per year downstream in _pick_per_year)."""
    out = {tag: {"units": {u: list(v) for u, v in (body.get("units") or {}).items()}}
           for tag, body in ns.items()}
    for tag, body in extra.items():
        units = out.setdefault(tag, {"units": {}})["units"]
        for u, v in (body.get("units") or {}).items():
            units.setdefault(u, []).extend(v)
    return out


def get_pay_versus_performance(cik: int) -> dict | None:
    """As-disclosed PvP table for a company, or None when the proxy has no
    tagged PvP (pre-2023 filers, non-reporting banks).

    {"years": [{fy_end, peo_total: [..], peo_paid: [..], non_peo_avg_total,
                non_peo_avg_paid, tsr, peer_tsr, net_income, co_selected},
               ...newest first],
     "multi_peo": bool, "filed": str, "source_url": str}

    List-valued PEO fields carry every tagged value for that year (usually
    one; two+ means a CEO transition year). Single-valued fields take the
    first value and are None when untagged (smaller reporting companies may
    omit peer TSR / company-selected measure by rule).
    """
    from data.sec_client import fetch_company_facts

    if not cik:
        return None
    facts = fetch_company_facts(int(cik)) or {}
    ns = (facts.get("facts", {}) or {}).get("ecd", {}) or {}
    ug = (facts.get("facts", {}) or {}).get("us-gaap", {}) or {}
    # SEC's companyfacts can miss proxies entirely (AUB) or stop at an old
    # one: read the latest DEF 14A's own XBRL then. Current companyfacts PvP
    # costs nothing extra.
    from datetime import date, timedelta
    stale_before = (date.today() - timedelta(days=_ECD_STALE_DAYS)).isoformat()
    if not ns or _newest_filed(ns) < stale_before:
        p_ecd, p_ug = _proxy_namespaces(int(cik))
        if p_ecd:
            ns, ug = _merge(ns, p_ecd), _merge(ug, p_ug)
    if not ns:
        return None

    per_field = {field: _pick_per_year(_facts_for(ns, tag))
                 for tag, field in _TAGS.items()}
    if not per_field["peo_total"]:
        return None

    # Net income column: the issuer re-tags a us-gaap net-income element
    # inside the proxy — use ONLY proxy-filed facts so the column is the
    # disclosed table, not our 10-K pipeline. Filers vary the element (WAL's
    # 2026 proxy used ProfitLoss for FY2025, NetIncomeLoss before): per year,
    # newest filing wins across the ladder; on a same-filing tie the ladder
    # order below is the preference.
    ni_by_year: dict[str, dict] = {}
    for tag in _NI_TAGS:
        ni_proxy = [e for e in _facts_for(ug, tag) if e.get("form") == "DEF 14A"]
        for y, rec in _pick_per_year(ni_proxy).items():
            cur = ni_by_year.get(y)
            if cur is None or rec["filed"] > cur["filed"]:
                ni_by_year[y] = rec  # earlier ladder tags win filed ties
    per_field["net_income"] = ni_by_year

    years = sorted(per_field["peo_total"], reverse=True)
    rows, multi_peo = [], False
    newest = per_field["peo_total"][years[0]]
    for y in years:
        row = {"fy_end": y}
        for field in ("peo_total", "peo_paid"):
            vals = (per_field[field].get(y) or {}).get("values") or []
            row[field] = vals
            multi_peo = multi_peo or len(vals) > 1
        for field in ("non_peo_avg_total", "non_peo_avg_paid", "tsr",
                      "peer_tsr", "net_income", "co_selected"):
            vals = (per_field[field].get(y) or {}).get("values") or []
            row[field] = vals[0] if vals else None
        rows.append(row)

    return {
        "years": rows,
        "multi_peo": multi_peo,
        "filed": newest.get("filed"),
        "source_url": _filing_url(int(cik), newest.get("accn") or ""),
    }
