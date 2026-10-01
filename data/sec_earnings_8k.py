"""Latest-quarter headline figures from a bank's earnings 8-K (EX-99.1).

THE TIMELINESS LAYER. A bank files its quarterly earnings press release /
financial supplement as Exhibit 99.1 of an Item-2.02 8-K ~4 weeks BEFORE the
10-Q lands, so this is the only primary-source view of the most-recent quarter
until the 10-Q is filed. It is deliberately the *least* trusted source on the
platform: EX-99.1 is free-form HTML (NOT XBRL), per-bank layout, and frequently
mixes GAAP, non-GAAP, segment and reconciliation rows under near-identical
labels. So this module is built to the cardinal rule — **a wrong number scraped
from a press release is worse than no number** — and renders n/a for anything it
cannot match AND sanity-check.

What makes it safe enough to ship (feasibility-gated over ABCB/PNFP/FFIN/CBSH/
FHN/WAL/ONB/FITB/RF/KEY, docs/COMPANY-REPORTED-PLAN.md §6):

  1. EX-99.1 is located deterministically from the filing index's exhibit-TYPE
     table (the `EX-99.1` row), never by guessing the filename — 10/10 located.
  2. Every figure is an EXACT label match (case-insensitive, footnote marks
     stripped) against the press-release tables, taking the FIRST numeric column
     — releases lead every table with the most-recent quarter. Ambiguous bare
     labels that collide with a different line (e.g. "Diluted" = share COUNT, not
     EPS) are excluded by requiring an explicit per-share label.
  3. The dollar SCALE (thousands vs millions) is detected ONCE per release by
     anchoring total assets / deposits to the prior 10-Q's tagged value, then
     applied to every dollar figure for internal consistency. If neither anchor
     resolves a scale, NO dollar figure is emitted.
  4. Each figure passes a gate or is dropped: balance-sheet totals must land
     within a band of the prior 10-Q (this REJECTS a segment subtotal grabbed by
     a first-match, e.g. KEY's $37B Consumer-Bank "Total assets" vs $189B
     consolidated); ratios must be 0–60(%); EPS |x|<100; net income / NII must be
     positive after scaling.

Everything carries `_preliminary: True`. The UI must label it as as-released and
must NEVER overwrite an audited 10-K/10-Q figure with it.

Per-share BOOK VALUE (reported_tbvps_status / reported_bvps_status) also reads
the same 8-K's supplementary exhibits (EX-99.2+, again located by index TYPE)
when EX-99.1 discloses nothing — period-verified table rows only (see
_supplement_rows). The headline figures above stay EX-99.1-only.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from data.sec_filing_scraper import (
    _get, latest_filing, instance_facts, _undimensioned_total,
)


# ── Locate the latest earnings 8-K + its EX-99.1 exhibit ─────────────────────
_LATEST_8K_TTL_S = 2 * 3600


def _latest_earnings_8k(cik) -> dict | None:
    """Most-recent earnings 8-K: {accession, accession_dash, date, cik} or None.
    Item 2.02 (Results of Operations) is the earnings item, trusted outright. A
    NEWER 8-K without 2.02 is taken only when its EX-99.1 proves itself the
    release for a newer quarter (_is_misitemized_release) — FBP and NPB
    furnished their Q2-2026 releases under Items 2.01/9.01 and 2.01/7.01, so
    a 2.02-only finder landed on their Q1 8-Ks (found 2026-10-01). A furnished
    deck or dividend notice never qualifies: the item code alone decides nothing.

    The result (a no-8-K bank included) is cached ~2h: this runs per bank on
    every metrics build, and the uncached submissions fetch × ~440 SEC filers
    was the bulk of the build's EDGAR load (the 2026-07-27 refresh-home-snapshot
    timeout incident). A new release is picked up within the TTL; a transient
    fetch EXCEPTION propagates uncached, as before."""
    return _submissions_record(cik).get("f8k")


def latest_periodic_filing(cik) -> dict | None:
    """Most-recent 10-Q/10-K from the SAME cached submissions record:
    {form, date (filed), report_date (period end)} or None. Lets the valuation
    layer tell when SEC's XBRL API (companyfacts) lags a filing the bank has
    already made — ONB/FRME/HBAN/CCBG's Q2-2026 10-Qs sat un-ingested for two
    months (found 2026-09-22) while every HoldCo book value rendered as
    current. Zero added fetches: the tbvps path already loads this record."""
    return _submissions_record(cik).get("periodic")


def _submissions_record(cik) -> dict:
    """{f8k, periodic} for a CIK, cached ~2h (see _latest_earnings_8k)."""
    from data import cache
    from data.ir_provider import _FURNISH_ITEMS
    # v2: f8k may be a mis-itemized release newer than the latest 2.02 (FBP/NPB).
    ckey = f"earnings_8k_latest:v2:{int(cik)}"
    hit = cache.get(ckey, max_age_s=_LATEST_8K_TTL_S)
    if hit is not None and "periodic" in hit:
        return hit
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    items = rec.get("items", [])
    accs = rec.get("accessionNumber", [])
    dates = rec.get("filingDate", [])
    rdates = rec.get("reportDate", [])
    periodic = None
    for i, form in enumerate(forms):
        if form in ("10-Q", "10-K"):
            periodic = {"form": form,
                        "date": dates[i] if i < len(dates) else "",
                        "report_date": rdates[i] if i < len(rdates) else ""}
            break
    f8k = None
    furnished = []                 # non-2.02 8-Ks newer than f8k, newest first
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        item_str = items[i] if i < len(items) else ""
        acc_dash = accs[i] if i < len(accs) else ""
        if not acc_dash:
            continue
        row = {"accession_dash": acc_dash, "accession": acc_dash.replace("-", ""),
               "date": dates[i] if i < len(dates) else "", "cik": int(cik)}
        if "2.02" in item_str:
            f8k = row
            break
        present = {s.strip() for s in item_str.replace(";", ",").split(",")}
        if present & _FURNISH_ITEMS:
            furnished.append(row)
    # A mis-itemized release can only supersede the 2.02 8-K when it reports a
    # NEWER quarter; candidates are newest-first, so the first one at or
    # before the 2.02's quarter ends the scan. Each check is accession-cached.
    floor = _release_quarter_end(f8k["date"]) if f8k else None
    for row in furnished[:_MISITEMIZED_MAX_CHECKS]:
        qe = _release_quarter_end(row["date"])
        if qe is None or (floor is not None and qe <= floor):
            break
        try:
            if _is_misitemized_release(row):
                f8k = row
                break
        except Exception as e:
            # Unverifiable: a newer release may exist, so the older 2.02 8-K
            # must not stand in for it. No 8-K this call, and nothing cached.
            print(f"[sec_earnings_8k] release check failed for cik {cik} "
                  f"{row['accession_dash']}: {type(e).__name__}: {e}")
            return {"f8k": None, "periodic": periodic}
    record = {"f8k": f8k, "periodic": periodic}
    try:
        cache.put(ckey, record)
    except Exception:
        pass
    return record


# Non-2.02 8-Ks checked per scan — bounds a bank with no 2.02 8-K at all.
_MISITEMIZED_MAX_CHECKS = 4


def _is_misitemized_release(f8k: dict) -> bool:
    """True when a non-2.02 8-K's EX-99.1 is demonstrably the earnings release
    for the quarter its filing date implies: the exhibit's opening text passes
    the earnings-headline gate (data.ir_provider._is_earnings_headline — ASB's
    "Announces Results of Annual Meeting" and Bank OZK's "Announces Date for
    … Earnings Release" fail it) AND the FIRST period that text names is that
    quarter-end ("FIRST BANCORP. ANNOUNCES EARNINGS FOR THE QUARTER ENDED JUNE
    30, 2026", filed 2026-07-22). A text that names no period, or opens on a
    different one, is not proven — False. Cached forever by accession (the
    filing is immutable); a fetch EXCEPTION propagates uncached."""
    from data import cache
    from data.ir_provider import _headline_text, _is_earnings_headline
    ckey = f"earnings_8k_misitemized:v1:{f8k['accession']}"
    hit = cache.get(ckey, max_age_s=None)
    if hit is not None:
        return bool(hit.get("ok"))
    ok = False
    doc = _ex991_document(f8k["cik"], f8k["accession_dash"])
    if doc:
        html = _get(f"https://www.sec.gov/Archives/edgar/data/{int(f8k['cik'])}/"
                    f"{f8k['accession']}/{doc}")
        text = _headline_text(html.decode("utf-8", "replace"))
        periods = _periods(text)
        ok = (_is_earnings_headline(text) and bool(periods)
              and periods[0] == _release_quarter_end(f8k["date"]))
    try:
        cache.put(ckey, {"ok": ok})
    except Exception:
        pass
    return ok


_EX99_TYPE = re.compile(r"EX-99\.(\d+)")


def _ex99_documents(cik, accession_dash) -> list[tuple]:
    """[(n, filename)] of the filing's EX-99.n exhibits, ascending n, read from
    the filing index's exhibit-type table (the authoritative SEC-declared
    type), never guessed from the name."""
    from lxml import html as lhtml
    acc = accession_dash.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
           f"{accession_dash}-index.htm")
    root = lhtml.fromstring(_get(url))
    out: list[tuple] = []
    for table in root.findall(".//table"):
        hdr = [th.text_content().strip().lower() for th in table.findall(".//th")]
        if "type" not in hdr or "document" not in hdr:
            continue
        ti, di = hdr.index("type"), hdr.index("document")
        for tr in table.findall(".//tr"):
            cells = tr.findall("td")
            if len(cells) <= max(ti, di):
                continue
            m = _EX99_TYPE.fullmatch(cells[ti].text_content().strip().upper())
            if not m:
                continue
            a = cells[di].find(".//a")
            doc = (a.text_content().strip() if a is not None
                   else cells[di].text_content().strip())
            if doc.split("/")[-1]:
                out.append((int(m.group(1)), doc.split("/")[-1]))
    return sorted(out, key=lambda t: t[0])   # stable: index order within n


def _ex991_document(cik, accession_dash) -> str | None:
    """Filename of the EX-99.1 exhibit (see _ex99_documents), or None."""
    return next((d for n, d in _ex99_documents(cik, accession_dash) if n == 1),
                None)


# ── Parse the press-release tables ───────────────────────────────────────────
def _num(s: str):
    """One press-release cell → float, handling accounting parens, $, %, commas.
    None if the cell isn't a number."""
    s = s.strip().replace("\xa0", " ").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") or s.endswith(")")
    body = s.strip("()").replace(",", "").replace("$", "").replace("%", "")
    if not re.match(r"^-?\d", body):
        return None
    try:
        v = float(body)
    except ValueError:
        return None
    return -v if neg else v


