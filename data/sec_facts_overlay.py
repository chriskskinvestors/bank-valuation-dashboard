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


# ── Multi-class common share counts ──────────────────────────────────────────
# A filer with two common classes tags its balance-sheet share counts ONLY per
# class, on us-gaap:StatementClassOfStockAxis — and companyfacts drops every
# dimensioned fact. So the blob holds no period-end count at all and the share
# chain falls back to something that is not the total: OCFC (post-Flushing,
# 2026-06-30) served its voting-only dei cover count 96,645,219 while the
# 1,812,000 non-voting common-equivalent shares also participate (release
# TBVPS $18.19 on 98,416,195 vs our $18.53); FCNCA served its Q2 WEIGHTED-
# AVERAGE 11,729,271 as the period-end count (true A+B 11,390,407 — BVPS 3%
# low); RBCAA resolved nothing. The per-class facts ARE in the filing's own
# iXBRL, so they are read from there and summed — or the count is n/a.
# v2: undimensioned counts and the dei cover's per-class counts are kept
#     (OPHC: voting class undimensioned, nonvoting dimensioned).
_CLASS_SHARES_CKEY_V = "v2"
_CLASS_AXIS = "StatementClassOfStockAxis"
_CLASS_COUNT_CONCEPTS = ("CommonStockSharesOutstanding", "CommonStockSharesIssued")
_TREASURY_CONCEPTS = ("TreasuryStockCommonShares", "TreasuryStockShares")
_COVER_CONCEPT = "EntityCommonStockSharesOutstanding"
# An undimensioned count is attributed to the one cover class lacking a
# balance-sheet count only when it is within this of that class's cover
# count (OPHC: 12,340,785 at 6/30 vs 12,622,470 on the Aug-10 cover, 2.2%).
_COVER_CLASS_TOL = 0.10


def class_share_entries(cik: int, filing: dict) -> list[dict]:
    """Instant share-count facts from `filing`'s iXBRL that companyfacts
    cannot carry: per-class outstanding / issued / treasury counts and the
    dei cover's per-class counts (exactly one member, on StatementClassOf-
    StockAxis) plus the UNDIMENSIONED counts and treasury the per-class
    tie-outs need. [{concept, cls, end, val}], cls None for undimensioned.
    Cached immutably by accession (a filing never changes)."""
    from data import cache
    from data.sec_filing_scraper import instance_facts
    ckey = f"class_shares:{_CLASS_SHARES_CKEY_V}:{filing['accession']}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return hit.get("entries", [])
    out = []
    for f in instance_facts({"cik": int(cik), "accession": filing["accession"],
                             "doc": filing["doc"]}):
        concept = f.concept.split(":")[-1]
        if f.period_start or not f.period_end:
            continue
        if concept not in _CLASS_COUNT_CONCEPTS + _TREASURY_CONCEPTS + (
                _COVER_CONCEPT,):
            continue
        if not f.members:
            if concept == _COVER_CONCEPT:
                continue
            cls = None
        elif (len(f.members) == 1
              and next(iter(f.members)).split(":")[-1] == _CLASS_AXIS):
            cls = next(iter(f.members.values()))
        else:
            continue
        out.append({"concept": concept, "cls": cls,
                    "end": f.period_end, "val": f.value})
    try:
        cache.put(ckey, {"entries": out})
    except Exception:
        pass
    return out


