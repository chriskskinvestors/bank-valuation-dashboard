"""Overlay a filed-but-unpublished 10-Q/10-K onto the companyfacts blob.

SEC's XBRL API (companyfacts) sometimes never ingests a filing the bank has
already made: on 2026-09-22 it held nothing past Q1-2026 for ONB, FRME, HBAN
and CCBG (Q2 10-Qs filed Jul 28-31, full inline XBRL in each) and nothing
past FY2025 for Citi. Every HoldCo figure the dashboard derives from
companyfacts — book value, tangible book value, shares, the XBRL TTM EPS —
then sits a quarter or two behind the bank's own disclosure.

The filing itself carries the same facts, tagged with the same us-gaap
concepts, in its inline-XBRL instance. When the latest periodic filing in
the submissions index covers a period-end NEWER than the freshest fact in
the blob, this module parses that filing's instance (data/sec_filing_scraper,
the parser the capital / fair-value extractors already use) and appends its
UNDIMENSIONED facts for the new period to the slim blob in companyfacts'
own entry shape. Everything downstream — TTM derivation, share resolution,
the intangible adjustment, staleness stamps — runs unchanged on the
completed series: no second formula, no second code path for the numbers.

Scope and limits
- Only entries whose period END is newer than the blob's freshest core
  fact are added; a filing's comparatives (which companyfacts already has)
  are skipped, so the blob stays lean and nothing is double-counted.
- Only the concepts the blob keeps (sec_client.SLIM_USGAAP_CONCEPTS + dei).
- Dimensioned facts (segment / class members) are never added — the blob's
  extractors read undimensioned totals.
- One filing (the latest) is overlaid. A bank two filings behind (Citi:
  Q1 and Q2 both missing) gets its balance sheet current from the latest
  10-Q; flows that need the skipped quarter resolve only if the YTD
  differences the TTM rules already apply can bridge it — else they stay
  None, never a guess.
- The overlay is applied at read time on top of the cached blob, never
  written into it: once SEC publishes the filing the blob's own entries
  take over and the overlay finds nothing to do.
- The instance parse is cached immutably per accession (a filing never
  changes); the lag check itself is free (the 2h-cached submissions record
  data/sec_earnings_8k already loads per bank).

Provenance: overlaid entries carry "overlay": True and the blob gets a
top-level "_overlay" record {accession, form, report_date, filed}; the
Corporate Profile card labels the figures "(co. 10-Q)" from it.
"""
from __future__ import annotations

_OVERLAY_CKEY_V = "v1"

