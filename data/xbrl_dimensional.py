"""
Dimensional XBRL facts from a single filing instance.

The flat companyfacts API collapses every dimensional fact (axis/member
breakdowns) to nothing: criticized/classified loan grades, ASC-825
fair-value tables and as-reported loan composition live ONLY in the
filing's XBRL instance document (docs/SNL-BUILD-PLAN.md — AQ Detail, FV,
Loan Comp As Rptd). This module downloads and parses that instance.

Instance location (the rendered R-files are not stable; this is):
  1. GET https://www.sec.gov/Archives/edgar/data/{cik}/{acc-nodash}/index.json
  2. Prefer the EDGAR-extracted iXBRL instance ``*_htm.xml`` (every
     inline-XBRL filing since ~2019 has exactly one).
  3. Else the classic instance ``{ticker}-{yyyymmdd}.xml`` — excluding
     linkbases (_cal/_def/_lab/_pre/_ref) and FilingSummary.xml.

Parse: xml.etree iterparse (instances run 5-25 MB; memory-light, one pass).
Facts are collected with contextRef/unitRef and resolved AFTER the pass, so
context ordering in the document doesn't matter. Output shape:

    {concept: [{"value", "unit", "period_start", "period_end",
                "dimensions": {axis_qname: member_qname}}]}

Only NUMERIC facts are kept (text blocks are huge and useless here). Raw
axis/member QNames are preserved everywhere — grade normalisation in
extract_credit_quality keeps the raw member names alongside, never instead.

Cache: instances are immutable, so parsed results live in data.cache under
``xbrl_dim:{cik}:{acc-nodash}`` with a 30-day ``cached_at`` stamp via the
shared freshness check. (The backend's own global TTL — 24h — expires
entries first locally; worst case is a daily re-parse, and the 30d stamp
stays correct if the backend TTL is ever raised. Same caveat as
census_client.)
"""

from __future__ import annotations

import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

from config import SEC_USER_AGENT

HEADERS = {"User-Agent": SEC_USER_AGENT}
INDEX_JSON_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
ARCHIVE_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

CACHE_TTL_SECONDS = 30 * 86400  # parsed instances are immutable

_XBRLI_NS = "http://www.xbrl.org/2003/instance"
_XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
_CONTEXT_TAG = f"{{{_XBRLI_NS}}}context"
_UNIT_TAG = f"{{{_XBRLI_NS}}}unit"

# ── SEC rate limit: one request per 0.12s, process-wide ─────────────────
_throttle_lock = threading.Lock()
_last_request = [0.0]


def _throttled_get(url: str, timeout: int = 30):
    """Shared-retry GET, throttled to stay under SEC's 10 req/s limit."""
    from data.http import get_with_retry
    with _throttle_lock:
        wait = 0.12 - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
    return get_with_retry(url, headers=HEADERS, timeout=timeout)


# ──────────────────────────────────────────────────────────────────────────
# Instance location
# ──────────────────────────────────────────────────────────────────────────

_LINKBASE_SUFFIXES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_ref.xml")


def _locate_instance(cik: int, acc_nodash: str) -> str | None:
    """URL of the filing's XBRL instance document, or None.

    Prefers the EDGAR-extracted iXBRL instance (*_htm.xml); falls back to a
    classic dated instance ({ticker}-{yyyymmdd}.xml), then any non-linkbase
    .xml. Shortest name wins ties (exhibit instances have longer names).
    """
    url = INDEX_JSON_URL.format(cik=int(cik), acc=acc_nodash)
    try:
        resp = _throttled_get(url, timeout=15)
        if resp is None:
            return None
        items = (resp.json().get("directory") or {}).get("item") or []
    except Exception as e:
        print(f"[xbrl-dim] index.json fetch failed for {acc_nodash}: "
              f"{type(e).__name__}: {e}")
        return None

    names = [it.get("name", "") for it in items if it.get("name")]

    def _pick(cands: list[str]) -> str:
        return sorted(cands, key=lambda n: (len(n), n))[0]

    htm_xml = [n for n in names if n.lower().endswith("_htm.xml")]
    if htm_xml:
        name = _pick(htm_xml)
    else:
        xmls = [
            n for n in names
            if n.lower().endswith(".xml")
            and not n.lower().endswith(_LINKBASE_SUFFIXES)
            and n.lower() != "filingsummary.xml"
        ]
        dated = [n for n in xmls if re.search(r"-\d{8}\.xml$", n)]
        cands = dated or xmls
        if not cands:
            return None
        name = _pick(cands)
    return ARCHIVE_FILE_URL.format(cik=int(cik), acc=acc_nodash, name=name)