# Trailing footnote markers to strip: superscript digits, asterisks, stray
# quote/bullet chars — but NOT a balanced parenthetical qualifier like "(TE)"
# or "(FTE)", which is part of the label and disambiguates it.
_LABEL_TRAIL = re.compile(r"[\*\d“”\"’'·•]+$")


def _clean_label(s: str) -> str:
    """Normalize a row label for matching: lowercase, strip trailing footnote
    marks / superscripts (keeping a balanced '(TE)'-style qualifier), collapse
    whitespace."""
    s = s.strip().lower().replace("\xa0", " ")
    # Typographic quotes → ASCII, so one label spelling matches both
    # ("shareholders’ equity" — BANR, HBCP; '(“tce”)' — HOPE).
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # Strip a trailing footnote run, but only when it's NOT closing a paren group
    # (so "margin (te)" keeps its ")"; "diluted shares8" loses its "8").
    if not s.endswith(")"):
        s = _LABEL_TRAIL.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def _table_rows(html_bytes: bytes, year_spans: bool = False) -> list[tuple]:
    """Every (clean_label, [numeric cells…]) row across the document's tables, in
    document order. A row needs a non-numeric label and ≥1 numeric cell.

    COLUMN ALIGNMENT (audit P3): positions are preserved within each table. The
    value list is read from the table's VALUE COLUMNS — the (colspan-expanded)
    td columns that carry a number in at least one row — so a blank cell in a
    value column stays None instead of letting the next (prior-period) column
    shift left into its place. Consumers take nums[0] as the latest quarter, and
    a blank latest-quarter cell must render n/a, never a prior period's figure.
    Columns that never carry a number anywhere in the table (spacers, '$'/'%'
    decoration, footnote refs) are dropped for every row alike, exactly as the
    old cell-filtering did.

    Per-row decoration (FRME 2026-Q2): some releases share ONE physical column
    between ratio values ('9.8' on %-rows) and dollar markers ('$' on $-rows),
    so the column IS a value column table-wide and a $-row reads
    [None, 29.8, …] — the P3 guard then misread the '$' cell as a blank latest
    quarter and dropped a cleanly disclosed figure. A cell whose entire text is
    a bare '$'/'%' is decoration for THAT row and is skipped; a truly blank
    cell is empty text, still lands as None, and the guard keeps working."""
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)
    rows: list[tuple] = []
    for table in root.findall(".//table"):
        grid, spans = _table_grid(table)
        rows.extend((cl, nums) for _, cl, nums, _
                    in _grid_rows(grid, spans if year_spans else None))
    return rows


def _table_grid(table) -> tuple[list, list]:
    """(grid, spans) for one <table>. grid is the colspan-expanded text grid
    (cell index == table column on every row); spans[r] lists row r's cells
    as (first_col, last_col, text) — which header cell COVERS a column."""
    grid: list[list[str]] = []
    spans: list[list[tuple]] = []
    for tr in table.findall(".//tr"):
        row: list[str] = []
        cells: list[tuple] = []
        for c in tr.findall(".//td"):
            txt = c.text_content().strip().replace("\xa0", " ")
            try:
                span = max(1, int(c.get("colspan") or 1))
            except (TypeError, ValueError):
                span = 1
            cells.append((len(row), len(row) + span - 1, txt))
            row.append(txt)
            row.extend([""] * (span - 1))
        grid.append(row)
        spans.append(cells)
    return grid, spans


def _grid_rows(grid: list, spans: list | None = None) -> list[tuple]:
    """(row_index, clean_label, nums, cols) for every data row of one table
    grid — _table_rows' per-table body; cols[i] is the table column nums[i]
    was read from. A value column is any column with a numeric cell in ANY
    row — unless `spans` is given (the book-value path, _book_value_rows):

    then a value column is any column with a numeric cell in a DATA row. A bare
    header year is not data, but it is kept as a column when it heads one no
    data row fills — an all-blank latest-quarter column must still read None
    (audit P3). A year whose colspan COVERS a data column heads that column
    instead: BHB Q2-2026 (8-K 0001104659-26-085415) centres "2026" over a
    colspan-2 cell whose first column is the '$' column, so that '$' column
    became the first "value" column and every row read [None, 23.43, …] —
    the whole table, TBVPS included, rendered n/a.

    NOT applied to the headline figures (extract_earnings_figures): the same
    fix there exposed rows its single release-wide scale and first-match
    gates cannot vet — $-thousands flows scaled by a $-millions summary
    table (BHB NII read as $37.9B, UCB/CSBB net income ×1000), average or
    six-month columns inside the ±band (FCAP deposits −20%, PBHC NII +98%)
    — measured over the 2026-09-30 sweep, 2026-10-01. Per-share book values
    are scale-free and gated against the reconstruction / tangible < book."""
    out: list[tuple] = []
    headers = ({r for r, row in enumerate(grid) if _is_year_header(row)}
               if spans is not None else set())
    num_cols = {i for r, row in enumerate(grid) if r not in headers
                for i, c in enumerate(row) if _num(c) is not None}
    num_cols |= {a for r in headers for a, b, t in (spans or [])[r]
                 if _num(t) is not None
                 and not any(a <= i <= b for i in num_cols)}
    num_cols = sorted(num_cols)
    for r, row in enumerate(grid):
        label_idx = next((i for i, c in enumerate(row) if c.strip()), None)
        if label_idx is None:
            continue
        label = row[label_idx]
        if _num(label) is not None:        # a number, not a label
            continue
        cl = _clean_label(label)
        if not cl:
            continue
        nums, cols = [], []
        for i in num_cols:
            if i <= label_idx:
                continue
            if i >= len(row):
                nums.append(None)
                cols.append(i)
                continue
            if row[i].strip() in ("$", "%"):   # this row's decoration cell
                continue
            nums.append(_num(row[i]))
            cols.append(i)
        if not any(n is not None for n in nums):
            continue
        out.append((r, cl, nums, cols))
    return out


