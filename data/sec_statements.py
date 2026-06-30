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
#
# COMBINED income+comprehensive: some filers (e.g. ABCB) fold the full income
# statement into a single "Statements of Income AND Comprehensive Income" R-file
# (interest income/expense, provision, noninterest income/expense, net income —
# then an OCI continuation). That IS the income statement and must be ACCEPTED,
# so the reject spares 'comprehensive' when it follows 'income and' (the combined
# title). A STANDALONE "Statements of Comprehensive Income" (OCI-only: starts at
# net income, no revenue/expense lines) is still rejected by the bare
# 'comprehensive'. Title is only a first cut — a content check (_is_income_body)
# is the true discriminator applied after parsing.
_STMT_PATTERNS = {
    "income": (re.compile(r"statements?\s+of\s+(income|operations|earnings)|"
                          r"\bincome\s+statements?\b", re.I),
               re.compile(r"(?i:(?<!income and )comprehensive)|"
                          r"parenthetical|cash\s+flow", re.I)),
    "balance": (re.compile(r"balance\s+sheet|financial\s+position|financial\s+condition", re.I),
                re.compile(r"parenthetical", re.I)),
    "cashflow": (re.compile(r"statements?\s+of\s+cash\s+flows", re.I),
                 re.compile(r"parenthetical", re.I)),
}
_DATE = re.compile(r"[A-Z][a-z]{2}\.?\s+\d{1,2},\s+\d{4}")
# XBRL metadata rows that are NOT economic line items and must never render as a
# statement line: an '[Extensible Enumeration]' tag (RF renders its
# defined-benefit-plan enumeration this way — a labelled row with no period
# value), and a bare footnote marker like '[1]' (a reference, not a line). These
# carry no number; dropping them keeps the rendered statement to real lines.
_NOISE_LABEL = re.compile(
    r"\[extensible\s+enumeration\]|^\s*\[\d+\]\s*$", re.I)
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
        if not label or _NOISE_LABEL.search(label):
            continue                                  # XBRL metadata, not a line
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


# ── Combined "Income AND Comprehensive Income" statements ────────────────────
# Some filers (ABCB) render the full income statement and the OCI section in one
# R-file titled "Statements of Income and Comprehensive Income". That R-file IS
# the income statement (interest income/expense, provision, noninterest
# income/expense, net income) — but it appends an other-comprehensive-income
# continuation (unrealized gains, total OCI, comprehensive-income total) that is
# NOT part of the income statement. We accept the combined R-file but strip that
# OCI continuation so the parsed statement ends at the company's income lines.
#
# Discriminating a COMBINED statement from a STANDALONE "Statements of
# Comprehensive Income" (OCI-only: starts AT net income, no revenue/expense
# lines) is by CONTENT, not title — _is_income_body requires real income lines
# ABOVE net income, which a pure-OCI statement lacks.

# A net-income line (the income statement's bottom line). Accepts the "net
# earnings" wording (FFIN titles its bottom line "Net earnings", not "Net
# income") as well as "net income". Excludes the OCI "comprehensive income"
# total and "other comprehensive income" rows, and the EPS line (FFIN's "NET
# EARNINGS PER SHARE, BASIC") via the trailing per-share lookahead — so the
# bottom-line match never lands on a per-share row.
_NET_INCOME = re.compile(
    r"^\s*net\s+(income|earnings)(\s+\(loss\))?\b(?!.*comprehensive)(?!.*per\s+share)",
    re.I)
# Income-statement lines that only ever appear ABOVE net income (revenue, cost,
# tax) — their presence proves a body is a real income statement, not OCI-only.
_INCOME_LINE = re.compile(
    r"interest\s+income|interest\s+expense|interest\s+and\s+(fees|dividend)|"
    r"noninterest\s+(income|expense)|non-interest\s+(income|expense)|"
    r"provision\s+for|total\s+revenue|net\s+revenue|"
    r"income\s+before\s+income\s+tax|income\s+tax\s+(expense|benefit)|"
    r"salaries", re.I)
