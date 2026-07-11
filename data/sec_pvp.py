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
    ug = (facts.get("facts", {}) or {}).get("us-gaap", {}) or {}
    ni_by_year: dict[str, dict] = {}
    for tag in ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                "ProfitLoss"):
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