# ── Supplementary exhibits (EX-99.2+): period-verified table rows only ───────
# Some banks print book value per share only in ANOTHER exhibit of the same
# earnings 8-K — RBCAA's EX-99.2 financial tables, FCNCA's EX-99.3 financial
# supplement (2026-09-30 sweep). The first-column-is-the-latest-quarter
# assumption that the EX-99.1 reader leans on does NOT carry over: investor
# decks commonly run oldest → newest, and a year-old TBVPS clears the ±15%
# reconstruction band. So a supplementary-exhibit row is admitted only when
# the header over ITS latest-quarter column names the release quarter-end.
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_ORDINAL_Q = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_PERIOD_TOKEN = re.compile(
    # "June 30, 2026" / "Jun. 30, 2026" / "June 30,2026" (a <br> joins cells)
    r"(?<![a-z])(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"\s*\d{1,2},?\s*(\d{4})(?!\d)"
    # "6/30/2026" / "6/30/26"
    r"|(?<![\d/])(\d{1,2})/\d{1,2}/(\d{4}|\d{2})(?![\d/])"
    # "2Q26" / "2Q 2026" / "2Q'26"
    r"|(?<![a-z0-9])([1-4])q\s?'?(\d{4}|\d{2})(?!\d)"
    # "Q2 2026" / "Q2'26"
    r"|(?<![a-z0-9])q([1-4])\s?'?(\d{4}|\d{2})(?!\d)"
    # "Second Quarter 2026" / "second quarter of 2026"
    r"|(first|second|third|fourth) quarter,?\s+(?:of\s+)?(\d{4})(?!\d)")


def _periods(text: str) -> list[tuple]:
    """Every (year, month) period a header text names, in order — month-day-
    year dates and quarter labels (a quarter maps to its calendar quarter-end
    month). A bare year names no period: it can't place a quarter."""
    out = []
    for m in _PERIOD_TOKEN.finditer(text.lower()):
        g = m.groups()
        if g[0]:
            y, mo = g[1], _MONTHS[g[0]]
        elif g[2]:
            y, mo = g[3], int(g[2])
        elif g[4] or g[6]:
            y, mo = (g[5], int(g[4]) * 3) if g[4] else (g[7], int(g[6]) * 3)
        else:
            y, mo = g[9], _ORDINAL_Q[g[8]] * 3
        out.append((int(y) + (2000 if len(y) == 2 else 0), mo))
    return out