# The OCI continuation that follows net income in a combined statement: the
# section header "Other comprehensive income" through the "Comprehensive income"
# total. These rows are dropped; per-share / share-count rows (which a normal
# income statement also carries) are NOT in this range and survive.
_OCI_START = re.compile(r"^\s*other\s+comprehensive\s+(income|loss)", re.I)
_OCI_TOTAL = re.compile(r"^\s*(total\s+)?comprehensive\s+(income|loss)\b", re.I)


def _is_income_body(parsed: dict | None) -> bool:
    """True iff a parsed R-file is a real income statement: it has a net-income
    line AND at least one revenue/expense line ABOVE it. A standalone OCI-only
    'Statements of Comprehensive Income' (which STARTS at net income, with no
    income lines above) fails this — the content discriminator the title cannot
    make. A non-income statement (balance, cash flow) has no net-income line and
    also fails, so this guard is only meaningful for income candidates."""
    if not parsed or not parsed.get("rows"):
        return False
    ni_idx = next((i for i, r in enumerate(parsed["rows"])
                   if not r["header"] and _NET_INCOME.match(r["label"])), None)
    if ni_idx is None:
        return False
    return any(_INCOME_LINE.search(r["label"]) for r in parsed["rows"][:ni_idx])


def _strip_oci(parsed: dict) -> dict:
    """Drop the other-comprehensive-income continuation from a combined
    income+comprehensive statement: the contiguous run from the 'Other
    comprehensive income' section header through the 'Comprehensive income'
    total. Net income and the per-share / weighted-share rows that follow the
    OCI block are preserved — only the OCI section is removed. A pure income
    statement (no OCI block) is returned unchanged."""
    rows = parsed["rows"]
    start = next((i for i, r in enumerate(rows) if _OCI_START.match(r["label"])), None)
    if start is None:
        return parsed
    end = next((j for j in range(start, len(rows)) if _OCI_TOTAL.match(rows[j]["label"])),
               None)
    if end is None:
        return parsed                       # no closing total → leave untouched
    kept = rows[:start] + rows[end + 1:]
    return {**parsed, "rows": kept}


# The grand-total row that ENDS a consolidated balance sheet: total assets =
# total liabilities + equity. Filers word the equity side "stockholders'",
# "shareholders'", "members'", or omit the qualifier ("Total liabilities and
# equity"); the apostrophe frequently renders as the cp1252 replacement char, so
# match loosely between "and" and "equity".
_BALANCE_END = re.compile(
    r"^\s*total\s+liabilities\s+and\b.{0,40}\bequity\b", re.I)


def _primary_balance(parsed: dict | None) -> dict | None:
    """Isolate the PRIMARY consolidated balance sheet within an R-file that also
    carries supplemental tables. JPM folds a "VIEs consolidated by the Firm"
    block + a footnote-[1] narrative + a 'December 31, (in millions) | 2025'
    year-as-value garbage row into the same R-file; USB folds a loan-composition
    table after the statement. parse_rfile ingests ALL of it, so the stitch sees
    duplicate 'Total assets'/'Total loans' subtotals (the VIE 43,295 / per-class
    loan totals) that corrupt the real values.

    The primary statement ENDS at its grand-total row 'Total liabilities and …
    equity' (= total assets); everything after it belongs to a supplemental
    table and is dropped. A supplemental table also widens the value grid:
    parse_rfile pads EVERY row to the max column count, which the wider VIE table
    / garbage row inflates (JPM: 11 cols for a 2-period balance sheet). That
    width mismatch makes _column_meta bail (it requires the header width to equal
    ncol) and drops the whole filing — JPM's QUARTERLY balance came back empty.
    So each kept row's values are trimmed to the real period count, restoring the
    header↔value alignment.

    When no grand-total row is present we do NOT truncate — never drop a real
    line on a guess (single-table balance sheets with no grand-total, or an
    unexpected layout, pass through unchanged)."""
    if not parsed or not parsed.get("rows"):
        return parsed
    rows = parsed["rows"]
    end = next((i for i, r in enumerate(rows)
                if not r["header"] and _BALANCE_END.match(r["label"])), None)
    if end is None:
        return parsed
    n = len(parsed.get("periods") or [])
    sliced = rows[:end + 1]
    if n:
        sliced = [r if r["header"] else {**r, "values": r["values"][:n]}
                  for r in sliced]
    return {**parsed, "rows": sliced}


