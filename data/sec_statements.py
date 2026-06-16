"""As-Reported financial statements from SEC filing R-files.

SEC renders each filing's primary statements (income, balance sheet, cash flows)
as "R-files" (R3.htm, R5.htm, …) generated from the presentation/label linkbase,
so they carry the COMPANY'S OWN labels, section order, subtotals, and custom
lines — the statement exactly as management reports it. That is SNL's
"As-Reported" view, the counterpart to the FDIC-templated uniform tables. We
parse those rendered tables faithfully: no us-gaap concept mapping, no
re-templating, no reconciliation needed — the numbers ARE the company's.

FilingSummary.xml maps each statement to its R-file. See the Templated/As-Reported
design in docs/DATA-SOURCING-ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import re

from lxml import etree, html as lhtml

from data.sec_filing_scraper import latest_filing, _get

# Match a statement type to its FilingSummary ShortName. (want, reject) — the
# reject pattern keeps 'comprehensive income' and parentheticals out of the
# primary income statement / balance sheet.
_STMT_PATTERNS = {
    "income": (re.compile(r"statements?\s+of\s+(income|operations|earnings)", re.I),
               re.compile(r"comprehensive|parenthetical", re.I)),
    "balance": (re.compile(r"balance\s+sheet|financial\s+position|financial\s+condition", re.I),
                re.compile(r"parenthetical", re.I)),
    "cashflow": (re.compile(r"statements?\s+of\s+cash\s+flows", re.I),
                 re.compile(r"parenthetical", re.I)),
}
_DATE = re.compile(r"[A-Z][a-z]{2}\.?\s+\d{1,2},\s+\d{4}")
# Per-share amounts ($/share) and share counts are reported in their OWN units,
# NOT the statement's "$ in Thousands/Millions" — so they must not be scaled.
_PERSHARE = re.compile(r"per share|per common share|\(in shares\)|in shares", re.I)

# As-reported NOTE tables (the SNL-depth disclosures past the primary statements).
# Notes are rendered as their own "(Details)" R-files; we pick the by-type
# composition table and reject sibling tables (maturities, narrative, rollforward)
# whose ShortName matches the same topic. ShortName conventions vary by filer
# (e.g. "Deposits (Details)" vs "Deposits - Composition of Deposits (Details)" vs
# "DEPOSITS - Schedule of Deposits (Details)"), so 'prefer' ranks the composition
# variant and 'reject' drops the wrong siblings.
_NOTE_SPECS = {
    "deposit_composition": {
        "want": re.compile(r"deposit", re.I),
        "prefer": re.compile(r"composition|schedule of deposits|by type|classification", re.I),
        "reject": re.compile(r"maturit|narrative|additional|contractual|insured|"
                             r"uninsured|pledged|interest expense|roll[- ]?forward", re.I),
    },
}
# A note whose ShortName is generic ("Deposits (Details)") can actually be a
# MATURITY ladder (rows are fiscal years / "Thereafter"), not a by-type
# composition — so guard on content, not just the ShortName.
_MATURITY_LABEL = re.compile(r"^((19|20)\d{2}|thereafter|due\b|within\b|after \d)", re.I)


def _filing_base(cik, accession):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def _iter_reports(base: str):
    """Yield (ShortName, HtmlFileName) for every Report in FilingSummary.xml."""
    root = etree.fromstring(_get(base + "FilingSummary.xml"))
    for r in root.iter():
        if r.tag.split("}")[-1] != "Report":
            continue
        short = fn = ""
        for c in r:
            tag = c.tag.split("}")[-1]
            if tag == "ShortName":
                short = (c.text or "").strip()
            elif tag == "HtmlFileName":
                fn = (c.text or "").strip()
        if short and fn:
            yield short, fn


def _statement_rfiles(base: str) -> dict:
    """statement_type -> R-file name, from FilingSummary.xml (first match wins)."""
    out: dict = {}
    for short, fn in _iter_reports(base):
        for stype, (want, reject) in _STMT_PATTERNS.items():
            if stype not in out and want.search(short) and not reject.search(short):
                out[stype] = fn
    return out


def _note_rfile(base: str, spec: dict) -> str | None:
    """Best note '(Details)' R-file for a spec, by ShortName ranking. Restricted
    to Details reports matching `want` and not `reject`; prefers the `prefer`
    (composition/schedule) variant over a generic sibling. Returns None if the
    filer discloses no matching note."""
    cands = [(s, f) for s, f in _iter_reports(base)
             if "(details)" in s.lower()
             and spec["want"].search(s) and not spec["reject"].search(s)]
    if not cands:
        return None
    preferred = [(s, f) for s, f in cands if spec["prefer"].search(s)]
    return (preferred or cands)[0][1]


def _is_maturity_table(parsed: dict) -> bool:
    """True when a parsed note R-file is actually a maturity ladder (most data
    rows are fiscal years / 'Thereafter') rather than a by-type composition —
    used to reject a note whose ShortName is generic (e.g. 'Deposits (Details)')
    but whose content is a maturity schedule."""
    labels = [r["label"].strip() for r in parsed.get("rows", []) if not r["header"]]
    if len(labels) < 3:
        return False
    hits = sum(1 for l in labels if _MATURITY_LABEL.match(l))
    return hits >= max(2, len(labels) // 2)


def _rfile_for(base: str, stype: str) -> str | None:
    """R-file name for a primary statement (income/balance/cashflow) or a note
    (a key in _NOTE_SPECS), from this filing's FilingSummary."""
    if stype in _NOTE_SPECS:
        return _note_rfile(base, _NOTE_SPECS[stype])
    return _statement_rfiles(base).get(stype)


