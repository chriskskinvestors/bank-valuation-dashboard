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
# Earnings releases carry no inline XBRL, so values come from text — and large
# (advanced-approaches) banks report EACH ratio twice, under the Standardized and
# Advanced methodologies (they differ ~10bp). The binding, headline-reported ones
# (what the filing / FDIC / SNL show) are the STANDARDIZED ratios, and those are
# exactly what the release NARRATES ("Tier 1 capital ratio was 16.9%"). So we lead
# with the PROSE value for every ratio — that resolves the approach ambiguity to
# Standardized — and CORROBORATE it against a matching cell in the structured
# table. A ratio is surfaced only when prose and a table cell AGREE (to ~5bp):
# double-confirmed in the bank's own document, methodology-consistent with the
# filing it will supersede. Anything not corroborated returns n/a — never a guess
# (cardinal rule). This makes a row/column/approach mis-pick fail SAFE.

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
# Prose restatement: "<label> … ratio … of/was/at/to/ended at 16.9%". The verb set
# is generous; the trailing % and the band check keep it precise.
_VERB = r"(?:ratio\s+)?(?:of|was|were|at|to|:|ended (?:the quarter )?at|" \
        r"(?:in|de)creased to)\s*"
_PROSE = {
    "cet1_ratio": re.compile(r"(?:common equity tier 1|cet1)[^%]{0,45}?" + _VERB
                             + r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I),
    "t1_ratio": re.compile(r"tier 1 (?:risk-based )?capital ratio[^%]{0,30}?" + _VERB
                           + r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I),
    "total_ratio": re.compile(r"total (?:risk-based )?capital ratio[^%]{0,30}?" + _VERB
                              + r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I),
    "lev_ratio": re.compile(r"tier 1 leverage[^%]{0,45}?" + _VERB
                            + r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I),
}
_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
_CORROBORATE_TOL = 0.051  # prose vs table cell, allowing 1-decimal rounding


def _rows_text(html: str) -> list[str]:
    """HTML -> one collapsed-text line per table row (label + cells together)."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?is)</tr\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return [re.sub(r"\s+", " ", _h.unescape(ln)).strip() for ln in t.split("\n")]


def _row_pcts(rows: list[str], label_re: str) -> list[float]:
    """Every percent value across ALL rows whose text starts with the label —
    used only to CORROBORATE a prose figure (does this number appear in the
    table?), so we don't have to guess which approach/column a row is."""
    pat = re.compile(r"^\s*" + label_re + r"\b", re.I)
    out = []
    for ln in rows:
        if "%" in ln and pat.match(ln):
            out += [float(x) for x in _PCT.findall(ln)]
    return out


def _prose_val(text: str, key: str) -> float | None:
    m = _PROSE[key].search(text)
    if not m:
        return None
    v = float(m.group(1))
    lo, hi = _RATIO_BANDS[key]
    return v if lo <= v <= hi else None


def extract_capital_ratios(html: str) -> dict:
    """Current-period STANDARDIZED capital ratios from an earnings release, each
    value double-confirmed (narrated AND present in the table) or None.

    For each ratio: take the prose-narrated figure (the binding Standardized one)
    and keep it only if a table cell in that ratio's row(s) corroborates it to
    ~5bp. CET1 must be confirmed AND must order CET1<=T1<=Total — otherwise the
    whole set is refused. Nothing unconfirmed is ever returned.
    """
    none = {k: None for k in _RATIO_BANDS}
    rows = _rows_text(html)
    text = re.sub(r"\s+", " ", _h.unescape(re.sub(r"(?s)<[^>]+>", " ", html)))

    out = {}
    for key in _RATIO_BANDS:
        pv = _prose_val(text, key)
        if pv is None:
            out[key] = None
            continue
        cells = _row_pcts(rows, _ROW_LABELS[key])
        out[key] = pv if any(abs(pv - c) <= _CORROBORATE_TOL for c in cells) else None

    # CET1 is the required anchor; without a confirmed CET1 we surface nothing.
    c, t1, tot = out["cet1_ratio"], out["t1_ratio"], out["total_ratio"]
    if c is None:
        return none
    if t1 is not None and c > t1 + 0.051:
        return none
    if t1 is not None and tot is not None and t1 > tot + 0.051:
        return none
    return out


# ── P&L extraction (increment 5e) — diluted EPS only ───────────────────────
# Scoped to DILUTED EPS: it's the single number the market reacts to, it's
# per-share (no unit ambiguity), and unlike net income it has no segment/YTD/
# annual variants all phrased the same way. (Net income via prose proved a
# variant tar pit — segment vs total vs YTD vs prior-year all read "net income of
# $X"; it's already covered cleanly by Company Reported's 10-Q iXBRL.)
#
# Two look-alikes to defeat, both cardinal-rule critical:
#   1. BASIC EPS — every pattern requires the word "diluted".
#   2. Non-GAAP "adjusted"/"core" EPS (and per-share-change/impact) — excluded by
#      context so we only ever surface the GAAP diluted figure (= the filing's
#      EarningsPerShareDiluted, which this supersedes).
# And the disambiguator: gather ALL clean candidates; if they DISAGREE (e.g. a
# prior-year EPS is also stated), return n/a — never guess which is current.
_EPS_CONN = r"\s*(?:of|was|or|:)?\s*\$\s?"
_EPS_PROSE = [
    re.compile(r"diluted\s+(?:earnings per (?:common )?share|eps)" + _EPS_CONN
               + r"(\d+\.\d{2})", re.I),
    re.compile(r"earnings per (?:common )?diluted share" + _EPS_CONN + r"(\d+\.\d{2})", re.I),
    # "Earnings per share - diluted $5.94" (JPM/PNC house style: diluted trails).
    re.compile(r"earnings per (?:common )?share\s*[-,–:]\s*diluted" + _EPS_CONN
               + r"(\d+\.\d{2})", re.I),
    re.compile(r"\$\s?(\d+\.\d{2})\s+per diluted (?:common )?share", re.I),
]
# A diluted-EPS value is rejected when one of these qualifiers appears in the text
# just before it: non-GAAP variants (adjusted/core/…), per-share IMPACT/ITEM
# figures ("reduction in EPS of $X", "reduced earnings by $X per share",
# "preferred dividends of $X per share"), and prior-period comparisons. The
# actual GAAP figure, if stated unqualified elsewhere, still qualifies — and if
# two clean values disagree the result is n/a anyway.
_EPS_EXCLUDE = re.compile(
    r"\b(?:adjusted|core|operating|non-?gaap|normalized|underlying|tangible book|"
    r"pre-?tax|after-?tax|cash|reduc\w*|increas\w*|impact\w*|benefit\w*|charge\w*|"
    r"accret\w*|dividends?|compared\s+to|versus|vs\.?|year[- ]ago|prior[- ]year)\b",
    re.I)