def resolve_class_shares(entries: list[dict], end: str) -> dict | None:
    """Total common shares outstanding at `end` summed across share classes,
    or None when the filing is not multi-class (fewer than two classes carry
    a per-class outstanding/issued count at any date — nothing to do).

    Returns {end, value, classes, status, reason}. status "resolved" → value
    is the exact per-class sum; "unresolved" → value None (per-share n/a —
    a multi-class filer's single-class or averaged count is a plausible-wrong
    denominator). Per class, at `end`:
      • outstanding tagged → that count;
      • else issued AND class-dimensioned treasury → issued − treasury;
      • else issued only → 0 when nothing is issued; otherwise issued less
        the undimensioned treasury NOT explained by the other classes'
        issued − outstanding gaps — all of that residual when this is the
        only such class, and it must be exactly zero when several are.
        OCFC: voting 102,258,357 issued − 96,604,195 outstanding = 5,654,162
        = total treasury, so the NVCE's 1,812,000 issued are all
        outstanding. No treasury tag / no tie-out → unresolved.
    Only classes with a count AT `end` are summed (a class tagged only in the
    prior column has been retired). No per-class count at `end`, a class
    whose outstanding ≠ issued − its own treasury, a "preferred" member under
    a common-share concept, a negative count, or two different values for
    one slot → unresolved.

    A filer can also tag ONE class undimensioned and the other per class —
    OPHC 10-Q 0001493152-26-036825: CommonStockSharesOutstanding 12,340,785
    with no member (its voting class) and 11,458,351 on
    NonvotingCommonStockMember, while the cover page counts both classes
    (12,622,470 / 11,458,351 at 2026-08-10). The balance sheet alone shows
    one class, so the cover decides: when it names two or more classes and
    exactly one of them has no balance-sheet count at `end`, the
    undimensioned count is that class's (within _COVER_CLASS_TOL of its
    cover count) — 23,799,136 in all, the release's "fully diluted" count
    ($134,380K ÷ it = $5.65, as printed). An undimensioned count that is
    instead the cover TOTAL is the whole already (None — nothing to do);
    one that matches neither is unresolved."""
    counted = [e for e in entries
               if e["cls"] and e["concept"] in _CLASS_COUNT_CONCEPTS]
    cover = _cover_classes(entries)
    if len({e["cls"] for e in counted}) < 2 and len(cover) < 2:
        return None
    # The classes outstanding at `end` are the ones the balance sheet tags at
    # `end`: a class tagged only in the prior column is gone (BANC: its NVCE
    # class appears at 2025-12-31 only — the release confirms "no non-voting
    # common stock equivalents outstanding as of June 30, 2026").
    classes = sorted({e["cls"] for e in counted if e["end"] == end})

    def _unresolved(reason):
        return {"end": end, "value": None, "classes": {},
                "status": "unresolved", "reason": reason}

    undim = {e["val"] for e in entries
             if e["cls"] is None and e["concept"] == "CommonStockSharesOutstanding"
             and e["end"] == end and e["val"] is not None}
    attributed = None
    if len(cover) >= 2 and len(undim) == 1:
        count = undim.pop()
        missing = [c for c in cover if c not in classes]
        if abs(count - sum(cover.values())) <= _COVER_CLASS_TOL * sum(cover.values()):
            return None                   # the undimensioned count IS the total
        if len(missing) == 1 and abs(count - cover[missing[0]]) <= (
                _COVER_CLASS_TOL * cover[missing[0]]):
            attributed = (missing[0], count)
            classes = sorted(classes + [missing[0]])
        elif missing:
            return _unresolved(
                f"undimensioned count {count:.0f} at {end} is neither the "
                f"cover total nor the uncounted class {missing}")
    if not classes:
        return _unresolved(f"no per-class count at {end}")
    # A preferred series tagged with a COMMON share concept (CFBK tags its
    # Series D preferred this way) contradicts itself — never sum it.
    if any("preferred" in c.split(":")[-1].lower() for c in classes):
        return _unresolved(f"preferred member under a common-share concept "
                           f"at {end}: {classes}")

    def _slot(concepts, cls):
        for c in concepts:
            vals = {e["val"] for e in entries
                    if e["concept"] == c and e["cls"] == cls and e["end"] == end}
            if len(vals) > 1:
                raise ValueError(f"conflicting {c} for {cls}: {sorted(vals)}")
            if vals:
                return vals.pop()
        return None

    try:
        counts, pending, known_gap = {}, [], 0.0
        for cls in classes:
            if attributed and cls == attributed[0]:
                counts[cls] = attributed[1]
                continue
            out = _slot(("CommonStockSharesOutstanding",), cls)
            iss = _slot(("CommonStockSharesIssued",), cls)
            tre = _slot(_TREASURY_CONCEPTS, cls)
            if out is not None:
                # CIA tags Class A "outstanding" = issued = 54,968,998 AND
                # 4,327,810 Class A treasury: the tags contradict each other,
                # and taking "outstanding" would overstate the count 10.5%.
                if (iss is not None and tre is not None
                        and abs(out - (iss - tre)) >= 0.5):
                    return _unresolved(
                        f"{cls} outstanding {out:.0f} ≠ issued {iss:.0f} − "
                        f"treasury {tre:.0f}")
                counts[cls] = out
                if iss is not None:
                    known_gap += iss - out
            elif iss is not None and tre is not None:
                counts[cls] = iss - tre
                known_gap += tre
            else:                       # issued only (every class here has
                pending.append(cls)     # an outstanding or issued count)
        # A class with nothing issued has nothing outstanding and can hold no
        # treasury (CBC: Class B authorized, 0 issued).
        for cls in [c for c in pending
                    if _slot(("CommonStockSharesIssued",), c) == 0]:
            counts[cls] = 0.0
            pending.remove(cls)
        if pending:
            # Treasury not explained by the other classes' issued − outstanding
            # gaps is exactly the pending classes' treasury: all of it when one
            # class is pending (CBC: 318,247,550 − 78,742,417 = 239,505,133),
            # none of it (must tie to zero) when several are.
            total_tre = _slot(_TREASURY_CONCEPTS, None)
            residual = None if total_tre is None else total_tre - known_gap
            if residual is None or residual < -0.5 or (
                    len(pending) > 1 and abs(residual) >= 0.5):
                return _unresolved(
                    f"treasury not attributable to {', '.join(pending)} "
                    f"(total {total_tre}, other classes' gap {known_gap:.0f})")
            for cls in pending:
                counts[cls] = _slot(("CommonStockSharesIssued",), cls)
            if len(pending) == 1:
                counts[pending[0]] -= residual
    except ValueError as e:
        return _unresolved(str(e))
    if any(v < 0 for v in counts.values()):
        return _unresolved(f"negative class count {counts}")
    return {"end": end, "value": sum(counts.values()), "classes": counts,
            "status": "resolved", "reason": ""}