# ──────────────────────────────────────────────────────────────────────────
# Instance parsing
# ──────────────────────────────────────────────────────────────────────────

def _parse_context(elem) -> dict:
    """One xbrli:context → {instant, start, end, dims:{axis_qname: member_qname}}."""
    instant = start = end = None
    period = elem.find(f"{{{_XBRLI_NS}}}period")
    if period is not None:
        for tag, slot in (("instant", "i"), ("startDate", "s"), ("endDate", "e")):
            node = period.find(f"{{{_XBRLI_NS}}}{tag}")
            if node is not None and node.text:
                if slot == "i":
                    instant = node.text.strip()
                elif slot == "s":
                    start = node.text.strip()
                else:
                    end = node.text.strip()
    # explicitMember lives in entity/segment or context/scenario — iter() covers both
    dims = {}
    for m in elem.iter(f"{{{_XBRLDI_NS}}}explicitMember"):
        axis = m.get("dimension")
        if axis and m.text:
            dims[axis] = m.text.strip()
    # typedMember too: if these were invisible, a fact carrying a typed axis
    # would look "grade-only" downstream and be mistaken for a total row.
    # The value is an arbitrary child element, not a QName — record a stable
    # "typed:{local}={text}" marker so the axis shows up as an unknown slice.
    for m in elem.iter(f"{{{_XBRLDI_NS}}}typedMember"):
        axis = m.get("dimension")
        if not axis:
            continue
        child = next(iter(m), None)
        if child is not None and isinstance(child.tag, str):
            local = child.tag.rsplit("}", 1)[-1]
            dims[axis] = f"typed:{local}={(child.text or '').strip()}"
        else:
            dims[axis] = "typed:"
    return {"instant": instant, "start": start, "end": end, "dims": dims}


def _parse_unit(elem) -> str | None:
    """One xbrli:unit → 'USD', 'shares', 'USD/shares', ... (measure local parts)."""
    measures = [
        (m.text or "").strip().split(":")[-1]
        for m in elem.iter(f"{{{_XBRLI_NS}}}measure")
        if m.text
    ]
    return "/".join(measures) if measures else None


def parse_instance(xml_bytes: bytes) -> dict | None:
    """
    Parse a raw XBRL instance document into

        {concept: [{"value", "unit", "period_start", "period_end",
                    "dimensions": {axis_qname: member_qname}}]}

    Numeric facts only; concept and dimension names keep the document's own
    QName prefixes (us-gaap:, dei:, company custom). Returns None on
    malformed XML or when no numeric facts were found — never raises.
    """
    contexts: dict[str, dict] = {}
    units: dict[str, str | None] = {}
    raw_facts: list[tuple] = []
    ns_prefixes: dict[str, str] = {}  # uri -> document prefix

    try:
        for event, payload in ET.iterparse(
            io.BytesIO(xml_bytes), events=("start-ns", "end")
        ):
            if event == "start-ns":
                prefix, uri = payload
                if prefix and uri not in ns_prefixes:
                    ns_prefixes[uri] = prefix
                continue
            elem = payload
            tag = elem.tag
            if not isinstance(tag, str):  # comments / processing instructions
                continue
            if tag == _CONTEXT_TAG:
                ctx_id = elem.get("id")
                if ctx_id:
                    contexts[ctx_id] = _parse_context(elem)
                elem.clear()
            elif tag == _UNIT_TAG:
                unit_id = elem.get("id")
                if unit_id:
                    units[unit_id] = _parse_unit(elem)
                elem.clear()
            elif elem.get("contextRef") is not None:
                # A fact. Keep numeric values only (text blocks are huge).
                try:
                    val = float((elem.text or "").strip())
                except ValueError:
                    elem.clear()
                    continue
                raw_facts.append((tag, elem.get("contextRef"),
                                  elem.get("unitRef"), val))
                elem.clear()
    except ET.ParseError as e:
        print(f"[xbrl-dim] malformed instance: {e}")
        return None

    def _qname(tag: str) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            prefix = ns_prefixes.get(uri)
            return f"{prefix}:{local}" if prefix else local
        return tag

    facts: dict[str, list] = {}
    for tag, ctx_ref, unit_ref, val in raw_facts:
        ctx = contexts.get(ctx_ref)
        if ctx is None:
            continue  # fact pointing at a missing context — drop, never guess
        facts.setdefault(_qname(tag), []).append({
            "value": val,
            "unit": units.get(unit_ref),
            "period_start": ctx["start"],
            "period_end": ctx["instant"] or ctx["end"],
            "dimensions": ctx["dims"],
        })
    return facts or None


