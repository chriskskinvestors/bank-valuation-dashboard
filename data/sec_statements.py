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
# reject pattern keeps 'comprehensive income', cash flows and parentheticals out
# of the primary income statement / balance sheet.
# Income accepts BOTH word orders: "statement(s) of income/operations/earnings"
# AND "income statement(s)" — PNC titles its primary income R-file "Consolidated
# Income Statement", which the "statement OF income" order alone misses. The
# reject still drops the comprehensive-income and cash-flow siblings (a
# "comprehensive income" title contains "income" but is not the income statement).
_STMT_PATTERNS = {
    "income": (re.compile(r"statements?\s+of\s+(income|operations|earnings)|"
                          r"\bincome\s+statements?\b", re.I),
               re.compile(r"comprehensive|parenthetical|cash\s+flow", re.I)),
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
    "loan_composition": {
        # The loan note is named "Loans and Allowance for Credit Losses", so
        # 'allowance' is in EVERY sibling's full ShortName — prefer/reject must
        # match the table-specific suffix (after the last ' - '), not the whole
        # name, or every candidate is wrongly rejected. (Handled in _note_rfile.)
        "want": re.compile(r"\bloan", re.I),
        "prefer": re.compile(r"composition of loan|loan portfolio|portfolio by|by loan class", re.I),
        # 'Federal Home Loan Bank Advances' contains 'loan' and would otherwise
        # match `want` — reject it (and other non-composition siblings) explicitly.
        "reject": re.compile(r"federal home loan|fhlb|home loan bank|advance|"
                             r"allowance|past due|delinquen|credit quality|risk rating|"
                             r"impaired|modif|nonaccrual|non-accrual|charge|narrative|"
                             r"additional|collateral|servic|commitment|industry|"
                             r"classification|held[- ]for[- ]sale|maturit|defaulted|"
                             r"off-balance|activity|aging|nonperform|non-perform", re.I),
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


def _specific(short: str) -> str:
    """The table-specific part of a note ShortName: the text after the LAST ' - '
    (the parent note name precedes it), with a trailing '(Details)' removed. Falls
    back to the whole name when there's no ' - '. prefer/reject match THIS, not the
    full name — the parent note name (e.g. 'Loans and Allowance for Credit Losses')
    carries words like 'allowance' that would otherwise reject every sibling."""
    s = re.sub(r"\s*\(details\)\s*$", "", short, flags=re.I)
    return s.split(" - ")[-1].strip() if " - " in s else s


def _note_rfile(base: str, spec: dict) -> str | None:
    """Best note '(Details)' R-file for a spec. `want` matches the full ShortName
    (the note topic); `prefer`/`reject` match the table-specific suffix (the
    sub-table). Prefers the composition/schedule variant over a generic sibling.
    Returns None when the filer discloses no matching composition table."""
    cands = []
    for s, f in _iter_reports(base):
        if "(details)" not in s.lower() or not spec["want"].search(s):
            continue
        suffix = _specific(s)
        if spec["reject"].search(suffix):
            continue
        cands.append((f, suffix))
    if not cands:
        return None
    preferred = [f for f, suffix in cands if spec["prefer"].search(suffix)]
    return preferred[0] if preferred else cands[0][0]


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


# XBRL concept/abstract rows that carry no economic line — dropped when
# collapsing a dimension-member table (the loan-composition rendering).
_DIM_NOISE = re.compile(r"\[(abstract|line items|member|roll ?forward|domain|table|axis)\]|"
                        r"financing receivable, credit quality indicator|"
                        r"accounts, notes, loans and financing receivable", re.I)


def _collapse_dimensional(parsed: dict) -> dict:
    """Loan-composition R-files render each loan class as an XBRL dimension-member
    HEADER row, with the balance in a following generic value row (label 'Loans').
    Collapse to one labeled data row per class: label = the member header (an
    'Axis | Member' prefix stripped to Member), values = the next value row's.
    XBRL concept/abstract noise rows are dropped; the leading no-dimension value
    is the grand total ('Total loans')."""
    rows = []
    pending = "Total loans"                     # the default (no-dimension) member
    for r in parsed["rows"]:
        if r["header"]:
            if _DIM_NOISE.search(r["label"]):
                continue
            pending = r["label"].split(" | ")[-1].strip()
        elif pending is not None:
            rows.append({"label": pending, "header": False, "values": r["values"]})
            pending = None
    return {"title": parsed.get("title", ""), "units_scale": parsed.get("units_scale", 1.0),
            "periods": parsed["periods"], "rows": rows}


# Notes whose R-file needs a shape transform before stitching (loan composition is
# rendered as XBRL dimension members; deposit composition is already row-labeled).
_NOTE_TRANSFORM = {"loan_composition": _collapse_dimensional}


def _rfile_for(base: str, stype: str) -> str | None:
    """R-file name for a primary statement (income/balance/cashflow) or a note
    (a key in _NOTE_SPECS), from this filing's FilingSummary."""
    if stype in _NOTE_SPECS:
        return _note_rfile(base, _NOTE_SPECS[stype])
    return _statement_rfiles(base).get(stype)


_SCALE_WORD = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}


def _units_scale(title: str) -> float:
    """Dollar scale from a statement title. The DOLLAR magnitude is the one in
    the '$ in <unit>' phrase; a separate 'shares in Thousands' clause governs
    share COUNTS, not dollars (KEY's income title is '… shares in Thousands,
    $ in Millions' — keying off the first 'in thousands' would wrongly scale
    every dollar line by 1e3 instead of 1e6). Prefer the explicit '$ in <unit>',
    then fall back to a bare 'in <unit>' for titles that omit the '$'."""
    t = title.lower()
    m = re.search(r"\$\s*in\s+(thousands|millions|billions)", t)
    if m:
        return _SCALE_WORD[m.group(1)]
    m = re.search(r"\bin\s+(thousands|millions|billions)", t)
    if m:
        return _SCALE_WORD[m.group(1)]
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

    header_rows, body = [], []
    for tr in trs:
        cells = tr.xpath("./th|./td")
        if not cells:
            continue
        # Column-header rows (title row + the date header) are built ENTIRELY of
        # <th> tags; data/label rows always carry at least one <td> (the 'pl'
        # label cell). Some filers (e.g. KEY) insert an empty spacer <td
        # class="th"> into every data row, so keying off the CLASS string 'th'
        # mis-routes every data row into the header — classify by TAG instead.
        if all(c.tag == "th" for c in cells):
            header_rows.append(tr)
        else:
            body.append(tr)

    def _has_th_class(cell) -> bool:
        return "th" in (cell.get("class") or "").split()

    def _valcells(cells):
        """Value cells of a data row: everything after the label cell, minus the
        empty spacer <td class="th"> some filers (KEY) insert between the label
        and the numbers. Dropping it keeps each value aligned to its period
        column; a genuine blank value cell (class 'text'/'num') is preserved."""
        return [c for c in cells[1:] if not _has_th_class(c)]

    ncol = 0
    for tr in body:
        ncol = max(ncol, len(_valcells(tr.xpath("./th|./td"))))

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
        valcells = _valcells(cells)
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
    def _column(f, period):
        idx = f["periods"].index(period)
        return {(_norm_label(r["label"]), r["header"]):
                (r["values"][idx] if idx < len(r["values"]) else None)
                for r in f["rows"] if not r["header"]}

    # Per period, the OWNING filing is the newest one that lists the period; its
    # rows define which lines the company broke out for that year (a line the
    # owner doesn't carry stays BLANK — the company's own absence, never
    # backfilled from an older filing). But the owner can list a line yet leave
    # THAT period's cell blank (KEY's latest balance sheet carries Dec-31-2023 as
    # a third date but fills only a few rows — Total assets is blank); such a hole
    # is backfilled from the newest OLDER filing that reports a number for that
    # exact (line, period). Never overwrites a real owner value.
    col: dict = {}                              # period -> {norm_key: value}
    for period in all_periods:
        owner_idx = next((i for i, f in enumerate(parsed)
                          if period in f["periods"]), None)
        if owner_idx is None:
            col[period] = {}
            continue
        merged = _column(parsed[owner_idx], period)   # owner defines present lines
        for key, val in list(merged.items()):
            if val is None:                           # a hole in an existing line
                for f in parsed[owner_idx + 1:]:      # older filings, newest first
                    if period in f["periods"]:
                        older = _column(f, period).get(key)
                        if older is not None:
                            merged[key] = older
                            break
        col[period] = merged
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
    ckey = f"asreported_my:v5:{stype}:{metas[0]['accession']}:{n_years}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached or None
    parsed = []
    for m in metas:
        base = _filing_base(m["cik"], m["accession"])
        try:
            fn = _rfile_for(base, stype)
            stmt = parse_rfile(_get(base + fn)) if fn else None
            if stmt and stype in _NOTE_TRANSFORM:
                stmt = _NOTE_TRANSFORM[stype](stmt)   # e.g. collapse loan dimensions
        except Exception as e:
            print(f"[sec_statements] multiyear {stype} failed for cik {cik}: "
                  f"{type(e).__name__}: {e}")
            return None                         # transient — don't cache
        if stmt and stype in _NOTE_SPECS and _is_maturity_table(stmt):
            stmt = None                         # a maturity ladder, not the composition note
        if stmt and stype in _NOTE_SPECS and \
                sum(1 for r in stmt["rows"] if not r["header"]) < 3:
            stmt = None                         # too few line items to be a real composition
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
