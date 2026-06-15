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


def _filing_base(cik, accession):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def _statement_rfiles(base: str) -> dict:
    """statement_type -> R-file name, from FilingSummary.xml (first match wins)."""
    root = etree.fromstring(_get(base + "FilingSummary.xml"))
    out: dict = {}
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
        if not (short and fn):
            continue
        for stype, (want, reject) in _STMT_PATTERNS.items():
            if stype not in out and want.search(short) and not reject.search(short):
                out[stype] = fn
    return out


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
        parsed = [_num(t, scale) for t in texts]
        is_header = bool(texts) and all(t in ("", "\xa0") for t in texts)
        rows.append({
            "label": label,
            "header": is_header,
            "values": [] if is_header else (parsed + [None] * ncol)[:ncol],
        })
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