def _cover_classes(entries: list[dict]) -> dict:
    """{class member: count} from the latest dei cover date that carries
    per-class counts; {} when the cover is undimensioned."""
    rows = [e for e in entries
            if e["cls"] and e["concept"] == _COVER_CONCEPT and e["val"] is not None]
    if not rows:
        return {}
    latest = max(e["end"] for e in rows)
    return {e["cls"]: e["val"] for e in rows if e["end"] == latest}


def _cover_lags_filing(cik: int, slim: dict) -> bool:
    """True when the blob's newest dei cover count was filed BEFORE the
    bank's latest 10-Q/10-K: that filing's cover count is absent from
    companyfacts, which drops dimensioned facts — the cover names its share
    classes (OPHC). A single-class cover is undimensioned and present, so
    this is false for the universe at large (zero instance fetches); a
    lagging SEC API is already completed by overlay_lagging_filing."""
    from data.sec_earnings_8k import latest_periodic_filing
    dei = ((slim.get("facts") or {}).get("dei") or {}).get(_COVER_CONCEPT) or {}
    filed = max((e.get("filed") or "" for u in dei.get("units", {}).values()
                 for e in u), default="")
    if not filed:
        return False
    try:
        periodic = latest_periodic_filing(cik)
    except Exception:
        return False
    return bool(periodic and periodic.get("date") and filed
                and periodic["date"] > filed)


def _has_undimensioned_count_at(slim: dict, end: str) -> bool:
    ug = (slim.get("facts") or {}).get("us-gaap") or {}
    return any(e.get("end") == end and e.get("form") in ("10-K", "10-Q")
               for c in _CLASS_COUNT_CONCEPTS
               for entries in (ug.get(c) or {}).get("units", {}).values()
               for e in entries)


