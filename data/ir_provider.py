"""IR earnings-release provider (DATA-SOURCING-ARCHITECTURE increment 5).

The freshest disclosure layer: a bank's quarterly earnings press release, filed
as Exhibit 99.1 to an 8-K under **Item 2.02** (Results of Operations), typically
~2-3 weeks BEFORE the 10-Q/10-K. Later increments parse its headline financials
into `SourceRecord`s so `data.source_resolver` can prefer it over the not-yet-
filed periodic report.

Earnings releases carry NO inline XBRL — that's precisely why they're earlier and
looser — so any value parsed from one must be defensively validated and reconciled
before it is ever surfaced (cardinal rule: never a plausible-wrong number). This
module is increment 5a: it only LOCATES and FETCHES the exhibit. Parsing the
release's financial tables lands in a separate, independently-verified increment.

Split for testability: the selection logic (`_latest_earnings_8k`, `_pick_ex99`)
is pure and unit-tested against fixtures; `latest_earnings_release` is the thin
network wrapper that feeds it live EDGAR JSON.
"""
from __future__ import annotations

import json
import re

from data.sec_filing_scraper import _get  # shared SEC fetch (one UA + urllib path)

# 8-K Item 2.02 — "Results of Operations and Financial Condition" — is the item a
# bank files its quarterly earnings press release under.
_EARNINGS_ITEM = "2.02"

_INDEX_DOC = re.compile(r'href="[^"]*?/([^"/]+\.(?:htm|html|txt))"', re.I)
_EX99_TYPE = re.compile(r"EX-99[.0-9]*", re.I)


def _dash_accession(acc18: str) -> str:
    """18-digit accession (no dashes) -> EDGAR dashed form, e.g.
    '000162828026024990' -> '0001628280-26-024990' (used in the -index.htm name)."""
    return f"{acc18[:10]}-{acc18[10:12]}-{acc18[12:18]}" if len(acc18) >= 18 else acc18


def _parse_index_html(html: str) -> list[dict]:
    """Pure: extract [{name, type}] for documents from a filing's -index.htm
    document table. EDGAR's index.json carries no usable document type (only
    image types), so the -index.htm table is the authoritative source of which
    file is EX-99.1 vs EX-99.2. Returns only rows that carry an EX-99 type."""
    out = []
    for row in re.split(r"<tr[ >]", html, flags=re.I):
        link = _INDEX_DOC.search(row)
        typ = _EX99_TYPE.search(row)
        if link and typ:
            out.append({"name": link.group(1), "type": typ.group(0).upper()})
    return out


def _latest_earnings_8k(submissions: dict) -> dict | None:
    """Pure: the most recent 8-K carrying Item 2.02, from an EDGAR submissions
    JSON. `recent` is newest-first, so the first match is the latest. Returns
    {accession (no dashes), filed_date, primary, form} or None."""
    rec = submissions.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    items = rec.get("items", [])
    dates = rec.get("filingDate", [])
    accs = rec.get("accessionNumber", [])
    primaries = rec.get("primaryDocument", [])
    for i, form in enumerate(forms):
        if not form.startswith("8-K"):
            continue
        item_str = items[i] if i < len(items) else ""
        present = {s.strip() for s in item_str.replace(";", ",").split(",") if s.strip()}
        if _EARNINGS_ITEM not in present:
            continue
        return {
            "accession": (accs[i] if i < len(accs) else "").replace("-", ""),
            "filed_date": dates[i] if i < len(dates) else "",
            "primary": primaries[i] if i < len(primaries) else "",
            "form": form,
        }
    return None


def _pick_ex99(items: list[dict]) -> str | None:
    """Pure: choose the earnings-release exhibit's document name from a filing
    index's `directory.item` list. Prefer EX-99.1 (the press release); else the
    lowest-numbered EX-99.x; else a filename heuristic for indexes that omit the
    type. Returns the document name, or None when no EX-99 is present."""
    typed = []
    for it in items:
        typ = (it.get("type") or "").upper().replace(" ", "")
        name = it.get("name") or ""
        if name and (typ.startswith("EX-99") or typ.startswith("EX99")):
            typed.append((typ, name))
    if typed:
        for typ, name in sorted(typed):
            if typ in ("EX-99.1", "EX99.1"):
                return name
        return sorted(typed)[0][1]  # lowest EX-99.x when there is no .1
    # Fallback: the index didn't carry exhibit types — match on filename.
    for it in items:
        name = (it.get("name") or "")
        low = name.lower()
        if "ex99" in low or "ex-99" in low or "exhibit99" in low:
            return name
    return None


def latest_earnings_release(cik) -> dict | None:
    """I/O: locate + fetch the latest 8-K Item 2.02 EX-99.1 earnings release for a
    CIK. Returns {url, html, filed_date, accession, form} or None. Any network or
    structure failure returns None — the resolver simply falls through to the SEC
    filing (this layer can only ever ADD a fresher source, never break one)."""
    try:
        cik10 = str(int(cik)).zfill(10)
        subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    except Exception:
        return None
    hit = _latest_earnings_8k(subs)
    if not hit or not hit["accession"]:
        return None
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{hit['accession']}"
    # Primary: the -index.htm document table is the only reliable source of which
    # file is EX-99.1 (press release) vs EX-99.2 (data supplement) — index.json
    # carries no document type. Fall back to index.json filenames only if that
    # table can't be read.
    doc = None
    try:
        idx_html = _get(f"{base}/{_dash_accession(hit['accession'])}-index.htm").decode(
            "utf-8", "replace")
        doc = _pick_ex99(_parse_index_html(idx_html))
    except Exception:
        doc = None
    if not doc:
        try:
            items = json.loads(_get(f"{base}/index.json")).get(
                "directory", {}).get("item", [])
            doc = _pick_ex99(items)
        except Exception:
            doc = None
    if not doc:
        return None
    try:
        html = _get(f"{base}/{doc}").decode("utf-8", "replace")
    except Exception:
        return None
    return {"url": f"{base}/{doc}", "html": html, "filed_date": hit["filed_date"],
            "accession": hit["accession"], "form": hit["form"]}