def _release_quarter_end(filed: str) -> tuple | None:
    """(year, month) of the latest calendar quarter-end strictly before the
    8-K's filing date — the quarter an Item-2.02 release reports (filed
    2026-07-24 → (2026, 6); filed ON a quarter-end, 2026-06-30 → (2026, 3):
    a release can't report a quarter that ends the day it is filed). None
    for an unparseable date."""
    try:
        y, m, _ = (int(p) for p in filed.split("-"))
    except (AttributeError, ValueError):
        return None
    qe_month = 3 * ((m - 1) // 3)            # last quarter-end month before m
    return (y - 1, 12) if qe_month == 0 else (y, qe_month)


def _column_periods(grid: list, spans: list, data_rows: set,
                    r: int, col: int) -> list[tuple]:
    """Periods named over column `col` for data row `r`: the nearest block of
    contiguous NON-data rows above r whose cells covering `col` name any
    period (joined in document order, so a "June 30," row over a "2026" row
    reads "June 30, 2026"). Nearest, so a mid-table re-header ("Year ended
    December 31, …") governs the rows beneath it, not the table's top."""
    block: list[str] = []
    for k in range(r - 1, -1, -1):
        if k in data_rows:
            if block and _periods(" ".join(block)):
                break
            block = []
            continue
        txt = next((t for a, b, t in spans[k] if a <= col <= b), "")
        block.insert(0, txt)
    return _periods(" ".join(block)) if block else []


_YEAR_CELL = re.compile(r"(?:19|20)\d{2}")


def _is_year_header(row: list) -> bool:
    """True for a header line whose only numeric cells are bare years
    ("(Dollars in millions…) | 2026 | 2026 | 2025", TFC; CFG's "2Q26 … 2026
    2025"). _grid_rows emits it as a data row, which would cut the header
    block off above it ("June 30" without its "2026")."""
    nums = [c.strip() for c in row if _num(c) is not None]
    return bool(nums) and all(_YEAR_CELL.fullmatch(c) for c in nums)


def _supplement_rows(html_bytes: bytes, period_end: tuple) -> list[tuple]:
    """(clean_label, nums) table rows of a SUPPLEMENTARY exhibit whose
    latest-quarter column (nums[0]'s column) is headed by `period_end` — every
    named period over it equal to the release quarter-end. Other rows are
    DROPPED (not kept as n/a): they are not known to be the current quarter,
    so they may not decide; a verified row carries the same exact label. No
    text-layer rows — flat page text has no columns to verify."""
    from lxml import html as lhtml
    rows: list[tuple] = []
    for table in lhtml.fromstring(html_bytes).findall(".//table"):
        grid, spans = _table_grid(table)
        drows = _grid_rows(grid)
        data_rows = {r for r, *_ in drows if not _is_year_header(grid[r])}
        for r, cl, nums, cols in drows:
            periods = _column_periods(grid, spans, data_rows, r, cols[0])
            if periods and all(p == period_end for p in periods):
                rows.append((cl, nums))
    return rows


# ── Table-less releases: the page text layer ─────────────────────────────────
# Some EX-99.1s have NO <table> at all: the release is filed as page images
# (Workiva "print to image" — one <img> per page) with the page's text in a
# hidden 1pt white-font layer beneath each image. AMAL Q2-2026 (8-K
# 0001823608-26-000177): 18 page JPGs, zero tables, and the text layer reads
# "… 1.75  Book value per common share $ 27.93 $ 27.05 $ 24.79 $ 27.93 $ 24.79
# Tangible book value per share (non-GAAP) $ 27.47 …" — one flat string per
# page. _table_rows found nothing, so a cleanly printed TBVPS read as
# not-disclosed. Same shape in the 2026-09-30 universe sweep: ACNB, BAC, CBC,
# EGBN, FGBI, GABC, NEWT (8 of 320 releases).
_BLOCK_TAGS = frozenset({"div", "p", "font", "pre", "span"})
# A short parenthesized token right after a label is a footnote ref ("(1)",
# "(a)"), not a negative one — label-side, like it is inside a table cell.
_FOOTNOTE_TOKEN = re.compile(r"^\([a-z0-9]{1,3}\)$", re.I)
# Rows sharing one value count before a block counts as tabular.
_MIN_ALIGNED_ROWS = 3


def _text_layer_rows(root) -> list[tuple]:
    """(clean_label, nums) rows, in _table_rows' shape, from the leaf text
    blocks of a table-less document. A row is a run of label words followed
    by a run of numbers ('$'/'%' decoration tokens skipped).

    COLUMN ALIGNMENT (audit P3) cannot be read from cell positions here: a
    blank cell simply vanishes from flat text, so a missing latest quarter
    would shift the prior period into nums[0]. The alignment evidence is the
    block itself: a statement page prints every row with the SAME number of
    period columns, so a row is trusted only when its value count equals the
    block's modal count AND at least _MIN_ALIGNED_ROWS rows share that count.
    Any other row keeps its label with nums = [None] — the same "blank latest
    cell → n/a" outcome a table gives, and it still wins first-match, so a
    later clean-looking occurrence can never stand in for it."""
    rows: list[tuple] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.lower() not in _BLOCK_TAGS:
            continue
        if any(isinstance(d.tag, str) and d.tag.lower() in _BLOCK_TAGS
               for d in el.iterdescendants()):
            continue                       # not a leaf block
        block: list[tuple] = []
        label: list[str] = []
        nums: list = []
        for tok in el.text_content().replace("\xa0", " ").split():
            if tok in ("$", "%"):
                continue
            v = None if (not nums and _FOOTNOTE_TOKEN.match(tok)) else _num(tok)
            if v is None:
                if nums:
                    block.append((label, nums))
                    label, nums = [], []
                label.append(tok)
            else:
                nums.append(v)
        if nums:
            block.append((label, nums))
        counts = Counter(len(n) for _, n in block)
        modal, freq = counts.most_common(1)[0] if counts else (0, 0)
        for label, nums in block:
            cl = _clean_label(" ".join(label))
            if not cl:
                continue
            aligned = freq >= _MIN_ALIGNED_ROWS and len(nums) == modal
            rows.append((cl, nums if aligned else [None]))
    return rows


# ── Table-less releases: absolutely-positioned text fragments ────────────────
# Another table-less shape (2026-09-30 sweep: FBP, USCB, CCBG): every word run
# is its own <div style="position:absolute; left:…px; top:…px">, one page per
# position:relative container. FBP Q1-2026 (8-K 0001057706-26-000010) prints
# "Tangible book value per share" + "(1)" + "$" "12.45" "$" "12.29" "$" "10.64"
# as separate divs at top≈422px; _text_layer_rows sees each div as its own
# block and supplies nothing. The row and its columns are rebuilt from the
# coordinates instead.
_POS_PX = re.compile(r"(left|top|font-size)\s*:\s*(-?[\d.]+)px", re.I)
_ABSOLUTE = re.compile(r"position\s*:\s*absolute", re.I)
# Fragments within this many px of a line's first top share the line.
_LINE_TOL_PX = 2.0
# Right edges within this many px of each other belong to one column.
_COL_TOL_PX = 8.0
# A numeric fragment set this much smaller than the page's value font is a
# superscript footnote marker, not a value.
_SUPERSCRIPT_RATIO = 0.75
# Approximate advance widths (em) of the glyphs in a numeric cell (Times-like
# serif), used to find a right-aligned number's right edge from its left.
_GLYPH_EM = {".": 0.25, ",": 0.25, "(": 0.333, ")": 0.333, "-": 0.333,
             "%": 0.833, " ": 0.25}
_DASHES = frozenset({"-", "–", "—"})


def _positioned_rows(root) -> list[tuple]:
    """(clean_label, nums) rows, in _table_rows' shape, from absolutely
    positioned text fragments. Per page: fragments within _LINE_TOL_PX of a
    line's first top form a line; left-to-right, a line reads as runs of
    (label, values) — the label is the LAST text fragment before the values
    (a two-column page puts body prose to the left of the figures on the same
    line), '$'/'%'/dash cells and footnote markers are skipped.

    COLUMN ALIGNMENT (audit P3): a page's value columns are the clusters of
    value right edges (numbers are right-aligned; right = left + estimated
    glyph width) that at least _MIN_ALIGNED_ROWS rows populate. A row's nums
    are its value in each such column right of its label — a blank cell stays
    None, so a missing latest quarter can never pull the prior period into
    nums[0]. A row with a value outside a supported column, or two values in
    one column, is unaligned: nums = [None] (it still wins first-match → n/a)."""
    pages: dict = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or not _ABSOLUTE.search(el.get("style") or ""):
            continue
        if any(isinstance(d.tag, str) and _ABSOLUTE.search(d.get("style") or "")
               for d in el.iterdescendants()):
            continue                       # only the innermost positioned text
        txt = " ".join(el.text_content().replace("\xa0", " ").split())
        pos = {k.lower(): float(v) for k, v in _POS_PX.findall(el.get("style"))}
        if not txt or "left" not in pos or "top" not in pos:
            continue
        pages.setdefault(el.getparent(), []).append(
            (pos["top"], pos["left"], pos.get("font-size", 10.0), txt))
    rows: list[tuple] = []
    for frags in pages.values():
        num_fonts = sorted(f for _, _, f, t in frags if _num(t) is not None)
        body_font = num_fonts[len(num_fonts) // 2] if num_fonts else 0.0
        lines: list[list] = []
        for fr in sorted(frags):
            if lines and fr[0] - lines[-1][0][0] <= _LINE_TOL_PX:
                lines[-1].append(fr)
            else:
                lines.append([fr])
        # records: [label, label_left, [(value, right_edge)], limit_left]
        records: list[list] = []
        for line in lines:
            line_recs: list[list] = []
            for _, left, font, txt in sorted(line, key=lambda f: f[1]):
                if txt in ("$", "%") or txt in _DASHES:
                    continue
                v = _num(txt)
                if v is not None and font < _SUPERSCRIPT_RATIO * body_font:
                    continue                                 # superscript mark
                if not (line_recs and line_recs[-1][2]) and _FOOTNOTE_TOKEN.match(txt):
                    continue                                 # "(1)" after a label
                if v is None:
                    line_recs.append([txt, left, [], None])
                    continue
                if not line_recs:
                    continue                                 # header / orphan number
                right = left + font * sum(_GLYPH_EM.get(c, 0.5) for c in txt)
                line_recs[-1][2].append((v, right))
            for i, rec in enumerate(line_recs):
                if i + 1 < len(line_recs):
                    rec[3] = line_recs[i + 1][1]
            records.extend(r for r in line_recs if r[2])
        # Columns: right-edge clusters populated by ≥ _MIN_ALIGNED_ROWS rows.
        edges = sorted((right, n) for n, r in enumerate(records) for _, right in r[2])
        clusters: list[list] = []
        for right, n in edges:
            if clusters and right - clusters[-1][-1][0] <= _COL_TOL_PX:
                clusters[-1].append((right, n))
            else:
                clusters.append([(right, n)])
        cols = [(c[0][0], c[-1][0]) for c in clusters
                if len({n for _, n in c}) >= _MIN_ALIGNED_ROWS]
        for label, left, vals, limit in records:
            cl = _clean_label(label)
            if not cl:
                continue
            mine = [i for i, (lo, hi) in enumerate(cols)
                    if lo > left and (limit is None or hi < limit)]
            slots: dict = {}
            for v, right in vals:
                hit = [i for i in mine if cols[i][0] <= right <= cols[i][1]]
                if len(hit) != 1 or hit[0] in slots:
                    slots = None
                    break
                slots[hit[0]] = v
            rows.append((cl, [slots.get(i) for i in mine] if slots else [None]))
    return rows


def _book_value_rows(html_bytes: bytes) -> list[tuple]:
    """Rows for the per-share BOOK VALUE extractors: the table rows, or — for a
    table-less release only — the positioned-fragment rows (FBP), else the
    page text-layer rows (AMAL). Deliberately NOT used
    for extract_earnings_figures: an image release mixes $-billion summary
    pages with $-million statements, and its single release-wide scale then
    mis-scales a first-matched flow (BAC Q2-2026: "Net income $9.1" billion
    read as $9.1M). Per-share values carry no scale, and the book-value gates
    (±15% vs the reconstruction, tangible < book) cross-check every pick."""
    rows = _table_rows(html_bytes, year_spans=True)
    if rows:
        return rows
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)
    if root.findall(".//table"):
        return rows
    return _positioned_rows(root) or _text_layer_rows(root)


# Exact label sets per figure. The FIRST row whose cleaned label is in the set
# wins, and its FIRST numeric column (latest quarter) is taken. Sets are kept
# tight to avoid grabbing a non-GAAP / segment / share-count sibling row.
_FIG_LABELS: dict[str, set] = {
    "total_assets": {"total assets"},
    "total_deposits": {"total deposits"},
    "net_income": {"net income"},
    # EPS: explicit per-share labels only — a bare "diluted" row is the diluted
    # share COUNT in many releases (ONB/FITB), so it is deliberately excluded.
    "diluted_eps": {
        "diluted earnings per share", "diluted earnings per common share",
        "net income - diluted", "net income per common share, diluted",
        "diluted eps", "earnings per diluted share",
        "diluted earnings per common share / as adjusted",
    },
    "net_interest_income": {"net interest income"},
    "nim": {
        "net interest margin", "net interest margin (te)",
        "net interest margin (tax equivalent)", "net interest margin (fte)",
        "net interest margin (gaap)", "net interest margin (nim)",
    },
    "roaa": {"return on average assets"},
    "roae": {"return on average equity", "return on average common equity"},
}

# Classes drive the per-figure sanity gate.
_DOLLAR_BS = ("total_assets", "total_deposits")          # anchored to prior 10-Q
_DOLLAR_FLOW = ("net_income", "net_interest_income")     # scaled by detected scale
_RATIO = ("nim", "roaa", "roae")                         # 0–60 (%)


def _first_match(rows: list[tuple], labels: set):
    # nums[0] is the latest-quarter COLUMN; a blank cell there is None (the row's
    # prior-period values must never shift into the current slot) → n/a upstream.
    for cl, nums in rows:
        if cl in labels:
            return nums[0]
    return None


# ── Company-Reported tangible book value per COMMON share ────────────────────
# TBVPS is a NON-GAAP, free-form line — no XBRL tag. Banks label it several ways;
# all denote per-COMMON-share tangible common equity. A "(non-GAAP)" qualifier is
# a suffix on the same figure, so it is stripped before matching (unlike the
# balanced "(TE)" qualifier that _clean_label deliberately preserves).
_TBVPS_LABELS: frozenset = frozenset({
    "tangible book value per share",
    "tangible book value per common share",
    "tangible common book value per share",
    "tangible common equity per share",
    "tangible common equity per common share",
    "tangible book value per common share outstanding",
    "tangible book value per common share at end of period",   # OCFC
    "tangible book value per common share at period end",      # EGBN
    # 2026-09-30 universe sweep — each verified per-COMMON-share tangible
    # book in its release (TCE ÷ common shares reproduces the printed figure).
    "tangible stockholders' equity (book value) per common share",  # AMTB
    "tangible stockholders' equity book value per common share",    # AMTB
    "common shareholders' tangible equity per share",               # BANR
    "tangible common shareholders' equity (tangible book value) per share",  # BANR
    "common shareholders' equity (tangible), per share",            # FULT
    "non-gaap tangible book value per share",                       # HBCP
    'tangible common equity ("tce") per share',                     # HOPE
    "tangible common equity per share of common stock",             # IBCP
    "tangible equity per common share",                             # MTB
    "tce per common share",                                         # PCB
    "tangible common equity per total common share outstanding",    # FRBT
    "tangible book value per share (total tangible stockholders' "
    "equity/shares outstanding)",                                   # PFS
    "tangible common equity book value per share",                  # UVSP
    "tangible book value per common share, net of tax",             # WAL
    "tangible book value per share, net of tax",                    # WAL
})

# Reported (GAAP) book value per COMMON share — the in-release cross-check anchor
# when the caller has no reconstruction/bvps to tie against (e.g. PNC, whose
# reconstruction is n/a on unresolvable preferred but which DOES disclose both
# book and tangible-book per common share in its release).
_BVPS_LABELS: frozenset = frozenset({
    "book value per share",
    "book value per common share",
    "book value per common share at end of period",            # OCFC
    "book value per common share at period end",               # EGBN
    "common shareholders' equity per share",                   # BANR, MTB
    "common shareholders' equity (book value) per share",      # BANR
    "common shareholders' equity per share of common stock",   # IBCP
    "common equity book value per share",                      # UVSP
})

# Release-INTERNAL tie-out anchor (the MBIN case): when neither a reconstruction
# nor any book-value-per-share line exists, the release's own non-GAAP
# reconciliation table often still discloses the two inputs of the printed
# TBVPS — tangible COMMON equity and ending common shares. Their ratio ties the
# printed figure out arithmetically. Labels are kept explicitly COMMON: a bare
# "tangible stockholders' equity" can include preferred and must never anchor a
# per-COMMON-share figure.
_TANGIBLE_CE_LABELS: frozenset = frozenset({
    "tangible common shareholders' equity",
    "tangible common stockholders' equity",
    "tangible common equity",
    "total tangible common equity",
})

_ENDING_SHARES_LABELS: frozenset = frozenset({
    "ending common shares",
    "common shares outstanding",
    "period end common shares outstanding",
    "common shares outstanding at period end",
})

# Trailing qualifier / footnote groups that ride on the SAME line as the figure
# and must be stripped before matching — a "(non-gaap)" or "(period end)"
# qualifier, or a short footnote token like "(b)", "(1)", "(a)", "(b)/(f)",
# "(i/c)". These sit inside a balanced paren, so _clean_label (which keeps a
# trailing ")") leaves them intact; we peel them here. A meaningful qualifier
# like "(te)" is NOT in this set, so it is preserved. Also peeled (2026-09-30
# sweep): "(non-gaap1)" (BHRB), "(gaap)" (BANR, HOPE), a split footnote pair
# "(m)/(n)" (SHBI), a dash-joined "– non-gaap" / "- gaap" (BNY, SHBI, FBP,
# RBCAA EX-99.2), ", period-end" (BHB), and a bare reconciliation-formula
# reference "x/dd" / "(non-gaap) aa/dd" (FCNCA; letters-slash-letters only,
# so no number or word of the label itself can be peeled) — all
# presentational suffixes on the same figure.
_TRAIL_QUALIFIER = re.compile(
    r"(?:\s*\((?:(?:non[- ]?)?gaap\d?|period[- ]end|end of period"
    r"|[a-z0-9]{1,3}(?:/[a-z0-9]{1,3})?)\)"
    r"|\s*/\s*\([a-z0-9]{1,3}\)"
    r"|\s*[–—-]\s*(?:non[- ]?)?gaap"
    r"|,\s*period[- ]end"
    r"|\s+[a-z]{1,2}/[a-z]{1,2})\s*$"
)


def _strip_trailing_qualifiers(cl: str) -> str:
    """Peel trailing '(non-gaap)' / '(period end)' / short footnote groups (in any
    order, repeatedly) from a cleaned label so the core label can be matched."""
    prev = None
    while prev != cl:
        prev = cl
        cl = _TRAIL_QUALIFIER.sub("", cl).strip()
    return cl


def _match_tbvps_label(cl: str) -> bool:
    """True when a cleaned row label denotes tangible book value per COMMON share,
    ignoring trailing '(non-GAAP)' / '(period end)' / footnote qualifiers — e.g.
    'tangible book value per common share (period end)(a)'."""
    return _strip_trailing_qualifiers(cl) in _TBVPS_LABELS


# SECOND-TIER labels without an explicit "per share" — ONB 2Q26 prints its TBVPS
# as "Tangible book value" (Per Common Share Data block, Table 5) and "Tangible
# common book value" (non-GAAP Table 14). The same words can label a DOLLAR
# TOTAL elsewhere, so they are consulted only when NO explicit per-share row
# exists, and the value must still clear every gate (per-share magnitude,
# tangible < book, ±15% of the reconstruction) — a $K/$M total fails them.
_TBVPS_BARE_LABELS: frozenset = frozenset({
    "tangible book value",
    "tangible common book value",
    "tangible book value at period end",    # KEY (Per common share block)
})

# LAST-RESORT prose form — JPM 2Q26 EX-99.1 states TBVPS only in a page-1
# bullet: "…up 9% YoY; tangible book value per share² of $113.35, up 10% YoY".
# Admitted only at a clause start (text start, or after . ; : • or "and") so a
# qualified variant ("excluding AOCI, tangible book value per share of $X",
# "compared with tangible book value per share of $Y") never matches, and only
# as "<label>[footnote] of|was $NN.NN".
_TBVPS_PROSE = re.compile(
    r"(?:^|[.;:•·▪]\s*|\band\s+)"
    r"tangible (?:common )?book value per (?:common )?share"
    r"(?:\d{1,2}|\s*\([a-z0-9]{1,3}\))?"
    r"\s+(?:of|was)\s+\$\s?(\d{1,3}(?:,\d{3})*\.\d{2})\b",
    re.IGNORECASE,
)


def _tbvps_prose_value(html_bytes: bytes) -> float | None:
    """The TBVPS stated in the release's prose, or None. Every clause-start
    match must state the SAME figure — two different values (current vs a
    prior-period comparison) are ambiguous and yield None, never a pick."""
    from lxml import html as lhtml
    text = lhtml.fromstring(html_bytes).text_content().replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    vals = {float(m.group(1).replace(",", "")) for m in _TBVPS_PROSE.finditer(text)}
    return vals.pop() if len(vals) == 1 else None


def _tbvps_candidate(html_bytes: bytes, rows: list[tuple],
                     prose: bool = True) -> tuple:
    """(value, explicit) — the release's TBVPS candidate BEFORE gating, by
    descending label strength: an explicit per-share table row, else a bare
    "tangible (common) book value" row, else the prose statement (unless
    prose=False — a supplementary exhibit's prose has no verifiable period).
    The first matching row of a tier decides (its blank latest-quarter cell
    → None, audit P3). explicit=False marks the two weaker tiers."""
    for cl, nums in rows:
        if _match_tbvps_label(cl):
            return nums[0], True
    for cl, nums in rows:
        if _strip_trailing_qualifiers(cl) in _TBVPS_BARE_LABELS:
            return nums[0], False
    return (_tbvps_prose_value(html_bytes) if prose else None), False


def _match_bvps_label(cl: str) -> bool:
    """True when a cleaned row label denotes (GAAP) book value per COMMON share."""
    return _strip_trailing_qualifiers(cl) in _BVPS_LABELS


# SECOND-TIER BVPS label without "per share" — ONB 2Q26 prints "Book value"
# ($21.80) in its Per Common Share Data block. The same words can label a
# dollar TOTAL, so it is consulted only when no explicit per-share row exists
# and must tie to the reconstruction (±15%); nothing weaker admits it.
_BVPS_BARE_LABELS: frozenset = frozenset({
    "book value",
    "book value at period end",             # KEY (Per common share block)
})


def _internal_tie_out(rows: list[tuple], v: float) -> bool:
    """True when the release's OWN tangible-common-equity and ending-common-shares
    rows arithmetically reproduce the extracted TBVPS `v` within 1% — the MBIN
    case: no reconstruction (unresolvable preferred) and no book-value-per-share
    line anywhere, but the reconciliation table prints both inputs of the figure
    (tangible common shareholders' equity $1,834,473K ÷ 45,938,075 ending common
    shares = $39.93 ✓).

    The two rows carry unknown, independent scales (equity in $K/$M, shares raw
    or in thousands), so the ratio is tried at ×1 / ×1e3 / ×1e6 — same plausible
    set as _detect_scale. The 1% band is far tighter than any cross-scale gap
    (×1000), so a wrong scale can never tie out; and the tie-out only ever
    ACCEPTS the release's printed figure, never computes a displayed value."""
    te = sh = None
    for cl, nums in rows:
        core = _strip_trailing_qualifiers(cl)
        # First row with a non-blank latest-quarter cell wins (a summary-table
        # variant of the same row can have a blank first column — audit P3).
        if te is None and core in _TANGIBLE_CE_LABELS \
                and nums[0] is not None and nums[0] > 0:
            te = nums[0]
        if sh is None and core in _ENDING_SHARES_LABELS \
                and nums[0] is not None and nums[0] > 0:
            sh = nums[0]
        if te is not None and sh is not None:
            break
    if te is None or sh is None:
        return False
    ratio = te / sh
    return any(abs(ratio * s - v) / v < 0.01 for s in (1.0, 1e3, 1e6))


def extract_reported_tbvps(
    ex991_html: bytes,
    reconstructed: float | None = None,
    bvps: float | None = None,
) -> float | None:
    """Value-only wrapper around extract_reported_tbvps_status (kept for the
    existing callers/tests that only want the number)."""
    return extract_reported_tbvps_status(ex991_html, reconstructed, bvps)[0]


def extract_reported_tbvps_status(
    ex991_html: bytes,
    reconstructed: float | None = None,
    bvps: float | None = None,
    period_end: tuple | None = None,
) -> tuple[float | None, str]:
    """The bank's OWN reported tangible book value per common share from one
    EX-99.1 document, or None when not cleanly disclosed / fails a sanity gate.

    Returns (value, status). status:
      "ok"            — value extracted and cross-checked.
      "not_disclosed" — no clean TBVPS row/anchor in the release (also covers
                        extraction noise: blank cells, non-per-share values,
                        a book-value row mismatched into the slot).
      "gate_rejected" — a CLEAN per-share figure (magnitude + tangible<book
                        both passed) disagrees with the reconstruction by
                        ≥15%. That is not noise: the release and the pipeline
                        cannot both be right (FSUN 2026-08: a stale share
                        count made the reconstruction $58.97 vs the release's
                        $35.16, and this gate silently discarded the truth
                        for three weeks). Callers must surface it.

    Company-Reported principle: take the disclosed number, don't rebuild it. The
    caller supplies the corrected reconstruction (`reconstructed`, from
    data.sec_client) and, when available, the reported book value per share
    (`bvps`) so the extracted figure can be cross-checked.

    Cardinal rule — never emit a plausible-wrong number. The matched value must:
      • be a positive per-share number in a sane band (0 < x < 10 000);
      • be LESS than reported book value per share (tangible < book), when bvps
        is known — this also rejects a book-value row mismatched into the slot
        (waived when the reconstruction itself has tangible == book: USCB);
      • land within 15% of the reconstruction, when the reconstruction resolved —
        this rejects an EPS/dividend value mis-grabbed into the TBVPS slot and a
        wrong-period/wrong-column pick.
    If no reconstruction anchor AND no bvps is passed in, the release's OWN
    reported book value per common share is used as the tangible-<-book anchor
    (the PNC case: reconstruction n/a on unresolvable preferred, but both book
    and tangible-book per common share are disclosed in the release). Failing
    that, the release's own reconciliation INPUTS anchor it: tangible common
    equity ÷ ending common shares must reproduce the figure within 1% (the MBIN
    case — no BVPS line anywhere, but both inputs printed; _internal_tie_out).
    Only when NOTHING ties the match — no reconstruction, no passed bvps, no
    in-release book value, no in-release tie-out — is the raw match rejected.
    Take the FIRST numeric column (releases lead every table with the
    most-recent period). The candidate is found by _tbvps_candidate: an
    explicit per-share row first, then a bare "tangible (common) book value"
    row (ONB), then the prose statement (JPM) — every tier faces the same gates.

    period_end=(year, month) marks the document as a SUPPLEMENTARY exhibit
    (EX-99.2+) of a release for that quarter: only table rows whose latest
    column is headed by that quarter-end are read (_supplement_rows) and the
    prose tier is off; every gate above applies unchanged.
    """
    rows = (_supplement_rows(ex991_html, period_end) if period_end
            else _book_value_rows(ex991_html))
    # In-release reported book value per common share, the fallback cross-check
    # anchor when the caller had none (same first-column / most-recent period).
    if bvps is None:
        for cl, nums in rows:
            # A blank latest-quarter book-value cell is None — it must not
            # anchor (its prior-period value is a different date's book value).
            if _match_bvps_label(cl) and nums[0] is not None:
                bvps = nums[0]
                break
    v, explicit = _tbvps_candidate(ex991_html, rows,
                                   prose=period_end is None)
    # No TBVPS disclosed, or a blank latest-quarter cell → n/a; never serve the
    # prior column as the current quarter (audit P3).
    if v is None:
        return None, "not_disclosed"
    # Positive, per-share magnitude (rejects a $-thousands equity total or a
    # negative/zero cell mis-aligned into the row).
    if not (0 < v < 10_000):
        return None, "not_disclosed"
    # Tangible < book, when book value per share is disclosed — unless the
    # reconstruction itself has tangible == book (no intangibles: USCB prints
    # $12.64 for both). There a book row in the slot IS the tangible figure,
    # and the ±15% reconstruction band below still gates the value.
    # The waiver covers the release's cent rounding only — a tangible figure
    # ABOVE book is never admitted (BCTF: $12.94 vs a $11.56 reconstruction).
    no_intangibles = (reconstructed is not None and bvps is not None
                      and abs(reconstructed - bvps) < 0.005)
    if (bvps is not None and bvps > 0 and not (v < bvps)
            and not (no_intangibles and v <= bvps + 0.01)):
        return None, "not_disclosed"
    # Tie to the corrected reconstruction within a tight band. This is the
    # primary defense against an EPS/dividend value grabbed into the slot.
    # A weak-tier (bare-label / prose) miss is not known to BE the release's
    # TBVPS (a growth-% or other row under the same words), so it is not
    # raised as a release-vs-reconstruction conflict.
    if reconstructed is not None and reconstructed > 0:
        if abs(v - reconstructed) / reconstructed >= 0.15:
            return None, "gate_rejected" if explicit else "not_disclosed"
        return v, "ok"
    # No reconstruction anchor: accept only if it cleared the book-value
    # cross-check (something real to tie to); otherwise nothing anchors it.
    # tangible < book alone is too weak for the bare/prose tiers (a growth-%
    # row passes it) — they need the reconstruction or the tie-out below.
    if explicit and bvps is not None and bvps > 0:
        return v, "ok"
    # Last anchor — the release's own reconciliation inputs (the MBIN case:
    # unresolvable preferred kills the reconstruction AND the release prints
    # no book-value-per-share line, but tangible common equity ÷ ending
    # common shares reproduces the printed figure). See _internal_tie_out.
    if _internal_tie_out(rows, v):
        return v, "ok"
    return None, "not_disclosed"


def _detect_scale(rows: list[tuple], anchor: dict):
    """The dollar scale (1e3 thousands / 1e6 millions / 1.0 raw) for this release,
    found by matching its total-assets or total-deposits cell to the prior 10-Q
    tagged value (raw dollars). Returns (scale, anchored_field) or (None, None)
    when neither balance-sheet anchor lands in any plausible scale band — in which
    case NO dollar figure is trustworthy and all are dropped."""
    for fig in _DOLLAR_BS:
        a = anchor.get(fig)
        v = _first_match(rows, _FIG_LABELS[fig])
        if a is None or v is None or v <= 0:
            continue
        for scale in (1e3, 1e6, 1.0):
            if a * 0.7 <= v * scale <= a * 1.4:   # within last quarter ±30/40%
                return scale, fig
    return None, None


def _anchor_balance_sheet(facts) -> dict:
    """Prior-filing tagged total assets / deposits (raw dollars) at its latest
    balance-sheet date — the cross-source anchor for scale + the BS gate."""
    bs = None
    for f in facts:
        if (f.concept.split(":")[-1] == "Assets" and not f.members
                and f.period_start is None):
            if bs is None or f.period_end > bs:
                bs = f.period_end
    if bs is None:
        return {}
    return {"total_assets": _undimensioned_total(facts, "Assets", bs),
            "total_deposits": _undimensioned_total(facts, "Deposits", bs)}


def extract_earnings_figures(ex991_html: bytes, anchor: dict) -> dict:
    """Headline latest-quarter figures from one EX-99.1 document, sanity-gated
    against `anchor` (the prior 10-Q's tagged balance-sheet totals).

    Returns {figure: value | None}. Dollar values are returned in RAW DOLLARS
    (scaled by the detected release scale); ratios as the as-printed percent
    number (3.88 = 3.88%); EPS as dollars/share. A figure is None (n/a) unless it
    both matched an exact label AND passed its gate — never a guess."""
    rows = _table_rows(ex991_html)
    out: dict = {k: None for k in _FIG_LABELS}

    scale, _anchored_on = _detect_scale(rows, anchor)

    for fig, labels in _FIG_LABELS.items():
        v = _first_match(rows, labels)
        if v is None:
            continue
        if fig in _DOLLAR_BS:
            a = anchor.get(fig)
            if a is None or scale is None:
                continue
            scaled = v * scale
            if a * 0.7 <= scaled <= a * 1.4:    # rejects a segment subtotal
                out[fig] = scaled
        elif fig in _DOLLAR_FLOW:
            # No direct anchor (flows aren't a clean BS instant); require the
            # release scale to be known and the figure positive & non-trivial.
            if scale is None or v <= 0:
                continue
            out[fig] = v * scale
        elif fig in _RATIO:
            if 0 <= abs(v) <= 60:
                out[fig] = v
        elif fig == "diluted_eps":
            if 0 < abs(v) < 100:                # excludes a share-count mis-match
                out[fig] = v
    return out


# ── Public, cached entry point ───────────────────────────────────────────────
def latest_earnings_8k_figures(cik) -> dict | None:
    """Latest-quarter headline figures from a bank's most-recent earnings 8-K
    (EX-99.1), or None when no earnings 8-K / no EX-99.1 / nothing extractable.

    {"period", "filed", "accession", "doc", "figures": {...}, "_preliminary": True}

      • period     — the prior 10-Q/10-K balance-sheet date the figures were
                     anchored against (provenance; the 8-K quarter is NEWER).
      • filed      — 8-K filing date (the as-released date).
      • figures    — {total_assets, total_deposits, net_income,
                     net_interest_income, diluted_eps, nim, roaa, roae}, each a
                     gated value or None (n/a). Dollars are raw; ratios are the
                     as-printed percent; EPS is $/share.
      • _preliminary — always True; surface labeled as-released, never as audited.

    Cached by 8-K accession (the fetch+parse runs once per release; an empty parse
    is cached so a no-figure release isn't re-fetched). A transient fetch/parse
    EXCEPTION is never cached."""
    if not cik:
        return None
    from data import cache

    f8k = _latest_earnings_8k(cik)
    if not f8k:
        return None

    # v2: per-row '$'/'%' decoration-cell skip in _table_rows (FRME miss).
    ckey = f"earnings_8k:v2:{f8k['accession']}"
    # Accession-keyed and version-prefixed = immutable; the default 24h read
    # ceiling would silently re-run the fetch+parse for every bank every day.
    payload = cache.get(ckey, max_age_s=None)
    if payload is None:
        try:
            doc = _ex991_document(cik, f8k["accession_dash"])
            if not doc:
                payload = {}
            else:
                # Anchor against the prior 10-Q (timeliest), falling back to 10-K.
                anchor_meta = latest_filing(cik, ("10-Q", "10-K"))
                anchor = (_anchor_balance_sheet(instance_facts(anchor_meta))
                          if anchor_meta else {})
                url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                       f"{f8k['accession']}/{doc}")
                figures = extract_earnings_figures(_get(url), anchor)
                if any(v is not None for v in figures.values()):
                    payload = {
                        "period": anchor_meta.get("date") if anchor_meta else None,
                        "filed": f8k["date"],
                        "accession": f8k["accession_dash"],
                        "doc": doc,
                        "figures": figures,
                        "_preliminary": True,
                    }
                else:
                    payload = {}
            try:
                cache.put(ckey, payload)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_earnings_8k] failed for cik {cik}: {type(e).__name__}: {e}")
            return None

    return payload or None