def overlay_class_shares(cik: int, slim: dict) -> dict:
    """Return `slim` plus a top-level "_class_shares" record (see
    resolve_class_shares, with the source accession/form) when the filer is
    multi-class; else `slim` itself. Only consulted when the blob has NO
    undimensioned outstanding/issued count at its balance-sheet date, or
    when the latest filing's cover count is missing from the blob — a
    per-class cover (_cover_lags_filing; OPHC's voting-only undimensioned
    count) — so a filer that tags the total never pays the instance fetch.
    The record is read by data/sec_client's share chain; nothing is written
    into the blob's facts (no fabricated undimensioned total)."""
    if not slim or not slim.get("facts"):
        return slim
    from data.sec_client import _balance_sheet_date
    from data.sec_filing_scraper import latest_filing
    end = _balance_sheet_date(slim)
    if not end or (_has_undimensioned_count_at(slim, end)
                   and not _cover_lags_filing(cik, slim)):
        return slim
    meta = latest_filing(cik, forms=("10-Q", "10-K"))
    if not meta:
        return slim
    rec = resolve_class_shares(class_share_entries(cik, meta), end)
    if rec is None:
        return slim
    out = dict(slim)
    out["_class_shares"] = {**rec, "accession": meta["accession"],
                            "form": meta.get("form")}
    print(f"[SEC] class shares: CIK {cik} {rec['status']} at {end} — "
          f"{rec['classes'] or rec['reason']} ({meta.get('form')} "
          f"{meta['accession']})", flush=True)
    return out


# ── Preferred carrying value from the filing's own inline XBRL ───────────────
# companyfacts carries NO dimensioned facts. A filer that tags its preferred
# per series — PreferredStockValue on StatementClassOfStockAxis members
# (STT: four series, NTRS, BOH, FRME, WSBC, NEWT, BYFC) — or only as
# "Additional paid-in capital, preferred" (BCBP) has no undimensioned value
# for sec_client's ladder, so the cardinal rule blanked every per-common-share
# figure for 8 of the 11 SEC filers in the 2026-10-06 coverage audit's
# "preferred outstanding, value unresolved" class. The 10-Q instance
# itself carries the equity-section total in up to four shapes; the record
# is accepted when they agree, or — a single shape — when it is plausible
# as a carrying TOTAL over the same-date preferred share count.
_PREFERRED_TOTAL_CKEY_V = "v1"
_EQUITY_AXIS = "StatementEquityComponentsAxis"
_PREFERRED_MEMBER = "PreferredStockMember"
# Carrying-value concepts in sec_client's ladder order; liquidation last.
_PREFERRED_VALUE_CONCEPTS = (
    "PreferredStockValue",
    "PreferredStockIncludingAdditionalPaidInCapital",
    "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
    "PreferredStockValueOutstanding",
    "PreferredStockLiquidationPreferenceValue",
)
_PREFERRED_APIC = "AdditionalPaidInCapitalPreferredStock"
_PREFERRED_COUNT_CONCEPTS = ("PreferredStockSharesOutstanding",
                             "PreferredStockSharesIssued")
_PREFERRED_ENTRY_CONCEPTS = frozenset(
    _PREFERRED_VALUE_CONCEPTS + _PREFERRED_COUNT_CONCEPTS
    + (_PREFERRED_APIC, "StockholdersEquity"))
_PREFERRED_AGREE_TOL = 0.005          # cross-shape agreement (rounding)


def preferred_entries(cik: int, filing: dict) -> list[dict]:
    """Instant facts from `filing`'s iXBRL that bear on the preferred
    carrying value: the ladder's value concepts, preferred share counts,
    APIC-preferred and StockholdersEquity — each with its members as
    {axis_local_name: member_local_name} ({} when undimensioned).
    [{concept, members, end, val}], cached immutably by accession."""
    from data import cache
    from data.sec_filing_scraper import instance_facts
    ckey = f"preferred_total:{_PREFERRED_TOTAL_CKEY_V}:{filing['accession']}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return hit.get("entries", [])
    out = []
    for f in instance_facts({"cik": int(cik), "accession": filing["accession"],
                             "doc": filing["doc"]}):
        concept = f.concept.split(":")[-1]
        if f.period_start or not f.period_end:
            continue
        if concept not in _PREFERRED_ENTRY_CONCEPTS:
            continue
        members = {a.split(":")[-1]: m.split(":")[-1]
                   for a, m in f.members.items()}
        out.append({"concept": concept, "members": members,
                    "end": f.period_end, "val": f.value})
    try:
        cache.put(ckey, {"entries": out})
    except Exception:
        pass
    return out


def _is_preferred_member(member: str) -> bool:
    return "Preferred" in member and "Common" not in member