# ──────────────────────────────────────────────────────────────────────────
# Fetch + cache
# ──────────────────────────────────────────────────────────────────────────

def fetch_dimensional_facts(cik: int, accession: str) -> dict | None:
    """
    All numeric facts (with dimensions) from one filing's XBRL instance.

    Returns {"cached_at", "instance_url", "accession", "facts": {...}} or
    None on any failure. Cached 30 days under ``xbrl_dim:{cik}:{acc-nodash}``
    — instances are immutable, the TTL only bounds local disk growth.
    """
    from data import cache
    from data.freshness import is_fresh

    acc = accession.replace("-", "")
    key = f"xbrl_dim:{int(cik)}:{acc}"
    cached = cache.get(key)
    if is_fresh(cached, CACHE_TTL_SECONDS):
        return cached

    instance_url = _locate_instance(cik, acc)
    if not instance_url:
        print(f"[xbrl-dim] no instance document found for CIK {cik} acc {accession}")
        return None
    try:
        resp = _throttled_get(instance_url, timeout=60)
    except Exception as e:
        print(f"[xbrl-dim] instance fetch failed {instance_url}: "
              f"{type(e).__name__}: {e}")
        return None
    if resp is None:
        return None

    facts = parse_instance(resp.content)
    if facts is None:
        print(f"[xbrl-dim] could not parse instance {instance_url}")
        return None

    payload = {
        "cached_at": datetime.now().isoformat(),
        "instance_url": instance_url,
        "accession": accession,
        "facts": facts,
    }
    try:
        cache.put(key, payload)
    except Exception as e:
        print(f"[xbrl-dim] cache put failed for {key}: {e}")
    return payload


def _list_filings(cik: int, forms: tuple[str, ...], limit: int) -> list[dict]:
    """Newest `limit` filings among `forms` from the submissions API (recent
    list is reverse-chronological). Each: {form, accession, filed, report_date}."""
    url = SUBMISSIONS_URL.format(cik=str(int(cik)).zfill(10))
    try:
        resp = _throttled_get(url, timeout=15)
        if resp is None:
            return []
        recent = (resp.json().get("filings") or {}).get("recent") or {}
    except Exception as e:
        print(f"[xbrl-dim] submissions fetch failed for CIK {cik}: "
              f"{type(e).__name__}: {e}")
        return []

    form_list = recent.get("form", [])

    def _safe(field, i, default=""):
        lst = recent.get(field, [])
        return lst[i] if i < len(lst) else default

    out = []
    for i, form in enumerate(form_list):
        if form in forms:
            out.append({
                "form": form,
                "accession": _safe("accessionNumber", i),
                "filed": _safe("filingDate", i),
                "report_date": _safe("reportDate", i),
            })
            if len(out) >= limit:
                break
    return out


def _latest_filing(cik: int, forms: tuple[str, ...]) -> dict | None:
    """Newest filing among `forms`. Returns {form, accession, filed, report_date}."""
    filings = _list_filings(cik, forms, 1)
    return filings[0] if filings else None


# ──────────────────────────────────────────────────────────────────────────
# Credit-quality extraction (FinancingReceivable × InternalCreditAssessment)
# ──────────────────────────────────────────────────────────────────────────

# Axis local-name substrings (case-insensitive)
_CREDIT_AXIS_HINTS = ("internalcreditassessment", "creditquality")
_CLASS_AXIS_HINTS = ("classoffinancingreceivable",)
_SEGMENT_AXIS_HINTS = ("portfoliosegment",)

# Concept local-name substrings to EXCLUDE: vintage-table per-origination-year
# columns and gross charge-offs would double count the "total" column; ratios
# aren't dollars.
_EXCLUDED_CONCEPT_HINTS = (
    "originated", "revolving", "convertedtoterm",
    "writeoff", "chargeoff", "recover", "percent",
)

# Normalised grade key ← member local-name substring (checked in order; raw
# member names are always kept in the output for honesty).
_GRADE_PATTERNS = (
    ("special_mention", "specialmention"),
    ("substandard", "substandard"),
    ("doubtful", "doubtful"),
    ("loss", "unlikelytobecollected"),  # taxonomy member for the Loss grade
    ("pass", "pass"),
)


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1].lower()


def _grade_key(member_qname: str) -> str:
    m = _local(member_qname)
    for key, pat in _GRADE_PATTERNS:
        if pat in m:
            return key
    if m in ("lossmember", "lossgrademember"):
        return "loss"
    return member_qname  # unrecognised grade — keep raw, never force a bucket