# A supplementary exhibit is read only for a release whose quarter-end is
# within this many days — the repo's release-staleness convention (valuation
# _otc_release_ps / otc_release._IR_STALE_DAYS). UBOH's latest earnings 8-K
# is from 2023-01-19: its EX-99.2 figure is a 4Q22 TBVPS, never "current".
_SUPPLEMENT_STALE_DAYS = 200


def _exhibit_status(f8k: dict, extract,
                    today=None) -> tuple[float | None, str]:
    """(value, status) of `extract(html, period_end)` over the earnings 8-K's
    EX-99.n exhibits in ascending n — EX-99.1 first (period_end=None, the
    release reader), then each supplementary exhibit (period_end = the
    release quarter-end, the period-verified reader). The FIRST exhibit whose
    status is not "not_disclosed" decides, so an EX-99.1 answer ("ok" OR a
    "gate_rejected" conflict) is never second-guessed by a later exhibit, and
    a later exhibit is fetched only when every earlier one disclosed nothing
    (RBCAA: EX-99.2 financial tables; FCNCA: EX-99.3 financial supplement).
    Supplements are skipped when the release quarter is unknown or older than
    _SUPPLEMENT_STALE_DAYS (`today` is injectable for tests)."""
    import calendar
    from datetime import date
    period_end = _release_quarter_end(f8k.get("date"))
    fresh = False
    if period_end is not None:
        y, m = period_end
        qend = date(y, m, calendar.monthrange(y, m)[1])
        fresh = ((today or date.today()) - qend).days <= _SUPPLEMENT_STALE_DAYS
    for n, doc in _ex99_documents(f8k["cik"], f8k["accession_dash"]):
        if n != 1 and not fresh:
            break                  # no verifiable / current quarter to read
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(f8k['cik'])}/"
               f"{f8k['accession']}/{doc}")
        value, status = extract(_get(url), None if n == 1 else period_end)
        if status != "not_disclosed":
            return value, status
    return None, "not_disclosed"