def _series_sum(entries: list[dict], concept: str, end: str,
                extra: dict | None = None) -> float | None:
    """Sum of `concept` over distinct StatementClassOfStockAxis preferred
    members at `end` (with `extra` axis→member also required, for the
    equity statement's per-series preferred column). None when no member
    carries a value, or when one member carries two different values."""
    per: dict = {}
    want = dict(extra or {})
    for e in entries:
        if e["concept"] != concept or e["end"] != end or e["val"] is None:
            continue
        m = e["members"]
        cls = m.get(_CLASS_AXIS)
        if cls is None or not _is_preferred_member(cls):
            continue
        if set(m) != set(want) | {_CLASS_AXIS}:
            continue
        if any(m.get(a) != v for a, v in want.items()):
            continue
        if cls in per and abs(per[cls] - e["val"]) > 0.5:
            return None
        per[cls] = e["val"]
    return sum(per.values()) if per else None


def _undimensioned(entries: list[dict], concept: str, end: str) -> float | None:
    vals = {e["val"] for e in entries
            if e["concept"] == concept and e["end"] == end
            and not e["members"] and e["val"] is not None}
    return vals.pop() if len(vals) == 1 else None


def _preferred_count_at(entries: list[dict], end: str) -> float | None:
    """Preferred shares at `end`: the undimensioned count when tagged, else
    the sum over series members (both Outstanding and Issued consulted, the
    smallest nonzero total — sec_client._same_date_preferred_count's rule)."""
    from data.sec_client import _same_date_preferred_count
    totals = []
    for concept in _PREFERRED_COUNT_CONCEPTS:
        u = _undimensioned(entries, concept, end)
        if u is not None:
            totals.append(u)
            continue
        per = {}
        for e in entries:
            m = e["members"]
            if (e["concept"] == concept and e["end"] == end
                    and e["val"] is not None and set(m) == {_CLASS_AXIS}
                    and _is_preferred_member(m[_CLASS_AXIS])):
                per[m[_CLASS_AXIS]] = e["val"]
        if per:
            totals.append(sum(per.values()))
    return _same_date_preferred_count(totals)


def resolve_preferred_total(entries: list[dict], end: str) -> dict | None:
    """The filer's preferred carrying value at `end` from its own instance,
    {end, value, basis}, or None when the instance does not establish one.

    Shapes read (all at `end`):
      1. the equity statement's preferred column — StockholdersEquity on
         StatementEquityComponentsAxis = PreferredStockMember (STT $3,559M,
         NTRS $884.9M, WSBC $224.2M);
      2. that column split per series — the same member plus a
         StatementClassOfStockAxis series, summed (BOH 180,000 + 165,000;
         FRME 125 + 25,000 $K);
      3. a ladder value concept tagged per series on StatementClassOfStock-
         Axis, summed — the first concept (ladder order) with any series
         (STT 493 + 1,481 + 842 + 743 = 3,559; BYFC 150,000);
      4. undimensioned AdditionalPaidInCapitalPreferredStock, plus any
         undimensioned par value (BCBP 25,243 $K over 2,548 shares).
    Members on any other axis are ignored (STT repeats PreferredStockValue
    on a Basel-approach axis). Two or more shapes must agree within
    _PREFERRED_AGREE_TOL or nothing is taken. A single shape is taken only
    when it is plausible as a carrying total over the same-date preferred
    share count (sec_client._not_a_carrying_total: no count, zero shares,
    or a par-only per-share amount all refuse it) and is below total
    equity. A zero is never a value."""
    from data.sec_client import _not_a_carrying_total
    cands: list[tuple[str, float, str]] = []
    for e in entries:
        if (e["concept"] == "StockholdersEquity" and e["end"] == end
                and e["members"] == {_EQUITY_AXIS: _PREFERRED_MEMBER}
                and e["val"]):
            cands.append(("equity statement preferred column", e["val"],
                          "PreferredStockValue"))
            break
    by_series = _series_sum(entries, "StockholdersEquity", end,
                            {_EQUITY_AXIS: _PREFERRED_MEMBER})
    if by_series:
        cands.append(("equity statement preferred column by series",
                      by_series, "PreferredStockValue"))
    for concept in _PREFERRED_VALUE_CONCEPTS:
        total = _series_sum(entries, concept, end)
        if total:
            cands.append((f"{concept} summed over series", total, concept))
            break
    apic = _undimensioned(entries, _PREFERRED_APIC, end)
    if apic:
        par = _undimensioned(entries, "PreferredStockValue", end) or 0.0
        cands.append(("APIC-preferred + par", apic + par, "PreferredStockValue"))
    if not cands:
        return None
    equity = _undimensioned(entries, "StockholdersEquity", end)
    basis, value, concept = cands[0]
    if value <= 0 or (equity is not None and value >= equity):
        return None
    if len(cands) >= 2:
        if any(abs(c[1] - value) > _PREFERRED_AGREE_TOL * value for c in cands):
            return None
        basis = " = ".join(c[0] for c in cands)
    else:
        shares = _preferred_count_at(entries, end)
        if shares is None or _not_a_carrying_total(concept, value, shares):
            return None
    # The per-series LIQUIDATION preference, when the instance also tags it:
    # some banks deduct that, not the carrying value, in their own per-common-
    # share figures (BAFN: $96,051K liquidation vs $90,238K carrying on a
    # $19,850K common base — the release's $4.82 vs a carrying-value $6.25).
    # analysis/valuation reads the gap to recognise the company's convention.
    liq = _series_sum(entries, "PreferredStockLiquidationPreferenceValue", end)
    if liq and "LiquidationPreference" in basis:
        liq = None                      # the value IS the liquidation figure
    return {"end": end, "value": value, "basis": basis,
            "liquidation": liq if liq and liq > value else None}