def _units_scale(title: str) -> float:
    t = title.lower()
    if "in thousands" in t:
        return 1e3
    if "in millions" in t:
        return 1e6
    if "in billions" in t:
        return 1e9
    return 1.0


def _num(text: str, scale: float):
    s = text.replace("$", "").replace(",", "").replace("\xa0", " ").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not re.match(r"^-?\d", s):
        return None
    try:
        return float(s) * scale * (-1 if neg else 1)
    except ValueError:
        return None


def parse_rfile(html_bytes: bytes) -> dict | None:
    """Parse a statement R-file into {title, units_scale, periods, basis, rows}.
    Each row: {label, header (section title, no values), values (aligned to
    periods)}. Header rows vs data rows are told apart by the value cells being
    empty (a section heading) vs carrying numbers."""
    h = lhtml.fromstring(html_bytes)
    trs = h.xpath("//table//tr")
    if not trs:
        return None
    tl = h.xpath("//th[contains(concat(' ', normalize-space(@class), ' '), ' tl ')]")
    title = " ".join(tl[0].text_content().split()) if tl else ""
    scale = _units_scale(title)

    def classes(tr):
        return " ".join((c.get("class") or "") for c in tr.xpath("./th|./td"))

    header_rows, body = [], []
    for tr in trs:
        cls = classes(tr)
        # Column-header rows use class 'th'; the title row uses 'tl'. Data/label
        # rows use 'pl'/'pr'/'nump'/'num'/'text' (none contain 'th' or 'tl').
        if re.search(r"\bth\b|\btl\b", cls):
            header_rows.append(tr)
        else:
            body.append(tr)

    ncol = 0
    for tr in body:
        ncol = max(ncol, len(tr.xpath("./th|./td")) - 1)

    periods: list = []
    if header_rows:
        last = [" ".join(c.text_content().split())
                for c in header_rows[-1].xpath("./th|./td")]
        dates = [d for d in last if _DATE.search(d)]
        periods = dates[-ncol:] if ncol and len(dates) >= ncol else dates
    basis = ""
    for tr in header_rows:
        for c in tr.xpath("./th|./td"):
            t = " ".join(c.text_content().split())
            if re.search(r"Months Ended|Year(s)? Ended", t):
                basis = t

    rows = []
    for tr in body:
        cells = tr.xpath("./th|./td")
        if not cells:
            continue
        label = " ".join(cells[0].text_content().split())
        if not label:
            continue
        valcells = cells[1:]
        texts = [" ".join(c.text_content().split()) for c in valcells]
        rscale = 1.0 if _PERSHARE.search(label) else scale
        parsed = [_num(t, rscale) for t in texts]
        is_header = bool(texts) and all(t in ("", "\xa0") for t in texts)
        rows.append({
            "label": label,
            "header": is_header,
            "values": [] if is_header else (parsed + [None] * ncol)[:ncol],
        })
    # SEC R-files append an XBRL element-definition footnote block below the
    # statement (rows like "X", "- Definition…", "Name:", "Namespace Prefix:")
    # that carry no period values. Truncate at the last row with a real number so
    # the rendered statement stops where the company's statement ends.
    last = max((i for i, r in enumerate(rows)
                if any(v is not None for v in r["values"])), default=-1)
    rows = rows[:last + 1]
    return {"title": title, "units_scale": scale, "periods": periods,
            "basis": basis, "rows": rows}


def as_reported_statements_for(cik) -> dict | None:
    """Cached As-Reported primary statements (income, balance sheet, cash flows)
    for a company, from its latest 10-K's SEC-rendered R-files. Returns
    {"meta": {...}, "statements": {type: parsed}} or None. A transient
    fetch/parse failure is never cached (so the next load retries)."""
    if not cik:
        return None
    from data import cache
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    ckey = f"asreported:v1:{meta['accession']}"
    cached = cache.get(ckey)
    if cached is not None:
        return {"meta": meta, "statements": cached} if cached else None
    base = _filing_base(meta["cik"], meta["accession"])
    try:
        stmts = {}
        for stype, fn in _statement_rfiles(base).items():
            parsed = parse_rfile(_get(base + fn))
            if parsed and parsed["rows"]:
                stmts[stype] = parsed
    except Exception as e:
        print(f"[sec_statements] failed for cik {cik}: {type(e).__name__}: {e}")
        return None   # transient — do not cache
    try:
        cache.put(ckey, stmts)
    except Exception:
        pass
    return {"meta": meta, "statements": stmts} if stmts else None