def _balance_parse(html_bytes: bytes) -> dict | None:
    """parse_rfile for a BALANCE R-file, then isolate the primary statement so a
    supplemental table sharing the R-file (JPM VIEs, USB loan composition) cannot
    inject duplicate subtotals or narrative/footnote rows."""
    return _primary_balance(parse_rfile(html_bytes))


def _income_parse(html_bytes: bytes) -> dict | None:
    """parse_rfile for an INCOME R-file, then strip any OCI continuation (combined
    'Income and Comprehensive Income' statements). Returns None if the parsed body
    is not a real income statement (e.g. a standalone OCI-only statement that
    slipped past the title matcher) — the content discriminator for the cardinal
    rule: never render a non-income statement as income."""
    parsed = parse_rfile(html_bytes)
    if not (parsed and parsed["rows"]) or not _is_income_body(parsed):
        return None
    return _strip_oci(parsed)


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
    ckey = f"asreported:v2:{meta['accession']}"
    cached = cache.get(ckey)
    if cached is not None:
        return {"meta": meta, "statements": cached} if cached else None
    base = _filing_base(meta["cik"], meta["accession"])
    try:
        stmts = {}
        for stype, fn in _statement_rfiles(base).items():
            raw = _get(base + fn)
            if stype == "income":
                parsed = _income_parse(raw)
            elif stype == "balance":
                parsed = _balance_parse(raw)
            else:
                parsed = parse_rfile(raw)
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


# An injected XBRL standard-label placeholder, NOT an economic line: SEC renders
# 'Common Stock Shares Issued Not Disclosed' (TFC's latest 10-K) when a filer
# tags a share-count concept with no value. It carries no number — render the
# real line or nothing, never the placeholder.
_PLACEHOLDER_LABEL = re.compile(r"shares?\s+issued\s+not\s+disclosed", re.I)

# Wording that VARIES across filings without changing a line's identity: a
# registrant's name spliced into an equity subtotal ('Total Huntington' vs
# 'Total Huntington Bancshares Inc'), and grammatical connectives. Stripped only
# to DETECT mergeable variants (NOT the stitch key — that stays _norm_label), and
# every candidate merge is still gated by the same-period guard below.
_VARIANT_DROP = re.compile(
    r"\binc\b|\bincorporated\b|\bcorp\b|\bcorporation\b|\bcompany\b|\bco\b|"
    r"\bbancshares\b|\bbancorp\b|\bbancorporation\b|\band\b|\bthe\b|\bof\b|\bat\b",
    re.I)


def _variant_tokens(label: str) -> frozenset:
    """Token SET for variant matching: drop parentheticals, embedded numbers, the
    cp1252-mangled apostrophe, punctuation, and registrant-name / connective
    filler. Two labels are variant-compatible when one token set is a subset of
    the other (a wording superset/subset of the SAME line)."""
    s = re.sub(r"\([^)]*\)", " ", label)               # drop parentheticals
    s = re.sub(r"[\d,]+", " ", s)                       # drop embedded numbers
    s = s.replace("�", " ").replace("'", " ").replace("’", " ")
    s = re.sub(r"[^\w\s]", " ", s)                      # drop punctuation
    s = _VARIANT_DROP.sub(" ", s)
    return frozenset(w for w in s.lower().split() if w)


