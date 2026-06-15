"""Scrape SEC filing documents (10-K/10-Q) and parse their inline XBRL.

The flat SEC companyfacts API DROPS XBRL dimensions, so dimensional disclosures
— regulatory capital (consolidated vs bank), credit-quality grades, fair-value
levels — are unavailable or ambiguous there. The filing HTML carries inline
XBRL (iXBRL) with full dimensional context, so we parse the document directly.
See docs/DATA-SOURCING-ARCHITECTURE.md and the flexible-sourcing memory.

This module is the SEC provider's core: locate the latest filing, fetch it, and
turn its iXBRL into a list of Facts (concept, scaled+signed value, period,
dimensional members). Higher layers map concepts/members to display lines.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

_UA = {"User-Agent": "KSK Investors research chris@kskinvestors.com"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def latest_filing(cik, forms=("10-K",)) -> dict | None:
    """Most recent filing among `forms`: {accession, doc, date, form} or None."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    for i, f in enumerate(rec.get("form", [])):
        if f in forms:
            return {"accession": rec["accessionNumber"][i].replace("-", ""),
                    "doc": rec["primaryDocument"][i],
                    "date": rec["filingDate"][i], "form": f, "cik": int(cik)}
    return None


def filing_url(cik, accession, doc) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"


@dataclass
class Fact:
    concept: str            # e.g. "us-gaap:TierOneRiskBasedCapital"
    value: float            # scaled + sign-applied, in base units (dollars, %, …)
    period_end: str         # ISO date (instant or duration end)
    period_start: str | None
    members: dict           # {dimension_qname: member_qname}
    unit: str | None


def _local(tag) -> str:
    """Local name of an lxml HTML tag like 'ix:nonfraction' -> 'nonfraction'."""
    return tag.split(":")[-1].lower() if isinstance(tag, str) else ""


def parse_inline_xbrl(html_bytes: bytes) -> list[Fact]:
    """Parse iXBRL numeric facts out of a filing document.

    Inline XBRL embeds facts as <ix:nonFraction name=… contextRef=… scale=…
    sign=…> elements and their dimensional context as <xbrli:context> blocks.
    lxml's HTML parser flattens namespaces into the tag string (e.g.
    'ix:nonfraction'), so we match on the local name. A negative value can be
    signalled by sign="-" OR by accounting parentheses in the text.
    """
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)

    # 1. contexts: id -> (start, end, {dimension: member})
    contexts: dict[str, tuple] = {}
    for el in root.iter():
        if _local(el.tag) != "context":
            continue
        cid = el.get("id")
        if not cid:
            continue
        start = end = None
        members: dict[str, str] = {}
        for sub in el.iter():
            ln = _local(sub.tag)
            if ln == "instant" or ln == "enddate":
                end = (sub.text or "").strip()
            elif ln == "startdate":
                start = (sub.text or "").strip()
            elif ln == "explicitmember":
                dim = sub.get("dimension")
                if dim:
                    members[dim] = (sub.text or "").strip()
        contexts[cid] = (start, end, members)

    # 2. numeric facts
    facts: list[Fact] = []
    for el in root.iter():
        if _local(el.tag) != "nonfraction":
            continue
        name = el.get("name")
        cref = el.get("contextref")
        if not name or cref not in contexts:
            continue
        raw = (el.text_content() or "").strip()
        if not raw:
            continue
        neg = raw.startswith("(") or el.get("sign") == "-"
        raw = raw.strip("()").replace(",", "").replace(" ", "").strip()
        if not re.match(r"^-?\d", raw):
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        scale = el.get("scale")
        if scale:
            try:
                val *= 10 ** int(scale)
            except ValueError:
                pass
        if neg:
            val = -abs(val)
        start, end, members = contexts[cref]
        facts.append(Fact(name, val, end, start, members, el.get("unitref")))
    return facts


def fetch_facts(cik, forms=("10-K",)) -> tuple[dict | None, list[Fact]]:
    """Locate the latest filing among `forms`, fetch it, and parse its iXBRL.
    Returns (filing_meta, facts); ([], []) shape on failure is avoided —
    filing_meta is None when nothing is found."""
    meta = latest_filing(cik, forms)
    if not meta:
        return None, []
    html = _get(filing_url(meta["cik"], meta["accession"], meta["doc"]))
    return meta, parse_inline_xbrl(html)
