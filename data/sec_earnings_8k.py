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
"""
from __future__ import annotations

import json
import re

from data.sec_filing_scraper import (
    _get, latest_filing, instance_facts, _undimensioned_total,
)


# ── Locate the latest earnings 8-K + its EX-99.1 exhibit ─────────────────────
def _latest_earnings_8k(cik) -> dict | None:
    """Most-recent Item-2.02 (Results of Operations) 8-K: {accession, accession_dash,
    date, cik} or None. Item 2.02 is the earnings item — a press-release-only 8-K
    (7.01/8.01) is skipped so we land on the quarter's results filing."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    items = rec.get("items", [])
    accs = rec.get("accessionNumber", [])
    dates = rec.get("filingDate", [])
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        item_str = items[i] if i < len(items) else ""
        if "2.02" not in item_str:
            continue
        acc_dash = accs[i] if i < len(accs) else ""
        if not acc_dash:
            continue
        return {"accession_dash": acc_dash, "accession": acc_dash.replace("-", ""),
                "date": dates[i] if i < len(dates) else "", "cik": int(cik)}
    return None


def _ex991_document(cik, accession_dash) -> str | None:
    """Filename of the EX-99.1 exhibit, read from the filing index's exhibit-type
    table (the authoritative SEC-declared type), never guessed from the name."""
    from lxml import html as lhtml
    acc = accession_dash.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
           f"{accession_dash}-index.htm")
    root = lhtml.fromstring(_get(url))
    for table in root.findall(".//table"):
        hdr = [th.text_content().strip().lower() for th in table.findall(".//th")]
        if "type" not in hdr or "document" not in hdr:
            continue
        ti, di = hdr.index("type"), hdr.index("document")
        for tr in table.findall(".//tr"):
            cells = tr.findall("td")
            if len(cells) <= max(ti, di):
                continue
            if cells[ti].text_content().strip().upper() == "EX-99.1":
                a = cells[di].find(".//a")
                doc = (a.text_content().strip() if a is not None
                       else cells[di].text_content().strip())
                return doc.split("/")[-1] or None
    return None


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
    # Strip a trailing footnote run, but only when it's NOT closing a paren group
    # (so "margin (te)" keeps its ")"; "diluted shares8" loses its "8").
    if not s.endswith(")"):
        s = _LABEL_TRAIL.sub("", s).strip()
    return re.sub(r"\s+", " ", s)


def _table_rows(html_bytes: bytes) -> list[tuple]:
    """Every (clean_label, [numeric cells…]) row across the document's tables, in
    document order. A row needs a non-numeric label and ≥1 numeric cell."""
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)
    rows: list[tuple] = []
    for table in root.findall(".//table"):
        for tr in table.findall(".//tr"):
            cells = [c.text_content().strip().replace("\xa0", " ")
                     for c in tr.findall(".//td")]
            cells = [c for c in cells if c.strip()]
            if len(cells) < 2:
                continue
            nums = [_num(c) for c in cells[1:]]
            nums = [n for n in nums if n is not None]
            if not nums:
                continue
            label = cells[0]
            if _num(label) is not None:        # a number, not a label
                continue
            cl = _clean_label(label)
            if not cl:
                continue
            rows.append((cl, nums))
    return rows


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
    for cl, nums in rows:
        if cl in labels:
            return nums[0]
    return None


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

    ckey = f"earnings_8k:v1:{f8k['accession']}"
    payload = cache.get(ckey)
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