# ── Multi-year stitching (Company Reported income statement) ────────────────
# One 10-K carries ~3 fiscal years; SNL-style depth is ~5. We stitch successive
# 10-Ks: union of the company's own line labels (each filing's order preserved),
# each (line, year) cell taken from the NEWEST filing that reported that year,
# and BLANK where a line wasn't broken out that year (the company's own absence,
# not a guess — matches the "never n/a" rule for Company Reported).

def _period_year(p: str) -> int:
    m = re.search(r"\d{4}", p or "")
    return int(m.group()) if m else 0


def _recent_10k_metas(cik, n: int) -> list:
    """Up to n most-recent 10-K filings {accession, doc, date, cik}, newest first."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form == "10-K":
            out.append({"accession": rec["accessionNumber"][i].replace("-", ""),
                        "doc": rec["primaryDocument"][i],
                        "date": rec["filingDate"][i], "cik": int(cik)})
            if len(out) >= n:
                break
    return out


def _norm_label(s: str) -> str:
    """Match key for a line across filings: drop year-varying numeric detail
    (allowance amounts, share counts, note numbers, dates) so the same line
    doesn't fragment when a filing embeds changing numbers in its label —
    e.g. 'AFS securities, net of allowance of $75 and $69'."""
    s = re.sub(r"[\d,]+", "", s).replace("$", "").replace("—", "").replace("–", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _merge_row_order(parsed: list) -> list:
    """Union of rows across filings (newest first), preserving each filing's
    internal order. Rows are matched on the NORMALIZED label so a line whose
    label carries changing numbers stays one row; the DISPLAY label is the newest
    filing's. Returns [(norm_key, display_label, header), …]."""
    merged: list = []
    keys: list = []                            # parallel norm keys for .index
    for f in parsed:
        prev = -1
        for r in f["rows"]:
            k = (_norm_label(r["label"]), r["header"])
            if k in keys:
                prev = keys.index(k)
            else:
                prev += 1
                merged.insert(prev, (k, r["label"], r["header"]))
                keys.insert(prev, k)
    return merged


def _stitch_statement(parsed: list, n_years: int = 5) -> dict | None:
    """Merge per-filing parsed statements (newest first) into one multi-year
    statement. Pure (no network) — unit-testable."""
    if not parsed:
        return None
    all_periods = sorted({p for f in parsed for p in f["periods"]},
                         key=_period_year, reverse=True)[:n_years]
    col: dict = {}                              # period -> {norm_key: value}
    for period in all_periods:
        for f in parsed:                        # newest first wins
            if period in f["periods"]:
                idx = f["periods"].index(period)
                col[period] = {(_norm_label(r["label"]), r["header"]):
                               (r["values"][idx] if idx < len(r["values"]) else None)
                               for r in f["rows"] if not r["header"]}
                break
    rows = []
    for key, label, header in _merge_row_order(parsed):
        if header:
            rows.append({"label": label, "header": True, "values": []})
        else:
            rows.append({"label": label, "header": False,
                         "values": [col.get(p, {}).get(key) for p in all_periods]})
    return {"periods": all_periods, "rows": rows, "units_scale": parsed[0]["units_scale"]}


def as_reported_statement_multiyear(cik, stype: str = "income", n_years: int = 5) -> dict | None:
    """Cached multi-year Company-Reported statement (stype = "income" |
    "balance" | "cashflow"), stitched from the bank's recent 10-K R-files. Cached
    by the latest 10-K accession (refreshes when a new 10-K files); a transient
    fetch failure is never cached. Returns {"meta", "filings", "statement"} or
    None."""
    if not cik:
        return None
    from data import cache
    # Balance sheets and note tables carry only ~2 periods per 10-K, so reach back further.
    metas = _recent_10k_metas(cik, 6 if (stype == "balance" or stype in _NOTE_SPECS) else 4)
    if not metas:
        return None
    ckey = f"asreported_my:v3:{stype}:{metas[0]['accession']}:{n_years}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached or None
    parsed = []
    for m in metas:
        base = _filing_base(m["cik"], m["accession"])
        try:
            fn = _rfile_for(base, stype)
            stmt = parse_rfile(_get(base + fn)) if fn else None
        except Exception as e:
            print(f"[sec_statements] multiyear {stype} failed for cik {cik}: "
                  f"{type(e).__name__}: {e}")
            return None                         # transient — don't cache
        if stmt and stype in _NOTE_SPECS and _is_maturity_table(stmt):
            stmt = None                         # a maturity ladder, not the composition note
        if stmt and stmt["periods"] and stmt["rows"]:
            stmt["_meta"] = m
            parsed.append(stmt)
        years = {_period_year(p) for f in parsed for p in f["periods"]}
        if len(years) >= n_years and len(parsed) >= 2:
            break
    stitched = _stitch_statement(parsed, n_years)
    if not stitched:
        return None
    result = {"meta": metas[0], "filings": [f["_meta"] for f in parsed],
              "statement": stitched}
    try:
        cache.put(ckey, result)
    except Exception:
        pass
    return result