def _variant_compatible(a: str, b: str) -> bool:
    """True when labels a and b are wording variants of the SAME line: their
    variant-token sets are in a subset relation (equal, or one a subset of the
    other). A single-token subset (e.g. 'Basic' ⊂ 'Basic earnings per common
    share' — PNC renames its EPS rows) is allowed ONLY when BOTH are per-share
    rows; otherwise a lone shared word ('total') would over-link unrelated
    subtotals. The same-period guard in _consolidate_variants is the final
    safeguard against merging two genuinely-distinct lines."""
    ta, tb = _variant_tokens(a), _variant_tokens(b)
    if not ta or not tb:
        return False
    if not (ta <= tb or tb <= ta):
        return False
    if min(len(ta), len(tb)) >= 2:
        return True
    return bool(_PERSHARE.search(a)) and bool(_PERSHARE.search(b))


def _consolidate_variants(stmt: dict | None) -> dict | None:
    """Fold cross-filing WORDING VARIANTS of the same line into one row, AFTER the
    strict-key stitch. Renamed lines otherwise fragment into blank-duplicate rows:
    PNC 'Net income (loss)'(2021)↔'Net income'(2022-25); 'Basic earnings per
    common share'↔'Basic'; KEY's two 'Common Shares, $1 par value…' rows; HBAN
    'Total Huntington shareholders' equity'↔'…Bancshares Inc…' and 'Total
    liabilities and shareholders' equity'↔'Total liabilities and equity'.

    CARDINAL over-merge guard: two rows merge ONLY if they NEVER both hold a
    non-blank value in the SAME period. If both are populated in any shared period
    they are DISTINCT lines (e.g. 'Net income' vs 'Net income attributable to
    noncontrolling interests', or 'Total equity' vs 'Total liabilities and
    equity') and stay separate — a merge can never overwrite or invent a value.
    The surviving row keeps the label whose values reach the NEWEST (leftmost)
    period; absorbed values fill only its blank cells. Header rows never merge.
    The injected 'shares issued not disclosed' placeholder is dropped outright."""
    if not stmt or not stmt.get("rows"):
        return stmt
    rows = [r for r in stmt["rows"]
            if r["header"] or not _PLACEHOLDER_LABEL.search(r["label"])]
    n = len(stmt.get("periods") or [])
    out: list = []
    for r in rows:
        if r["header"]:
            out.append(dict(r))
            continue
        target = None
        for o in out:
            if o["header"] or not _variant_compatible(o["label"], r["label"]):
                continue
            # Guard: skip if ANY period already holds a value in BOTH rows.
            if any(o["values"][i] is not None and r["values"][i] is not None
                   for i in range(min(len(o["values"]), len(r["values"])))):
                continue
            target = o
            break
        if target is None:
            out.append(dict(r))
            continue
        merged = [target["values"][i] if (i < len(target["values"])
                  and target["values"][i] is not None)
                  else (r["values"][i] if i < len(r["values"]) else None)
                  for i in range(n)]

        def _first(vals):
            return next((i for i, v in enumerate(vals) if v is not None), n)
        if _first(r["values"]) < _first(target["values"]):
            target["label"] = r["label"]      # newer wording reaches a newer period
        target["values"] = merged
    return {**stmt, "rows": out}


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
    return _consolidate_variants(
        {"periods": all_periods, "rows": rows,
         "units_scale": parsed[0]["units_scale"]})


# ── Multi-quarter stitching (Company Reported — discrete single quarters) ────
# Audit invariant A21: a discrete-quarter income/cashflow figure must be a TRUE
# single quarter, NEVER a year-to-date cumulative mislabeled as a quarter. A
# 10-Q's income statement renders a "Three Months Ended <q-end>" column whose
# duration is ~one quarter — that column IS the discrete quarter (no math). Q4
# has no 10-Q, so Q4 = FY (10-K "12 Months") − 9M (that fiscal year's Q3 10-Q
# "9 Months" YTD), within one fiscal year. Balance sheets are point-in-time:
# each quarter-end column is the snapshot, stitched newest-first with no
# differencing. When the discrete-quarter column can't be cleanly identified for
# a period, that period is omitted (renders blank) — never a guessed number.