def reported_tbvps(
    cik,
    reconstructed: float | None = None,
    bvps: float | None = None,
) -> float | None:
    """Value-only wrapper around reported_tbvps_status."""
    return reported_tbvps_status(cik, reconstructed=reconstructed, bvps=bvps)[0]


def _shows_preferred_equity(rows: list[tuple]) -> bool:
    """True when the release carries a preferred-stock EQUITY row (balance
    sheet or TCE reconciliation — NPB's "Less: preferred stock" $24,979K) with
    a nonzero latest value. Preferred DIVIDEND rows (MS, RJF, SFBS print only
    those) and trust-preferred debt are not equity evidence."""
    return any("preferred stock" in cl and "dividend" not in cl
               and "trust" not in cl and nums[0]
               for cl, nums in rows)


def extract_reported_bvps_status(
    ex991_html: bytes,
    reconstructed: float | None = None,
    tbvps: float | None = None,
    period_end: tuple | None = None,
) -> tuple[float | None, str]:
    """The bank's OWN reported (tangible-INCLUSIVE) book value per common share
    from one EX-99.1 document — the BVPS sibling of
    extract_reported_tbvps_status, sharing its status vocabulary and gates
    (release-first increment 1, owner directive 2026-08-19).

    Gates (cardinal rule — never emit a plausible-wrong number):
      • positive per-share magnitude (0 < x < 10 000);
      • book ≥ tangible when TBVPS is known (EQUALITY allowed — a
        no-intangibles bank like SFST legitimately prints equal values;
        rejecting only v < tbvps catches a tangible row mismatched into the
        book slot);
      • ±15% vs the reconstruction when it resolved → "ok"/"gate_rejected";
      • no reconstruction: the in-release TBVPS (caller-passed, else matched
        from the same document) anchors it — when the release shows
        preferred equity, for an explicit per-COMMON label only (NPB); with
        NOTHING to tie to → "not_disclosed".
    period_end: a supplementary exhibit, as in extract_reported_tbvps_status.
    """
    rows = (_supplement_rows(ex991_html, period_end) if period_end
            else _book_value_rows(ex991_html))
    if tbvps is None:
        for cl, nums in rows:
            # A blank latest-quarter cell must not anchor (audit P3).
            if _match_tbvps_label(cl) and nums[0] is not None:
                tbvps = nums[0]
                break
    for cl, nums in rows:
        if not _match_bvps_label(cl):
            continue
        v = nums[0]
        if v is None:
            return None, "not_disclosed"
        if not (0 < v < 10_000):
            return None, "not_disclosed"
        if tbvps is not None and tbvps > 0 and v < tbvps:
            return None, "not_disclosed"
        if reconstructed is not None and reconstructed > 0:
            if abs(v - reconstructed) / reconstructed >= 0.15:
                return None, "gate_rejected"
            return v, "ok"
        # No reconstruction usually means unresolvable PREFERRED (PNC, MBIN,
        # NPB). When the release itself carries preferred EQUITY, a label that
        # doesn't say COMMON may be total equity ÷ common shares: NPB 2Q26
        # "Book value per share (GAAP)" $17.10 = $589,993K total equity incl.
        # its "Less: preferred stock" $24,979K ÷ 34.49M shares — per-common
        # book is $16.38. There only an explicit per-COMMON label passes.
        if tbvps is not None and tbvps > 0 and (
                "common" in cl or not _shows_preferred_equity(rows)):
            return v, "ok"
        return None, "not_disclosed"
    # Second tier (no explicit per-share row): a bare "book value" row, the
    # FIRST one decides. Stricter than the explicit tier: it must tie to the
    # reconstruction — a $K/$M total or a growth % under the same words fails
    # the per-share magnitude or the ±15% band — and a miss is "not_disclosed",
    # never a release-vs-reconstruction conflict (the row is not known to BE
    # the BVPS).
    for cl, nums in rows:
        if _strip_trailing_qualifiers(cl) not in _BVPS_BARE_LABELS:
            continue
        v = nums[0]
        if (v is None or not (0 < v < 10_000)
                or (tbvps is not None and tbvps > 0 and v < tbvps)
                or reconstructed is None or reconstructed <= 0
                or abs(v - reconstructed) / reconstructed >= 0.15):
            return None, "not_disclosed"
        return v, "ok"
    return None, "not_disclosed"


