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

import html as _h
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


# ── Capital-ratio extraction (increment 5b) ───────────────────────────────
# Earnings releases carry no inline XBRL, so values come from text — but the
# release states its CAPITAL RATIOS in two independent places: a multi-quarter
# table (newest column first) AND a prose restatement ("CET1 capital ratio of
# 9.96%"). We extract the current-period (leftmost) value from the labelled table
# row and CONFIRM it against the prose figure. CET1 is the anchor: if the table's
# leftmost CET1 matches the narrated CET1, the table is correctly aligned (right
# column, right rows) and the sibling ratios from that same column are trusted;
# if CET1 can't be confirmed, the whole set returns n/a. This in-document
# cross-check needs no external ground truth and makes any column/row mis-pick
# fail SAFE to n/a rather than surface a plausible-wrong number (cardinal rule).

_RATIO_BANDS = {
    "cet1_ratio": (4.0, 30.0),
    "t1_ratio": (5.0, 32.0),
    "total_ratio": (7.0, 35.0),
    "lev_ratio": (3.0, 20.0),
}

# Table-row label anchors (matched at the START of a row's text).
_ROW_LABELS = {
    "cet1_ratio": r"(?:common equity tier 1|cet1)",
    "t1_ratio": r"tier 1 (?:risk-based )?capital",
    "total_ratio": r"total (?:risk-based )?capital",
    "lev_ratio": r"(?:tier 1 )?leverage",
}
_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
# Prose restatement: "<CET1 label> ... ratio of/was/at 9.96%".
_CET1_PROSE = re.compile(
    r"(?:common equity tier 1|cet1)[^%]{0,40}?(?:ratio\s+)?(?:of|was|at|:)\s*"
    r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I)


def _rows_text(html: str) -> list[str]:
    """HTML -> one collapsed-text line per table row (so a row's label and its
    cells stay together, columns left-to-right)."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?is)</tr\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return [re.sub(r"\s+", " ", _h.unescape(ln)).strip() for ln in t.split("\n")]


def _row_current_pct(rows: list[str], label_re: str) -> float | None:
    """First percent value (the current/leftmost column) in the first row whose
    text STARTS with the label and carries a `%`. None if no such row."""
    pat = re.compile(r"^\s*" + label_re, re.I)
    for ln in rows:
        if "%" in ln and pat.match(ln):
            m = _PCT.search(ln)
            if m:
                return float(m.group(1))
    return None


def extract_capital_ratios(html: str) -> dict:
    """Current-period capital ratios from an earnings release, or None each.

    Strategy (cardinal-rule safe): pull the leftmost % from each labelled ratio
    row, then require the table's CET1 to MATCH the prose-restated CET1 (the
    headline figure every release narrates). Agreement proves the table is
    aligned; the sibling ratios from the same column are then trusted, subject to
    band + CET1<=T1<=Total ordering checks. If CET1 can't be confirmed, return
    all-None — never a guessed value.
    """
    none = {k: None for k in _RATIO_BANDS}
    rows = _rows_text(html)
    table = {}
    for key, lab in _ROW_LABELS.items():
        v = _row_current_pct(rows, lab)
        lo, hi = _RATIO_BANDS[key]
        table[key] = v if (v is not None and lo <= v <= hi) else None

    text = re.sub(r"\s+", " ", _h.unescape(re.sub(r"(?s)<[^>]+>", " ", html)))
    pm = _CET1_PROSE.search(text)
    prose_cet1 = float(pm.group(1)) if pm else None

    # CET1 anchor: table and prose must agree (to 1bp) or we trust nothing.
    if table["cet1_ratio"] is None or prose_cet1 is None:
        return none
    if abs(table["cet1_ratio"] - prose_cet1) > 0.011:
        return none
    out = dict(table)
    # Ordering guard on the confirmed column: CET1 <= Tier 1 <= Total.
    c, t1, tot = out["cet1_ratio"], out["t1_ratio"], out["total_ratio"]
    if t1 is not None and c > t1 + 0.011:
        return none  # misaligned row pick — refuse the whole set
    if t1 is not None and tot is not None and t1 > tot + 0.011:
        return none
    return out


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