_MONTHS_ENDED = re.compile(r"(\d+)\s+Months?\s+Ended", re.I)
_YEAR_ENDED = re.compile(r"Years?\s+Ended", re.I)
_MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
               "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _period_key(p: str) -> tuple | None:
    """(year, month) of a rendered period-end like 'Sep. 30, 2025'. None if it
    doesn't parse — used to align a discrete-quarter column to its quarter-end
    and to match a Q3 10-Q's nine-month column to the 10-K's fiscal year."""
    if not p:
        return None
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+\d{1,2},\s+(\d{4})", p)
    if not m:
        return None
    mon = _MONTH_ABBR.get(m.group(1)[:3].lower())
    return (int(m.group(2)), mon) if mon else None


def _column_meta(html_bytes: bytes, ncol: int) -> list | None:
    """Per-value-column (duration_months | None, period_end_str) for a statement
    R-file, aligned to the `ncol` value columns parse_rfile produced. Duration
    comes from the "N Months Ended" / "Year(s) Ended" header band and the
    period-end from the date header row, each expanded by its colspans so a
    column's duration is read from period/duration METADATA, not column position.
    Year-Ended is treated as 12 months. Returns None when the expanded header
    width doesn't match ncol (an unrecognized layout — the caller then yields no
    discrete quarter rather than guess)."""
    h = lhtml.fromstring(html_bytes)
    trs = h.xpath("//table//tr")
    if not trs:
        return None

    # Mirror parse_rfile EXACTLY: column-header rows are built entirely of <th>
    # tags; data rows carry a <td> label cell (and some filers a spacer <td
    # class="th">). Classifying by TAG — not the class string 'th' — is what
    # keeps a spacer-bearing data row out of the header band (KEY/ZION), so this
    # metadata stays aligned to the value columns parse_rfile emits.
    header_rows = []
    for tr in trs:
        cells = tr.xpath("./th|./td")
        if cells and all(c.tag == "th" for c in cells):
            header_rows.append(tr)
    if not header_rows:
        return None

    def expand(cells):
        out = []
        for c in cells:
            txt = " ".join(c.text_content().split())
            out += [txt] * max(1, int(c.get("colspan") or 1))
        return out

    # The date header row and any duration band are pure-<th> rows. Drop the
    # leading title ('tl') cell so its colspan doesn't shift alignment; the
    # remaining cells expand (by colspan) to one entry per value column.
    def _drop_title(cells):
        return [c for c in cells if "tl" not in (c.get("class") or "").split()]

    # Duration band: the pure-<th> row carrying "N Months Ended" / "Year Ended".
    # Absent (balance sheets, some 10-K layouts) → treat every column as point-
    # in-time / 12-month (no discrete-quarter claim is made on those).
    dur_cells = None
    for tr in header_rows:
        cells = tr.xpath("./th|./td")
        if any(_MONTHS_ENDED.search(" ".join(c.text_content().split()))
               or _YEAR_ENDED.search(" ".join(c.text_content().split()))
               for c in cells):
            dur_cells = _drop_title(cells)
            break
    date_cells = _drop_title(header_rows[-1].xpath("./th|./td"))
    dates = expand(date_cells)

    def _dur_months(txt):
        m = _MONTHS_ENDED.search(txt)
        if m:
            return int(m.group(1))
        return 12 if _YEAR_ENDED.search(txt) else None

    if dur_cells is not None:
        durs = [_dur_months(t) for t in expand(dur_cells)]
    else:
        durs = [12] * len(dates)
    # Both bands must expand to the same width AND to ncol, or the layout isn't
    # one we can map cell-for-cell — bail (caller yields no discrete quarter).
    if not (len(durs) == len(dates) == ncol):
        return None
    return [(durs[i], dates[i]) for i in range(ncol)]