def reported_bvps_status(
    cik,
    reconstructed: float | None = None,
    tbvps: float | None = None,
) -> tuple[float | None, str]:
    """Cached wrapper for extract_reported_bvps_status — the BVPS sibling of
    reported_tbvps_status (same status vocabulary, same accession+anchor
    cache discipline, same never-cache-exceptions rule)."""
    if not cik:
        return None, "not_disclosed"
    from data import cache

    f8k = _latest_earnings_8k(cik)
    if not f8k:
        return None, "not_disclosed"

    rk = f"{reconstructed:.4f}" if reconstructed is not None else "na"
    tk = f"{tbvps:.4f}" if tbvps is not None else "na"
    # v2: "… per common share at end of period" label (OCFC miss).
    # v3: "(end of period)" qualifier (BBT) + bare "book value" tier (ONB).
    # v4: table-less text-layer rows (AMAL) + "at period end" label (EGBN).
    # v5: sweep label/qualifier variants (BANR, MTB, IBCP, UVSP, KEY),
    #     positioned-fragment releases (FBP), and no-reconstruction rows
    #     must say COMMON when the release shows preferred equity (NPB).
    # v6: supplementary exhibits EX-99.2+ (RBCAA/FCNCA) + formula-ref suffixes.
    # v7: the finder also selects mis-itemized releases (FBP/NPB Q2-2026).
    # v8: header-year phantom column dropped in _table_rows (BHB).
    ckey = f"reported_bvps:v8:{f8k['accession']}:{rk}:{tk}"
    # Accession+anchor-keyed = immutable; no 24h read ceiling.
    cached = cache.get(ckey, max_age_s=None)
    if cached is not None:
        return cached.get("value"), cached.get("status") or "not_disclosed"

    try:
        value, status = _exhibit_status(
            f8k, lambda html, pe: extract_reported_bvps_status(
                html, reconstructed=reconstructed, tbvps=tbvps, period_end=pe))
        try:
            cache.put(ckey, {"value": value, "status": status})
        except Exception:
            pass
        return value, status
    except Exception as e:
        print(f"[sec_earnings_8k] reported_bvps failed for cik {cik}: "
              f"{type(e).__name__}: {e}")
        return None, "not_disclosed"

