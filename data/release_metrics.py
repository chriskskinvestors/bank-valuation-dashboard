"""
Release-day metrics from a bank's earnings press release (Results board).

Extends the ir_provider extraction (capital ratios, diluted EPS) to the
metric set the Results board's expandable rows show the morning a bank
reports: profitability ratios (NIM, efficiency, ROA, ROE, ROTCE), per-share
values (tangible book value, declared dividend), and credit quality (NCO
ratio, NPAs/total assets, ACL/total loans).

Same discipline as ir_provider (cardinal rule: never a plausible-wrong
number), releases carry no inline XBRL so everything is prose:

  - Every pattern anchors on the metric's LABEL and captures only the FIRST
    percent/dollar after it ([^%] / [^$%] connectors cannot cross an earlier
    value), so a trailing prior-period comparison can never be captured.
  - A prior-period / non-GAAP qualifier just BEFORE the label disqualifies
    that candidate ("compared with a net interest margin of…", "adjusted
    efficiency ratio of…").
  - ALL clean candidates for a metric must AGREE (a re-stated prior-year or
    adjusted variant that slips through forces a disagreement) — else None.
  - Every value is band-checked; out-of-band → not a candidate.
  - Credit ratios are DENOMINATOR-PINNED (…% *of total assets* / *of total
    loans*): a ratio quoted against a different base is never mis-labeled.

Anything not confidently found is None — rendered as '—', never guessed.

Fetch layer: one cached record per CIK. The extraction for a given 8-K
accession is immutable, so a cache hit re-checks ONLY the (cheap) submissions
index every 15 min and re-extracts only when a NEW accession appears —
earnings-morning freshness at ~4 requests/bank/hour.
"""

from __future__ import annotations

import html as _h
import json
import re

# Percent-metric spec: label regex, (lo, hi) plausibility band.
# The connector [^%]{0,60} can't cross a percent sign, so only the FIRST
# percent after the label is ever a candidate.
# Verbs are word-bounded with MANDATORY trailing whitespace — "of" must never
# match inside "charge-offs", and a bare table row ("Net interest margin 3.71 %
# 3.69 %") has no verb at all, so it can never match.
_VERB = r"(?:ratio\s+)?(?::\s*|\b(?:of|was|were|at|to|or|" \
        r"ended (?:the (?:quarter|year) )?at|" \
        r"(?:in|de)creased to|improved to|expanded to|contracted to|declined to)\s+)"
# Point-first decimals included: CBSH prints ratios without a leading zero
# (".19%", ".94%") — a leading-digit-only pattern is blind to the bank's
# entire number style (caught live 2026-07-16).
_NUM = r"(\d{1,2}(?:\.\d{1,2})?|\.\d{1,2})"

_PCT_SPECS = {
    "nim": (r"net interest margin", (0.5, 8.0)),
    "efficiency": (r"efficiency ratio", (20.0, 110.0)),
    "roa": (r"return on (?:average )?(?:total )?assets", (0.05, 4.0)),
    # "tangible" can't slip in: after "on (average )?" the next word must be
    # "(common )(shareholders')equity", so ROTCE text never matches the ROE spec.
    "roe": (r"return on (?:average )?(?:common )?(?:shareholders'?,? )?equity",
            (0.5, 40.0)),
    "rotce": (r"return on (?:average )?tangible common (?:shareholders'?,? )?equity",
              (0.5, 60.0)),
    # Credit — the label side of the two denominator-pinned forms (see below).
    "nco_ratio": (r"net (?:loan )?charge-offs?(?: ratio)?", (0.0, 5.0)),
}

# Denominator-pinned credit ratios: EITHER "<label> … X% of <denominator>"
# (value then base) OR "<label> to <denominator> … X%" (base in the label).
_PINNED_SPECS = {
    "npa_assets": (r"non-?performing assets", r"(?:total|period-end) assets",
                   (0.0, 10.0)),
    "acl_loans": (r"allowance for credit losses(?: \([^)]{0,8}\))?",
                  r"(?:total|gross|period-end|average)? ?loans(?: and leases)?"
                  r"(?: held for investment)?",
                  (0.1, 6.0)),
}

# A qualifier in the ~30 chars before the label disqualifies the candidate:
# prior-period comparisons, non-GAAP variants, and SEGMENT qualifiers — "Card
# Services net charge-off rate of 3.47%" is a segment figure, not firmwide
# (JPM, caught in the 2026-07-06 ground-truth pass). Over-exclusion is safe
# (n/a); a mis-scoped figure is not.
_EXCLUDE_BEFORE = re.compile(
    r"\b(?:adjusted|core|operating|non-?gaap|normalized|underlying|pro forma|"
    r"compared (?:to|with)|versus|vs\.?|year[- ]ago|prior[- ](?:year|quarter)|"
    r"linked[- ]quarter|from|down from|up from|"
    r"card(?: services)?|consumer|wholesale|commercial|mortgage|auto|"
    r"banking|lending|segment(?:'s)?)\s*(?:an?\s+|the\s+)?"
    # A label word may sit between the qualifier and the match ("Adjusted
    # Diluted EPS*" where the bare-EPS pattern anchors at "EPS" — FBK,
    # caught live 2026-07-14 pm: 1.14 adjusted shipped as GAAP).
    r"(?:diluted\s+|basic\s+)?$", re.I)
# ", excluding …" / ", on an adjusted basis" right after the value → non-GAAP.
_EXCLUDE_AFTER = re.compile(
    r"^[\s,(]*(?:excluding|adjusted|as adjusted|non-?gaap|core|operating|"
    r"on an? adjusted)", re.I)

_AGREE_PCT = 0.011      # two-decimal percent agreement
_AGREE_USD = 0.011      # cent agreement for per-share values