def _discrete_quarter_index(meta: list, q_end: tuple) -> int | None:
    """Index of the unique value column that is BOTH ~3 months (a single
    quarter; 2–4 mo tolerates 13-week fiscal quarters) AND ends at q_end —
    the discrete quarter, told apart from the YTD column that shares the same
    period-end. None (→ n/a) if no such column, or if it's ambiguous."""
    hits = [i for i, (d, p) in enumerate(meta)
            if d is not None and 2 <= d <= 4 and _period_key(p) == q_end]
    return hits[0] if len(hits) == 1 else None


def _recent_10q_metas(cik, n: int) -> list:
    """Up to n most-recent 10-Q filings {accession, doc, date, cik}, newest
    first — parallels _recent_10k_metas, filtering form == '10-Q'."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form == "10-Q":
            out.append({"accession": rec["accessionNumber"][i].replace("-", ""),
                        "doc": rec["primaryDocument"][i],
                        "date": rec["filingDate"][i], "cik": int(cik)})
            if len(out) >= n:
                break
    return out


def _column_values(stmt: dict, idx: int) -> dict:
    """{norm_key: value} for one column index of a parsed statement (data rows
    only) — the per-period slice used to stitch and to difference FY − 9M."""
    return {(_norm_label(r["label"]), r["header"]):
            (r["values"][idx] if idx < len(r["values"]) else None)
            for r in stmt["rows"] if not r["header"]}


def _q_label(q_end: tuple) -> str:
    """Compact column label for a quarter-end (year, month): 'Q3'25'."""
    year, mon = q_end
    return f"Q{(mon - 1) // 3 + 1}'{str(year)[2:]}"


def _minus_quarter(qe: tuple) -> tuple:
    """The (year, month) quarter-end exactly 3 months before qe — the nine-month
    YTD end that pairs with a fiscal year-end for Q4 = FY − 9M."""
    y, m = qe
    m -= 3
    while m <= 0:
        m += 12
        y -= 1
    return (y, m)


def _quarter_ends_desc(latest_q: tuple, n: int) -> list:
    """The n fiscal-quarter-ends newest-first starting at latest_q (year, month),
    stepping back 3 months each time. Generic over fiscal-year-end (the bank's
    own quarter-end month set is preserved, e.g. Mar/Jun/Sep/Dec or off-cycle)."""
    out, (y, m) = [], latest_q
    for _ in range(n):
        out.append((y, m))
        m -= 3
        while m <= 0:
            m += 12
            y -= 1
    return out


def _assemble(parsed: list, col: dict, periods: list) -> dict | None:
    """Build the stitched statement dict from a period→{key:value} map, using
    _merge_row_order for the union label order (newest filing's display label)."""
    have = [p for p in periods if p in col]
    if not have:
        return None
    rows = []
    for key, label, header in _merge_row_order(parsed):
        if header:
            rows.append({"label": label, "header": True, "values": []})
        else:
            rows.append({"label": label, "header": False,
                         "values": [col.get(p, {}).get(key) for p in periods]})
    return _consolidate_variants(
        {"periods": [_q_label(p) for p in periods], "rows": rows,
         "units_scale": parsed[0]["units_scale"]})