# A qualifier in the SAME CLAUSE right after the value marks it non-GAAP: "diluted
# EPS of $0.59, excluding the charge". Only whitespace/comma/paren may sit between
# — NOT a ';' or '.', which start a NEW clause (e.g. "$0.58 per diluted share;
# adjusted net income was …" is a separate statement, so $0.58 stays GAAP).
_EPS_TRAIL = re.compile(
    r"^[\s,(]*(?:excluding|adjusted|as adjusted|non-?gaap|core|operating)", re.I)
_EPS_BAND = (-5.0, 50.0)       # diluted EPS per share


def extract_pnl(html: str) -> dict:
    """Current-quarter GAAP DILUTED EPS from an earnings release: {diluted_eps}
    or None. Gathers every "diluted EPS = $X" not preceded by a non-GAAP/change/
    comparison qualifier; returns the value only when all such clean candidates
    agree (to a cent). If none match, or they disagree (a prior-year/adjusted
    figure also reads clean), returns None — never a guessed value (cardinal
    rule). CANDIDATE only; display is gated on the ground-truth audit."""
    text = re.sub(r"\s+", " ", _h.unescape(re.sub(r"(?s)<[^>]+>", " ", html)))
    vals = []
    for pat in _EPS_PROSE:
        for m in pat.finditer(text):
            if _EPS_EXCLUDE.search(text[max(0, m.start() - 26):m.start()]):
                continue
            if _EPS_TRAIL.match(text[m.end():m.end() + 28]):
                continue
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if _EPS_BAND[0] <= v <= _EPS_BAND[1]:
                vals.append(v)
    if not vals or (max(vals) - min(vals)) > 0.011:
        return {"diluted_eps": None}   # none, or ambiguous → n/a
    return {"diluted_eps": vals[0]}


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


# ── Freshest-wins wire-in (increment 5d) ───────────────────────────────────
_QENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


def _quarter_end_before(filed_iso: str) -> str | None:
    """The quarter-end the release covers — most recent quarter-end strictly
    before the filing date (releases land ~2-4 weeks after quarter close)."""
    from datetime import date
    try:
        y, m, d = (int(x) for x in filed_iso.split("-"))
        filed = date(y, m, d)
    except Exception:
        return None
    cands = [date(yy, qm, qd) for yy in (y, y - 1) for (qm, qd) in _QENDS
             if date(yy, qm, qd) < filed]
    return max(cands).isoformat() if cands else None


def _compute_fresh_diluted_eps(cik) -> dict | None:
    try:
        rel = latest_earnings_release(cik)
    except Exception:
        return None
    if not rel:
        return None
    eps = extract_pnl(rel["html"]).get("diluted_eps")
    if eps is None:
        return None
    qend = _quarter_end_before(rel.get("filed_date", ""))
    if not qend:
        return None
    # Only "fresh" if the release's quarter is NEWER than what the latest filing
    # already reports — otherwise the 10-Q/10-K has it and there's no lead to add.
    # If we can't confirm (fetch/parse fails), return None (don't show an
    # unverifiable "preliminary" value).
    try:
        from data.sec_filing_scraper import latest_filing, instance_facts
        meta = latest_filing(cik, ("10-Q", "10-K"))
        if not meta:
            return None
        for f in instance_facts(meta):
            if (f.concept.endswith("EarningsPerShareDiluted") and not f.members
                    and f.period_end and f.period_end >= qend):
                return None  # filing already covers this quarter (or newer)
    except Exception:
        return None
    return {"eps": eps, "quarter": qend, "filed_date": rel.get("filed_date"),
            "url": rel.get("url")}


def fresh_diluted_eps(cik) -> dict | None:
    """The current-quarter GAAP diluted EPS from the latest earnings release,
    returned ONLY when that quarter is fresher than the latest 10-Q/10-K already
    reports (the freshest-wins decision). {eps, quarter, filed_date, url} or None.
    Cached ~12h (the fetch+parse is heavy and the release changes quarterly)."""
    if not cik:
        return None
    from data import cache as _cache
    from data.freshness import is_fresh
    key = f"ir_fresh_eps:v1:{int(cik)}"
    try:
        cached = _cache.get(key)
        if cached is not None and is_fresh(cached, 12 * 3600):
            return cached.get("value")
    except Exception:
        pass
    val = _compute_fresh_diluted_eps(cik)
    try:
        from datetime import datetime
        _cache.put(key, {"cached_at": datetime.now().isoformat(), "value": val})
    except Exception:
        pass
    return val