def overlay_preferred_total(cik: int, slim: dict) -> dict:
    """Return `slim` plus a top-level "_preferred_total" record (see
    resolve_preferred_total, with the source accession/form) when the blob's
    own ladder leaves the filer's preferred present but unresolved; else
    `slim` itself. A filer whose preferred resolves — or who has none —
    never pays the instance fetch. Read by sec_client._resolve_preferred_stock
    at the blob's balance-sheet date; nothing is written into the facts."""
    if not slim or not slim.get("facts"):
        return slim
    from data.sec_client import _balance_sheet_date, _resolve_preferred_stock
    from data.sec_filing_scraper import latest_filing
    value, present = _resolve_preferred_stock(slim)
    if not present or value is not None:
        return slim
    end = _balance_sheet_date(slim)
    if not end:
        return slim
    meta = latest_filing(cik, forms=("10-Q", "10-K"))
    if not meta:
        return slim
    rec = resolve_preferred_total(preferred_entries(cik, meta), end)
    if rec is None:
        return slim
    out = dict(slim)
    out["_preferred_total"] = {**rec, "accession": meta["accession"],
                               "form": meta.get("form")}
    print(f"[SEC] preferred total: CIK {cik} ${rec['value']:,.0f} at {end} "
          f"from {rec['basis']} ({meta.get('form')} {meta['accession']})",
          flush=True)
    return out


# ── MSRs bundled in the intangibles rollup, tagged where companyfacts can't see ─
# TCE convention keeps mortgage servicing rights IN tangible equity, so
# sec_client nets a same-date MSR out of the IntangibleAssetsNetExcludingGoodwill
# rollup — but only an MSR companyfacts carries (undimensioned
# ServicingAssetAtFairValueAmount). Citi tags its $788M of MSRs only as a
# company extension (c:MortgageServicingRightsMSR) and as fair-value-axis
# members, so the resolver deducted the full $5,004M rollup: TBVPS 100.42 vs
# the release's 100.89, ROATCE 9.77% vs 9.72%. The MSR is read from the
# filing's own instance — and netted ONLY when the filing also tags the
# rollup's ex-MSR remainder itself (Citi: c:IntangibleAssetsExcludingMortgage-
# ServicingRights = 4,216 = 5,004 − 788), which proves the rollup includes
# the MSRs. A bank that reports MSRs beside (not inside) its intangibles
# never ties out and is left alone.
_MSR_CKEY_V = "v1"
_RECURRING_MEMBER = "FairValueMeasurementsRecurringMember"
# The remainder must equal rollup − MSR exactly at the filer's reporting unit
# ($K or $M sums are exact). PEBO's "AndServicingRights" remainder is $8K off
# (a non-compete agreement also sits in its rollup) — no proof, no netting.
_MSR_TIE = 1_000.0