def _stitch_balance_quarters(parsed_q: list, parsed_k: list, q_ends: list) -> dict | None:
    """Point-in-time balance-sheet stitch over quarter-ends (newest first). Each
    10-Q balance column is a quarter-end snapshot; the 10-K supplies year-end
    (Q4) columns. No differencing. Each (line, q-end) cell is taken from the
    newest filing reporting that exact quarter-end; blank where a line wasn't
    broken out — the company's own absence, never a guess."""
    sources = parsed_q + parsed_k        # 10-Qs (newest first) then 10-Ks
    col: dict = {}                       # q_end -> {norm_key: value}
    for qe in q_ends:
        for f in sources:
            mc = f["_colmeta"]
            # Balance columns are point-in-time: pick the column whose period-end
            # matches this quarter-end (balance R-files carry date-only headers).
            idx = next((i for i, (_, p) in enumerate(mc)
                        if _period_key(p) == qe), None)
            if idx is not None:
                col[qe] = _column_values(f, idx)
                break
    return _assemble(sources, col, q_ends)


def _stitch_flow_quarters(parsed_q: list, parsed_k: list, q_ends: list) -> dict | None:
    """Discrete-quarter stitch for a FLOW statement (income / cash flow). Q1–Q3
    are the "three months ended" column lifted straight from each 10-Q (no math,
    per A21). Q4 = FY (10-K 12-month column) − 9M (that fiscal year's Q3 10-Q
    nine-month YTD column), both ending at the same fiscal year-end; emitted only
    when BOTH are present, else that quarter is omitted (blank, never guessed).
    The differencing never crosses a fiscal year and never uses a single-quarter
    column on either side."""
    q_by_qend: dict = {}                 # q_end -> (parsed, discrete_idx)
    nine_by_end: dict = {}               # nine-month-END (year, month) -> (parsed, idx)
    for f in parsed_q:
        mc = f["_colmeta"]
        # This filing's own quarter-end = the latest period-end carrying a
        # ~3-month duration column.
        cand = sorted({_period_key(p) for d, p in mc
                       if d is not None and 2 <= d <= 4 and _period_key(p)},
                      reverse=True)
        if cand:
            qe = cand[0]
            di = _discrete_quarter_index(mc, qe)
            if di is not None and qe not in q_by_qend:
                q_by_qend[qe] = (f, di)
        # A nine-month column (Q3 10-Q) anchors Q4 = FY − 9M: keyed by its own
        # period-end, which is exactly the fiscal year-end minus one quarter.
        for i, (d, p) in enumerate(mc):
            if d == 9 and _period_key(p):
                nine_by_end.setdefault(_period_key(p), (f, i))
    k_by_fy: dict = {}                   # fiscal-year-end (year, month) -> (parsed, 12mo_idx)
    for f in parsed_k:
        mc = f["_colmeta"]
        for i, (d, p) in enumerate(mc):
            pk = _period_key(p)
            if d == 12 and pk:
                k_by_fy.setdefault(pk, (f, i))

    sources = parsed_q + parsed_k
    col: dict = {}
    for qe in q_ends:
        if qe in q_by_qend:
            f, di = q_by_qend[qe]
            col[qe] = _column_values(f, di)      # discrete quarter, as reported
            continue
        # No 10-Q for this quarter: it is a fiscal year-end (Q4) iff a 10-K
        # reports a 12-month column ending here. Derive Q4 = FY − 9M, where the
        # 9-month YTD ends exactly one quarter (3 months) earlier — same fiscal
        # year, so the difference never crosses a year boundary.
        nine_end = _minus_quarter(qe)
        if qe in k_by_fy and nine_end in nine_by_end:
            fk, ik = k_by_fy[qe]
            fq, iq = nine_by_end[nine_end]
            fy = _column_values(fk, ik)
            nine = _column_values(fq, iq)
            diff = {}
            for k in set(fy) | set(nine):
                a, b = fy.get(k), nine.get(k)
                diff[k] = (a - b) if (a is not None and b is not None) else None
            col[qe] = diff
        # else: omit (blank) — cannot derive a clean discrete quarter.
    return _assemble(sources, col, q_ends)