# The blob's freshness anchor — the same core concepts sec_client stamps
# sec_as_of from, so the overlay opens exactly when that stamp would lag.
_CORE_CONCEPTS = ("StockholdersEquity",
                  "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                  "Assets", "NetIncomeLoss")

# Unit bucket for a concept that has no prior entries in the blob (else the
# concept's existing unit key is reused). companyfacts uses these three.
_PER_SHARE = ("EarningsPerShareDiluted", "EarningsPerShareBasic",
              "CommonStockDividendsPerShareDeclared")


def _unit_for(concept: str, existing_units: dict) -> str:
    if existing_units:
        return next(iter(existing_units))
    if concept in _PER_SHARE:
        return "USD/shares"
    if "Shares" in concept:
        return "shares"
    return "USD"


def _fp(form: str, report_date: str) -> str:
    if form == "10-K":
        return "FY"
    try:
        return f"Q{(int(report_date[5:7]) - 1) // 3 + 1}"
    except (TypeError, ValueError):
        return ""


def _blob_as_of(slim: dict) -> str | None:
    """Freshest 10-Q/10-K period end across the core concepts, or None."""
    ug = (slim.get("facts") or {}).get("us-gaap") or {}
    latest = None
    for concept in _CORE_CONCEPTS:
        for entries in (ug.get(concept) or {}).get("units", {}).values():
            for e in entries:
                if e.get("form") in ("10-K", "10-Q"):
                    end = e.get("end")
                    if end and (latest is None or end > latest):
                        latest = end
    return latest


def filing_entries(cik: int, filing: dict) -> list[dict]:
    """Every undimensioned us-gaap / dei fact in `filing`'s iXBRL instance as
    a companyfacts-shaped entry (concept and namespace attached), cached
    immutably by accession. `filing` = latest_filing()-shaped meta:
    {accession, doc, date, form, cik} plus report_date."""
    from data import cache
    from data.sec_client import SLIM_USGAAP_CONCEPTS, _SLIM_VER
    from data.sec_filing_scraper import instance_facts
    # Keyed on the slim concept set too: the cached entries are filtered by
    # it, so a concept added later must not read as absent from old parses.
    ckey = f"filing_overlay:{_OVERLAY_CKEY_V}:{_SLIM_VER}:{filing['accession']}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return hit.get("entries", [])
    fp = _fp(filing.get("form", ""), filing.get("report_date", ""))
    fy = (filing.get("report_date") or "")[:4]
    out = []
    for f in instance_facts({"cik": int(cik), "accession": filing["accession"],
                             "doc": filing["doc"]}):
        if f.members or not f.period_end:
            continue
        ns, _, concept = f.concept.rpartition(":")
        if ns == "us-gaap" and concept not in SLIM_USGAAP_CONCEPTS:
            continue
        if ns not in ("us-gaap", "dei"):
            continue
        e = {"ns": ns, "concept": concept, "end": f.period_end, "val": f.value,
             "accn": filing.get("accession_dash") or filing["accession"],
             "fy": int(fy) if fy.isdigit() else None, "fp": fp,
             "form": filing.get("form"), "filed": filing.get("date"),
             "overlay": True}
        if f.period_start:
            e["start"] = f.period_start
        out.append(e)
    try:
        cache.put(ckey, {"entries": out})
    except Exception:
        pass
    return out


def overlay_lagging_filing(cik: int, slim: dict) -> dict:
    """Return `slim` with the latest filed-but-unpublished 10-Q/10-K's new-
    period facts appended (a shallow copy — the cached blob is never
    mutated), or `slim` itself when companyfacts is current, the filing
    index is unavailable, or the instance can't be parsed. Failure is a
    no-op with a log line: the row then renders the (dated) blob figures.

    Then fills any EARLIER quarter companyfacts skipped entirely
    (_fill_skipped_quarters) — PNFP/CBC/ENBP 2026-09: the Q3-2025 10-Q was
    never ingested although later 10-Qs were, so no TTM window could form."""
    if not slim or not slim.get("facts"):
        return slim
    try:
        from data.sec_earnings_8k import latest_periodic_filing
        periodic = latest_periodic_filing(cik)
    except Exception as e:
        print(f"[SEC] overlay: filing index unavailable for CIK {cik}: "
              f"{type(e).__name__}: {e}")
        return slim
    if not periodic:
        return slim
    out = _overlay_latest(cik, slim, periodic)
    return _fill_skipped_quarters(cik, out)


def _overlay_latest(cik: int, slim: dict, periodic: dict) -> dict:
    """The latest-filing overlay (see overlay_lagging_filing)."""
    from data.sec_filing_scraper import latest_filing
    as_of = _blob_as_of(slim)
    if not periodic or not periodic.get("report_date") or not as_of:
        return slim
    if periodic["report_date"] <= as_of:
        return slim
    try:
        meta = latest_filing(cik, forms=("10-Q", "10-K"))
        if not meta:
            return slim
        meta["report_date"] = periodic["report_date"]
        entries = filing_entries(cik, meta)
    except Exception as e:
        print(f"[SEC] overlay: instance parse failed for CIK {cik}: "
              f"{type(e).__name__}: {e}")
        return slim
    new = [e for e in entries if e["end"] > as_of]
    if not new:
        return slim
    out = dict(slim)
    facts = {ns: {c: {**cd, "units": {u: list(v) for u, v in cd.get("units", {}).items()}}
                  for c, cd in (slim["facts"].get(ns) or {}).items()}
             for ns in slim["facts"]}
    for e in new:
        ns_map = facts.setdefault(e["ns"], {})
        cd = ns_map.setdefault(e["concept"], {"units": {}})
        unit = _unit_for(e["concept"], cd["units"])
        entry = {k: v for k, v in e.items() if k not in ("ns", "concept")}
        cd["units"].setdefault(unit, []).append(entry)
    out["facts"] = facts
    out["_overlay"] = {"accession": meta["accession"], "form": meta.get("form"),
                       "report_date": periodic["report_date"],
                       "filed": meta.get("date"), "n_facts": len(new)}
    print(f"[SEC] overlay: CIK {cik} companyfacts as of {as_of} < "
          f"{meta.get('form')} for {periodic['report_date']} filed "
          f"{meta.get('date')} — {len(new)} facts overlaid from the filing's "
          f"own iXBRL", flush=True)
    return out


# Flow concepts whose period ENDS reveal a skipped filing (any duration fact
# ending at a quarter-end means companyfacts has that filing's flows).
_FLOW_ANCHORS = ("NetIncomeLoss", "ProfitLoss", "EarningsPerShareDiluted")
_NEAR_DAYS = 15                    # 52/53-week calendars: same period-end


def _quarter_end_before(end: str, months: int) -> str:
    """Month-end `months` before `end` (ISO), e.g. 2026-06-30, 9 → 2025-09-30."""
    from datetime import date, timedelta
    y, m = int(end[:4]), int(end[5:7]) - months
    while m <= 0:
        m += 12
        y -= 1
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def _days_apart(a: str, b: str) -> int:
    from datetime import date
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _fill_skipped_quarters(cik: int, blob: dict) -> dict:
    """Append the flows of any 10-Q/10-K that companyfacts SKIPPED inside the
    trailing window (the three quarter-ends before the freshest flow end).

    A quarter-end with no duration fact at all in any flow anchor means that
    filing was never ingested (companyfacts' own entries for later periods are
    present, so this is not a lag at the head — the latest-filing overlay does
    not reach it). Only then is the filing index read (15-min cached) and the
    first 10-Q/10-K filed after that quarter-end parsed (cached per accession),
    and only its facts for that quarter-end that the blob lacks are appended.
    Healthy banks cost nothing: the check reads the blob only. Any failure is
    a no-op — the TTM rules then return None for the gap, never a guess."""
    ug = (blob.get("facts") or {}).get("us-gaap") or {}
    ends: set = set()
    for concept in _FLOW_ANCHORS:
        for entries in (ug.get(concept) or {}).get("units", {}).values():
            for e in entries:
                if e.get("start") and e.get("end") and e.get("form") in ("10-Q", "10-K"):
                    ends.add(e["end"])
    if not ends:
        return blob
    latest = max(ends)
    wanted = [_quarter_end_before(latest, m) for m in (3, 6, 9)]
    missing = [q for q in wanted
               if not any(_days_apart(q, e) <= _NEAR_DAYS for e in ends)]
    if not missing:
        return blob
    try:
        from data.sec_filing_scraper import _recent_metas
        metas = _recent_metas(cik, ("10-Q", "10-K"), 8)
    except Exception as e:
        print(f"[SEC] gap overlay: filing index unavailable for CIK {cik}: "
              f"{type(e).__name__}: {e}")
        return blob
    have = {(ns, c, e.get("start"), e.get("end"))
            for ns, concepts in (blob.get("facts") or {}).items()
            if isinstance(concepts, dict)
            for c, cd in concepts.items()
            for entries in (cd.get("units") or {}).values()
            for e in entries}
    add, filled = [], []
    for q in missing:
        after = sorted((m for m in metas if (m.get("date") or "") > q),
                       key=lambda m: m.get("date") or "")
        if not after:
            continue
        meta = dict(after[0], report_date=q)
        try:
            entries = filing_entries(cik, meta)
        except Exception as e:
            print(f"[SEC] gap overlay: instance parse failed for CIK {cik} "
                  f"{meta.get('accession')}: {type(e).__name__}: {e}")
            continue
        got = [e for e in entries
               if _days_apart(e["end"], q) <= _NEAR_DAYS
               and (e["ns"], e["concept"], e.get("start"), e["end"]) not in have]
        if got:
            add.extend(got)
            filled.append({"accession": meta["accession"], "form": meta.get("form"),
                           "report_date": q, "filed": meta.get("date"),
                           "n_facts": len(got)})
    if not add:
        return blob
    out = dict(blob)
    facts = {ns: {c: {**cd, "units": {u: list(v) for u, v in cd.get("units", {}).items()}}
                  for c, cd in (blob["facts"].get(ns) or {}).items()}
             for ns in blob["facts"]}
    for e in add:
        cd = facts.setdefault(e["ns"], {}).setdefault(e["concept"], {"units": {}})
        unit = _unit_for(e["concept"], cd["units"])
        cd["units"].setdefault(unit, []).append(
            {k: v for k, v in e.items() if k not in ("ns", "concept")})
    out["facts"] = facts
    out["_overlay_gaps"] = filled
    print(f"[SEC] gap overlay: CIK {cik} companyfacts skipped "
          f"{', '.join(f['form'] + ' ' + f['report_date'] for f in filled)} — "
          f"{len(add)} facts filled from the filings' own iXBRL", flush=True)
    return out