def msr_entries(cik: int, filing: dict) -> list[dict]:
    """Instant facts from `filing`'s iXBRL that can carry an MSR balance
    (local name holds "ServicingAsset" or "MortgageServicingRight", any
    namespace, undimensioned or recurring-fair-value-only) plus every
    undimensioned "Intangible" fact — the candidate ex-MSR remainders.
    [{concept, recurring, end, val}]; cached immutably by accession."""
    from data import cache
    from data.sec_filing_scraper import instance_facts
    ckey = f"msr_entries:{_MSR_CKEY_V}:{filing['accession']}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return hit.get("entries", [])
    out = []
    for f in instance_facts({"cik": int(cik), "accession": filing["accession"],
                             "doc": filing["doc"]}):
        if f.period_start or not f.period_end:
            continue
        local = f.concept.split(":")[-1]
        members = [m.split(":")[-1] for m in f.members.values()]
        is_msr = ("Intangible" not in local
                  and ("ServicingAsset" in local or "MortgageServicingRight" in local))
        if is_msr and (not members or members == [_RECURRING_MEMBER]):
            out.append({"concept": f.concept, "recurring": bool(members),
                        "end": f.period_end, "val": f.value})
        elif "Intangible" in local and not members:
            out.append({"concept": f.concept, "recurring": False,
                        "end": f.period_end, "val": f.value})
    try:
        cache.put(ckey, {"entries": out})
    except Exception:
        pass
    return out


def _local(concept: str) -> str:
    return concept.split(":")[-1]


def resolve_msr_in_rollup(entries: list[dict], end: str,
                          rollup: float) -> float | None:
    """The MSR balance inside the `rollup` (IntangibleAssetsNetExcludingGoodwill
    at `end`), or None. A candidate MSR m (0 < m < rollup) counts only when
    some OTHER undimensioned intangible fact at `end` equals rollup − m: the
    filing itself states the ex-MSR remainder. Two different candidates that
    both tie → ambiguous → None."""
    at = [e for e in entries if e["end"] == end]
    # An "Intangible…" concept is a remainder candidate even when its name
    # mentions MSRs (Citi's IntangibleAssetsExcludingMortgageServicingRights);
    # only non-intangible servicing concepts are MSR candidates.
    msrs = {e["val"] for e in at
            if "Intangible" not in _local(e["concept"])
            and ("ServicingAsset" in e["concept"]
                 or "MortgageServicingRight" in e["concept"])
            and 0 < e["val"] < rollup}
    remainders = [e["val"] for e in at
                  if "Intangible" in _local(e["concept"]) and not e["recurring"]
                  and abs(e["val"] - rollup) >= _MSR_TIE]
    tied = {m for m in msrs
            if any(abs((rollup - m) - r) < _MSR_TIE for r in remainders)}
    return tied.pop() if len(tied) == 1 else None


def overlay_msr(cik: int, slim: dict) -> dict:
    """Return `slim` plus a top-level "_msr_in_rollup" record {end, value,
    accession, form} when the latest filing proves an MSR sits inside the
    blob's IntangibleAssetsNetExcludingGoodwill rollup at the balance-sheet
    date; else `slim` itself. Only consulted when that rollup is tagged at
    the date and companyfacts holds no same-date undimensioned MSR — a filer
    whose MSR the blob already carries never pays the instance fetch. Read
    by sec_client's intangible resolvers; nothing is written into the facts."""
    if not slim or not slim.get("facts"):
        return slim
    from data.sec_client import _balance_sheet_date, _val_end
    from data.sec_filing_scraper import latest_filing
    end = _balance_sheet_date(slim)
    rollup, r_end = _val_end(slim, "IntangibleAssetsNetExcludingGoodwill")
    if not end or not rollup or r_end != end:
        return slim
    _, msr_end = _val_end(slim, "ServicingAssetAtFairValueAmount")
    if msr_end == end:
        return slim
    meta = latest_filing(cik, forms=("10-Q", "10-K"))
    if not meta:
        return slim
    msr = resolve_msr_in_rollup(msr_entries(cik, meta), end, rollup)
    if msr is None:
        return slim
    out = dict(slim)
    out["_msr_in_rollup"] = {"end": end, "value": msr,
                             "accession": meta["accession"],
                             "form": meta.get("form")}
    print(f"[SEC] MSR in rollup: CIK {cik} ${msr:,.0f} of ${rollup:,.0f} "
          f"intangibles at {end} ({meta.get('form')} {meta['accession']})",
          flush=True)
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