# ANOTHER bare number right after a candidate's value is the signature of a
# flattened multi-period TABLE ROW ("3.71 % 3.69 % 3.66 %", "$14.87 $14.60") —
# which period the first cell is depends on column order, i.e. luck. Prose
# always separates values with connective text (", compared with"), so a
# trailing number disqualifies the candidate. Prose-led by design; a table-only
# release degrades to n/a, never to a column guess.
_TABLE_TAIL = re.compile(r"^[\s,]*[($]?\d")
# A true non-GAAP variant marker BETWEEN the label and the value ("efficiency
# ratio, as adjusted, of 52.1%") disqualifies. Basis markers (FTE / taxable-
# equivalent / a bare "(non-GAAP)" footnote tag) are NOT variants — they mark
# the conventional reported basis — and stay eligible.
_CONNECTOR_EXCLUDE = re.compile(
    r"\b(?:adjusted|core|operating|normalized|underlying|pro forma)\b", re.I)


def _flat_text(html: str) -> str:
    """Release HTML → one collapsed text string (tags stripped, entities
    unescaped, whitespace normalized) — the prose the patterns scan."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _h.unescape(t))


def _clean(text: str, matches, band, agree_tol) -> float | None:
    """The agreed value across clean candidates, or None. `matches` yields
    (match_start, value_str, value_end); a candidate is dropped when a
    disqualifying phrase precedes its label, a non-GAAP variant marker sits in
    its label→value span, its value trails into another bare number (table
    row), or a qualifier trails it. No candidates, any unparseable value, or
    disagreement → None."""
    vals = []
    for start, vs, end in matches:
        if _EXCLUDE_BEFORE.search(text[max(0, start - 34):start]):
            continue
        if _CONNECTOR_EXCLUDE.search(text[start:end]):
            continue
        if _TABLE_TAIL.match(text[end:end + 12]):
            continue
        if _EXCLUDE_AFTER.match(text[end:end + 30]):
            continue
        try:
            v = float(vs)
        except ValueError:
            return None
        if band[0] <= v <= band[1]:
            vals.append(v)
    if not vals or (max(vals) - min(vals)) > agree_tol:
        return None
    return vals[0]


def _conn(n: int) -> str:
    """Label→value connector window: can't cross a percent sign (only the
    FIRST % after the label is a candidate), a bps token (an unparsed VALUE —
    walking past it reaches the NEXT metric's percent: CFG live 2026-07-16,
    "net charge-offs of 37 bps, … ■ Strong ACL coverage of 1.48%" shipped
    NCOs of 1.48%, real 0.37%), a bullet/clause separator, or a sentence
    boundary. Digits stay crossable — the legit dollar-then-ratio form
    ("$12.3 million, or 0.25% of average loans") depends on it."""
    return (r"(?:(?!bps\b|basis\s+points?\b|[■•;]|\.\s[A-Z])[^%])"
            + "{0,%d}?" % n)


# "A and B were X% and Y%, respectively" — the pair form maps values by
# ORDER. First-%-after-label handed the SECOND label the FIRST value (CCFN
# 2026-07-21: "Return on average assets and return on average equity were
# 1.70% and 14.65%, respectively" shipped ROE = 1.70). The generic pattern
# is suppressed inside these constructs; a candidate whose value trails
# into "and <num>% … respectively" belongs to the pair parser.
_RESP_TAIL = re.compile(
    r"^\s*(?:%\s*)?and\s+(?:\d[\d.]*|\.\d+)\s*%[^.;]{0,40}respectively", re.I)


def _pct_metric(text: str, label_re: str, band) -> float | None:
    """Percent metric: '<label> … <verb> X.XX%', the basis-point form
    '<label> … <verb> N bps' (÷100), or either side of an
    'A and B were X% and Y%, respectively' pair."""
    pat = re.compile(label_re + _conn(60) + _VERB + _NUM + r"\s*%", re.I)
    bps = re.compile(label_re + _conn(60) + _VERB +
                     r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:bps|basis points?)\b", re.I)
    # Pair forms: label FIRST ("<label> and <other> were (V1)% and V2%") and
    # label SECOND ("<other> and <label> were V1% and (V2)%"), respectively-
    # anchored so an unrelated 'and' can never bind.
    pair1 = re.compile(label_re + r"\s+and\s+[^.;%]{3,70}?\s+"
                       r"(?:were|are)\s+" + _NUM + r"\s*%\s+and\s+"
                       r"(?:\d[\d.]*|\.\d+)\s*%[^.;]{0,40}respectively", re.I)
    pair2 = re.compile(r"\band\s+" + label_re + r"\s+(?:were|are)\s+"
                       r"(?:\d[\d.]*|\.\d+)\s*%\s+and\s+" + _NUM +
                       r"\s*%[^.;]{0,40}respectively", re.I)

    def _gen():
        for m in pat.finditer(text):
            if _RESP_TAIL.match(text[m.end():m.end() + 60]):
                continue                  # the pair parser owns this value
            yield m.start(), m.group(1), m.end()
        for m in bps.finditer(text):
            yield m.start(), str(float(m.group(1)) / 100), m.end()
        for m in pair1.finditer(text):
            yield m.start(), m.group(1), m.end()
        for m in pair2.finditer(text):
            yield m.start(), m.group(1), m.end()
    return _clean(text, _gen(), band, _AGREE_PCT)


def _pinned_metric(text: str, label_re: str, denom_re: str, band) -> float | None:
    """Denominator-pinned percent: the base must appear either in the label
    ('<label> to <denominator> … X%') or right after the value ('<label> …
    X% of <denominator>') — a ratio against any other base never qualifies."""
    p_after = re.compile(label_re + _conn(80) + _NUM +
                         r"\s*%\s*of " + denom_re, re.I)
    p_label = re.compile(label_re + r"\s+to\s+" + denom_re +
                         _conn(40) + _VERB + _NUM + r"\s*%", re.I)
    def _gen():
        for m in p_after.finditer(text):
            yield m.start(), m.group(1), m.end()
        for m in p_label.finditer(text):
            yield m.start(), m.group(1), m.end()
    return _clean(text, _gen(), band, _AGREE_PCT)


# Per-share values. The connector [^$%]{0,40} can't cross an earlier $ or %,
# so a growth restatement ("up 8.2% from $22.71") can't be captured; the
# growth-then-value form ("increased 3% to $23.45") gets its own pattern
# whose one allowed % is the growth figure, with the value pinned to "to $".
_TBV_LABEL = r"tangible book value per (?:common )?share"
_TBV_PATS = [
    re.compile(_TBV_LABEL + r"[^$%]{0,40}?(?::|\b(?:of|was|were|at|"
               r"ended [^$%]{0,20}at))\s*\$\s?(\d{1,3}\.\d{2})", re.I),
    re.compile(_TBV_LABEL + r"[^$%]{0,30}?\d{1,2}(?:\.\d{1,2})?\s*%[^$%]{0,20}?"
               r"to\s*\$\s?(\d{1,3}\.\d{2})", re.I),
]
_TBV_BAND = (1.0, 500.0)

_DIV_PATS = [
    re.compile(r"(?<!special )(?<!annual )dividend of \$\s?(\d{1,2}\.\d{2,4}) per "
               r"(?:common )?share", re.I),
    re.compile(r"(?<!special )(?<!annual )dividend[^$%]{0,30}?to \$\s?"
               r"(\d{1,2}\.\d{2,4}) per (?:common )?share", re.I),
]
_DIV_BAND = (0.005, 10.0)

# Diluted EPS from prose — mega-cap releases narrate it but hide the number
# from table extraction (BAC renders its highlights as positioned <div>s with
# zero <table> markup; GS uses single-period KPI stacks; caught live
# 2026-07-14). LABEL-LED forms only, protected by _clean's guards (adjusted/
# compared-with exclusion, first-$ discipline, disagreement→None):
#   BAC: "Diluted earnings per share (EPS) of $1.21 compared to $0.90"
#   GS:  "Earnings Per Common Share of $ 20.98"   MS: "EPS of $3.43"
# The value-led "…, or $X.XX per diluted share" form is deliberately ABSENT:
# in "compared with $4.3 billion, or $2.60 per diluted share" the comparison
# marker sits outside the before-label window, and a release narrating
# per-share ONLY in that comparison clause would ship the year-ago EPS as a
# lone clean candidate.
# Connectors are 60 chars: OZK writes 'Diluted earnings per common share
# ("EPS") for the first quarter of 2026 were $1.44' — the parenthetical +
# period phrase is ~45 chars of connector. [^$%] still can't cross an
# earlier value, and a release that narrates BOTH quarters with full labels
# produces disagreeing candidates → None (never a column guess).
_EPS_PATS = [
    re.compile(r"diluted (?:earnings per (?:common )?share|eps)"
               r"(?:\s*\(“?\"?eps”?\"?\))?"
               r"[^$%]{0,60}?(?::|\b(?:of|was|were|at))\s*\$\s?(\d{1,2}\.\d{2})",
               re.I),
    re.compile(r"(?:\beps\b|earnings per (?:diluted|common) share)"
               r"[^$%]{0,60}?(?::|\b(?:of|was|were|at))\s*\$\s?(\d{1,2}\.\d{2})",
               re.I),
]
_EPS_BAND = (0.01, 60.0)


def _dollar_metric(text: str, pats, band) -> float | None:
    def _gen():
        for pat in pats:
            for m in pat.finditer(text):
                yield m.start(), m.group(1), m.end()
    return _clean(text, _gen(), band, _AGREE_USD)


def extract_release_metrics(html: str, expected_qend: str | None = None) -> dict:
    """{nim, efficiency, roa, roe, rotce, nco_ratio, npa_assets, acl_loans,
    tbv_ps, div_ps} from an earnings release — never guessed. Prose first
    (the narrated value is the bank's own headline); where prose gives None
    and `expected_qend` is known, a structurally-parsed TABLE value fills the
    gap (see extract_table_metrics — the current-quarter column is identified
    by its period HEADER, never by position luck). Percent keys are percents
    (3.42 = 3.42%); per-share keys are dollars."""
    text = _flat_text(html)
    out = {}
    for key, (label, band) in _PCT_SPECS.items():
        out[key] = _pct_metric(text, label, band)
    for key, (label, denom, band) in _PINNED_SPECS.items():
        out[key] = _pinned_metric(text, label, denom, band)
    out["tbv_ps"] = _dollar_metric(text, _TBV_PATS, _TBV_BAND)
    out["div_ps"] = _dollar_metric(text, _DIV_PATS, _DIV_BAND)
    out["eps_diluted"] = _dollar_metric(text, _EPS_PATS, _EPS_BAND)
    if expected_qend and any(v is None for v in out.values()):
        tab = extract_table_metrics(html, expected_qend)
        for k, v in tab.items():
            if out.get(k) is None:
                out[k] = v
    return out


# ── Table extraction (v2) ───────────────────────────────────────────────────
# For table-style releases (PNC/MTB/WFC narrate little): parse each <table>'s
# structure, find its PERIOD HEADER row, and read a metric row's cell in the
# column whose header equals the release's own quarter-end. Deterministic by
# construction — refused (n/a) whenever the structure is ambiguous:
#   - no period row, or the expected quarter absent from it → skip table
#   - expected quarter appears TWICE (quarter vs year-to-date columns) → skip
#   - a row's value count ≠ the period count (colspan drift) → skip row
#   - an adjusted/core label → skip row; disagreeing tables → None (as prose).

_MONTHS3 = {m[:3]: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_Q_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_QTOK = re.compile(r"\b([1-4])Q\s?['’]?(\d{2}|\d{4})\b", re.I)
_QWORD = re.compile(r"\b(first|1st|second|2nd|third|3rd|fourth|4th)\s+"
                    r"(?:quarter|qtr\.?),?\s+(\d{4})", re.I)
_QNUM = {"first": 1, "1st": 1, "second": 2, "2nd": 2,
         "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
# A bare ordinal-quarter header cell ("1st Qtr", "4th Quarter") — year on a
# separate row (TCBI/CFR split headers, caught in the 2026-07-14 sweep).
_QORD = re.compile(r"^\s*(1st|2nd|3rd|4th)\s+(?:quarter|qtr\.?)\s*$", re.I)
_DTOK = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
# Month-year with NO day ("Jun 2026", "September 2025") — FBK-style column
# headers (caught live 2026-07-13: every table skipped). Quarter-end months
# only; "May 2026" is a monthly column, not a quarter period.
_MONYR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(20\d{2})\b")
_QE_MONTH = {3: 31, 6: 30, 9: 30, 12: 31}
# Header cells that mark a trailing %-change/comparison column (FITB "Seq",
# "Yr/Yr"; generic "% Change", "bps"; JPM "$ O/(U)" over/under, and the
# analogous "B/(W)" better/worse) — never period columns.
_CHANGE_COL = re.compile(r"(?i)\b(?:seq|yr/yr|yoy|qoq|change|bps|%)\b|%"
                         r"|[ob]/\([uw]\)")


def _period_qend(cell: str) -> str | None:
    """A header cell's period as an ISO quarter-end date, or None. Accepts
    '1Q26' / 'Q1 2026'-less common '1Q 2026', 'First Quarter 2026', and a
    full date ('March 31, 2026'). Bare years are NOT periods (a 'Three months
    ended <date>' colspan above bare '2026 | 2025' cells is unresolvable at
    the cell level — the table is skipped instead)."""
    from datetime import date as _date
    m = _QTOK.search(cell)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        y += 2000 if y < 100 else 0
        mo, dy = _Q_END[q]
        return _date(y, mo, dy).isoformat()
    m = _QWORD.search(cell)
    if m:
        mo, dy = _Q_END[_QNUM[m.group(1).lower()]]
        return _date(int(m.group(2)), mo, dy).isoformat()
    m = _DTOK.search(cell)
    if m:
        mon = _MONTHS3.get(m.group(1)[:3].lower())
        if mon:
            try:
                return _date(int(m.group(3)), mon, int(m.group(2))).isoformat()
            except ValueError:
                return None
    m = _MONYR.search(cell)
    if m:
        mon = _MONTHS3.get(m.group(1)[:3].lower())
        if mon and mon in _QE_MONTH:
            return _date(int(m.group(2)), mon, _QE_MONTH[mon]).isoformat()
    return None


def _table_rows(table_html: str) -> list[list[str]]:
    """One <table>'s cells as [[cell_text, ...], ...] (tags stripped,
    entities unescaped, whitespace collapsed)."""
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        # Zero-width characters (C pads every cell with U+200B) are not
        # str.strip() whitespace — normalize them away or padding cells
        # read as non-empty labels/columns.
        rows.append([re.sub(r"\s+", " ",
                            re.sub("[\u200b\u200c\ufeff]", " ",
                                   _h.unescape(re.sub(r"(?s)<[^>]+>", " ", c)))
                            ).strip()
                     for c in cells])
    return rows


_FOOTNOTE = re.compile(r"^\(\d\)$")
# Point-first alternative for CBSH-style cells (".94"), see _NUM.
_CELL_NUM = re.compile(r"\(?\$?\s?(\d{1,3}(?:,\d{3})*\.?\d{0,4}|\.\d{1,4})\)?")


def _row_values(cells: list[str]) -> list[float]:
    """Numeric values across a row's non-label cells, in order. Footnote
    markers ('(1)') and symbol-only cells are skipped; a parenthesized
    decimal is negative; thousands separators are stripped."""
    vals = []
    for c in cells[1:]:
        if not c or _FOOTNOTE.match(c) or c in ("%", "$"):
            continue
        for m in _CELL_NUM.finditer(c):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            raw = m.group(0)
            if raw.startswith("(") and "." in m.group(1):
                v = -v
            elif _FOOTNOTE.match(raw):
                continue
            vals.append(v)
    return vals


# Table-row label anchors (matched at the START of the row's first cell) with
# the value kind: '%' rows must carry a percent sign somewhere, '$' rows a
# dollar sign — a same-named row in a different unit never qualifies.
_TABLE_SPECS = {
    "nim": (r"(?:net interest margin|nim\b)", "%", (0.5, 8.0)),
    "efficiency": (r"efficiency ratio", "%", (20.0, 110.0)),
    "roa": (r"return on (?:average )?(?:total )?assets", "%", (0.05, 4.0)),
    "roe": (r"(?:return on (?:average )?(?:common )?(?:shareholders['’]?,? )?equity"
            r"|roe\b)", "%", (0.5, 40.0)),
    "rotce": (r"(?:return on (?:average )?tangible common "
              r"(?:shareholders['’]?,? )?equity|rotce\b)", "%", (0.5, 60.0)),
    "nco_ratio": (r"net (?:loan )?charge-?offs?(?: \(recoveries\))?"
                  r"(?: (?:to|/) average (?:total )?loans| ratio)", "%", (0.0, 5.0)),
    "npa_assets": (r"non-?performing assets (?:to|/|as a percentage of) "
                   r"total assets", "%", (0.0, 10.0)),
    "acl_loans": (r"allowance for credit losses(?: on loans(?: hfi)?,?)? "
                  r"(?:to|/|as a percentage of) (?:total )?loans", "%",
                  (0.1, 6.0)),
    "tbv_ps": (r"tangible book value per (?:common )?share", "$", (1.0, 500.0)),
    "div_ps": (r"(?:cash )?dividends?(?: declared| paid)? per (?:common )?share",
               "$", (0.005, 10.0)),
    # Actuals fill (2026-07-13, owner): EPS + total revenue from the release
    # itself so the boards aren't blank while FMP's consensus feed lags a
    # fresh report. GAAP diluted EPS here; the ADJUSTED variant (what street
    # consensus usually compares to) is in _ADJ_TABLE_SPECS below.
    # Prefix ("Diluted EPS"), postfix ("Earnings per share - diluted", JPM)
    # and mid-form ("Earnings per diluted share", MS) label forms.
    "eps_diluted": (r"(?:diluted (?:eps\b|(?:earnings|net income)(?: \(loss\))? "
                    r"per (?:common )?share)"
                    r"|(?:earnings|net income)(?: \(loss\))? per (?:common )?"
                    r"share\s*[-–—]\s*diluted\b"
                    r"|(?:earnings|net income)(?: \(loss\))? per diluted "
                    r"(?:common )?share)", "$", (0.01, 60.0)),
    # "$K": magnitude row (reported in the table's stated unit — see the
    # in-thousands/in-millions scale sniff in extract_table_metrics); band
    # is in RAW dollars post-scale.
    # Anchored to label end (+ optional footnote) so taxable-equivalent /
    # adjusted variants ("Total revenue - TE (1)") never merge in and
    # trip the cross-table disagreement guard. The ", net of interest
    # expense" form (C) is deliberately INCLUDED so a firmwide row always
    # candidates alongside any segment-table "Total revenues" rows — they
    # disagree and the guard refuses, instead of a lone segment row
    # shipping as the firmwide figure. "Net revenue - reported" is JPM's
    # GAAP line ("- managed" never matches the end anchor's basis word).
    "total_revenue": (r"(?:total revenues?(?:, net of interest expense)?"
                      r"|net revenues?\s*[-–—]\s*reported)"
                      r"\s*(?:\(\d\))?\s*$", "$K", (5e6, 100e9)),
}

# Rows whose label is an "Adjusted …" variant are normally refused (the
# core/adjusted exclusion) — these specs OPT IN to exactly those rows.
_ADJ_TABLE_SPECS = {
    "eps_adj": (r"adjusted diluted (?:earnings|net income)(?: \(loss\))? per "
                r"(?:common )?share", "$", (0.01, 60.0)),
}


def extract_table_metrics(html: str, expected_qend: str) -> dict:
    """Metric values read from the release's structured tables at the column
    whose PERIOD HEADER equals `expected_qend` (ISO quarter-end). All the
    ambiguity refusals documented above apply; candidates across tables must
    agree (as prose) or the metric is None."""
    cands: dict = {k: [] for k in {**_TABLE_SPECS, **_ADJ_TABLE_SPECS}}
    for thtml in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(thtml)
        qends, hdr_i, n_change = None, None, 0
        for i, cells in enumerate(rows[:8]):
            found = [q for c in cells if (q := _period_qend(c))]
            if len(found) >= 2:
                # A caption/banner row ("2Q26 Change vs. | 1Q26 | 2Q25") can
                # sit ABOVE the real period header (JPM, caught live
                # 2026-07-14) — advance while the NEXT row carries strictly
                # MORE period tokens (value rows carry none, so this can
                # never walk past the header).
                while i + 1 < len(rows):
                    nxt = [q for c in rows[i + 1] if (q := _period_qend(c))]
                    if len(nxt) <= len(found):
                        break
                    found, i = nxt, i + 1
                qends, hdr_i = found, i
                # Trailing change columns on the header row itself ("$ O/(U)"
                # / "O/(U) %", "QoQ%", "% Change") — counted so value rows
                # that carry the change cells still align. Any trailing
                # non-change token → count nothing (rows with extras skip).
                last_p = max(j for j, c in enumerate(rows[i])
                             if _period_qend(c))
                trail = [c for c in rows[i][last_p + 1:] if c]
                if trail and all(_CHANGE_COL.search(c) for c in trail):
                    n_change = len(trail)
                break
            # FITB-style SPLIT header: month names on one row, years on the
            # next ("March | December | March" over "2026 | 2025 | 2025 |
            # Seq | Yr/Yr"). Pair them in order; extra non-year cells on the
            # year row are trailing change columns (counted for alignment).
            if i + 1 < len(rows):
                months = [_MONTHS3.get(c.strip().rstrip(".,")[:3].lower())
                          for c in cells if c.strip()]
                months = [m for m in months if m in _QE_MONTH]
                if len(months) >= 2 and months == [
                        _MONTHS3.get(c.strip().rstrip(".,")[:3].lower())
                        for c in cells if c.strip()]:
                    nxt = [c.strip() for c in rows[i + 1] if c.strip()]
                    years = [int(c) for c in nxt if re.fullmatch(r"20\d{2}", c)]
                    extras = [c for c in nxt if not re.fullmatch(r"20\d{2}", c)]
                    if (len(years) >= len(months)
                            and all(_CHANGE_COL.search(c) for c in extras)):
                        from datetime import date as _date
                        qends = [_date(y, m, _QE_MONTH[m]).isoformat()
                                 for m, y in zip(months, years)]
                        hdr_i, n_change = i + 1, len(extras)
                        break
            # TCBI-style split: ordinal-quarter row ("1st Quarter | 4th
            # Quarter | …") over a years row — pair 1:1 like months. A
            # leading non-year cell on the year row is the units/label slot
            # and is ignored; trailing extras must all be change tokens.
            if i + 1 < len(rows):
                ords = [_QORD.match(c) for c in cells if c.strip()]
                if len(ords) >= 2 and all(ords):
                    nxt = [c.strip() for c in rows[i + 1] if c.strip()]
                    if nxt and not re.fullmatch(r"20\d{2}", nxt[0]) \
                            and not _CHANGE_COL.search(nxt[0]):
                        nxt = nxt[1:]
                    years = [int(c) for c in nxt if re.fullmatch(r"20\d{2}", c)]
                    extras = [c for c in nxt if not re.fullmatch(r"20\d{2}", c)]
                    if (len(years) >= len(ords)
                            and all(_CHANGE_COL.search(c) for c in extras)):
                        from datetime import date as _date
                        qends = []
                        for om, y in zip(ords, years):
                            mo, dy = _Q_END[_QNUM[om.group(1).lower()]]
                            qends.append(_date(y, mo, dy).isoformat())
                        hdr_i, n_change = i + 1, len(extras)
                        break
            # CFR-style INVERTED split: a years row ("2026 | 2025") ABOVE the
            # ordinal-quarter row ("1st Qtr | 4th | 3rd | 2nd | 1st Qtr").
            # Colspans are lost in cell extraction, so year assignment is
            # provable only when the ordinals form exactly one strictly-
            # descending run per year (the standard descending-recency
            # layout) — any other arrangement is refused, never guessed.
            nz = [c.strip() for c in cells if c.strip()]
            if nz and all(re.fullmatch(r"20\d{2}", c) for c in nz) \
                    and i + 1 < len(rows):
                years = [int(c) for c in nz]
                qs = [_QORD.match(c) for c in rows[i + 1] if c.strip()]
                if len(qs) >= 2 and all(qs):
                    ordinals = [_QNUM[m.group(1).lower()] for m in qs]
                    run_idx, ridx = [], 0
                    for j, q in enumerate(ordinals):
                        if j and q >= ordinals[j - 1]:
                            ridx += 1
                        run_idx.append(ridx)
                    if ridx + 1 == len(years):
                        from datetime import date as _date
                        qends = []
                        for q, ri in zip(ordinals, run_idx):
                            mo, dy = _Q_END[q]
                            qends.append(_date(years[ri], mo, dy).isoformat())
                        hdr_i = i + 1
                        break
        if not qends:
            continue                      # no period row → skip table
        if qends.count(expected_qend) == 1:
            col = qends.index(expected_qend)
        elif qends.count(expected_qend) > 1 and hdr_i:
            # Combined "Three Months Ended | Six Months Ended" statement
            # header repeats the quarter-end date under BOTH spans (NPB
            # 2026-07-21: "June 30, 2026 | Mar 31, 2026 | June 30, 2025 |
            # June 30, 2026") — the bare count!=1 refusal threw away the
            # whole income statement, so EPS/revenue never table-filled and
            # FMP's junk $0.09 stood against the release's $0.60. The span
            # row directly above the period row PROVES the layout: a
            # quarter-span marker strictly before a YTD-span marker means
            # the quarter block leads, so the FIRST occurrence of the
            # expected quarter-end is the quarter column (YTD twins can
            # only sit in the trailing block). Any other arrangement keeps
            # the refusal — never guessed.
            above = " ".join(c for c in rows[hdr_i - 1] if c).lower()
            q_m = re.search(r"three months? ended|quarters? ended", above)
            y_m = re.search(r"(?:six|nine|twelve) months? ended", above)
            if not (q_m and y_m and q_m.start() < y_m.start()):
                continue
            col = qends.index(expected_qend)
        else:
            continue                      # ambiguous period row → skip table
        # Magnitude sniff for "$K" specs: the table's own units statement
        # (e.g. "(dollars in thousands, except per share data)") — anywhere
        # in the table (WFC puts it below the header row). No stated unit →
        # magnitude rows are refused, never guessed from size.
        table_text = " ".join(" ".join(c for c in cells if c)
                              for cells in rows).lower()
        # "in millions" / "$ in millions" / JPM's "($ millions, except per
        # share …)" — a unit word without either marker stays unrecognized.
        _m = re.search(r"(?:\bin|\(\$)\s?(thousands|millions|billions)\b",
                       table_text)
        scale = {None: None, "thousands": 1_000, "millions": 1_000_000,
                 "billions": 1_000_000_000}[_m.group(1) if _m else None]
        # Dollar affirmation for "$" (per-share) specs: WFC-style blocks put
        # the $ sign only on each block's FIRST row, so accept a row without
        # its own $ when the table's units statement covers per-share values.
        table_per_share = "per share" in table_text

        def _try(specs, label, row_text, vals):
            for key, (lab_re, kind, band) in specs.items():
                if not re.match(r"\s*" + lab_re, label, re.I):
                    continue
                if kind == "$K":
                    if scale is None:
                        continue
                    v = vals[col] * scale
                elif kind == "$" and "$" not in row_text and not table_per_share:
                    continue
                elif kind == "%" and "%" not in row_text and "$" in row_text:
                    # A $-bearing row can't be the ratio (TFC omits % signs
                    # entirely — the specific label + band disambiguate; only
                    # an explicit $ marks the row as a dollar line).
                    continue
                else:
                    v = vals[col]
                if band[0] <= v <= band[1]:
                    cands[key].append(v)

        for cells in rows[hdr_i + 1:]:
            # Label = the first NON-EMPTY cell (WFC/JPM indent data rows with
            # a leading empty cell — cells[0] as label skipped every row,
            # caught live 2026-07-13). Values parse only AFTER the label cell
            # so footnote digits in labels ("ROE 3") never enter the value
            # list and break period alignment.
            label_idx = next((i for i, c in enumerate(cells) if c), None)
            if label_idx is None:
                continue
            label = cells[label_idx]
            row_text = " ".join(cells)
            # _row_values itself skips its first cell (the label slot), so
            # hand it the row FROM the label cell onward.
            vals = _row_values(cells[label_idx:])
            if len(vals) == len(qends) + n_change and n_change:
                vals = vals[:len(qends)]  # trailing change cols (header-counted)
            if len(vals) != len(qends):
                continue                  # colspan drift → alignment unproven
            if _CONNECTOR_EXCLUDE.search(label):
                # adjusted/core variant row — only the opt-in specs may read it
                _try(_ADJ_TABLE_SPECS, label, row_text, vals)
                continue
            _try(_TABLE_SPECS, label, row_text, vals)
    out = {}
    for key, vs in cands.items():
        kind = {**_TABLE_SPECS, **_ADJ_TABLE_SPECS}[key][1]
        if kind == "$K":
            tol = max(_AGREE_USD, 0.002 * max(vs)) if vs else 0
        else:
            tol = _AGREE_USD if kind == "$" else _AGREE_PCT
            # Rounding-aware agreement: a headline table and a detail table
            # print the SAME figure at different precision (CFG 2026-07-16:
            # efficiency 61.1 vs 61.08 poisoned the key to None) — widen to
            # the coarsest candidate's half-ULP. Same-precision candidates
            # keep the strict tolerance, so a misaligned-column pair (3.17
            # vs 3.22) still refuses.
            if vs:
                tol = max(tol, 0.5 * 10.0 ** -min(_dec(v) for v in vs))
        if vs and (max(vs) - min(vs)) <= tol:
            out[key] = max(vs, key=_dec)      # the most precise candidate
        else:
            out[key] = None
    return out


def _dec(v: float) -> int:
    """Printed decimal places of a parsed table value (61.1 → 1, 61.08 → 2).
    repr() round-trips the short decimal strings these cells hold."""
    s = repr(v)
    return len(s.split(".", 1)[1]) if "." in s else 0


def _prior_quarter_end(qend: str | None) -> str | None:
    """ISO quarter-end immediately before `qend` (calendar quarters)."""
    if not qend:
        return None
    try:
        y, m, _ = (int(x) for x in qend.split("-"))
    except ValueError:
        return None
    prior = {3: (-1, 12), 6: (0, 3), 9: (0, 6), 12: (0, 9)}.get(m)
    if not prior:
        return None
    dy, pm = prior
    return f"{y + dy}-{pm:02d}-{_QE_MONTH[pm]}"


def _year_ago_qend(qend: str | None) -> str | None:
    """Same quarter-end one year earlier."""
    if not qend:
        return None
    try:
        y, rest = qend.split("-", 1)
        return f"{int(y) - 1}-{rest}"
    except ValueError:
        return None


# ── Fetch/cache layer ──────────────────────────────────────────────────────

def _current_accession(cik) -> str | None:
    """The FRONTIER accession — the newest 8-K that could BE the earnings
    release (Item 2.02, or exhibit-bearing without 2.02: ASB furnishes its
    news release under Item 9.01 only) per the (cheap) submissions index;
    None when unavailable. One request. Compared against the stored record's
    `frontier`, not its release accession, so a furnished non-release 8-K
    (investor deck) costs at most ONE re-selection pass — never a refetch
    loop."""
    from data.ir_provider import _earnings_8k_candidates
    from data.sec_filing_scraper import _get
    try:
        cik10 = str(int(cik)).zfill(10)
        subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    except Exception:
        return None
    cands = _earnings_8k_candidates(subs)
    return cands[0]["accession"] if cands else None


def release_metrics(cik) -> dict | None:
    """Extracted metrics for the CIK's latest earnings release:
    {metrics: {...}, capital: {...}, url, filed_date, accession} or None.

    Cached per CIK. An extraction is immutable per accession, so a fresh-
    within-15-min cache serves directly; past that only the submissions index
    is re-checked and the stored extraction is re-stamped unless a NEW
    accession appeared (then the new release is fetched and extracted).
    Failures never overwrite a good cached extraction."""
    if not cik:
        return None
    from datetime import datetime
    from data import cache as _cache
    from data.freshness import is_fresh

    # v15 (2026-07-21): respectively-pair form — first-%-after-label
    # handed the SECOND label the FIRST value (CCFN ROE 1.70, real
    # 14.65). v14 point-first decimals (".19%") in prose/cell
    # patterns — CBSH's entire number style was invisible. v13 rounding-
    # aware cross-table agreement — CFG's efficiency (61.1 headline vs
    # 61.08 detail) poisoned to None under the
    # strict tolerance. v12 prose EPS accepts "per common share" + the
    # OZK-style ~45-char parenthetical/period connector; v11 digit-free
    # percent connector + bps value form; v10 the label-affirmation guard;
    # v9 the financial-supplement feed; v8 ordinal-quarter headers; v7 the
    # prose diluted-EPS spec; v6 the JPM/C table shapes; v5 the guarded-AI
    # fill (data/release_ai). Extractions are immutable per accession, so
    # spec improvements MUST bump this version or cached releases never
    # re-extract.
    key = f"release_metrics:v16:{int(cik)}"
    try:
        cached = _cache.get(key)
    except Exception:
        cached = None
    # 15-min re-check: on report morning a fetch minutes before the 8-K
    # lands re-stamps LAST quarter's release — at the old 1h TTL, C served
    # its April release until 9:39 on Jul-14. One cheap submissions-index
    # request per bank per 15 min past TTL.
    if cached is not None and is_fresh(cached, 900):
        return cached.get("value")

    def _stamp(value):
        try:
            _cache.put(key, {"cached_at": datetime.now().isoformat(),
                             "value": value})
        except Exception:
            pass
        return value

    prev = (cached or {}).get("value")
    acc = _current_accession(cik)
    if acc is None:
        # Submissions unreachable: keep serving what we had (re-stamped so one
        # outage doesn't turn into a re-check storm); nothing if we had nothing.
        return _stamp(prev) if prev else None
    if prev and prev.get("frontier", prev.get("accession")) == acc:
        # Frontier unchanged — same release. A COMPLETE extraction is
        # immutable — but an AI fill that failed at extraction time must NOT
        # be locked in for the quarter (Jul-14: one bad window on report
        # morning left the six megabank exhibits deterministic-only,
        # permanently). Retry the AI fill against the same release, bounded.
        # Cap sized to the 900s re-check TTL: 24 × ~15 min ≈ a 6-hour retry
        # horizon (was 8 × 1h under the old hourly TTL).
        if prev.get("ai_state") == "ok" or prev.get("ai_attempts", 0) >= 24:
            return _stamp(prev)
        from data.ir_provider import latest_earnings_release as _ler
        try:
            rel = _ler(cik)
        except Exception:
            rel = None
        if rel and rel.get("accession") == prev.get("accession"):
            prev["ai_state"] = _ai_fill(prev, cik, rel)
        prev["ai_attempts"] = prev.get("ai_attempts", 0) + 1
        return _stamp(prev)

    from data.ir_provider import (_quarter_end_before, extract_capital_ratios,
                                  latest_earnings_release)
    try:
        rel = latest_earnings_release(cik)
    except Exception:
        rel = None
    if not rel:
        return _stamp(prev) if prev else None
    if prev and rel.get("accession") == prev.get("accession"):
        # The frontier moved (a furnished non-release 8-K) but the selected
        # release is unchanged — advance the stored frontier, keep the
        # extraction (and its AI state) intact.
        prev["frontier"] = acc
        return _stamp(prev)
    qend = _quarter_end_before(rel.get("filed_date") or "")
    prior_qend = _prior_quarter_end(qend)
    val = {
        "qend": qend,
        "metrics": extract_release_metrics(rel["html"], expected_qend=qend),
        # Q/Q from the SAME document's comparative column — same reporting
        # basis as the current quarter, zero extra fetches. Table-only (the
        # prose narrates the current quarter).
        "prior_metrics": (extract_table_metrics(rel["html"], prior_qend)
                          if prior_qend else {}),
        "prior_qend": prior_qend,
        "yoy_metrics": (extract_table_metrics(rel["html"], _year_ago_qend(qend))
                        if _year_ago_qend(qend) else {}),
        "yoy_qend": _year_ago_qend(qend),
        "capital": extract_capital_ratios(rel["html"]),
        "url": rel.get("url"),
        "filed_date": rel.get("filed_date"),
        "accession": rel.get("accession"),
        "frontier": acc,
    }
    val["ai_state"] = _ai_fill(val, cik, rel)
    val["ai_attempts"] = 1
    return _stamp(val)


# Segment DETAIL-page boundary. Generic "segment" is useless as a cut marker:
# consolidated pages embed segment-summary tables (names as row labels) and
# page-1 TOCs list every section — so the marker is a detail-page HEADER
# ("SEGMENT RESULTS", "<segment name> FINANCIAL HIGHLIGHTS"), and matches
# inside the TOC region are ignored. Measured on the 2026-07-14 JPM/BAC/C/WFC
# supplements: keeps consolidated NIM/ROA/capital/TBV, excludes every
# segment-ratio page (JPM Card 3.47% NCO).
_SEG_DETAIL = re.compile(
    r"(?i)\bsegment (?:results|detail|information)\b|"
    r"(?:consumer & community banking|corporate & investment bank|"
    r"asset & wealth management|commercial banking|consumer banking|"
    r"global wealth|global banking|global markets|institutional securities|"
    r"wealth (?:&|and) investment management|u\.?s\.? personal banking|"
    r"branded cards|retail services|services|markets|banking|wealth)"
    r"\s+(?:financial highlights|selected financial|segment results)")
_TOC_FLOOR = 8_000       # detail markers inside page 1 are a table of contents
_NO_MARKER_CAP = 25_000  # unrecognized layout → consolidated front tables only
                         # (C 2Q26: first business credit row at 26k)


def _consolidated_slice(sup_text: str, cap: int = 120_000) -> str:
    """The supplement's FIRMWIDE front section: everything before the first
    segment detail-page header (megabank supplements lead with consolidated
    highlights/statements, then per-segment pages whose qualified ratios —
    e.g. JPM Card Services' 3.47% NCO rate — must never reach the AI as
    firmwide candidates). No recognizable boundary → a conservative front
    cap. Cutting early loses coverage, never correctness."""
    for m in _SEG_DETAIL.finditer(sup_text):
        if m.start() >= _TOC_FLOOR:
            return sup_text[:m.start()][:cap]
    return sup_text[:_NO_MARKER_CAP]


def _ai_fill(val: dict, cik, rel: dict) -> str:
    """Fill val's None metric cells in place from the guarded-AI extraction
    (data/release_ai — verbatim-evidence verified, unit-safe keys only).
    Deterministic values are NEVER overwritten; capital keys land in the
    period dicts the exhibit reads. Returns the fill state: "ok" (filled, or
    nothing to do — no key configured) / "pending" (extraction failed; the
    caller retries on a later pass rather than locking the accession)."""
    try:
        from data.release_ai import has_api_key, release_ai_metrics
        if not has_api_key():
            return "ok"                   # nothing to retry without a key
        text = _flat_text(rel.get("html") or "")
        # Megabanks keep NIM/ROA/Tier 1 and the 5-quarter history tables in
        # the 8-K's SECOND exhibit (financial supplement) — append its
        # consolidated front section so those cells can fill. All release_ai
        # guards (verbatim quote, number-in-quote, bands, segment rejection,
        # period proof) apply unchanged.
        try:
            from data.ir_provider import earnings_supplement
            sup = earnings_supplement(cik, val.get("accession") or "")
        except Exception:
            sup = None
        if sup:
            text += ("\n\n[FINANCIAL SUPPLEMENT — CONSOLIDATED SECTION]\n"
                     + _consolidated_slice(_flat_text(sup.get("html") or "")))
        ai = release_ai_metrics(
            cik, val.get("accession") or "", text,
            qend=val.get("qend"), prior_qend=val.get("prior_qend"),
            yoy_qend=val.get("yoy_qend"))
    except Exception as e:
        print(f"[release_metrics] AI fill failed: {type(e).__name__}: {e}")
        return "pending"
    if not ai:
        return "pending"
    for bucket_key, period in (("metrics", "cur"), ("prior_metrics", "prior"),
                               ("yoy_metrics", "yoy")):
        bucket = val.setdefault(bucket_key, {})
        for k, v in (ai.get(period) or {}).items():
            if bucket.get(k) is None:
                bucket[k] = v
    # Current-quarter capital: the dedicated extractor's values win; AI fills
    # the gaps in the capital dict the exhibit merges over `metrics`.
    cap = val.setdefault("capital", {})
    for k in ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio"):
        if cap.get(k) is None and (ai.get("cur") or {}).get(k) is not None:
            cap[k] = ai["cur"][k]
    return "ok"