def as_reported_statement_multiquarter(cik, stype: str = "income",
                                       n_quarters: int = 12) -> dict | None:
    """Cached multi-QUARTER Company-Reported statement (stype = "income" |
    "balance" | "cashflow") stitched from the bank's recent 10-Qs (+ 10-Ks for
    the Q4/year-end column), reaching back n_quarters. Discrete single quarters
    only (audit A21): Q1–Q3 are each 10-Q's "three months ended" column; Q4 =
    FY 10-K − nine-month 10-Q; a quarter that can't be cleanly derived is blank,
    never guessed. Balance sheets are point-in-time quarter-end snapshots. Cached
    by the latest 10-Q accession; a transient fetch failure is never cached.
    Returns {"meta", "filings", "statement"} or None."""
    if not cik:
        return None
    from data import cache
    # 12 quarters ≈ 3 years: ~7 recent 10-Qs (3-month + YTD columns) plus
    # ~3 recent 10-Ks (the FY/year-end columns and Q4 derivation).
    q_metas = _recent_10q_metas(cik, 7)
    k_metas = _recent_10k_metas(cik, 3)
    if not q_metas:
        return None
    ckey = f"asreported_mq:v2:{stype}:{q_metas[0]['accession']}:{n_quarters}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached or None

    def _parse_with_meta(metas):
        out = []
        for m in metas:
            base = _filing_base(m["cik"], m["accession"])
            fn = _statement_rfiles(base).get(stype)
            if not fn:
                continue
            raw = _get(base + fn)
            if stype == "income":
                stmt = _income_parse(raw)
            elif stype == "balance":
                stmt = _balance_parse(raw)
            else:
                stmt = parse_rfile(raw)
            if not (stmt and stmt["rows"]):
                continue
            ncol = max((len(r["values"]) for r in stmt["rows"]
                        if not r["header"]), default=0)
            meta = _column_meta(raw, ncol) if ncol else None
            if meta is None:
                continue                         # unrecognized layout → skip filing
            stmt["_colmeta"] = meta
            stmt["_meta"] = m
            out.append(stmt)
        return out

    try:
        parsed_q = _parse_with_meta(q_metas)
        parsed_k = _parse_with_meta(k_metas)
    except Exception as e:
        print(f"[sec_statements] multiquarter {stype} failed for cik {cik}: "
              f"{type(e).__name__}: {e}")
        return None                              # transient — don't cache
    if not parsed_q:
        return None

    # Newest quarter-end anchors the 12-quarter window. For flow statements it's
    # the most recent ~3-month (discrete-quarter) column; for the point-in-time
    # balance sheet there is no duration band, so it's the most recent period-end
    # any 10-Q reports (the filing's own quarter-end snapshot).
    latest = None
    for f in parsed_q:
        for d, p in f["_colmeta"]:
            pk = _period_key(p)
            if not pk:
                continue
            if stype != "balance" and not (d is not None and 2 <= d <= 4):
                continue
            if latest is None or pk > latest:
                latest = pk
    if latest is None:
        return None
    q_ends = _quarter_ends_desc(latest, n_quarters)

    if stype == "balance":
        stitched = _stitch_balance_quarters(parsed_q, parsed_k, q_ends)
    else:
        stitched = _stitch_flow_quarters(parsed_q, parsed_k, q_ends)
    if not stitched:
        return None
    used = parsed_q + parsed_k
    result = {"meta": q_metas[0], "filings": [f["_meta"] for f in used],
              "statement": stitched}
    try:
        cache.put(ckey, result)
    except Exception:
        pass
    return result


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
    ckey = f"asreported_my:v6:{stype}:{metas[0]['accession']}:{n_years}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached or None
    parsed = []
    for m in metas:
        base = _filing_base(m["cik"], m["accession"])
        try:
            fn = _rfile_for(base, stype)
            if not fn:
                stmt = None
            elif stype == "income":
                stmt = _income_parse(_get(base + fn))   # strip OCI / guard non-income
            elif stype == "balance":
                stmt = _balance_parse(_get(base + fn))  # isolate primary balance table
            else:
                stmt = parse_rfile(_get(base + fn))
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