def extract_credit_quality(facts: dict) -> dict | None:
    """
    Pure extraction (testable offline): from a parse_instance() facts dict,
    pull amortized cost by internal credit grade at the latest instant.

    Strategy:
      • candidate facts: USD, concept local-name mentions receivable/loan,
        NOT a vintage per-origination-year/charge-off concept, and carry an
        internal-credit-assessment-like axis;
      • as_of = latest period_end among candidates; one dominant concept
        (most graded facts at as_of, ties prefer "...BeforeAllowance...");
      • totals by grade come from grade-axis-only facts when the filer
        tagged a total row ("direct"), else summed across class members
        ("summed_across_classes");
      • facts carrying any axis beyond grade/class/segment are skipped —
        an unknown slice could double count, and we never guess.
    """
    candidates = []  # (concept, entry, grade_axis)
    for concept, entries in facts.items():
        cl = _local(concept)
        if "receivable" not in cl and "loan" not in cl:
            continue
        if any(h in cl for h in _EXCLUDED_CONCEPT_HINTS):
            continue
        for e in entries:
            if e.get("unit") != "USD":
                continue
            dims = e.get("dimensions") or {}
            grade_axis = next(
                (a for a in dims
                 if any(h in _local(a) for h in _CREDIT_AXIS_HINTS)), None)
            if grade_axis:
                candidates.append((concept, e, grade_axis))
    if not candidates:
        return None

    as_of = max((e["period_end"] or "") for _, e, _ in candidates)
    current = [(c, e, ga) for c, e, ga in candidates if e["period_end"] == as_of]
    if not current:
        return None  # no candidate carries a period end — nothing to date
    counts = Counter(c for c, _, _ in current)
    concept = max(counts, key=lambda c: (counts[c], "beforeallowance" in _local(c)))

    direct: dict[str, float] = {}        # grade_raw -> value (grade-only facts)
    class_facts: list[tuple] = []        # (class_raw, grade_raw, value), class axis
    segment_facts: list[tuple] = []      # same but portfolio-segment axis only
    seen: set[tuple] = set()
    for c, e, ga in current:
        if c != concept:
            continue
        dims = e["dimensions"]
        grade_raw = dims[ga]
        class_axis = next(
            (a for a in dims if any(h in _local(a) for h in _CLASS_AXIS_HINTS)), None)
        seg_axis = next(
            (a for a in dims if any(h in _local(a) for h in _SEGMENT_AXIS_HINTS)), None)
        if any(a not in (ga, class_axis, seg_axis) for a in dims):
            continue  # extra axis (geography, vintage, ...) — skip, never guess
        class_raw = (dims[class_axis] if class_axis
                     else dims[seg_axis] if seg_axis else None)
        key = (grade_raw, class_raw)
        if key in seen:
            continue  # duplicate fact (same cell tagged twice)
        seen.add(key)
        if class_raw is None:
            direct[grade_raw] = e["value"]
        elif class_axis:
            class_facts.append((class_raw, grade_raw, e["value"]))
        else:
            segment_facts.append((class_raw, grade_raw, e["value"]))

    # by_class: prefer class-axis detail; segment-only subtotals are used
    # ONLY when no class detail exists (using both would double count).
    detail = class_facts or segment_facts
    by_class: dict[str, dict[str, float]] = {}
    for class_raw, grade_raw, val in detail:
        g = _grade_key(grade_raw)
        row = by_class.setdefault(class_raw, {})
        row[g] = row.get(g, 0.0) + val

    grade_members: dict[str, list[str]] = {}
    total_by_grade: dict[str, float] = {}
    if direct:
        totals_source = "direct"
        pairs = direct.items()
    else:
        totals_source = "summed_across_classes"
        agg: dict[str, float] = {}
        for _, grade_raw, val in detail:
            agg[grade_raw] = agg.get(grade_raw, 0.0) + val
        pairs = agg.items()
    for grade_raw, val in pairs:
        g = _grade_key(grade_raw)
        total_by_grade[g] = total_by_grade.get(g, 0.0) + val
        if grade_raw not in grade_members.setdefault(g, []):
            grade_members[g].append(grade_raw)

    if not total_by_grade:
        return None

    # SNL conventions: classified = substandard + doubtful + loss;
    # criticized = special mention + classified. Only computed when at least
    # one classified-bucket grade was actually tagged — otherwise None (n/a).
    classified = criticized = None
    if any(g in total_by_grade for g in ("substandard", "doubtful", "loss")):
        classified = sum(total_by_grade.get(g, 0.0)
                         for g in ("substandard", "doubtful", "loss"))
        criticized = classified + total_by_grade.get("special_mention", 0.0)

    return {
        "as_of": as_of,
        "concept": concept,
        "total_by_grade": total_by_grade,
        "grade_members": grade_members,
        "by_class": by_class,
        "totals_source": totals_source,
        "classified": classified,
        "criticized": criticized,
    }