# Extraction-spec version of the reported_tbvps cache key. Bump on any change
# to the extraction/gating logic — a cached result under the old spec must not
# serve the old answer forever.
# v3: per-row '$'/'%' decoration-cell skip in _table_rows (FRME miss) —
#     cached {"value": None} under v2 would otherwise serve the miss forever.
# v4: value → (value, status); the stored shape gained "status".
# v5: release-internal tie-out anchor (MBIN) — a v4 None for the no-anchor
#     case would otherwise serve the miss forever.
# v6: "… per common share at end of period" labels (OCFC miss).
# v7: bare "tangible (common) book value" rows (ONB) + prose statement (JPM).
# v8: "(end of period)" trailing qualifier (BBT) — changes which row matches.
# v9: table-less releases read from the page text layer (AMAL miss) and
#     "… at period end" labels (EGBN). (Parallel branches bumped the same
#     numbers for different specs — v9 so no spec's cache serves another.)
# v10: 2026-09-30 sweep label/qualifier variants (BANR, MTB, WAL, BNY, …),
#      table-less positioned-fragment releases (FBP, USCB), and the
#      no-intangibles tangible == book allowance (USCB).
# v11: supplementary exhibits EX-99.2+ of the same 8-K (RBCAA EX-99.2, FCNCA
#      EX-99.3) + bare reconciliation-formula label suffixes ("x/dd").
# v12: _latest_earnings_8k also selects an 8-K whose EX-99.1 proves itself a
#      newer quarter's release despite a non-2.02 item (FBP 2.01, NPB 2.01/7.01).
# v13: a header year spanning a value column no longer adds a phantom first
#      column to _table_rows (BHB: every row read [None, 23.43, …]).
_REPORTED_TBVPS_CKEY_V = "v13"


def reported_tbvps_status(
    cik,
    reconstructed: float | None = None,
    bvps: float | None = None,
) -> tuple[float | None, str]:
    """The bank's OWN reported tangible book value per common share, read straight
    from its latest earnings release (8-K EX-99.1, else the first supplementary
    EX-99.n that discloses it — _exhibit_status), or None when it isn't cleanly
    disclosed / fails a sanity gate (→ caller falls back to the reconstruction).

    Returns (value, status) — see extract_reported_tbvps_status. A
    "gate_rejected" status means the release and the reconstruction disagree
    ≥15%: one of them IS wrong, and the caller must make that visible rather
    than silently serving the fallback.

    Company-Reported principle: prefer the disclosed number over a rebuild. Pass
    the corrected reconstruction (`reconstructed`, data.sec_client's
    tangible_book_value_per_share) and reported book value per share (`bvps`) so
    the extracted figure is cross-checked before it is trusted (see
    extract_reported_tbvps for the gates).

    Cached by 8-K accession + the anchors it was gated against (a different
    reconstruction/bvps must re-gate). A None result is cached so a
    non-disclosing release isn't re-fetched; a transient fetch/parse EXCEPTION is
    never cached (returns None without poisoning the cache)."""
    if not cik:
        return None, "not_disclosed"
    from data import cache

    f8k = _latest_earnings_8k(cik)
    if not f8k:
        return None, "not_disclosed"

    # Fold the anchors into the key: the same release gated against a different
    # reconstruction/bvps is a different question and must not reuse a stale None.
    rk = f"{reconstructed:.4f}" if reconstructed is not None else "na"
    bk = f"{bvps:.4f}" if bvps is not None else "na"
    ckey = f"reported_tbvps:{_REPORTED_TBVPS_CKEY_V}:{f8k['accession']}:{rk}:{bk}"
    # Accession+anchor-keyed = immutable; no 24h read ceiling (see above).
    cached = cache.get(ckey, max_age_s=None)
    if cached is not None:
        # {"value": float|None, "status": str}; None values are cached too.
        return cached.get("value"), cached.get("status") or "not_disclosed"

    try:
        value, status = _exhibit_status(
            f8k, lambda html, pe: extract_reported_tbvps_status(
                html, reconstructed=reconstructed, bvps=bvps, period_end=pe))
        try:
            cache.put(ckey, {"value": value, "status": status})
        except Exception:
            pass
        return value, status
    except Exception as e:
        print(f"[sec_earnings_8k] reported_tbvps failed for cik {cik}: "
              f"{type(e).__name__}: {e}")
        return None, "not_disclosed"