def get_credit_quality_breakdown(cik: int, form: str | None = None) -> dict | None:
    """
    Credit-quality (pass / special mention / substandard / doubtful) loan
    breakdown from the latest 10-K/10-Q XBRL instance.

    form: restrict to one form type (e.g. "10-K"); default newest of either.

    Returns
        {as_of, concept, total_by_grade, grade_members, by_class,
         totals_source, classified, criticized,
         source: {form, accession, filed, report_date, url}}
    or None when the filer didn't tag the disclosure dimensionally.
    """
    forms = (form,) if form else ("10-K", "10-Q")
    filing = _latest_filing(cik, forms)
    if not filing or not filing.get("accession"):
        return None
    bundle = fetch_dimensional_facts(cik, filing["accession"])
    if not bundle:
        return None
    breakdown = extract_credit_quality(bundle.get("facts") or {})
    if breakdown is None:
        print(f"[xbrl-dim] no credit-quality dimensional facts in "
              f"{filing['form']} {filing['accession']} (CIK {cik})")
        return None
    breakdown["source"] = {
        "form": filing["form"],
        "accession": filing["accession"],
        "filed": filing["filed"],
        "report_date": filing["report_date"],
        "url": bundle.get("instance_url"),
    }
    return breakdown


# How many filings the two history modes walk: 5 10-Ks ≈ 5 FY-end instants;
# 9 10-K/10-Q filings ≈ the last 8 quarter-ends plus overlap.
_HISTORY_ANNUAL_FILINGS = 5
_HISTORY_QUARTERLY_FILINGS = 9


def credit_quality_history(cik: int, quarterly: bool = False) -> dict:
    """
    {period_end "YYYY-MM-DD" → credit-quality breakdown} across recent filings
    (each filing contributes its single latest instant — extract_credit_quality
    keeps only as_of; the comparative column is dropped there, so history NEEDS
    one filing per period). Annual: the last 5 10-Ks. Quarterly: the last 9
    10-K/10-Q filings. Newest filing wins a shared period end.

    A filing whose XBRL lacks the grade disclosure simply contributes nothing
    (its period renders n/a downstream — never a guess). The merged dict is
    cached 30 days keyed by the newest accession (a new filing re-merges), but
    ONLY when every instance fetch succeeded — a transient SEC failure must
    not bake a 30-day gap into the cache (fetch=None is retryable; a parsed
    filing with no tagged grades is a real, cacheable absence).
    """
    from data import cache
    from data.freshness import is_fresh

    n = _HISTORY_QUARTERLY_FILINGS if quarterly else _HISTORY_ANNUAL_FILINGS
    forms = ("10-K", "10-Q") if quarterly else ("10-K",)
    filings = [f for f in _list_filings(cik, forms, n) if f.get("accession")]
    if not filings:
        return {}

    key = (f"crit_hist:v1:{int(cik)}:"
           f"{filings[0]['accession'].replace('-', '')}:{'q' if quarterly else 'a'}")
    cached = cache.get(key)
    if is_fresh(cached, CACHE_TTL_SECONDS):
        return cached.get("by_period", {})

    by_period: dict[str, dict] = {}
    complete = True
    for filing in reversed(filings):        # oldest → newest: newest wins a tie
        bundle = fetch_dimensional_facts(cik, filing["accession"])
        if not bundle:
            complete = False                # fetch/parse failure — retryable
            continue
        breakdown = extract_credit_quality(bundle.get("facts") or {})
        if breakdown is None:
            continue                        # not tagged in this filing — real absence
        breakdown["source"] = {
            "form": filing["form"],
            "accession": filing["accession"],
            "filed": filing["filed"],
            "report_date": filing["report_date"],
            "url": bundle.get("instance_url"),
        }
        by_period[breakdown["as_of"]] = breakdown

    if complete:
        try:
            cache.put(key, {"cached_at": datetime.now().isoformat(),
                            "by_period": by_period})
        except Exception as e:
            print(f"[xbrl-dim] cache put failed for {key}: {e}")
    return by_period
