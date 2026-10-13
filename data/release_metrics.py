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
    # JPM's name for it: "reported overhead ratio of 54% and managed overhead
    # ratio of 54%" (owner 2026-10-13: map it) — never the managed one.
    "efficiency": (r"(?:efficiency|(?<!managed )overhead) ratio", (20.0, 110.0)),
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
# A sentence-leading "Excluding …," clause qualifies the whole sentence:
# "Excluding AOCI losses/gains, the tangible book value of the Company's
# common stock … was $14.29 per share" is the non-GAAP variant of the GAAP
# $12.20 stated one sentence earlier (FDVA 2Q26). Same sentence only — no
# period, semicolon or dollar figure between the clause and the label.
_EXCLUDE_SENTENCE = re.compile(r"\bexcluding\b[^.;$]{0,60}$", re.I)
# A MULTI-QUARTER period in the candidate's label→value span or earlier in its
# sentence: the value is year-to-date/annual, never the quarter's. WBHC
# 2026-10-09's shareholder letter states only "Earnings Per Diluted Share for
# the nine-month period ended September 30, 2026, were $5.29" — shipped as the
# quarter's EPS actual ($5.29 vs a ~$1.8 quarter). n/a beats a period mix-up.
_MULTI_PERIOD = re.compile(
    r"\b(?:nine|six|twelve|9|6|12)[- ]months?\b|\byear[- ]to[- ]date\b|"
    r"\bytd\b|\bfirst (?:half|nine months)\b|\bfull[- ]year\b|"
    r"\bfor the (?:fiscal )?year\b(?![- ]ago)", re.I)
# …and the same period phrase right AFTER the value: GS 3Q26 "The firm's
# efficiency ratio was 59.4% for the first nine months of 2026, compared
# with 62.1% …" shipped as the quarter's efficiency (the quarter's isn't
# stated). The phrase must lead the tail — "18.7% for the third quarter of
# 2026 and 20.7% for the first nine months" keeps its quarter value.
_MULTI_PERIOD_AFTER = re.compile(
    r"^\s*,?\s*(?:(?:for|during|in|over) the (?:first |last |past |trailing )?"
    r"(?:nine|six|twelve|9|6|12)[- ]months?\b|(?:on a )?year[- ]to[- ]date\b"
    r"|ytd\b|for the (?:fiscal |full )?year\b(?![- ]ago))", re.I)

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
# "Operating expenses" is the GAAP expense line, not an "operating" variant:
# Citi 3Q26 "Efficiency Ratio (total operating expenses/total revenues, net)"
# was refused as non-GAAP.
_CONNECTOR_EXCLUDE = re.compile(
    r"\b(?:adjusted|core|operating(?! expenses?\b)|normalized|underlying|"
    r"pro forma)\b|"
    # A forecast, not a result: NEWT 2Q26 "within our 2Q26 EPS guidance range
    # of $0.42-$0.52" shipped $0.42 as diluted EPS (reported: $0.47).
    r"\b(?:guidance|outlook|forecast|target)\b", re.I)
# A table ROW label naming an exclusion ("Net interest margin FTE excluding
# episodic items", FFIC) is a variant too — table readers only (prose
# "excluding" handling lives in _EXCLUDE_*).
_ROW_VARIANT = re.compile(r"\bexcluding\b", re.I)


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
        if _EXCLUDE_SENTENCE.search(text[max(0, start - 70):start]):
            continue
        heading = _list_heading(text, start)
        if (_MULTI_PERIOD.search(text[start:end])
                or _MULTI_PERIOD.search(_sentence_head(text, start))
                or _MULTI_PERIOD_AFTER.match(text[end:end + 50])
                or _MULTI_PERIOD.search(_value_tail(text, end))
                or (_MULTI_PERIOD.search(heading)
                    and not _QTR_HDR.search(heading))):
            continue
        if _CONNECTOR_EXCLUDE.search(text[start:end]):
            continue
        if _TABLE_TAIL.match(text[end:end + 12]):
            continue
        if _EXCLUDE_AFTER.match(text[end:end + 30]):
            continue
        try:
            v = _Num(float(vs), _printed_dec(vs))
        except ValueError:
            return None
        if band[0] <= v <= band[1]:
            vals.append(v)
    # Printed precision tightens agreement: two two-decimal figures 0.01
    # apart are different numbers (BSRR 2Q26: the quarter's 3.74% beside the
    # six-month 3.75%), so the tolerance is the coarser half-ULP when that
    # is stricter than agree_tol ("3.7%" vs "3.71%" keeps agree_tol).
    if vals:
        agree_tol = min(agree_tol, 0.5 * 10.0 ** -min(_dec(v) for v in vals)
                        + 1e-9)
    if not vals or (max(vals) - min(vals)) > agree_tol:
        return None
    return vals[0]


# A sentence ends at ". " before a capital — past an optional list bullet
# ("o", "·", "▪") or page number ("2025. 3 ASSET QUALITY The…", TMP), and
# through zero-width spaces (JUVF) — or at a bullet / semicolon.
_SENTENCE_BREAK = re.compile(
    r"\.[\s​]+(?=(?:[o·▪*\-–—]\s+|\d{1,3}\s+)?[A-Z“\"(])|[■•▪●·;]")


def _sentence_head(text: str, start: int) -> str:
    """The candidate's sentence up to its label: BSRR 2Q26 "The Company's
    improved financial performance metrics for the first half of 2026
    include a net interest margin of 3.75%" puts its period phrase ~85
    chars before the label — past the old fixed 70-char window."""
    # One char past the label start, so a break right before the label
    # ("…twelve months. Net interest margin…", OVLY) still sees its capital.
    return _SENTENCE_BREAK.split(text[max(0, start - 400):start + 1])[-1][:-1]


_NEXT_VALUE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%|\$\s?\d")


def _value_tail(text: str, end: int) -> str:
    """The value's own trailing clause: up to the next percent / dollar
    value or the sentence end. WNEB 2Q26 "net charge-offs of $1.8 million,
    or 0.34% of average loans, on an annualized basis, for the six months
    ended June 30, 2026" — the period phrase trails the value past the
    leading-only _MULTI_PERIOD_AFTER; the quarter's 0.33% is the figure."""
    tail = text[end:end + 300]
    m = _NEXT_VALUE.search(tail)
    tail = tail[:m.start()] if m else tail
    # A sentence end here may sit before a closing quote (ZION: "…of
    # 0.06%.” OPERATING PERFORMANCE"), so any non-decimal period ends it.
    return re.split(r"[.;■•▪●·][\"”’)]*(?:\s|$)", tail, maxsplit=1)[0]


# An inline list bullet: a standalone "o" opening an item (BSRR's sub-
# bullets, also right after a heading: "● Solid Capital and Liquidity o
# Tangible book value …"), or a bullet glyph.
_BULLET = re.compile(r"(?<!\S)o(?=\s+[A-Z“\"(])|[·▪•●■]")


def _list_heading(text: str, start: int) -> str:
    """The heading of the bullet list a candidate sits in, or "": walking
    back over list items (each ends with a period) to the first segment
    that does not — BSRR 2Q26 "● Strong YTD Earnings and Profitability
    (first half compared to same period last year) o Diluted earnings per
    share … o Efficiency ratio (1) improved to 57.70%" (the quarter's is
    58.91%). Only a candidate in the FIRST sentence of its item counts —
    flattened text loses paragraph ends, so a later sentence may be a new
    paragraph after the list."""
    parts = _BULLET.split(text[max(0, start - 1500):start + 1])
    if len(parts) < 2 or _SENTENCE_BREAK.search(parts[-1]):
        return ""
    for seg in reversed(parts[:-1]):
        seg = _SENTENCE_BREAK.split(seg.strip())[-1].strip()
        # An item may lack a closing period, but a heading carries no value
        # (CHMG "▪ YTD net income of $18.0 million and EPS of $3.73 ▪ QTD
        # … EPS of $1.82 for the second quarter" is two items).
        if seg and not seg.endswith(".") and not _NEXT_VALUE.search(seg):
            return seg
    return ""


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


# "$6.3 million or " / "$12.3 million, or " right before a percent value.
_DOLLAR_OR = re.compile(r"\$\s?[\d,.]+\s*(?:[mb]illion|thousand)?\s*,?\s*or\s+$",
                        re.I)
_DOLLAR_PAIR_KEYS = frozenset({"nco_ratio"})

# "A and B were X% and Y%, respectively" — the pair form maps values by
# ORDER. First-%-after-label handed the SECOND label the FIRST value (CCFN
# 2026-07-21: "Return on average assets and return on average equity were
# 1.70% and 14.65%, respectively" shipped ROE = 1.70). The generic pattern
# is suppressed inside these constructs; a candidate whose value trails
# into "and <num>% … respectively" belongs to the pair parser.
_RESP_TAIL = re.compile(
    r"^\s*(?:%\s*)?and\s+(?:\d[\d.]*|\.\d+)\s*%[^.;]{0,40}respectively", re.I)
# The DOLLAR pair: "book value per share and tangible book value per share
# … of $14.50 and $14.09, respectively" (CCNB 2Q26 wire release, prod
# 2026-10-06: first-$-after-label served TBV $14.50 against the company's
# $14.09 — one candidate, inside every band, nothing to disagree with).
# A value trailed by "and $X" is one side of a pair whatever follows, so
# the generic patterns never take it; only the respectively-anchored pair
# patterns below may bind a pair, by ORDER. An unanchored pair yields
# nothing (the table fills it, else n/a) — never the first value.
_RESP_TAIL_USD = re.compile(r"^\s*and\s+\$\s?\d{1,3}\.\d{2}\b", re.I)


def _pct_metric(text: str, label_re: str, band,
                plain_only: bool = False,
                marked_plain: bool = False,
                dollar_pair: bool = False) -> float | None:
    """Percent metric: '<label> … <verb> X.XX%', the basis-point form
    '<label> … <verb> N bps' (÷100), or either side of an
    'A and B were X% and Y%, respectively' pair. `plain_only` drops a
    candidate marked tax-equivalent (_NIM_TE) in its label→value span or
    earlier in its sentence ("Fully taxable equivalent net interest margin
    was 3.38%", FRME) — the plain-basis release (see _plain_bases).
    `marked_plain` keeps ONLY candidates explicitly marked plain
    (_PLAIN_MARK: "GAAP net interest margin was 3.59%", COFS) — evidence
    that the release states the plain basis (see _plain_bases).
    `dollar_pair` admits the "$X million, or N%" form — a ratio companion
    to its dollar amount, true only of the credit ratios (_DOLLAR_PAIR_KEYS:
    "net charge-offs of $12.3 million, or 0.25%"). For a level metric it
    is a CHANGE: BKU 2Q26 "Net Interest Margin (NIM) p $6.3 million or 3%
    from prior quarter" (NII growth) shipped NIM 3.0 (table: 3.06%)."""
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

    def _te(start, label_end):
        """Tax-equivalent marker from the clause start to `label_end` (a
        pair's span holds the OTHER label, so only its own label counts).
        The clause starts after the last sentence break or value — FHN's
        "Net interest income (FTE) increased $9 million to $679 million and
        net interest margin of 3.49%" marks the income, not the margin.
        Under `marked_plain` it instead drops a candidate whose clause
        carries no plain marker."""
        before = re.split(r"\.\s|[■•;$%]", text[max(0, start - 80):start])[-1]
        clause = before + " " + text[start:label_end]
        return ((plain_only and bool(_NIM_TE.search(clause)))
                or (marked_plain and not _PLAIN_MARK.search(clause)))

    def _lab_end(m, lead=""):
        return m.start() + re.match(lead + label_re, text[m.start():],
                                    re.I).end()

    def _gen():
        for m in pat.finditer(text):
            if _RESP_TAIL.match(text[m.end():m.end() + 60]):
                continue                  # the pair parser owns this value
            if not dollar_pair and _DOLLAR_OR.search(text[m.start():m.start(1)]):
                continue                  # "$6.3 million or 3%": a change
            if not _te(m.start(), m.end()):
                yield m.start(), m.group(1), m.end()
        for m in bps.finditer(text):
            if not _te(m.start(), m.end()):
                g = m.group(1)            # "37 bps" → "0.37"
                yield (m.start(), f"{float(g) / 100:.{_printed_dec(g) + 2}f}",
                       m.end())
        for m in pair1.finditer(text):
            if not _te(m.start(), _lab_end(m)):
                yield m.start(), m.group(1), m.end()
        for m in pair2.finditer(text):
            if not _te(m.start(), _lab_end(m, r"and\s+")):
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
    # DOLLAR-change-then-level: "Tangible book value per share (Non-GAAP)
    # increased $1.67, or 15.6%, to $12.38 from $10.71" (FSRL 2Q26, found
    # 2026-08-02 auditing the 75 smallest banks). The two patterns above both
    # fail by design — their [^$%] connector cannot cross the $1.67 change — so
    # the form yielded nothing at all rather than a wrong number.
    #
    # Deliberately narrow: an explicit change VERB, then the change amount,
    # then an OPTIONAL ", or N%," clause, then the level pinned to "to $". The
    # capture is the level, never the change. Anything looser would risk
    # grabbing a change or a year-ago figure, which is the failure mode the
    # first-$ discipline elsewhere in this file exists to prevent.
    # The verb may carry a short period phrase before "by $": "Tangible Book
    # Value per share improved during the quarter by $0.12 to $12.20" (FDVA
    # 2Q26, 2026-10-06 — an OTC bank whose release is its only disclosure;
    # this form yielded nothing and the card showed no TBV at all).
    re.compile(_TBV_LABEL + r"[^$%]{0,40}?"
               r"\b(?:increased|decreased|rose|fell|grew|declined|improved)"
               r"(?:\s+(?:during|in|for|over)\s+the\s+(?:quarter|period|year|"
               r"first|second|third|fourth)[^$%]{0,12}?)?\s*"
               r"(?:by\s*)?\$\s?\d{1,3}\.\d{2}\s*,?\s*"
               r"(?:or\s*\d{1,2}(?:\.\d{1,2})?\s*%\s*,?\s*)?"
               r"to\s*\$\s?(\d{1,3}\.\d{2})", re.I),
    # "The tangible book value of the Company's common stock on June 30,
    # 2026, was $12.20 per share" — the level pinned to "was/of/at $X per
    # share" so the comparison figure after it is never the capture (FDVA).
    re.compile(r"tangible book value of (?:the )?(?:company'?s |bank'?s )?"
               r"common (?:stock|shares)[^$%]{0,50}?\b(?:was|were|of|at)\s*"
               r"\$\s?(\d{1,3}\.\d{2})\s*per (?:common )?share", re.I),
    # Pair forms (see _RESP_TAIL_USD): label FIRST — "<label> and <other>
    # … of/were $(V1) and $V2, respectively" — and label SECOND — "<other>
    # and <label> … of/were $V1 and $(V2), respectively". Respectively-
    # anchored so an unrelated "and" can never bind. The label-first form
    # is derived into the BV lists below by the label replace; the
    # label-second form starts with "and", so each label adds its own.
    re.compile(_TBV_LABEL + r"\s+and\s+[^$%.;]{3,70}?\b(?:of|was|were|at)\s*"
               r"\$\s?(\d{1,3}\.\d{2})\s+and\s+\$\s?\d{1,3}\.\d{2}"
               r"[^.;]{0,40}respectively", re.I),
    re.compile(r"\band\s+" + _TBV_LABEL + r"[^$%]{0,40}?\b(?:of|was|were|at)\s*"
               r"\$\s?\d{1,3}\.\d{2}\s+and\s+\$\s?(\d{1,3}\.\d{2})"
               r"[^.;]{0,40}respectively", re.I),
]
_TBV_BAND = (1.0, 500.0)

# Book value per share — the same three label-led forms as TBV. The label
# must not be the tail of "tangible (common) book value per share": prose
# patterns scan anywhere, so the fixed-width lookbehinds keep a TBV sentence
# from becoming a BV candidate. (Table rows match at the label START, so the
# table spec needs no such guard.) Without a deterministic BV the wire banks'
# BV/share came only from the AI fill — GLBZ 2026-10-01 showed TBV $7.16
# from its Q2 release beside a year-old reconstructed BV $7.10 (release
# table: "Book value per share $ 7.27").
_BV_LABEL = r"(?<!tangible )(?<!tangible common )book value per (?:common )?share"
# Plus the bare verb-then-level form: "book value per common share decreased
# to $18.38 compared to $19.80" (BANC 2Q26) — the level is pinned to "to $",
# so the comparison figure that follows is never the capture.
_VERB_TO = (r"[^$%]{0,40}?\b(?:increased|decreased|rose|fell|grew|declined|"
            r"improved)\s+to\s*\$\s?(\d{1,3}\.\d{2})")
_BV_PATS = [re.compile(p.pattern.replace(_TBV_LABEL, _BV_LABEL, 1), re.I)
            for p in _TBV_PATS if p.pattern.startswith(_TBV_LABEL)] + [
    re.compile(_BV_LABEL + _VERB_TO, re.I),
    re.compile(r"\band\s+" + _BV_LABEL + r"[^$%]{0,40}?\b(?:of|was|were|at)\s*"
               r"\$\s?\d{1,3}\.\d{2}\s+and\s+\$\s?(\d{1,3}\.\d{2})"
               r"[^.;]{0,40}respectively", re.I),
    # "the book value of the Company's common stock … was $X per share" —
    # the TBV sentence of the same shape is kept out by the lookbehind.
    re.compile(r"(?<!tangible )book value of (?:the )?(?:company'?s |bank'?s )?"
               r"common (?:stock|shares)[^$%]{0,50}?\b(?:was|were|of|at)\s*"
               r"\$\s?(\d{1,3}\.\d{2})\s*per (?:common )?share", re.I)]
_BV_BAND = (1.0, 900.0)
# Explicitly per-COMMON label — the only BV that may serve when the release
# carries preferred EQUITY: there a bare "book value per share" can be total
# equity (incl. preferred) ÷ common shares. NPB 2Q26 "Book value per share
# (GAAP)" $17.69 includes its $24,979K preferred (same rule as
# sec_earnings_8k.extract_reported_bvps_status).
_BV_COMMON_LABEL = r"(?<!tangible )book value per common share"
_BV_COMMON_PATS = [re.compile(p.pattern.replace(_TBV_LABEL, _BV_COMMON_LABEL, 1),
                              re.I) for p in _TBV_PATS if p.pattern.startswith(_TBV_LABEL)] + [
    re.compile(_BV_COMMON_LABEL + _VERB_TO, re.I),
    # "book value of the Company's COMMON stock … was $X per share" is per
    # common share by construction, so it serves even when the release is
    # prose-only (preferred can't be ruled out) or carries preferred equity.
    _BV_PATS[-1]]


def _bv_ties_to_common(html: str, bv: float | None) -> bool:
    """True when the release's own rows prove a bare-label BV is per COMMON
    share: BV × ending common shares ties (±0.5%, at a $/$K/$M scale) to the
    stated common equity or to total equity − preferred, and does NOT also
    tie to total equity (the NPB shape: total incl. preferred ÷ shares).
    TCBI 2Q26: $77.01 × 43,470,167 = $3,347.6M = $3,647.7M − $300M preferred.
    Anything unprovable is False — the caller then renders n/a."""
    if not bv:
        return False
    try:
        from data.sec_earnings_8k import _table_rows
        rows = _table_rows((html or "").encode("utf-8"))
    except Exception:
        return False

    def first(pred):
        return next((n[0] for cl, n in rows if pred(cl) and n and n[0]), None)

    shares = first(lambda cl: "shares outstanding" in cl and "average" not in cl
                   and "weighted" not in cl)
    total = first(lambda cl: re.fullmatch(
        r"(?:total )?(?:stockholders|shareholders)['’]? equity(?: \(gaap\))?", cl))
    common = first(lambda cl: re.fullmatch(
        r"(?:total )?common (?:stockholders['’]? |shareholders['’]? )?equity", cl))
    pref = first(lambda cl: "preferred stock" in cl and "dividend" not in cl
                 and "trust" not in cl)
    if not shares:
        return False

    def ties(target):
        return bool(target) and any(
            abs(bv * shares / s - target) / target < 0.005 for s in (1, 1e3, 1e6))
    per_common = ties(common) or (total and pref and ties(total - pref))
    return bool(per_common) and not ties(total)


def _release_shows_preferred_equity(html: str) -> bool:
    """True when the release carries a nonzero preferred-stock EQUITY row
    (sec_earnings_8k._shows_preferred_equity — one rule for both release
    readers). Unknown counts as True — no parseable table rows (a prose-only
    release can't rule preferred out) or a detection failure: the
    per-common-only path can only turn a value into n/a, never a wrong one."""
    try:
        from data.sec_earnings_8k import _shows_preferred_equity, _table_rows
        rows = _table_rows((html or "").encode("utf-8"))
        return _shows_preferred_equity(rows) if rows else True
    except Exception:
        return True

_DIV_PATS = [
    # JPM 3Q26 "Common dividend of $4.4 billion or $1.65 per share" — the
    # per-share figure trails the dollar total.
    re.compile(r"(?<!special )(?<!preferred )dividends? of \$\s?[\d,.]+ "
               r"(?:billion|million),? or \$\s?(\d{1,2}\.\d{2,4}) per "
               r"(?:common )?share", re.I),
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
# BASIC vs diluted. "Basic EPS of $1.25" / "Basic earnings per common share
# was $1.25" is never the diluted figure (the bare-EPS pattern anchors at
# "EPS" / "earnings per common share"). And the pair "Basic and diluted
# earnings per common share were $1.25 per share and $1.08 per share,
# respectively" (TCBX 2Q26 — diluted is $1.08) handed the FIRST value to
# the diluted reading: a value trailed by "(per share) and $X" is one side
# of a pair, which only _EPS_PAIR may bind — by the STATED order of "basic
# and diluted" / "diluted and basic". Anything else is n/a (the table fills).
_EPS_BASIC_BEFORE = re.compile(r"\bbasic\s+$", re.I)
_PER_SH = r"(?:\s+per\s+(?:common\s+|diluted\s+|basic\s+)?share)?"
_EPS_PAIR_TAIL = re.compile(_PER_SH + r"\s*,?\s*and\s+\$\s?\d", re.I)
# "respectively" is optional: "Reports 2Q26 Basic and Diluted EPS of $0.48
# and $0.47" (NEWT — its only statements; the statement table is a page
# image) maps by the stated order too. What the stated order can NOT map is
# a PERIOD pair: "Basic and diluted earnings per share for the quarters
# ended March 31, 2026 and 2025 were $1.43 and $0.97, respectively" (STLE
# 1Q26) is this year's and last year's EPS — 0.97 shipped as diluted.
_EPS_PAIR = re.compile(
    r"\b(basic|diluted)\s+(?:and|&)\s+(diluted|basic)\s+"
    r"(?:earnings per (?:common )?share|eps\b)(?:\s*\(“?\"?eps”?\"?\))?"
    r"([^$%]{0,60}?)(?::|\b(?:of|was|were|at))\s*\$\s?(\d{1,2}\.\d{2})"
    + _PER_SH + r"\s*,?\s*and\s+\$\s?(\d{1,2}\.\d{2})" + _PER_SH +
    r"(\s*,?\s*respectively)?", re.I)
# Two periods named in the label ("for the second quarter of 2026 and 2025",
# ENBP) or right after an unqualified pair ("$0.48 and $0.43 for 2Q26 and
# 1Q26") — the two values are periods, not basic/diluted.
_EPS_PAIR_PERIODS = re.compile(r"\b(?:and|&)\b", re.I)
_EPS_PAIR_PERIODS_AFTER = re.compile(
    r"^\s*,?\s*(?:for|in|during)\b[^.$;]{0,40}?\b(?:and|&)\b", re.I)


def _eps_metric(text: str) -> float | None:
    """Diluted EPS from prose: the label-led _EPS_PATS (never a basic figure,
    never one side of a pair) plus the order-mapped basic/diluted pair."""
    def _gen():
        for pat in _EPS_PATS:
            for m in pat.finditer(text):
                if _EPS_BASIC_BEFORE.search(text[max(0, m.start() - 12):m.start()]):
                    continue
                if _EPS_PAIR_TAIL.match(text[m.end():m.end() + 60]):
                    continue
                yield m.start(), m.group(1), m.end()
        for m in _EPS_PAIR.finditer(text):
            first, second = m.group(1).lower(), m.group(2).lower()
            if first == second or _EPS_PAIR_PERIODS.search(m.group(3)):
                continue
            if not m.group(6) and _EPS_PAIR_PERIODS_AFTER.match(
                    text[m.end():m.end() + 60]):
                continue
            basic, diluted = ((m.group(4), m.group(5)) if first == "basic"
                              else (m.group(5), m.group(4)))
            if float(diluted) > float(basic):
                continue                  # dilution never raises EPS
            yield m.start(), diluted, m.end()
    return _clean(text, _gen(), _EPS_BAND, _AGREE_USD)

# ADJUSTED / ex-items EPS — the basis street consensus is quoted on, so the
# Results board can only score a surprise once the feed's actual is proven to
# BE this figure (2026-10-06 review P0-1). Prose label forms, each verified
# against the bank's real 2Q26 EX-99.1 text:
#   HBAN "adjusted EPS 1 was $0.39"      (footnote digit before the verb)
#   ONB  "record adjusted EPS 1 of $0.65"
#   AUB  "adjusted diluted operating earnings per common share (1) of $0.94"
#   ISTR "Core diluted earnings per common share were $0.75 for the quarter"
#   FITB "adjusted EPS (a) of $1.02"
#   ZION "diluted EPS of $3.05 (or $1.74 excluding notable items)"
#   JPM  "NET INCOME EXCLUDING SIGNIFICANT ITEMS OF $16.9 BILLION ($6.14 PER
#        SHARE)"
_ADJ_EPS_PATS = [
    re.compile(r"\b(?:adjusted|core)(?: diluted)?(?: operating)?(?: diluted)? "
               r"(?:eps\b|earnings per (?:diluted |common )?share)"
               r"(?:\s*[-–—,]\s*diluted)?\s*(?:\(\w\)\s*|\d\s+)?"
               r"(?:\(non-?gaap\)\s*)?"
               r"(?::|\b(?:of|was|were))\s*\$\s?(\d{1,2}\.\d{2})", re.I),
    re.compile(r"\(or \$\s?(\d{1,2}\.\d{2}) excluding (?:notable|significant) "
               r"items\)", re.I),
    # JPM 3Q26 headline: "NET INCOME OF $15.8 BILLION ($5.75 PER SHARE)
    # EXCLUDING A SIGNIFICANT ITEM" (2Q26 put the exclusion first).
    re.compile(r"net income of \$\s?[\d,.]+ (?:billion|million)\s*\(\s*\$\s?"
               r"(\d{1,2}\.\d{2}) per (?:diluted )?(?:common )?share\s*\)\s*"
               r"excluding (?:an? |the )?(?:notable|significant) items?\b", re.I),
    re.compile(r"excluding (?:notable|significant) items of \$\s?[\d,.]+ "
               r"(?:billion|million)\s*\(\s*\$\s?(\d{1,2}\.\d{2}) per "
               r"(?:diluted )?(?:common )?share\)", re.I),
]
# A comparison in the same sentence before the candidate marks a PRIOR
# period's figure (ZION: "compared with 2Q25 Net Earnings of $243 million,
# diluted EPS of $1.63 (or $1.58 excluding notable items)"), as does "from"
# right before the label or a prior-period phrase right after the value (WAL:
# "rose 6.3% from an adjusted EPS 2 of $2.22 in the prior quarter").
_ADJ_EPS_PRIOR = re.compile(
    r"compared (?:with|to)|versus|\bvs\b|prior[- ](?:year|quarter)|"
    r"year[- ]ago|linked[- ]quarter", re.I)
_ADJ_EPS_FROM = re.compile(r"\bfrom\s+(?:an?\s+|the\s+)?$", re.I)
# Immediately after the value only: HBAN's "adjusted EPS 1 was $0.39, higher
# by $0.02 from the prior quarter" is the CURRENT figure plus a change.
_ADJ_EPS_PRIOR_AFTER = re.compile(
    r"^\s*(?:in|for|during) the (?:prior|previous|preceding|linked|"
    r"year[- ]ago)\b", re.I)
# "… of $2.45 and $4.69 for the three and six months …, respectively" — a
# value pair maps by ORDER, which only the period words disambiguate; the
# table path (column-proven) owns these, prose refuses them.
_ADJ_EPS_PAIR = re.compile(r"^\s*and \$", re.I)
# Sentence-bounded span: a period only continues it inside a decimal ($3.05).
_SPAN = r"(?:[^.;]|\.(?=\d))"
# ANY mention of a non-GAAP / ex-items EPS — broader than _ADJ_EPS_PATS on
# purpose: NTRS 2Q26 says "Excluding notable items in the period, earnings per
# share increased 40%" and states no figure. A release with such a mention
# but no extractable value leaves the board's basis UNCONFIRMED (n/a), never
# "no adjusted figure exists".
_ADJ_EPS_MARK = re.compile(
    r"\b(?:adjusted|operating|core|non-?gaap)\s+(?:diluted\s+)?(?:eps\b|"
    r"(?:net )?earnings per (?:diluted |common )?share|net income (?:and|&) "
    r"eps\b)"
    r"|\bexcluding\b" + _SPAN + r"{0,100}?(?:\beps\b|earnings per (?:diluted |"
    r"common )?share|\(\s*\$\s?\d{1,2}\.\d{2} per (?:diluted )?share\))"
    r"|(?:\beps\b|earnings per (?:diluted |common )?share)" + _SPAN +
    r"{0,60}?\bexcluding\b", re.I)


def _adj_eps_metric(text: str) -> float | None:
    """The release's adjusted / ex-items diluted EPS from prose, or None.
    Every clean candidate must agree to the cent (a release narrating two
    quarters' adjusted EPS without a comparison cue disagrees → None)."""
    vals = []
    for pat in _ADJ_EPS_PATS:
        for m in pat.finditer(text):
            before = text[max(0, m.start() - 90):m.start()].rsplit(". ", 1)[-1]
            after = text[m.end():m.end() + 60]
            if (_ADJ_EPS_PRIOR.search(before) or _ADJ_EPS_FROM.search(before)
                    or _ADJ_EPS_PRIOR_AFTER.match(after)
                    or _ADJ_EPS_PAIR.match(after)
                    or _MULTI_PERIOD_AFTER.match(after)):
                continue
            if _TABLE_TAIL.match(after[:12]):
                continue                  # flattened multi-period table row
            v = float(m.group(1))
            if _EPS_BAND[0] <= v <= _EPS_BAND[1]:
                vals.append(v)
    if not vals or (max(vals) - min(vals)) > _AGREE_USD:
        return None
    return vals[0]


# A conference-call / webcast NOTICE opens with the logistics (WSBC 2026-10-02,
# furnished under Item 2.02: "WesBanco, Inc. to Host 2026 Third Quarter
# Earnings Conference Call and Webcast on Thursday, October 22"). A results
# release opens with the results ("Reports Net Income of …").
_NOTICE_OPEN = re.compile(
    r"\b(?:to host|will host|hosts?|conference call|webcast|invites?)\b", re.I)
_RESULTS_OPEN = re.compile(
    r"\breport(?:s|ed)\b[^.]{0,120}?\b(?:net income|earnings|results|net loss)\b",
    re.I)


def is_call_notice(html: str) -> bool:
    """True when the document's opening (~500 chars) is call/webcast
    logistics and does not report results."""
    head = _flat_text(html)[:500]
    return bool(_NOTICE_OPEN.search(head)) and not _RESULTS_OPEN.search(head)


# Results language: what a figure-less exhibit must STATE to count as a
# report. Item 2.02 is not proof — NEWT furnished a DIVIDEND announcement
# under 2.02 (2026-09-14, "NewtekOne, Inc. Declares Dividends on Common Stock
# and Series B Preferred Shares") and WSBC a call notice (2026-10-02).
_STATES_RESULTS = re.compile(
    r"\bnet (?:income|earnings|loss)\b|\bearnings per (?:diluted |common |"
    r"basic )?share\b|\bdiluted (?:eps|earnings)\b|"
    r"\b(?:quarterly|quarter|financial|operating) results\b", re.I)


def states_results(html: str) -> bool:
    """True when an exhibit states results in its first ~2000 chars and is
    not a call notice. Judges an 8-K exhibit that yielded no quarterly
    figure: WBHC's nine-month shareholder letter ("Net Income for the nine
    months ended …") is its Q3 report; a dividend announcement or a call
    notice is not."""
    return (not is_call_notice(html)
            and bool(_STATES_RESULTS.search(_flat_text(html)[:2000])))


def adjusted_eps_stated(html: str) -> bool:
    """True when the release mentions any adjusted / non-GAAP / ex-items EPS
    (see _ADJ_EPS_MARK) — whether or not a value could be extracted."""
    return bool(_ADJ_EPS_MARK.search(_flat_text(html)))


def _dollar_metric(text: str, pats, band) -> float | None:
    def _gen():
        for pat in pats:
            for m in pat.finditer(text):
                if _RESP_TAIL_USD.match(text[m.end():m.end() + 40]):
                    continue              # the pair patterns own this value
                yield m.start(), m.group(1), m.end()
    return _clean(text, _gen(), band, _AGREE_USD)


def extract_release_metrics(html: str, expected_qend: str | None = None) -> dict:
    """{nim, efficiency, roa, roe, rotce, nco_ratio, npa_assets, acl_loans,
    tbv_ps, bv_ps, div_ps} from an earnings release — never guessed. Prose first
    (the narrated value is the bank's own headline); where prose gives None
    and `expected_qend` is known, a structurally-parsed TABLE value fills the
    gap (see extract_table_metrics — the current-quarter column is identified
    by its period HEADER, never by position luck). Percent keys are percents
    (3.42 = 3.42%); per-share keys are dollars."""
    text = _flat_text(html)
    out = {}
    for key, (label, band) in _PCT_SPECS.items():
        out[key] = _pct_metric(text, label, band,
                               dollar_pair=key in _DOLLAR_PAIR_KEYS)
    # Plain-basis NIM release (_plain_bases): a narrated NIM serves only
    # when dropping its tax-equivalent candidates leaves the SAME value — a
    # TE narration (FRME "Net interest margin on a fully taxable equivalent
    # basis of 3.38%"; its GAAP row is 3.25%) would sit beside plain prior /
    # year-ago columns, a mixed-basis delta. The full candidate set still
    # decides first, so dropping a TE candidate can never unmask a lone
    # disagreeing one (ATLO's "3.10% for the six months"). Efficiency
    # follows the same rule (_BASIS_KEYS).
    plain = _plain_bases(html)
    for key in plain:
        if out[key] is not None and out[key] != _pct_metric(
                text, *_PCT_SPECS[key], plain_only=True,
                dollar_pair=key in _DOLLAR_PAIR_KEYS):
            out[key] = None
    for key, (label, denom, band) in _PINNED_SPECS.items():
        out[key] = _pinned_metric(text, label, denom, band)
    out["tbv_ps"] = _dollar_metric(text, _TBV_PATS, _TBV_BAND)
    out["bv_ps"] = _dollar_metric(text, _BV_PATS, _BV_BAND)
    out["bv_ps_common"] = _dollar_metric(text, _BV_COMMON_PATS, _BV_BAND)
    out["div_ps"] = _dollar_metric(text, _DIV_PATS, _DIV_BAND)
    out["eps_diluted"] = _eps_metric(text)
    out["eps_adj"] = _adj_eps_metric(text)
    for key in plain:
        if out[key] is None or not expected_qend:
            continue
        # …and a narrated NIM that IS the release's own tax-equivalent row
        # value is the TE figure under a bare label: WTFC 2Q26 "Net interest
        # margin … at 3.52%" = its "fully taxable-equivalent (non-GAAP)"
        # row; its GAAP row (which then serves) is 3.50%.
        te = extract_table_metrics(html, expected_qend, specs={
            "te": (r"(?=.*?(?:" + _NIM_TE.pattern + r"))"
                   + _TABLE_SPECS[key][0], "%", _TABLE_SPECS[key][2])},
            adj_specs={}).get("te")
        if te is not None and abs(out[key] - te) < 0.005:
            out[key] = None
    if expected_qend and any(v is None for v in out.values()):
        tab = extract_table_metrics(html, expected_qend)
        for k, v in tab.items():
            if out.get(k) is None:
                out[k] = v
    bv_common = out.pop("bv_ps_common", None)
    if out.get("bv_ps") is not None and _release_shows_preferred_equity(html):
        out["bv_ps"] = (bv_common if bv_common is not None
                        else out["bv_ps"] if _bv_ties_to_common(html, out["bv_ps"])
                        else None)
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
# Q-first form ("Q2 2026", CUBB's reconciliation headers) — never a FISCAL
# quarter label ("Fiscal Q4 2026" is not a calendar quarter; RVSB/SMBC).
_QTOK_QF = re.compile(r"\bQ([1-4])\s?['’]?(\d{2}|\d{4})\b", re.I)
_FISCAL = re.compile(r"(?i)\bfiscal\b|\bfy")
# Numeric date ("6/30/2026", RF) — any valid date, like _DTOK, so a monthly
# column ("7/31/2026") still counts as a column, never silently dropped.
# Two-digit years read as 20xx ("6/30/26", CBFV 2Q26's ratio tables).
_NUMDATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
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
# analogous "B/(W)" better/worse; HBAN "LQ" linked-quarter; RF "2Q26 vs.
# 1Q26" comparisons, whose period tokens must never read as periods) —
# never period columns.
# EWBC "Qtr-o-Qtr | Yr-o-Yr" basis-point change columns.
_CHANGE_COL = re.compile(r"(?i)\b(?:seq|yr/yr|yoy|qoq|qtr-o-qtr|yr-o-yr|lq|vs|"
                         r"versus|change|"
                         r"bps|%)\b|%"
                         r"|[ob]/\([uw]\)")


def _period_qend(cell: str) -> str | None:
    """A header cell's period as an ISO quarter-end date, or None. Accepts
    '1Q26' / 'Q1 2026'-less common '1Q 2026', 'First Quarter 2026', and a
    full date ('March 31, 2026'). Bare years are NOT periods (a 'Three months
    ended <date>' colspan above bare '2026 | 2025' cells is unresolvable at
    the cell level — the table is skipped instead)."""
    from datetime import date as _date
    m = _QTOK.search(cell)
    if not m and not _FISCAL.search(cell):
        m = _QTOK_QF.search(cell)
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
    m = _NUMDATE.search(cell)
    if m:
        y = int(m.group(3))
        try:
            return _date(y + 2000 if y < 100 else y, int(m.group(1)),
                         int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


class _Cell(str):
    """A cell's text that remembers its HTML colspan / rowspan — the only
    proof of which header cells sit above which columns (_span_pairs)."""

    def __new__(cls, text: str, colspan: int = 1, rowspan: int = 1):
        o = super().__new__(cls, text)
        o.colspan, o.rowspan = colspan, rowspan
        return o


def _span_attr(attrs: str, name: str) -> int:
    m = re.search(name + r"\s*=\s*[\"']?\s*(\d{1,3})", attrs or "", re.I)
    return max(1, int(m.group(1))) if m else 1


def _table_rows(table_html: str) -> list[list[str]]:
    """One <table>'s cells as [[cell_text, ...], ...] (tags stripped,
    entities unescaped, whitespace collapsed). Each cell is a _Cell (a
    str) carrying its colspan / rowspan."""
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html):
        cells = re.findall(r"(?is)<t[dh]([^>]*)>(.*?)</t[dh]>", tr)
        # Zero-width characters (C pads every cell with U+200B) are not
        # str.strip() whitespace — normalize them away or padding cells
        # read as non-empty labels/columns.
        rows.append([_Cell(re.sub(r"\s+", " ",
                                  re.sub("[\u200b\u200c\ufeff]", " ",
                                         _h.unescape(re.sub(r"(?s)<[^>]+>", " ", c)))
                                  ).strip(),
                           _span_attr(a, "colspan"), _span_attr(a, "rowspan"))
                     for a, c in cells])
    return rows


def _span_layout(rows: list[list[str]], last: int) -> list[list[tuple]]:
    """[start, end) grid columns of every cell in rows[0..last], honoring
    colspan and the rowspans of cells above."""
    held: dict[int, int] = {}            # column → last row it is held for
    out = []
    for r_i, cells in enumerate(rows[:last + 1]):
        pos, c = [], 0
        for cell in cells:
            while held.get(c, -1) >= r_i:
                c += 1
            cs = getattr(cell, "colspan", 1)
            rs = getattr(cell, "rowspan", 1)
            pos.append((c, c + cs))
            if rs > 1:
                for k in range(c, c + cs):
                    held[k] = r_i + rs - 1
            c += cs
        out.append(pos)
    return out


def _span_year_columns(rows: list[list[str]], hdr_i: int) -> list | None:
    """Grid ranges of the year cells of a SPAN-PAIRED split header (the
    years row hdr_i under a month / ordinal row whose cell count differs —
    _span_pairs proved the pairing), else None. Value rows of such tables
    are read by grid position (_span_row_values): CFBK 2Q26 prints "2026 |
    2025 | % change | 2026 | 2025 | % change", and its EPS row carries no
    change cells — four values that only the column grid can place."""
    if hdr_i < 1:
        return None
    parts = [c.strip() for c in rows[hdr_i - 1] if c.strip()]
    yrow = [c.strip() for c in rows[hdr_i] if c.strip()]
    years = [c for c in yrow if _YEAR_CELL.match(c)]
    split = (all(_MONTHS3.get(c.rstrip(".,")[:3].lower()) in _QE_MONTH
                 for c in parts) or all(_QORD.match(c) for c in parts))
    # Only the split header itself: quarter-end month / ordinal cells over
    # bare years (+ change cells) — never a banner over period tokens (FNB
    # "For the Six Months Ended" over "2Q26 | 1Q26 | 2Q25 | 2026 | 2025").
    if (not split or len(parts) < 2 or len(parts) == len(years)
            or not all(_YEAR_CELL.match(c) or _CHANGE_COL.search(c) for c in yrow)
            or not _span_pairs(rows, hdr_i - 1, hdr_i)):
        return None
    lay = _span_layout(rows, hdr_i)
    return [lay[hdr_i][n] for n, c in enumerate(rows[hdr_i])
            if _YEAR_CELL.match(c.strip())]


def _span_row_values(cells: list[str], pos: list[tuple], ycols: list,
                     k: int) -> list | None:
    """The first k period values of a row by grid overlap with the header's
    year cells: one number per column (None when the column is blank or a
    slot), or None (row unread) when a column holds two numbers."""
    out = []
    for ys, ye in ycols[:k]:
        under = [c for c, (cs, ce) in zip(cells, pos) if c and cs < ye and ys < ce]
        vals = _row_values([""] + under, slots=True)
        if len(vals) > 1:
            return None
        out.append(vals[0] if vals else None)
    return out


def _span_pairs(rows: list[list[str]], i: int, j: int) -> list[tuple] | None:
    """[(part index, year), …] in year-row order when the HTML spans PROVE
    which period-part cell (row i: "June 30," / "1st Quarter") each year
    cell (row j) sits under: every year cell overlaps exactly one non-empty
    part cell (JMSB's year cells also span the "$" column left of the month
    cell), every part cell is over at least one year, in order. BANC 2Q26
    "June 30, | March 31, | June 30, | June 30," (the last colspan=3) over
    "2026 | 2026 | 2025 | 2026 | 2025". Anything else → None (refused)."""
    lay = _span_layout(rows, j)
    parts = [lay[i][n] for n, c in enumerate(rows[i]) if c.strip()]
    out = []
    for n, c in enumerate(rows[j]):
        if not _YEAR_CELL.match(c.strip()):
            continue
        ys, ye = lay[j][n]
        cover = [k for k, (ps, pe) in enumerate(parts) if ps < ye and ys < pe]
        if len(cover) != 1:
            return None
        out.append((cover[0], int(c.strip()[:4])))   # "2026 (1)"
    ks = [k for k, _ in out]
    if not out or ks != sorted(ks) or set(ks) != set(range(len(parts))):
        return None
    return out


_FOOTNOTE = re.compile(r"^\(\d\)$")
# Point-first alternative for CBSH-style cells (".94"), see _NUM.
_CELL_NUM = re.compile(r"\(?\$?\s?(\d{1,3}(?:,\d{3})*\.?\d{0,4}|\.\d{1,4})\)?")


# A cell that HOLDS a column without a number: "N/A" (UNTY's adjusted EPS
# for a quarter with no adjustments — "1.51 | N/A | 1.33"), "NM", or a
# dash ("—", or U+FFFD where a cp1252 dash was decoded as UTF-8). Dropping
# such a cell shifted every later value one column left, so the row either
# failed the width check (UNTY: 2 values under 3 periods) or, beside change
# columns, read a neighbor's value.
_SLOT_CELL = re.compile(r"(?i)^\*?(?:n/?a|n\.?m\.?|[—–\-�]+)$")


def _row_values(cells: list[str], slots: bool = False,
                n_periods: int = 0) -> list[float]:
    """Numeric values across a row's non-label cells, in order. Footnote
    markers ('(1)') and symbol-only cells are skipped; a parenthesized
    decimal is negative; thousands separators are stripped. `slots`: an
    N/A / NM / dash cell yields None in its column's place (see _SLOT_CELL)
    instead of vanishing — opt-in, callers must handle None. With slots,
    once `n_periods` values are in, a footnote-shaped "(6)" cell is a
    trailing change column's negative value, held as None (WFC 3Q26 "1.87 |
    2.00 | 1.66 | (6) | 13": dropping the "(6)" broke the 3 + 2 width) —
    inside the period columns it stays a footnote, as before."""
    vals = []
    for c in cells[1:]:
        if slots and c and (_SLOT_CELL.match(c) or (
                n_periods and len(vals) >= n_periods and _FOOTNOTE.match(c))):
            vals.append(None)
            continue
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
                if slots and n_periods and len(vals) >= n_periods:
                    vals.append(None)     # "(4)%": a change value (Citi)
                continue
            vals.append(_Num(v, _printed_dec(m.group(1))))
    return vals


def _printed_dec(s: str) -> int:
    """Digits printed after the decimal point ("3.90" → 2, "61.1" → 1)."""
    return len(s.split(".", 1)[1]) if "." in s else 0


class _Num(float):
    """A parsed value that remembers its PRINTED precision: "3.90" is a
    two-decimal figure, but float 3.9 reprs as one decimal — which widened
    the rounding-aware agreement to ±0.05 and let a six-month 3.85 agree
    with the quarter's 3.90 (GBCI 2Q26). Arithmetic yields plain floats."""

    def __new__(cls, v: float, dec: int):
        o = super().__new__(cls, v)
        o.dec = dec
        return o


# Ratio-denominator connectors ("to", "/", "as a % of", "as a percentage
# of") and the leading qualifiers a credit-ratio label may carry.
_PCT_OF = r"(?:to|/|as a (?:%|percent(?:age)?) of) "
_NCO_PREFIX = r"(?:(?:qtd|quarterly|annualized)\s+)*"

# Table-row label anchors (matched at the START of the row's first cell) with
# the value kind: '%' rows must carry a percent sign somewhere, '$' rows a
# dollar sign — a same-named row in a different unit never qualifies.
_TABLE_SPECS = {
    # JPM's NIM is "NET YIELD ON INTEREST-EARNING ASSETS" (its supplement's
    # average-rates block); the "… excluding Markets" memo row is a variant.
    "nim": (r"(?:net interest margin|nim\b|net yield on (?:average )?"
            r"interest-earning assets)", "%", (0.5, 8.0)),
    # JPM names its efficiency ratio the "overhead ratio" (owner 2026-10-13:
    # map it); the "managed" overhead ratio is a non-GAAP basis and never
    # starts a row label matched here (start-anchored).
    "efficiency": (r"(?:efficiency|overhead) ratio", "%", (20.0, 110.0)),
    "roa": (r"return on (?:average )?(?:total )?assets", "%", (0.05, 4.0)),
    "roe": (r"(?:return on (?:average )?(?:common )?(?:shareholders['’]?,? )?equity"
            r"|roe\b)", "%", (0.5, 40.0)),
    "rotce": (r"(?:return on (?:average )?tangible common "
              r"(?:shareholders['’]?,? )?equity|rotce\b)", "%", (0.5, 60.0)),
    # Denominator forms: "to average loans", "/ average loans", "as a % of
    # average total loans (annualized)" (WFC), "as a percentage of average
    # loans HFI" (FBK); a leading "annualized" / "QTD" qualifier is allowed.
    # The band floor is −1: a NET RECOVERY quarter prints a negative ratio
    # ("(0.02)"), a real value the 0.0 floor used to refuse.
    # Citi: "Total NCLs (annualized) as a % of total average loans" (net
    # credit losses = its net charge-offs).
    "nco_ratio": (_NCO_PREFIX + r"(?:net (?:loan )?charge-?offs?(?: \(recoveries\))?"
                  r"|(?:total )?(?:ncls?|net credit losses))"
                  r"(?:\s*\(annualized\))?"
                  r"(?: " + _PCT_OF + r"(?:qtd )?(?:total )?average (?:total )?loans"
                  r"| ratio| rate)", "%", (-1.0, 5.0)),
    # The inverted-sign label: "QTD annualized net recoveries (chargeoffs)
    # to QTD average loans" (UNTY) prints recoveries POSITIVE — the value is
    # negated into nco_ratio (see _INVERTED).
    "nco_ratio_inv": (_NCO_PREFIX + r"net recoveries\s*\(charge-?offs\)"
                      r"(?:\s*\(annualized\))? " + _PCT_OF +
                      r"(?:qtd )?average (?:total )?loans", "%", (-5.0, 1.0)),
    # "Nonaccrual assets (incl. OREO) to total assets" is the NPA line for
    # banks that name it so (owner 2026-10-13: map it — UNTY).
    "npa_assets": (r"(?:non-?performing|non-?accrual) assets(?: \([^)]{0,30}\))? "
                   + _PCT_OF + r"total assets", "%", (0.0, 10.0)),
    # Citi: "Total ACLL as a % of total loans" (allowance for credit losses
    # on loans).
    "acl_loans": (r"(?:allowance for credit losses(?: (?:on|for) loans(?: hfi)?,?)?"
                  r"|(?:total )?acll) " + _PCT_OF + r"(?:total )?loans", "%",
                  (0.1, 6.0)),
    # Capital ratios (kind "%r": the row must carry a % or say "ratio" — a
    # bare "Tier 1 capital | 25" in $ millions sits inside the band). Rows
    # scoped to the BANK subsidiary are refused (_capital_scope_ok); a
    # Standardized row beats an Advanced one (_SCOPE_PAIRS). The current-
    # quarter values only fill gaps the prose-corroborated extractor
    # (ir_provider.extract_capital_ratios) leaves — see merge_table_capital.
    "cet1_ratio": (r"(?:common equity tier (?:1|one)|cet ?1)(?:\s*\([^)]{0,12}\))?"
                   r"(?: risk-based)?(?: capital)?(?: ratio)?",
                   "%r", (4.0, 30.0)),
    # "Tier 1 capital to average assets" / "Tier 1 Capital / Average Assets"
    # / "Tier 1 capital to total assets" (PDLB) are LEVERAGE ratios.
    # AVBC: "Tier 1 capital as a percentage of average assets" too.
    "t1_ratio": (r"(?:risk-based )?tier (?:1|one) (?:risk-based )?capital"
                 r"(?!\s*(?:to|/|as a (?:%|percent(?:age)?) of)\s*"
                 r"(?:quarterly |adjusted )?(?:average |total )+assets)"
                 r"(?!.*leverage)", "%r", (5.0, 32.0)),
    "total_ratio": (r"(?:risk-based )?total (?:risk-based )?capital"
                    r"(?! to (?:quarterly |adjusted )?average)", "%r", (7.0, 35.0)),
    "lev_ratio": (r"(?:tier (?:1|one) )?leverage(?: ratio)?"
                  r"|tier (?:1|one) capital\s*(?:to|/|as a (?:%|percent(?:age)?)"
                  r" of)\s*(?:quarterly |adjusted )?(?:average |total )+assets",
                  "%r", (3.0, 20.0)),
    # COMMON only: "Tangible equity to tangible assets" includes preferred.
    "tce_ratio": (r"(?:tangible common equity|tce) (?:ratio|" + _PCT_OF +
                  r"(?:total )?tangible assets)", "%", (2.0, 25.0)),
    # Never "… deposits and other borrowings" (a funding cost) nor a spot
    # rate ("Cost of total deposits, Qtr-End" / "at period end").
    "cost_of_deposits": (r"(?:annualized |average )?(?:cost of (?:total )?deposits"
                         r"|total (?:cost of )?deposits? costs?)"
                         r"(?!.*\b(?:and|borrowings?|spot|qtr[- ]end|quarter[- ]end"
                         r"|period[- ]end|end of (?:the )?period|at)\b)",
                         "%", (0.0, 7.0)),
    # EWBC "Average loan yield" / "Average cost of deposits".
    "loan_yield": (r"(?:average )?(?:yield on (?:total )?loans(?: and leases)?"
                   r"|(?:total )?loans? yields?)", "%", (1.0, 12.0)),
    "tbv_ps": (r"tangible book value per (?:common )?share", "$", (1.0, 500.0)),
    "bv_ps": (r"book value per (?:common )?share", "$", (1.0, 900.0)),
    "bv_ps_common": (r"book value per common share", "$", (1.0, 900.0)),
    "div_ps": (r"(?:cash )?dividends?(?: declared| paid)? per (?:common )?share",
               "$", (0.005, 10.0)),
    # The bare "Dividends paid (QTD)" row (UNTY) is per share only by its
    # SECTION ("SHARE INFORMATION" / "COMMON SHARE DATA"); kind "$s" reads
    # it only under such a section — elsewhere the same label is a dollar
    # total (cash-flow / equity statements).
    "div_ps_sec": (r"(?:cash )?dividends?(?: declared| paid)?(?: \(qtd\))?\s*$",
                   "$s", (0.005, 10.0)),
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
    # GS: "Total net revenues" (its income statement's firmwide line).
    "total_revenue": (r"(?:total (?:net )?revenues?(?:, net of interest expense)?"
                      r"|net revenues?\s*[-–—]\s*reported)"
                      r"\s*(?:\(\d\))?\s*$", "$K", (5e6, 100e9)),
    # EPS tie-out inputs (analysis/valuation._release_eps_tie_out): net
    # income applicable to common ÷ weighted average diluted shares from the
    # SAME release must reproduce eps_diluted. NI is "$K" (scale-sniffed
    # like total_revenue; no stated unit → refused). The share count is "#":
    # the RAW printed value — share rows routinely carry a different
    # magnitude than the table's dollar unit ("dollars in thousands, except
    # per share data" says nothing about the share row), so a sniffed scale
    # would be a guess. The tie-out instead tries the count at ×1/×1e3/×1e6
    # (the sec_earnings_8k._internal_tie_out discipline) and its band is far
    # tighter than any cross-scale gap, so an unknown scale can never fake a
    # tie. Both labels are end-anchored like total_revenue so "…per diluted
    # share" / basic-share variants never merge in; a bare "Diluted" sub-row
    # under a "Weighted average shares outstanding:" header has no anchor
    # and is deliberately unreachable (coverage loss, never a wrong row).
    "ni_common": (r"net income(?: \(loss\))? "
                  r"(?:applicable|available|attributable) to common "
                  r"(?:(?:share|stock)holders['’]?|shares)"
                  r"\s*(?:\(\d\))?\s*$", "$K", (1e5, 60e9)),
    "dil_shares": (r"(?:(?:weighted[- ])?average (?:number of )?(?:common )?"
                   r"shares(?: outstanding)?\s*[-–—:]\s*diluted"
                   r"|diluted (?:weighted[- ])?average (?:common )?shares"
                   r"(?: outstanding)?"
                   r"|(?:weighted[- ])?average diluted (?:common )?shares"
                   r"(?: outstanding)?)"
                   r"\s*(?:\((?:in )?(?:thousands|millions|\d)\))?\s*$",
                   "#", (10.0, 1e12)),
}

# Rows whose label is an "Adjusted …" variant are normally refused (the
# core/adjusted exclusion) — these specs OPT IN to exactly those rows.
_ADJ_TABLE_SPECS = {
    # Real 2Q26 row labels: "Adjusted diluted earnings per share" (FBK),
    # "Adjusted EPS, diluted" (ONB), "Adjusted diluted EPS" (EWBC),
    # "Adjusted operating earnings per common share, diluted" (AUB),
    # "Adjusted earnings per share - diluted (non-GAAP)" (NBHC), "Core
    # diluted earnings per common share (non-GAAP)" (NIC/ISTR). A "- basic"
    # row is never the diluted figure.
    "eps_adj": (r"(?:adjusted|core)(?: diluted)?(?: operating)?(?: diluted)? "
                r"(?:eps\b|(?:earnings|net income)(?: \(loss\))? per "
                r"(?:diluted |common )?share)(?!\s*[-–—,]?\s*basic)",
                "$", (0.01, 60.0)),
}


def _period_header(rows: list[list[str]], detail: bool = False) -> tuple | None:
    """The table's PERIOD HEADER: (qends, hdr_i, span_i, n_change) — the ISO
    quarter-end per period column, the header row index, the row where a
    "Three Months Ended | Six Months Ended" span banner would sit, and the
    count of trailing extra columns (%-change, year-to-date) value rows may
    also carry — or None when no row proves the layout (bare years, lone
    dates). Shared by the single-column reader (extract_table_metrics), the
    period-series reader (extract_table_series) and release_shares: one
    header proof, several consumers.

    `detail=True` returns (qends, hdr_i, span_i, n_change, widths): the
    set of row value counts that prove alignment. A plain header accepts
    the quarters alone or + every change column (as always). An EXTENDED
    header — trailing change-period or year-to-date columns folded out of
    the periods (_fold_change_periods / the YTD trail) — accepts any count
    from the quarters up to every extra column: the quarter columns LEAD,
    and its ratio rows print a subset of the extras (JPM's supplement:
    quarters + YTD, no change cells). Dash / N/A / NM cells hold their
    columns (_row_values slots), so a missing value can't shift a quarter.
    The 4-tuple's n_change is the full extra count."""
    got = _period_header_raw(rows)
    if not got:
        return None
    qends, hdr_i, span_i, n_change = got[:4]
    n_ytd = got[4] if len(got) > 4 else 0
    qends, n_fold, n_rest = _fold_change_periods(rows, qends, hdr_i)
    if detail:
        k = len(qends)
        # A folded change period may print as a "$/bp | %" PAIR (FHN 2Q26:
        # "… | 0.15 | $ — | — % | $ 0.02 | 13 %") — up to two cells each.
        extra = n_change + 2 * n_fold + n_ytd
        full = k + extra
        if n_fold or n_ytd:
            # A row narrower than the full width must still be WIDER than
            # the year-to-date block (its periods + any trailing change
            # cells): FUNC 2Q26 prints "Return on average assets | 1.20 |
            # 1.20" under "Three Months Ended | Six Months Ended" twins —
            # the SIX-month pair (its quarterly NIM is 3.98%, the row's
            # 3.89%), which a quarters-first reading took as the quarter.
            ytd_cols = n_ytd + n_rest + (n_change if n_rest else 0)
            widths = {w for w in range(k, full + 1)
                      if w == full or w > ytd_cols}
        else:
            widths = {k, k + n_change}
        inter = _interleaved(rows[hdr_i], k, k + n_fold)
        return qends, hdr_i, span_i, n_change, widths, inter
    return qends, hdr_i, span_i, n_change + n_fold + n_ytd


def _interleaved(hdr_cells: list[str], k: int, n_raw: int) -> tuple | None:
    """(value positions of the first k periods, full row width) when the
    header row prints CHANGE columns BETWEEN its period columns — BMRC 2Q26
    "June 30, 2026 | March 31, 2026 | % Change | June 30, 2025 | % Change |
    June 30, 2026 | June 30, 2025 | % Change": a positional read put the
    9.4% change in the year-ago EPS column ($9.40 vs the real $(0.53)).
    Each header cell is one value column, so a FULL-width row maps period j
    to its own position; a row of exactly k values (change cells left
    blank — ASB's ratio rows) reads in order. None when every change column
    trails the periods (the ordinary layouts), or when the change cells
    name periods themselves (MTB "Change 2Q26 vs. 1Q26") and were already
    counted as header columns — the legacy positional read is right there."""
    nz = [c for c in hdr_cells if c]
    per = [j for j, c in enumerate(nz) if _period_qend(c)
           and not _CHANGE_COL.search(c)]
    if len(per) < 2 or len(per) != n_raw or len(per) < k:
        return None
    seq = nz[per[0]:]
    pos = [j - per[0] for j in per]
    if not any(_CHANGE_COL.search(c) for c in seq[:pos[-1]]):
        return None
    return pos[:k], len(seq)


def _fold_change_periods(rows: list[list[str]], qends: list[str],
                         hdr_i: int) -> tuple[list[str], int, int]:
    """(qends, n_folded, n_ytd_folded): trailing header columns that RE-NAME leading
    periods are extra columns, not periods —
      CHANGE columns: a run of distinct NON-current leading periods under a
      "change" banner (WFC 3Q26 "Sep 30, 2026 | Jun 30, 2026 | Sep 30, 2025
      | Jun 30, 2026 | Sep 30, 2025" under "Sep 30, 2026 % Change from"; GS
      "% CHANGE FROM"; JPM "3Q26 Change" over "2Q26 | 3Q25");
      then optionally a YEAR-TO-DATE block of distinct leading periods,
      when the band carries a quarter marker BEFORE a year-to-date marker
      (WFC supplement "Quarter ended … Nine months ended": the YTD block
      trails the quarters — NPB's "Three Months Ended | Six Months Ended"
      twin columns fold the same way; a YTD-first band is refused as
      before, never folded).
    Read as periods, the duplicates made the prior and year-ago columns
    ambiguous (refused) and every 3-value ratio row mismatched the 5-period
    width (skipped)."""
    n = len(qends)
    above = [r for r in rows[:hdr_i] if any(r)][-3:]
    band = " ".join(c for r in above + [rows[hdr_i]] for c in r if c)
    q_m, y_m = _QTR_HDR.search(band), _YTD_HDR.search(band)
    ytd_after = bool(q_m and y_m and q_m.start() < y_m.start())
    for k in range(2, n):
        head, tail = qends[:k], qends[k:]
        if len(set(head)) != k:
            break
        if not all(q in head for q in tail):
            continue
        j = 0
        while j < len(tail) and tail[j] in head[1:] and tail[j] not in tail[:j]:
            j += 1
        rest = tail[j:]
        if j and not re.search(r"(?i)\bchange\b", band):
            break
        if rest and not (ytd_after and len(set(rest)) == len(rest)):
            break
        return head, n - k, len(rest)
    return qends, 0, 0


def _period_header_raw(rows: list[list[str]]) -> tuple | None:
    """_period_header's layout proof before change-period folding: a
    4-tuple, or a 5-tuple whose last item counts trailing year-to-date
    columns (see the single-row branch)."""
    # span_i: the row where a "Three Months Ended | Six Months Ended"
    # SPAN banner would sit for this table's header shape — directly
    # above the period row for a single-row header, but above the MONTH
    # row for a split month/year header (ONB 2Q26: span, then "June 30,"
    # cells, then years — three rows; found 2026-08-20 wiring the EPS
    # tie-out, whose NI/share rows live in exactly those tables).
    # The first 8 NON-BLANK rows: Workiva supplements (JPM) pad tables with
    # blank rows that used to push a real header past a fixed 8-row window.
    seen = 0
    for i, cells in enumerate(rows):
        if not any(cells):
            continue
        seen += 1
        if seen > 8:
            break
        found = [q for _, q in _row_periods(cells)]
        if len(found) >= 2:
            # A caption/banner row ("2Q26 Change vs. | 1Q26 | 2Q25") can
            # sit ABOVE the real period header (JPM, caught live
            # 2026-07-14) — advance while the NEXT row carries strictly
            # MORE period tokens (value rows carry none, so this can
            # never walk past the header).
            while i + 1 < len(rows):
                nxt = [q for _, q in _row_periods(rows[i + 1])]
                if len(nxt) <= len(found):
                    break
                found, i = nxt, i + 1
            n_change = 0
            # Trailing change columns on the header row itself ("$ O/(U)"
            # / "O/(U) %", "QoQ%", "% Change", RF "2Q26 vs. 1Q26") —
            # counted so value rows that carry the change cells still
            # align. Any trailing non-change token → count nothing (rows
            # with extras skip).
            periods = _row_periods(rows[i])
            # The banner band: the rows above + the header row up to its
            # LAST period cell. Trailing extra columns are not a banner — CTBI
            # "2Q 2026 | 1Q 2026 | 2Q 2025 | YTD 2026 | YTD 2025" is a quarter
            # header with YTD columns beside it (the point-in-time BV row
            # carries the three quarter values only) — and trailing bare-
            # YEAR columns are where a YTD banner belongs: FNB "For the Six
            # Months Ended June 30," over "2Q26 | 1Q26 | 2Q25 | 2026 | 2025".
            trail = [c for c in rows[i][periods[-1][0] + 1:] if c]
            if (not any(_YEAR_CELL.match(c) for c in trail) and _ytd_only_head(
                    rows[:i] + [rows[i][:periods[-1][0] + 1]], i, i)) or (
                    _novel_header(rows[i]) and (
                        _ytd_only_head(rows, i, i)
                        or any(_CHANGE_COL.search(c) for c in
                               rows[i][periods[0][0]:periods[-1][0]] if c))):
                # Refused under a lone year-to-date banner, in EVERY header
                # form — KEY "Six months ended" over "6/30/2026 | 6/30/2025",
                # and the legacy date / 2Q26 forms just the same: NKSH "Six
                # Months Ended" over "June 30, 2026 | June 30, 2025" read the
                # six-month diluted EPS $1.57 as the quarter's ($0.79), OPBK
                # "For the Six Months Ended" over "2Q2026 | 2Q2025" the
                # six-month efficiency 60.65 as 2Q25's (59.25). A header
                # readable only through the new forms (numeric dates,
                # Q-first tokens, dropped comparison cells) is also refused
                # with change columns BETWEEN its periods (AUB "6/30/2026 |
                # 3/31/2026 | QoQ | QoQ % change | 6/30/2025 | …": a blank
                # change cell would shift a period) — never guessed.
                return None
            if trail and all(_CHANGE_COL.search(c) for c in trail):
                n_change = len(trail)
            elif (trail and all(_YEAR_CELL.match(c) or _CHANGE_COL.search(c)
                                for c in trail)
                  and any(_YEAR_CELL.match(c) for c in trail)):
                # Trailing bare years under a year-to-date banner beside a
                # QUARTERLY banner (JPM supplement: "QUARTERLY TRENDS |
                # NINE MONTHS ENDED SEPTEMBER 30," over "3Q26 | … | 3Q25 |
                # … | 2026 | 2025 | 2025"): a YTD block AFTER the quarter
                # columns — extra columns, never periods.
                band = " ".join(c for r in [r for r in rows[:i] if any(r)][-2:]
                                for c in r if c)
                if _YTD_HDR.search(band) and _QTR_HDR.search(band):
                    return found, i, i - 1, n_change, len(trail)
            return found, i, i - 1, n_change
        # FITB-style SPLIT header: month names on one row, years on the
        # next ("March | December | March" over "2026 | 2025 | 2025 |
        # Seq | Yr/Yr"). Pair them in order; extra non-year cells on the
        # year row are trailing change columns (counted for alignment).
        # The counts must pair EXACTLY: FCCO 2Q26 prints "June 30, | March
        # 31, | June 30," over six years (each month spans a 2026 | 2025
        # pair); zipping took Jun-26 / Mar-25 / Jun-26 and dropped three
        # year cells. Unequal counts pair only when the HTML colspans prove
        # each year's month (_span_pairs) — else no header (n/a).
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
                pairs = (list(enumerate(years)) if len(years) == len(months)
                         else _span_pairs(rows, i, i + 1))
                if pairs and all(_CHANGE_COL.search(c) for c in extras):
                    if _ytd_only_head(rows, i, i + 1):
                        return None       # FITB "Year to Date" over "June | June"
                    from datetime import date as _date
                    qends = [_date(y, months[k], _QE_MONTH[months[k]]).isoformat()
                             for k, y in pairs]
                    # span banner sits above the months row
                    return qends, i + 1, i - 1, len(extras)
        # TCBI-style split: ordinal-quarter row ("1st Quarter | 4th
        # Quarter | …") over a years row — pair 1:1 like months. A
        # leading non-year cell on the year row is the units/label slot
        # and is ignored; trailing extras must all be change tokens. Counts
        # must pair exactly (see the month/year branch) — never truncated.
        if i + 1 < len(rows):
            ords = [_QORD.match(c) for c in cells if c.strip()]
            if len(ords) >= 2 and all(ords):
                nxt = [c.strip() for c in rows[i + 1] if c.strip()]
                if nxt and not re.fullmatch(r"20\d{2}", nxt[0]) \
                        and not _CHANGE_COL.search(nxt[0]):
                    nxt = nxt[1:]
                years = [int(c) for c in nxt if re.fullmatch(r"20\d{2}", c)]
                extras = [c for c in nxt if not re.fullmatch(r"20\d{2}", c)]
                pairs = (list(enumerate(years)) if len(years) == len(ords)
                         else _span_pairs(rows, i, i + 1))
                if pairs and all(_CHANGE_COL.search(c) for c in extras):
                    from datetime import date as _date
                    qends = []
                    for k, y in pairs:
                        mo, dy = _Q_END[_QNUM[ords[k].group(1).lower()]]
                        qends.append(_date(y, mo, dy).isoformat())
                    return qends, i + 1, i - 1, len(extras)
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
                    return qends, i + 1, i - 1, 0
        # Split shapes the legacy branches above refuse (_split_header).
        got = _split_header(rows, i)
        if got:
            return got
    return None


# ── Split (multi-row) period headers ────────────────────────────────────────
# Colspans are lost in cell extraction, so a header split across rows is
# placed only when the rows PROVE the pairing:
#   period-part row ("June 30," / "1st Quarter" / "Q2" / bare "Second" over
#   a "Quarter" row) then a years row  → 1:1 (equal counts), or
#   ordinal parts + one trailing year-to-date cell over K+2 years whose
#   tail is [latest, latest-1] (CUBB) → K quarters + 2 YTD columns;
#   a years row ABOVE the parts (CFR "2026 | 2025" over "1st Qtr | 4th |
#   3rd …", HBAN "2026 | 2025" over "Second | First | Fourth …" over
#   "Quarter …") → 1:1, or one strictly-descending run of quarters per year.
# A leading units/caption cell ("(dollars in thousands)") is allowed on any
# header row; trailing cells must all be change columns. Anything else →
# None (the table is skipped, never guessed).
_YTD = "YTD"                 # a year-to-date column: kept, never a quarter
_YEAR_CELL = re.compile(r"^20\d{2}(?:\s*\(\d\))?$")
_MON_CELL = re.compile(
    r"(?i)^(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|"
    # "SEPTEMBER 30 ," — GS 3Q26 prints a space before the comma.
    r"dec)\.?(?:\s+(\d{1,2}))?\s*,?$")
_ORD_CELL = re.compile(
    r"(?i)^(?:(first|second|third|fourth|1st|2nd|3rd|4th)"
    r"(\s+(?:quarter|qtr\.?))?|q([1-4])|([1-4])q)$")
_QTR_WORD = re.compile(r"(?i)^(?:quarter|qtr\.?)$")


def _hdr_qend(cell: str) -> str | None:
    """A header cell's period — None for a change/comparison column, whose
    period tokens ("2Q26 vs. 1Q26") are never a period of their own."""
    return None if _CHANGE_COL.search(cell) else _period_qend(cell)


def _ytd_only_head(rows: list[list[str]], i: int, last: int) -> bool:
    """A year-to-date banner with no quarter marker in the header band
    (the two nearest non-blank rows above row i, then rows i .. last) —
    FITB "Year to Date" over "June | June", KEY "Six months ended" over
    "6/30/2026 | 6/30/2025": the columns are YTD. Blank spacer rows don't
    count toward the two, so a spacer can't hide the banner."""
    above = [r for r in rows[:i] if any(r)][-2:]
    head = " ".join(c for r in above + rows[i:last + 1] for c in r if c)
    return bool(_YTD_HDR.search(head)) and not _QTR_HDR.search(head)


def _novel_header(cells: list[str]) -> bool:
    """True when a header row's reading depends on this module's newer
    forms — a numeric or Q-first period cell, or comparison cells whose
    period tokens _row_periods dropped (a legacy every-token reading, SFBS,
    is not novel)."""
    every = [j for j, c in enumerate(cells) if c and _period_qend(c)]
    if [j for j, _ in _row_periods(cells)] != every:
        return True
    return any(not (_QTOK.search(cells[j]) or _QWORD.search(cells[j])
                    or _DTOK.search(cells[j]) or _MONYR.search(cells[j]))
               for j in every)


def _row_periods(cells: list[str]) -> list[tuple[int, str]]:
    """(cell index, qend) of a header row's period cells. Comparison cells
    are dropped only when they TRAIL every period (RF "… | 2Q25 | 2Q26 vs.
    1Q26 | 2Q26 vs. 2Q25"). One sitting BETWEEN period columns (SFBS "%
    Change From Period Ending March 31, 2026 to …", whose value rows don't
    follow the header's layout) keeps the legacy every-token reading: its
    tokens duplicate the periods and the table is refused as before."""
    every = [(j, q) for j, c in enumerate(cells) if (q := _period_qend(c))]
    clean = [(j, q) for j, q in every if not _CHANGE_COL.search(cells[j])]
    if clean and any(j < clean[-1][0] and _CHANGE_COL.search(cells[j])
                     for j, _ in every):
        return every
    return clean


def _header_parts(cells: list[str], kind) -> tuple | None:
    """(parts, tail) of a header row: an optional leading units/caption
    cell, then the run of `kind` cells, then the tail. The caption may not
    itself be a period, a change column, a year-to-date marker or a non-GAAP
    section heading (OCFC "Core Ratios (Annualized):")."""
    nz = [c for c in cells if c]
    if nz and not kind.match(nz[0]):
        lab = nz.pop(0)
        if (_hdr_qend(lab) or _CHANGE_COL.search(lab) or _YTD_HDR.search(lab)
                or _CONNECTOR_EXCLUDE.search(lab)):
            return None
    k = 0
    while k < len(nz) and kind.match(nz[k]):
        k += 1
    return nz[:k], nz[k:]


def _part_quarter(c: str) -> tuple[int, bool] | None:
    """(quarter number, self-proving) of a period-part cell — a quarter-end
    month ("June 30,"; day, if any, must be the quarter-end) or an ordinal.
    A bare ordinal word ("Second") proves nothing alone — it needs a
    "Quarter" row beneath it."""
    m = _MON_CELL.match(c)
    if m:
        mon = _MONTHS3.get(m.group(1)[:3].lower())
        if mon not in _QE_MONTH or (m.group(2) and int(m.group(2))
                                    != _QE_MONTH[mon]):
            return None
        return mon // 3, True
    m = _ORD_CELL.match(c)
    if m:
        if m.group(3) or m.group(4):
            return int(m.group(3) or m.group(4)), True
        return _QNUM[m.group(1).lower()], bool(m.group(2))
    return None


# Extra-column words in a split header's tails: comparison ("Increase/
# (Decrease) from", "vs.") and year-to-date ("Nine Months", "YTD") cells.
_EXTRA_HDR = re.compile(
    r"(?i)increase|decrease|change|\bfrom\b|\bvs\b|months?|\bytd\b|nine|six|"
    r"twelve|year")


def _extended_split_tail(rows, i, last, qs, ptail, y) -> tuple | None:
    """A split header whose year row runs ON past the quarters into change
    LABELS and a year-to-date block — Citi's supplement, oldest first:
        3Q   | 4Q   | 1Q   | 2Q   | 3Q   | (Decrease) from | Months | …
        2025 | 2025 | 2026 | 2026 | 2026 | 2Q26 | 3Q25 | 2025 | 2026 | (Decrease)
    The "2Q26 | 3Q25" cells name the comparison periods of two change
    columns; "2025 | 2026 | (Decrease)" is the nine-month block. Read as a
    header of its own, "2Q26 | 3Q25" put 2Q26 on the 3Q25 column. Proven
    only when: one year per quarter part, the change labels are distinct
    NON-latest quarters among the paired periods, the rest are years /
    change words, every parts-row tail cell is an extra-column word, and
    the band names a change. Returns the 5-tuple (…, n_change, n_ytd) —
    an extended header, read with the YTD-aware widths — or None."""
    from datetime import date as _date
    if not y or len(y[0]) != len(qs) or not y[1]:
        return None
    qends = [_date(int(yy[:4]), *_Q_END[q]).isoformat()
             for q, yy in zip(qs, y[0])]
    if len(set(qends)) != len(qends):
        return None
    tail = y[1]
    labels = []
    for c in tail:
        q = None if _CHANGE_COL.search(c) else _period_qend(c)
        if not q:
            break
        labels.append(q)
    rest = tail[len(labels):]
    latest = max(qends)
    if (not labels or len(set(labels)) != len(labels)
            or any(q not in qends or q == latest for q in labels)):
        return None
    if not all(_YEAR_CELL.match(c) or _EXTRA_HDR.search(c) for c in rest):
        return None
    if not all(_EXTRA_HDR.search(c) for c in ptail):
        return None
    band = " ".join(c for r in [r for r in rows[:i] if any(r)][-2:]
                    + rows[i:last + 2] for c in r if c)
    if not re.search(r"(?i)increase|decrease|change", band):
        return None
    if rest and not _YTD_HDR.search(band):
        return None
    return qends, last + 1, i - 1, len(labels), len(rest)


def _split_header(rows: list[list[str]], i: int) -> tuple | None:
    """A split period header starting at row i (see the block comment
    above), as _period_header's tuple, or None."""
    from datetime import date as _date

    def row(j):
        return rows[j] if 0 <= j < len(rows) else []

    def is_change(tail):
        return all(_CHANGE_COL.search(c) for c in tail)

    def parts_at(j):
        """(quarters, tail, last header row, n_change) of a period-part row
        at j (+ an optional "Quarter" row beneath), or None."""
        for kind in (_MON_CELL, _ORD_CELL):
            p = _header_parts(row(j), kind)
            if not p or len(p[0]) < 2:
                continue
            parts, tail = p
            qs = [_part_quarter(c) for c in parts]
            if not all(qs):
                return None
            last, n_chg = j, 0
            qw = _header_parts(row(j + 1), _QTR_WORD)
            if qw and len(qw[0]) == len(parts) and is_change(qw[1]):
                last, n_chg = j + 1, len(qw[1])
            elif not all(proof for _, proof in qs):
                return None               # bare "Second" with no "Quarter"
            return [q for q, _ in qs], tail, last, n_chg, kind
        return None

    def qend(y, q):
        mo, dy = _Q_END[q]
        return _date(y, mo, dy).isoformat()

    def ytd_only(last):
        return _ytd_only_head(rows, i, last)

    # (A) parts row, then the years row beneath it.
    p = parts_at(i)
    if p:
        qs, ptail, last, n_chg, kind = p
        y = _header_parts(row(last + 1), _YEAR_CELL)
        ext = _extended_split_tail(rows, i, last, qs, ptail, y)
        if ext:
            return ext
        if y and y[0] and is_change(y[1]) and not ytd_only(last + 1):
            years = [int(c[:4]) for c in y[0]]
            if is_change(ptail) and len(years) == len(qs):
                return ([qend(yy, q) for q, yy in zip(qs, years)], last + 1,
                        i - 1, max(len(ptail), n_chg, len(y[1])))
            # K ordinal quarters + one YTD cell ("Six Months Ended June 30,"
            # — or a bare "June 30," under a "Six Months Ended" banner)
            # over K+2 years ending [latest, latest-1] (CUBB 2Q26).
            if (len(ptail) == 1 and not y[1] and not n_chg
                    and len(years) == len(qs) + 2
                    and kind is _ORD_CELL
                    and (_YTD_HDR.search(ptail[0])
                         or (_MON_CELL.match(ptail[0]) and _YTD_HDR.search(
                             " ".join(c for c in row(i - 1) if c))))
                    and years[-2:] == [max(years[:-2]), max(years[:-2]) - 1]):
                return ([qend(yy, q) for q, yy in zip(qs, years)]
                        + [_YTD, _YTD], last + 1, i - 1, 0)
        return None
    # (B) years row ABOVE the parts (CFR / HBAN).
    nz = [c for c in row(i) if c]
    if nz and all(_YEAR_CELL.match(c) for c in nz):
        p = parts_at(i + 1)
        if not p or not is_change(p[1]) or ytd_only(p[2]):
            return None
        qs, ptail, last, n_chg, _kind = p
        years = [int(c) for c in nz]
        n_change = max(len(ptail), n_chg)
        if len(years) == len(qs):
            return ([qend(yy, q) for q, yy in zip(qs, years)], last, i - 1,
                    n_change)
        # Exactly one strictly-descending run of quarters per year (the
        # standard descending-recency layout) — else refused.
        run_idx, ridx = [], 0
        for j, q in enumerate(qs):
            if j and q >= qs[j - 1]:
                ridx += 1
            run_idx.append(ridx)
        if ridx + 1 == len(years):
            return ([qend(years[ri], q) for q, ri in zip(qs, run_idx)], last,
                    i - 1, n_change)
    return None


def _cmp_cols(hdr_cells: list[str], qends: list[str],
              n_change: int) -> list[tuple[int, int]] | None:
    """(A, B) period-column pairs of the header's trailing "A vs. B"
    comparison cells (RF "2Q26 vs. 1Q26 | 2Q26 vs. 2Q25"), or None unless
    EVERY change cell names two uniquely-placed periods."""
    nz = [c for c in hdr_cells if c]
    if not n_change or len(nz) < n_change:
        return None
    out = []
    for c in nz[-n_change:]:
        sides = re.split(r"(?i)\bvs\b\.?|\bversus\b", c)
        if len(sides) != 2:
            return None
        a, b = (_period_qend(s) for s in sides)
        if not a or not b or qends.count(a) != 1 or qends.count(b) != 1:
            return None
        out.append((qends.index(a), qends.index(b)))
    return out


def _cmp_proves(vals: list[float], n: int, cmp: list[tuple[int, int]]) -> bool:
    """A row carrying a "$ change | % change" PAIR per comparison column
    (n periods + 2 values each) aligns only when every dollar change equals
    its two periods' difference to the printed precision — arithmetic proof
    the period columns sit where the header says (RF 2Q26 NII: 1,277 −
    1,248 = 29). Magnitudes only: parenthesized integers parse unsigned."""
    if len(vals) != n + 2 * len(cmp):
        return False

    def ulp(v):
        return 1.0 if float(v).is_integer() else 10.0 ** -_dec(v)

    for k, (a, b) in enumerate(cmp):
        d, va, vb = vals[n + 2 * k], vals[a], vals[b]
        if abs(abs(d) - abs(va - vb)) > max(ulp(d), ulp(va), ulp(vb)) + 1e-9:
            return False
    return True


# Same metric, two explicitly-scoped rows. KEY 2Q26 prints ROTCE "from
# continuing operations" 12.89% (its page-1 highlights) AND "consolidated"
# 12.91% (the reconciliation row; the highlights table's "From consolidated
# operations:" section) — disagreeing candidates refused both. The scope is
# read from the row label, else from the nearest label-only SECTION row above
# it in the same table; when a metric has BOTH scopes, the consolidated
# (whole-company) rows serve and the continuing-operations rows step aside.
# Only explicit markers decide — an unscoped row is never dropped.
# The same rule for two more explicit pairs: a STANDARDIZED capital row beats
# an ADVANCED one (GS 3Q26 prints CET1 12.4% Standardized and 13.6% Advanced
# under section rows; JPM's supplement the same — the Standardized ratio is
# the headline/binding one, see ir_provider's capital note), and a REPORTED-
# basis row beats a MANAGED-basis one (JPM's "Reported Basis" / "Managed
# Basis" sections and "- reported" / "- managed" rows). (winner, loser)
# pairs:
_SCOPE_TAGS = {
    "cons": re.compile(r"(?i)\bconsolidated\b"),
    "cont": re.compile(r"(?i)\bcontinuing operations\b"),
    "std": re.compile(r"(?i)\bstandardized\b"),
    "adv": re.compile(r"(?i)\badvanced\b"),
    # "Reported Basis" / "Managed Basis (b)" sections, "Overhead ratio -
    # reported" / "Overhead ratio - managed" rows (JPM supplement: 2Q26
    # reported 48 vs managed 47 refused both).
    "rep": re.compile(r"(?i)\breported\b"),
    "mgd": re.compile(r"(?i)\bmanaged\b"),
}
_SCOPE_PAIRS = (("cons", "cont"), ("std", "adv"), ("rep", "mgd"))
# A BANK-SUBSIDIARY scope ("Unity Bank", "Bank capital ratios:") — capital
# ratios there are the bank's, never the holdco's (holdco ≠ bank-sub), so
# capital candidates carrying it are refused outright. A name with a holdco
# word ("Unity Bancorp", "Bank of Marin Bancorp") is not bank-scoped.
_SCOPE_BANK = re.compile(r"(?i)\bbank\b")
_SCOPE_HOLDCO = re.compile(
    r"(?i)\b(?:banc(?:orp|shares)|bankshares|corp(?:oration)?|company|"
    r"consolidated|holding|financial|group|inc)\b")


def _row_scope(text: str) -> frozenset:
    """The explicit scope tags a label (or section row) carries."""
    tags = {t for t, rx in _SCOPE_TAGS.items() if rx.search(text)}
    for win, lose in _SCOPE_PAIRS:
        if win in tags and lose in tags:
            tags -= {win, lose}           # both named → neither decides
    if _SCOPE_BANK.search(text) and not _SCOPE_HOLDCO.search(text):
        tags.add("bank")
    return frozenset(tags)


# A tax-equivalent NIM label ("Net interest margin (FTE)", "- TE", "tax-
# equivalent basis", "fully taxable equivalent"). There is no separate FTE
# metric: where the release ALSO prints a readable PLAIN NIM row in any
# period-proven table (SBSI 2Q26: "Net interest margin 2.80 %" over "Net
# interest margin (FTE) (1) 2.90 %" — one rule matched both and the
# disagreement refused both), only plain rows serve the NIM — release-wide,
# so the current / prior / year-ago columns all read one basis (prose too:
# see extract_release_metrics). A release printing no plain row keeps its
# TE rows (the conventional reported basis — see _CONNECTOR_EXCLUDE).
# The EFFICIENCY ratio has the same two bases (SBSI 2Q26 "Efficiency ratio
# 54.42 %" over "Efficiency ratio (FTE) (1) 52.96 %" refused both; EWBC
# 36.73 vs "(FTE)" 36.68) and follows the same release-wide rule.
_BASIS_KEYS = ("nim", "efficiency")
_NIM_TE = re.compile(r"\b(?:fte|te|teq)\b|(?<!non )(?<!non-)\b(?:fully )?"
                     r"tax(?:able)?[- ]equivalent", re.I)
# An explicit PLAIN-basis marker in prose: "GAAP net interest margin was
# 3.59%" (COFS 2Q26), "Net Interest Margin (NIM) (GAAP) increased to 3.68%"
# (CBFV 2Q26), "(non taxable equivalent)" — never "non-GAAP".
_PLAIN_MARK = re.compile(r"(?<!non.)\bgaap\b|\bnon[- ]tax(?:able)?[- ]"
                         r"equivalent\b|\bno tax adjustment\b", re.I)
# What may follow the NIM label on a PLAIN row: footnote markers ("(2)",
# "6", "(a)", "*"), a GAAP / annualized / "(NIM)" / "(%)" tag, or an explicit
# no-TE tag (WNEB "(no tax adjustment)", FCCO "(non taxable equivalent)").
# Anything else is a variant, never plain evidence — TCBK's "Net interest
# margin less effect of acquired loan discount accretion" (4.07% beside its
# FTE 4.11%) must not make the FTE rows step aside.
_NIM_PLAIN_TAIL = re.compile(
    r"(?:\s*(?:\(\s*(?:[a-z]|\d{1,2}|gaap|annualized|%|[“\"]?nim[”\"]?|"
    r"no tax adjustment|non[- ]taxable[- ]equivalent)\s*\)|"
    r"\d{1,2}(?:\s*,\s*\d{1,2})*|\*|,|gaap|annualized))*\s*", re.I)


def _plain_label(key: str, label: str, te_table: bool = False) -> bool:
    """A PLAIN row label for a _BASIS_KEYS metric: its table label plus only
    _NIM_PLAIN_TAIL — in a TE-titled table (_te_table) only with an
    explicit plain marker ("Net Interest Margin (GAAP)")."""
    m = re.match(r"\s*" + _TABLE_SPECS[key][0], label, re.I)
    return (bool(m) and bool(_NIM_PLAIN_TAIL.fullmatch(label[m.end():]))
            and (not te_table or bool(_PLAIN_MARK.search(label))))


def _te_table(html: str, start: int, rows: list[list[str]], hdr_i: int) -> bool:
    """True when the table is TITLED tax-equivalent — a short title row above
    the period header, or an UPPER-CASE heading right above the table: CARE
    2Q26 "NET INTEREST MARGIN (FTE) (QTD AVERAGES)" heads an average-balance
    table whose row reads just "Net Interest Margin 4" (3.40 — the FTE
    figure; its GAAP NIM is 3.38). Its bare-labeled rows are not plain."""
    if any(_NIM_TE.search(c) for r in rows[:hdr_i] for c in r if c and len(c) <= 80):
        return True
    pre = _flat_text((html or "")[max(0, start - 1500):start])
    return any(m.start() >= len(pre) - 100
               and not re.search(r"[a-z]", pre[max(0, m.start() - 20):m.start()])
               for m in _NIM_TE.finditer(pre))


def _plain_bases(html: str) -> frozenset:
    """The _BASIS_KEYS for which some period-proven table prints a READABLE
    plain row (one value per period column, + any counted change columns) —
    that metric's release basis is then the plain one: only plain rows serve
    it (TE rows and variants step aside, in every column alike). An
    unreadable row is no evidence (BUSE "Net interest margin (i)(v)"
    interleaves $ amounts).
    PROSE that states an explicitly plain-marked value (_PLAIN_MARK) is the
    same evidence: CBFV 2Q26 narrates "Net Interest Margin (NIM) (GAAP)
    increased to 3.68%" while its only readable table row is "Net Interest
    Margin (FTE) (Non-GAAP)" (3.73 / 3.88 / 3.55) — the served current 3.68
    sat beside FTE prior / year-ago columns. The TE rows then step aside
    (prior / year-ago n/a), never a mixed-basis delta."""
    text = _flat_text(html)
    found = {k for k in _BASIS_KEYS if _pct_metric(
        text, *_PCT_SPECS[k], marked_plain=True) is not None}
    for tm in re.finditer(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(tm.group(1))
        hdr = _period_header(rows)
        if not hdr:
            continue
        qends, hdr_i, _span_i, n_change = hdr
        cap_cell = next((c for c in rows[hdr_i] if c), "")
        if _segment_table(
                _flat_text((html or "")[max(0, tm.start() - 1500):tm.start()]),
                " ".join(c for r in rows[:hdr_i] for c in r if c),
                "" if _period_qend(cap_cell) else cap_cell):
            # A segment's row is no evidence of the FIRM's basis: Citi's
            # Services page prints a bare "Efficiency ratio 45%", which made
            # the firmwide "Efficiency ratio (total operating expenses/
            # total revenues, net)" step aside.
            continue
        te_tab = _te_table(html, tm.start(), rows, hdr_i)
        for cells in rows[hdr_i + 1:]:
            j = next((i for i, c in enumerate(cells) if c), None)
            if j is None:
                continue
            for key in _BASIS_KEYS:
                if key not in found and _plain_label(key, cells[j], te_tab) and len(
                        _row_values(cells[j:])) in (len(qends),
                                                    len(qends) + n_change):
                    found.add(key)
    return frozenset(found)


# AVERAGE-BALANCE tables print each period as a GROUP of columns ("Average
# Balance | Interest | Rate/Yield", UNTY 3Q26) under one period header, so
# a row carries k × g values and the period's yield is the group's rate
# cell. The group layout is PROVEN by a sub-header row of k identical
# groups, each with exactly one rate/yield cell and one balance cell —
# else the table is read as an ordinary period table (where these rows
# fail the width check). Only these two rows are read from such a table:
_AVG_YIELD_ROWS = {
    # "Total loans (B)", "Loans, net", "Loans and leases (2)" — never a
    # category ("Commercial loans") or held-for-sale line.
    "loan_yield": (r"(?:total )?(?:net )?loans(?: and leases)?(?:,? net"
                   r"(?: of unearned income)?| receivable| held for investment"
                   r"| hfi)?(?:\s*\(net of unearned income\))?"
                   r"(?:\s*\([^)]{0,6}\))*(?:\s*\d)?\s*$", (1.0, 12.0)),
    # The NIM row of an average-rate table (Citi: "Net interest income as a
    # % of average interest-earning assets (NIM)").
    "nim": (r"(?:net interest income as a % of average interest-earning "
            r"assets|net interest margin)(?:\s*\([^)]{0,6}\))*(?:\s*\d)?\s*$",
            (0.5, 8.0)),
    # The ALL-deposits cost (noninterest-bearing included) — an
    # "interest-bearing deposits" row is a different quantity.
    "cost_of_deposits": (r"total deposits(?:\s*\([^)]{0,6}\))*(?:\s*\d)?\s*$",
                         (0.0, 7.0)),
}
_AVG_RATE_CELL = re.compile(r"(?i)yield|rate")
_AVG_BAL_CELL = re.compile(r"(?i)balance")


def _measure_major(rows: list[list[str]], hdr_i: int, qends: list[str]) -> list | None:
    """The base periods of a MEASURE-major average-rate table — each
    measure's block repeats the periods (Citi's supplement: "Average Volumes
    | Interest | % Average Rate" over "3Q25 | 2Q26 | 3Q26" × 3) — or None.
    Proven by a banner of g ≥ 2 measure cells whose LAST names the rate,
    over a period row that is exactly the base periods repeated g times."""
    above = [r for r in rows[:hdr_i] if any(r)]
    if not above:
        return None
    banner = [c for c in above[-1] if c]
    g = len(banner)
    if g < 2 or len(qends) % g or not _AVG_RATE_CELL.search(banner[-1]):
        return None
    k = len(qends) // g
    base = qends[:k]
    if k < 2 or len(set(base)) != k or qends != base * g:
        return None
    return base


def _avg_groups(rows: list[list[str]], hdr_i: int, k: int) -> tuple | None:
    """(sub-header row index, group width g, rate position p) when one of
    the two non-blank rows under the period header splits into k identical
    column groups (optionally after a leading caption cell), each holding
    exactly one rate/yield cell and one balance cell; else None."""
    seen = 0
    for j in range(hdr_i + 1, len(rows)):
        nz = [c.lower() for c in rows[j] if c]
        if not nz:
            continue
        seen += 1
        if seen > 2:
            break
        if len(nz) % k:
            nz = nz[1:]                   # leading caption cell
        if not nz or len(nz) % k:
            continue
        g = len(nz) // k
        grp = nz[:g]
        if g < 2 or any(nz[i * g:(i + 1) * g] != grp for i in range(k)):
            continue
        rate = [i for i, c in enumerate(grp) if _AVG_RATE_CELL.search(c)]
        if len(rate) == 1 and sum(1 for c in grp if _AVG_BAL_CELL.search(c)) == 1:
            return j, g, rate[0]
    return None


# Alias spec keys → (target key, sign): the inverted-sign NCO label and the
# section-gated dividend row read into their metric's candidates.
_ALIASES = {"nco_ratio_inv": ("nco_ratio", -1.0), "div_ps_sec": ("div_ps", 1.0)}
# A per-share SECTION ("SHARE INFORMATION", "COMMON SHARE DATA:", "Per
# Common Share") — the only place a bare "Dividends paid" row is per share.
_SHARE_SECTION = re.compile(r"(?i)\bshare\b")
# Sub-row labels that only name a DENOMINATOR or a BASIS and take their
# subject from the row above: WFC "Allowance for credit losses for loans |
# $14,458" then "As a % of total loans | 1.38%"; GS / JPM "EARNINGS PER
# COMMON SHARE" (section) then "Diluted 1 | $17.01".
_PCT_SUBROW = re.compile(r"(?i)^as a (?:%|percent(?:age)?) of\b")
# A bare loans row under an average-RATES / YIELDS section is the loan
# yield (JPM supplement "AVERAGE RATES" → "Loans | 6.59", WFC "Average
# Interest Rates" → "Loans | 5.65"); a bare total-loans
# row under a net-charge-off RATES section is the NCO ratio (JPM "NET
# CHARGE-OFF/(RECOVERY) RATES" → "Total retained loans | 0.59").
_RATES_SECTION = re.compile(r"(?i)^(?:average |annualized )?(?:interest )?"
                            r"(?:yields?|rates)"
                            r"(?:\s*(?:and|/|&)\s*(?:yields?|rates))?\b(?!.*balance)")
_LOANS_SUBROW = re.compile(r"(?i)^(?:total )?loans(?: and leases)?"
                           r"(?:\s*\([a-z\d]\))*\s*$")
_NCO_SECTION = re.compile(r"(?i)\bnet charge-?off(?:s|/\(recovery\))? "
                          r"(?:rates?|ratios?)\b")
_NCO_SUBROW = re.compile(r"(?i)^total (?:retained )?loans(?:\s*\([a-z\d]\))*\s*$")
_EPS_SUBROW = re.compile(r"(?i)^diluted\b(?:\s*\(?\d\)?)?\s*$")
_EPS_SECTION = re.compile(r"(?i)\b(?:earnings|net income) per (?:common )?share\b")
# Citi's supplement: "Diluted earnings per share:" (section) then "Income
# (loss) from continuing operations | …" and "Net income (loss) | $2.85" —
# only the NET INCOME row is the diluted EPS.
_DIL_EPS_SECTION = re.compile(r"(?i)^diluted earnings per (?:common )?share\b")
_NI_SUBROW = re.compile(r"(?i)^net income(?: \(loss\))?(?:\s*\(\d\))?\s*$")


def _compose_label(label: str, prev_label: str, section_label: str,
                   outer_label: str = "") -> str:
    """The row's effective label (see _PCT_SUBROW / _EPS_SUBROW); anything
    else is returned unchanged. A composed label still has to match a spec
    from its START, so only the subject row's own name can qualify it."""
    if _PCT_SUBROW.match(label) and prev_label:
        return f"{prev_label} {label}"
    if _LOANS_SUBROW.match(label) and not re.search(
            r"(?i)balance", section_label) and (
            _RATES_SECTION.search(section_label)
            or _RATES_SECTION.search(outer_label)):
        # one level of nesting: JPM "AVERAGE RATES (c)" → "INTEREST-
        # EARNING ASSETS" → "Loans | 6.59"
        return "yield on loans"
    if _NCO_SUBROW.match(label) and _NCO_SECTION.search(section_label):
        return "net charge-off ratio"
    if _EPS_SUBROW.match(label):
        m = _EPS_SECTION.search(section_label)
        if m:
            return f"{m.group(0)} - diluted"
    if _NI_SUBROW.match(label) and _DIL_EPS_SECTION.match(section_label):
        return "diluted earnings per share"
    return label


# Business-SEGMENT vocabulary. A table titled for a segment (KEY "Line of
# Business Results"; FHN's page headings "COMMERCIAL, CONSUMER, AND WEALTH
# Quarterly, Unaudited" / "WHOLESALE …" just above the table) or rows under
# a segment section heading (KEY "Consumer Bank") print the same labels as
# the firmwide rows — "Efficiency ratio 47.86%" beside the firm's 58.54% —
# and are never firmwide figures ("division" headings likewise: a segment
# row never supplies a consolidated metric — no consolidated row for a
# period → n/a). Change-column headers (_fold_change_
# periods) made these tables readable, so they are now named and skipped.
_SEGMENT_TITLE = re.compile(
    r"(?i)\bsegments?\b|\bdivisions?\b|\blines? of business\b"
    r"|\bbusiness (?:lines?|units?)\b"
    r"|\bwholesale\b|\bconsumer (?:and|&) community bank|\bconsumer bank(?:ing)?\b"
    r"|\bcommercial bank(?:ing)?\b|\bwealth (?:management|(?:and|&) investment)"
    r"|\bcorporate (?:and|&) investment bank|\bretail bank(?:ing)?\b"
    r"|\basset (?:and|&) wealth\b|\bglobal (?:banking|markets|wealth)\b"
    r"|\bcommercial, consumer\b|\bcard services\b|\bpersonal banking\b"
    r"|\binstitutional securities\b|\bplatform solutions\b")
# A VARIANT block heading: an adjusted / core basis (or "operating" marked
# non-GAAP) AND a block of figures — TRMK "FINANCIAL RATIOS - OPERATING
# (NON-GAAP)", OCFC "Core Ratios". A bare "NON-GAAP MEASURES:" block is NOT
# one: TBV / TCE / ROTCE are conventionally non-GAAP (FLG). Nor is
# "Operating results:" (GAAP results of operations).
_VARIANT_BLOCK = re.compile(
    r"(?i)(?=.*(?:\b(?:adjusted|core|normalized|underlying|pro forma)\b"
    r"|\boperating\b.*\bnon-?gaap\b|\bnon-?gaap\b.*\boperating\b))"
    r"(?=.*\b(?:ratios|measures|metrics|data)\b)")
_BPS_ROW = re.compile(r"(?i)\bbps\b|\bbasis points?\b")
_RECOVERY_LABEL = re.compile(r"(?i)\brecover")
# A row with no all-numeric cell (a banner / sub-header inside the table).
_NUMERIC_CELL = re.compile(r"^[\s$%(),.\d*\-]+$")


# A header CAPTION cell that is a bare segment name followed by its units
# note: Citi 3Q26 "Services ($ in millions, …)", "Markets (…)", "Banking
# (…)", "Wealth (…)", "USCC (a) (…)", "All Other (Managed Basis) (a)(b)
# (…)" beside the firmwide "Citigroup ($ in millions, …)" — every segment
# repeats "Total revenues, net of interest expense" / "RoTCE" / "Efficiency
# Ratio", which refused the firm's own figures.
_SEGMENT_CAPTION = re.compile(
    r"(?i)^(?:services|markets|banking|wealth|uscc|all other|personal banking"
    r"|corporate(?:\s*(?:and|&|/)\s*other)?|cards?)\b\s*(?:\([^)]{0,30}\)\s*)*"
    r"(?:\(\$|$)")


# …and Citi's supplement page heading: an UPPER-CASE bare segment name
# right before the units caption ("SERVICES (In millions of dollars …)").
_SEGMENT_PAGE = re.compile(
    r"(?:^|\s)(?:SERVICES|MARKETS|BANKING|WEALTH|USCC|ALL OTHER|PERSONAL "
    r"BANKING|CORPORATE)(?:\s*\([a-z0-9]\))*\s*\(In (?:millions|billions)")


def _segment_table(pre_text: str, title: str, caption: str = "") -> bool:
    """True for a segment table: segment vocabulary in the table's own
    title rows, a bare segment name as the header's caption cell, or an
    UPPER-CASE segment page heading in the text right above it (prose that
    merely mentions "commercial banking" is mixed case and never trips
    this)."""
    if _SEGMENT_TITLE.search(title) or _SEGMENT_CAPTION.match(caption.strip()):
        return True
    if _SEGMENT_PAGE.search(pre_text[-120:]):
        return True
    m = _SEGMENT_TITLE.search(pre_text[-100:])
    return bool(m) and m.group(0).isupper()


def _skip_row(cells: list[str], label: str, st: dict) -> bool:
    """Advance the row-block state on a NON-DATA row and say whether to
    skip the row. `st` keys: ytd / seg / var (rows of a year-to-date,
    segment or non-GAAP block), section / outer (the two nearest label-only
    headings). A label-only heading opens or closes blocks (a colon sub-
    heading like "Loans:" keeps the open ones); a multi-cell banner row with
    no numeric cell switches the year-to-date block (EWBC 2Q26's Selected
    Ratios table runs a "Six Months Ended" block under the quarter block —
    "Return on average assets | 1.77 | 1.59" was read as 2Q26 / 1Q26)."""
    nz = [c for c in cells if c]
    if len(nz) == 1:
        sub = label.rstrip().endswith(":")
        st["ytd"] = _ytd_section(label, st["ytd"])
        st["seg"] = bool(_SEGMENT_TITLE.search(label)) or (st["seg"] and sub)
        # TRMK 2Q26 "FINANCIAL RATIOS - OPERATING (NON-GAAP)" heads plain-
        # labeled rows ("Return on average equity | 10.62 | n/a …"). Only a
        # BLOCK heading counts — a reconciliation sub-heading naming one
        # adjusted metric (PNFP "Adjusted return on average assets") sits
        # over the GAAP row it starts from.
        st["var"] = bool(_VARIANT_BLOCK.search(label)
                         and not re.search(r"(?i)reconcil", label)) or (
            st["var"] and sub)
        st["outer"], st["section"] = st["section"], label
        return True
    if not any(_NUMERIC_CELL.match(c) for c in nz[1:]):
        txt = " ".join(nz)
        if len(txt) > 80:
            return True               # a note / caption, not a banner (AROW)
        if _YTD_HDR.search(txt) and not _QTR_HDR.search(txt):
            st["ytd"] = True
        elif _QTR_HDR.search(txt) and not _YTD_HDR.search(txt):
            st["ytd"] = False
        # An in-body PERIOD row re-declares the columns for the rows under
        # it: EWBC 2Q26 "June 30, 2026 | June 30, 2025 | Yr-o-Yr" (2
        # periods + 1 change) heads rows whose 3 values matched the table
        # header's 3 periods — "Net interest margin | 3.46 | 3.35 | 11"
        # (six-month 2026 / 2025, bps change) read as 2Q26 / 1Q26; LNKB
        # 4Q25 re-declares five dates under its three-period header, so its
        # 5-value capital rows put 6/30/2025 in the 12/31/2024 column. A
        # period set other than the table header's becomes the rows'
        # header — st["sub"] = (its periods, its trailing change-cell count,
        # None when a non-change cell trails) — until the header's own set
        # is re-declared. Only after a data row: before one, a period row
        # continues the header itself (INDB's "Jun 2026 vs. | Jun 2026 vs."
        # over "Mar 2026 | Jun 2025").
        per = _row_periods(cells)
        if per and st.get("qends") and st.get("data"):
            found = [q for _, q in per]
            if set(found) == set(st["qends"]):
                st["sub"] = None
            else:
                trail = [c for c in cells[per[-1][0] + 1:] if c]
                st["sub"] = (found, len(trail) if all(
                    _CHANGE_COL.search(c) for c in trail) else None)
        return True
    st["data"] = True
    return st["ytd"] or st["seg"]


def _ytd_section(label: str, in_block: bool) -> bool:
    """Whether the rows after a label-only SECTION row belong to a year-to-
    date block. UNTY 3Q26 prints "FINANCIAL DATA - YEAR TO DATE" inside its
    three-period quarter table: the block's two-column rows ("4.52 | 4.15 |
    8.9" — 9M EPS, 9M EPS, % change) count three values and read the
    nine-month EPS as the quarter's, so the real 1.70 was refused as a
    disagreement. A YTD heading opens the block; the next heading that is
    not a colon-terminated sub-heading ("Loans:") closes it."""
    if _YTD_HDR.search(label) and not _QTR_HDR.search(label):
        return True
    return in_block and label.rstrip().endswith(":")


def extract_table_metrics(html: str, expected_qend: str,
                          specs: dict | None = None,
                          adj_specs: dict | None = None,
                          quarter_only: bool = False) -> dict:
    """Metric values read from the release's structured tables at the column
    whose PERIOD HEADER equals `expected_qend` (ISO quarter-end). All the
    ambiguity refusals documented above apply; candidates across tables must
    agree (as prose) or the metric is None. `specs` / `adj_specs` replace
    _TABLE_SPECS / _ADJ_TABLE_SPECS (the revenue-basis read uses its own).
    `quarter_only` also skips a table whose header rows mark a year-to-date
    block and no quarter (_YTD_HDR without _QTR_HDR): a lone "Six Months
    Ended" table has UNIQUE period columns, so the twin-column refusal can't
    see it (ZION 2Q26: its H1 adjusted revenue $1,737M beside the quarter's
    $878M poisoned the key to None)."""
    specs = _TABLE_SPECS if specs is None else specs
    adj_specs = _ADJ_TABLE_SPECS if adj_specs is None else adj_specs
    all_specs = {**specs, **adj_specs}
    cands: dict = {k: [] for k in all_specs}     # [(value, scope, plain)]
    plain = _plain_bases(html) if any(k in specs for k in _BASIS_KEYS) \
        else frozenset()
    for tm in re.finditer(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        thtml = tm.group(1)
        rows = _table_rows(thtml)
        hdr = _period_header(rows, detail=True)
        if not hdr:
            continue                      # no period row → skip table
        qends, hdr_i, span_i, n_change, widths, inter = hdr
        cap_cell = next((c for c in rows[hdr_i] if c), "")
        if _segment_table(
                _flat_text((html or "")[max(0, tm.start() - 1500):tm.start()]),
                " ".join(c for r in rows[:hdr_i] for c in r if c),
                "" if _period_qend(cap_cell) else cap_cell):
            continue                      # a business segment's table
        base = (_measure_major(rows, hdr_i, qends)
                if specs is _TABLE_SPECS else None)
        if base:
            # Rates are the LAST block: a row's last k values, every one a
            # printed percent ("$14,967 | … | 2.40% | 2.54% | 2.43%").
            if expected_qend in base:
                k, col = len(base), base.index(expected_qend)
                for cells in rows[hdr_i + 1:]:
                    nz = [c for c in cells if c]
                    if len(nz) < 2:
                        continue
                    vals = _row_values(cells[cells.index(nz[0]):], slots=True)
                    pct = [c for c in nz[1:] if c.rstrip().endswith("%")]
                    if len(vals) < k or len(vals) % k or len(pct) < k \
                            or not all(c.rstrip().endswith("%")
                                       for c in nz[-k:]):
                        continue
                    v = vals[-k + col]
                    if v is None or _ROW_VARIANT.search(nz[0]):
                        continue
                    for key, (lab_re, band) in _AVG_YIELD_ROWS.items():
                        if (re.match(r"\s*" + lab_re, nz[0], re.I)
                                and band[0] <= v <= band[1]):
                            cands[key].append((v, frozenset(),
                                               key == "nim"))
            continue
        te_tab = bool(plain) and _te_table(html, tm.start(), rows, hdr_i)
        if quarter_only:
            head_text = " ".join(c for r in rows[:hdr_i + 1] for c in r if c)
            if _YTD_HDR.search(head_text) and not _QTR_HDR.search(head_text):
                continue                  # year-to-date-only table
        if qends.count(expected_qend) == 1:
            col = qends.index(expected_qend)
        elif qends.count(expected_qend) > 1 and span_i is not None \
                and span_i >= 0:
            # Combined "Three Months Ended | Six Months Ended" statement
            # header repeats the quarter-end date under BOTH spans (NPB
            # 2026-07-21: "June 30, 2026 | Mar 31, 2026 | June 30, 2025 |
            # June 30, 2026") — the bare count!=1 refusal threw away the
            # whole income statement, so EPS/revenue never table-filled and
            # FMP's junk $0.09 stood against the release's $0.60. The span
            # row (span_i — header-shape-aware: directly above a single-row
            # period header, above the MONTH row of a split month/year
            # header) PROVES the layout: a quarter-span marker strictly
            # before a YTD-span marker means the quarter block leads, so
            # the FIRST occurrence of the expected quarter-end is the
            # quarter column (YTD twins can only sit in the trailing
            # block). Any other arrangement keeps the refusal — never
            # guessed.
            above = " ".join(c for c in rows[span_i] if c).lower()
            q_m = re.search(r"three months? ended|quarters? ended", above)
            y_m = re.search(r"(?:six|nine|twelve) months? ended", above)
            if not (q_m and y_m and q_m.start() < y_m.start()):
                continue
            col = qends.index(expected_qend)
        else:
            continue                      # ambiguous period row → skip table
        tcol = col
        ycols = _span_year_columns(rows, hdr_i)
        lay = _span_layout(rows, len(rows) - 1) if ycols else None
        cmp = _cmp_cols(rows[hdr_i], qends, n_change)
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

        def _try(specs, label, row_text, vals, scope, section_label):
            if vals[col] is None:
                return                    # N/A / NM / dash in this column
            for key, (lab_re, kind, band) in specs.items():
                if not re.match(r"\s*" + lab_re, label, re.I):
                    continue
                if kind == "$K":
                    if scale is None:
                        continue
                    v = vals[col] * scale
                elif kind in ("$", "$s") and "$" not in row_text \
                        and not table_per_share:
                    continue
                elif kind == "$s" and not _SHARE_SECTION.search(section_label):
                    continue
                elif kind == "%r" and (
                        "bank" in scope
                        or ("$" in row_text and "%" not in row_text)
                        or ("%" not in row_text
                            and "ratio" not in label.lower()
                            and "ratio" not in section_label.lower())):
                    # Capital: a bank-subsidiary row is not the holdco's
                    # ratio; a dollar-capital row ("Tier 1 capital | 25"
                    # in $ millions) is not a ratio — "ratio" must be in
                    # the label or its section heading (WFC "Risk-based
                    # capital ratios and components:" → "Tier 1 capital").
                    continue
                elif kind in ("%", "%r") and "%" not in row_text and (
                        "$" in row_text
                        or sum(1 for c in row_text.split(" | ")
                               if _BPS_ROW.search(c)) >= len(qends)):
                    # …and a basis-point row is not a percent row: BHRB
                    # 2Q26 "Net charge-offs (recoveries) to average loans |
                    # 6.5 bps | 0.6 bps" read 0.6 bps as 0.6%. (bps CHANGE
                    # cells beside percent values — OPBK "57.64 | … | (33)
                    # bps" — are not: every period cell must be in bps.)
                    # A $-bearing row can't be the ratio (TFC omits % signs
                    # entirely — the specific label + band disambiguate; only
                    # an explicit $ marks the row as a dollar line).
                    continue
                elif kind == "#" and ("$" in row_text or "%" in row_text):
                    # A share COUNT row carries neither symbol; a $/% on the
                    # row marks a dollar or ratio line mismatched into it.
                    continue
                else:
                    v = vals[col]
                if key == "nco_ratio" and v < 0 and not _RECOVERY_LABEL.search(label):
                    # A parenthesized NCO ratio is a net RECOVERY only when
                    # the label says recoveries: BANR's allowance roll-
                    # forward prints charge-offs negative ("Net charge-offs
                    # (101)" … "/ average loans receivable (0.001)%") — the
                    # sign is presentation, the real ratio +0.001%.
                    continue
                if band[0] <= v <= band[1]:
                    cands[key].append((v, scope, key in _BASIS_KEYS
                                       and _plain_label(key, label, te_tab)))

        avg = (_avg_groups(rows, hdr_i, len(qends))
               if specs is _TABLE_SPECS else None)
        section, prev_label = frozenset(), ""
        st = {"ytd": False, "seg": False, "var": False, "section": "",
              "outer": "", "qends": qends}
        for r_i, cells in enumerate(rows[hdr_i + 1:], hdr_i + 1):
            # Label = the first NON-EMPTY cell (WFC/JPM indent data rows with
            # a leading empty cell — cells[0] as label skipped every row,
            # caught live 2026-07-13). Values parse only AFTER the label cell
            # so footnote digits in labels ("ROE 3") never enter the value
            # list and break period alignment.
            label_idx = next((i for i, c in enumerate(cells) if c), None)
            if label_idx is None:
                continue
            label = cells[label_idx]
            if sum(1 for c in cells if c) == 1:
                section = _row_scope(label)       # label-only section row
            if _skip_row(cells, label, st):
                continue                  # heading / banner / YTD / segment
            section_label, outer_label = st["section"], st["outer"]
            label, prev_label = (_compose_label(label, prev_label,
                                                section_label, outer_label),
                                 label)
            if _YTD_HDR.search(label) and not _QTR_HDR.search(label):
                # A year-to-date ROW: WSBF 2Q26 "Net interest margin - YTD
                # | 3.00 | 2.97 | …" (six months) under the same quarter
                # header as its "- QTD" row (3.03) — never a quarter value.
                continue
            scope = _row_scope(label) | section
            row_text = " | ".join(cells)
            # _row_values itself skips its first cell (the label slot), so
            # hand it the row FROM the label cell onward.
            sub = st.get("sub")
            if sub:
                # Under a re-declared period row (see _skip_row): its own
                # periods, exact width (+ its trailing change cells) only.
                sq, s_chg = sub
                if s_chg is None or sq.count(expected_qend) != 1:
                    continue
                k, col = len(sq), sq.index(expected_qend)
                vals = _row_values(cells[label_idx:], slots=True, n_periods=k)
                if len(vals) not in (k, k + s_chg):
                    continue
                vals = vals[:k]
            elif ycols:
                # Span-paired header: each period's value is the number
                # under its year cell's columns — never a positional count.
                k, col = len(qends), tcol
                vals = _span_row_values(cells, lay[r_i], ycols, k)
                if vals is None or all(v is None for v in vals):
                    continue
            else:
                k, col = len(qends), tcol
                vals = _row_values(cells[label_idx:], slots=True, n_periods=k)
            if not sub and not ycols and avg and len(vals) == k * avg[1]:
                v = vals[col * avg[1] + avg[2]]
                if v is not None and not _ROW_VARIANT.search(label):
                    for key, (lab_re, band) in _AVG_YIELD_ROWS.items():
                        if (re.match(r"\s*" + lab_re, label, re.I)
                                and band[0] <= v <= band[1]):
                            cands[key].append((v, scope, False))
                continue
            if inter and len(vals) != k:
                if len(vals) != inter[1]:
                    continue              # interleaved: full rows (or k) only
                vals = [vals[j] for j in inter[0]]
            elif len(vals) != k:
                if len(vals) in widths:
                    vals = vals[:k]       # trailing change / YTD columns
                elif (cmp and None not in vals
                        and _cmp_proves(vals, k, cmp)):
                    vals = vals[:k]       # "$ chg | % chg" per comparison
                else:
                    continue              # colspan drift → alignment unproven
            if (_CONNECTOR_EXCLUDE.search(label) or _ROW_VARIANT.search(label)
                    or st["var"]):
                # adjusted/core variant row — only the opt-in specs may read it
                _try(adj_specs, label, row_text, vals, scope,
                     f"{section_label} | {outer_label}")
                continue
            _try(specs, label, row_text, vals, scope,
                 f"{section_label} | {outer_label}")
    for alias, (target, sign) in _ALIASES.items():
        if alias in cands and target in cands:
            cands[target] += [(_Num(sign * v + 0.0, _dec(v)), sc, pl)
                              for v, sc, pl in cands.pop(alias)]
    out = {}
    for key, cs in cands.items():
        for win, lose in _SCOPE_PAIRS:
            if (any(win in sc for _, sc, _ in cs)
                    and any(lose in sc for _, sc, _ in cs)):
                cs = [c for c in cs if lose not in c[1]]
        if key in plain:
            cs = [c for c in cs if c[2]]
        vs = [v for v, _, _ in cs]
        kind = all_specs[key][1]
        if kind in ("$K", "#"):
            # Relative agreement for magnitudes/counts. For "#" this is
            # deliberately scale-BLIND: two tables printing the share count
            # at different magnitudes (thousands vs raw) disagree and refuse
            # — the tie-out then falls back to the median gate alone, a
            # graceful degradation, never a scale guess.
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
    if specs is _TABLE_SPECS:
        out["rev_sum"] = None
        if out.get("total_revenue") is None:
            out["rev_sum"] = _revenue_sum(html, expected_qend)
    return out


def _revenue_sum(html: str, qend: str) -> float | None:
    """Net interest income + noninterest income at `qend` — the release's
    revenue when it prints no total line (UNTY 3Q26: $34,291K + $2,359K).
    Owner 2026-10-13: show the sum, labeled as one (key `rev_sum`, never
    `total_revenue`). Both lines read from period-proven quarter columns
    under the revenue-basis specs (end-anchored: FTE / "after provision"
    variants never match); either missing → None."""
    t = extract_table_metrics(html, qend, specs={
        k: _REV_TABLE_SPECS[k] for k in ("nii", "nonii")}, adj_specs={},
        quarter_only=True)
    if t.get("nii") is None or t.get("nonii") is None:
        return None
    return t["nii"] + t["nonii"]


# ── Revenue basis (2026-10-06 review follow-up) ─────────────────────────────
# The consensus feed's revenueActual has EPS's GAAP-vs-adjusted problem: ZION
# 2Q26 scored "+29.6%" on a GAAP-basis actual incl. $252M of Visa/SBIC gains
# (the release's NII $677M + noninterest income $460M = $1,137M) against an
# ex-items consensus, while the release's own "Adjusted taxable-equivalent
# revenue (non-GAAP)" was $878M.
# The Results board scores a revenue surprise only when the feed's figure is
# PROVEN to be one of the release's stated figures (data/earnings_results.
# revenue_basis). These are only match targets — never displayed.
_REV_BAND = (1e5, 200e9)
# REPORTED revenue lines, any of which the feed may carry: the GAAP total
# (the exhibit's total_revenue spec), the FTE/TE total (HBAN "Total revenue -
# FTE (1)", ONB "Total revenue (FTE)"), WTFC-style "Net revenue (3)", and the
# definitional sum NII + noninterest income (ZION states no total line).
_REV_TABLE_SPECS = {
    "rev_total": _TABLE_SPECS["total_revenue"],
    "rev_fte": (r"(?:total |net )?revenues?\s*(?:[-–—,]\s*|\(\s*)"
                r"(?:fte|te|(?:fully )?tax(?:able)?[- ]equivalent)(?: basis)?"
                r"\s*\)?\s*(?:\(\d\)|\d)?\s*$", "$K", _REV_BAND),
    "rev_net": (r"net revenues?\s*(?:\(\d\)|\d)?\s*$", "$K", _REV_BAND),
    "nii": (r"net interest income\s*(?:\(\d\)|\d)?\s*$", "$K", _REV_BAND),
    "nonii": (r"(?:total )?non-?interest income\s*(?:\(\d\)|\d)?\s*$", "$K",
              _REV_BAND),
}
# ADJUSTED / ex-items revenue rows (opt-in past the adjusted-label refusal):
# ONB "Adjusted revenue" / "Total adjusted revenue", ZION "Adjusted
# taxable-equivalent revenue (non-GAAP)". "Adjusted pre-provision net
# revenue" and "adjusted noninterest income" never match.
_REV_ADJ_TABLE_SPECS = {
    "rev_adj": (r"(?:total )?(?:adjusted|core|operating)(?: total)?(?: net)?"
                r"(?: (?:fte|te|(?:fully )?tax(?:able)?[- ]equivalent))? "
                r"revenues?(?:\s*[-–—,]?\s*\(?(?:fte|te|non-?gaap)\)?)?"
                r"\s*(?:\(\w\)|\d)?\s*$", "$K", _REV_BAND),
}
# ANY mention of an adjusted / ex-items revenue — or of an adjusted
# noninterest income, which makes adjusted revenue the likely consensus basis
# even with no total stated (HBAN's "Total adjusted noninterest income
# (Non-GAAP)"). With such a mention and no extractable adjusted figure the
# basis stays UNCONFIRMED; a reported-revenue match proves nothing then.
_ADJ_REV_MARK = re.compile(
    r"\b(?:adjusted|core|operating|underlying|non-?gaap)\s+(?:total\s+)?"
    r"(?:net\s+)?(?:(?:fully\s+)?tax(?:able)?[- ]equivalent\s+|fte\s+)?"
    r"(?:revenues?|non-?interest income|fee income)\b"
    r"|\b(?:revenues?|non-?interest income)\s*,?\s*(?:\([^)]{0,20}\)\s*)?"
    r"excluding\b"
    r"|\bexcluding (?:the )?(?:impact of )?(?:notable|significant|"
    r"non-?recurring|certain) items\b" + _SPAN + r"{0,40}?\brevenues?\b",
    re.I)


def revenue_basis_facts(html: str, qend: str | None) -> dict:
    """{reported: [raw $...], adjusted: raw $ | None, adj_stated: bool} — the
    release's own current-quarter revenue figures, table-read at the column
    whose header proves `qend` (no qend → no figures)."""
    t = (extract_table_metrics(html, qend, specs=_REV_TABLE_SPECS,
                               adj_specs=_REV_ADJ_TABLE_SPECS,
                               quarter_only=True)
         if qend else {})
    reported = [t[k] for k in ("rev_total", "rev_fte", "rev_net")
                if t.get(k) is not None]
    if t.get("nii") is not None and t.get("nonii") is not None:
        reported.append(t["nii"] + t["nonii"])
    adj = t.get("rev_adj")
    return {"reported": reported, "adjusted": adj,
            "adj_stated": adj is not None
            or bool(_ADJ_REV_MARK.search(_flat_text(html)))}


# Header-row words that mark a YEAR-TO-DATE or annual column block ("Six
# Months Ended", "Nine months ended", "Year Ended", "Full Year", "YTD").
# A table whose header rows carry one is not a discrete-quarter series —
# the period-series reader skips it outright (a lone YTD table has UNIQUE
# period columns, so the twin-column refusal alone would not catch it).
_YTD_HDR = re.compile(
    r"(?i)\b(?:six|nine|twelve|6|9|12)[- ]months?\b"
    r"|\byears?[- ](?:ended|ending|to[- ]date)\b|\bytd\b|\bfull[- ]year\b"
    r"|\bannual\b")
# …and the words that PROVE a quarterly block ("Three Months Ended" — split
# across cells in many small-bank statements, so the bare word counts —
# "Quarter Ended", "Quarterly", "1st Qtr", CUBB "Q2" / "Q2 2026"). No proof
# → no series.
_QTR_HDR = re.compile(r"(?i)\bthree\b|\bquarter|\bqtr\b|\b3[- ]months?\b"
                      # Citi's bare "3Q | 4Q" part cells — NOT "2Q2026"
                      # period tokens (OPBK: "For the Six Months Ended" over
                      # "2Q2026 | 2Q2025" is a year-to-date table).
                      r"|\bq[1-4]\b|\b[1-4]q\b")


def extract_table_series(html: str, key: str = "eps_diluted") -> dict[str, float]:
    """{ISO quarter-end: value} for `key` across EVERY period column of the
    release's structured tables — the discrete-quarter SERIES a five-quarter
    table prints (FDVA/BKSC/PTBS 2026: "Earnings per Share - Diluted | 0.42 |
    0.36 | 0.36 | 0.38 | 0.35" under "June 30, 2026 | March 31, 2026 | …").
    Built for the OTC composite TTM (analysis/release_eps): the sum of four
    discrete quarters, never a single quarter served as twelve months (audit
    A21), so every column must be a PROVEN discrete quarter:

      - the same period-header proof as extract_table_metrics
        (_period_header: period tokens, split month/year rows — never
        positional luck); no header → table skipped
      - a quarter-end appearing TWICE (quarter + YTD twin columns) → skipped
      - a YTD/annual marker anywhere in the header rows (_YTD_HDR) →
        skipped; no quarter marker (_QTR_HDR) → skipped
      - a row whose value count ≠ period count (colspan drift) → skipped
      - an adjusted/core label (the connector exclusion) → skipped
      - candidates for one quarter-end across tables must agree
        (rounding-aware, as extract_table_metrics) or that quarter is absent

    Values keep their sign — a loss quarter ("$ (0.50)", FDVA 4Q25) is a real
    component of a TTM, and dropping it would turn −0.14 into +0.36. The
    magnitude band is the spec's upper bound (|v| ≤ 60). Only per-share ("$")
    specs are supported. A quarter that fails any guard is simply ABSENT from
    the result — the caller (A21 discipline) refuses a TTM with a gap."""
    lab_re, kind, band = _TABLE_SPECS[key]
    if kind != "$":
        raise ValueError(f"extract_table_series supports per-share specs only ({key})")
    cands: dict[str, list[float]] = {}
    for thtml in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(thtml)
        hdr = _period_header(rows)
        if not hdr:
            continue
        qends, hdr_i, _span_i, n_change = hdr
        if len(set(qends)) != len(qends):
            continue                      # quarter/YTD twin columns
        head_text = " ".join(c for r in rows[:hdr_i + 1] for c in r if c)
        if _YTD_HDR.search(head_text) or not _QTR_HDR.search(head_text):
            continue
        table_text = " ".join(" ".join(c for c in cells if c)
                              for cells in rows).lower()
        table_per_share = "per share" in table_text
        st = {"ytd": False, "seg": False, "var": False, "section": "",
              "outer": "", "qends": qends}
        for cells in rows[hdr_i + 1:]:
            label_idx = next((i for i, c in enumerate(cells) if c), None)
            if label_idx is None:
                continue
            label = cells[label_idx]
            if _skip_row(cells, label, st) or st["var"] or st.get("sub"):
                continue                  # heading / YTD / segment / non-GAAP
            row_text = " ".join(cells)
            vals = _row_values(cells[label_idx:])
            if len(vals) == len(qends) + n_change and n_change:
                vals = vals[:len(qends)]
            if len(vals) != len(qends):
                continue
            if _CONNECTOR_EXCLUDE.search(label) or _ROW_VARIANT.search(label):
                continue                  # adjusted/core variant row
            if not re.match(r"\s*" + lab_re, label, re.I):
                continue
            if ("$" not in row_text and not table_per_share) or "%" in row_text:
                continue
            for q, v in zip(qends, vals):
                if abs(v) <= band[1]:
                    cands.setdefault(q, []).append(v)
    out: dict[str, float] = {}
    for q, vs in cands.items():
        tol = max(_AGREE_USD, 0.5 * 10.0 ** -min(_dec(v) for v in vs))
        if (max(vs) - min(vs)) <= tol:
            out[q] = max(vs, key=_dec)
    return out


def _dec(v: float) -> int:
    """Printed decimal places of a parsed value (61.1 → 1, 61.08 → 2, and
    "3.90" → 2 — see _Num). A plain float (no printed form kept) falls back
    to repr(), which round-trips short decimals but drops trailing zeros."""
    dec = getattr(v, "dec", None)
    if dec is not None:
        return dec
    s = repr(float(v))
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

# How recently poll-events must have ingested SOMETHING for the store to be
# trusted as the frontier authority. Generous vs the 1-minute poll cadence, so a
# brief blip doesn't stampede every bank back onto EDGAR, but far short of a
# quarter — a poller wedged longer than this degrades to the live index rather
# than silently freezing every bank's release metrics.
_POLLER_LIVENESS_S = 3 * 3600

_CIK_TICKER: dict[int, str] = {}
_CIK_TICKER_GEN = 0


def _ticker_for_cik(cik) -> str | None:
    """CIK → the ticker its 8-Ks are attributed to, resolved the SAME canonical
    way the adapter used when it wrote those rows (a registrant with preferred /
    ETN siblings must land on the same primary common or the lookup misses).
    Memoized, but an EMPTY map is never pinned — that was audit P1 #3's bug —
    and rebuilt when bank_universe reloads a newer snapshot (P1-7)."""
    global _CIK_TICKER, _CIK_TICKER_GEN
    try:
        key = int(cik)
    except (TypeError, ValueError):
        return None
    from data.bank_universe import universe_generation
    gen = universe_generation()
    if not _CIK_TICKER or _CIK_TICKER_GEN != gen:
        try:
            from data.bank_universe import get_universe_tickers
            from data.events.sec_8k import _canonical_cik_map
            built = _canonical_cik_map(sorted(get_universe_tickers()))
        except Exception:
            built = {}
        if not built:
            return _CIK_TICKER.get(key)      # prior map (or None); retry next call
        _CIK_TICKER, _CIK_TICKER_GEN = built, gen
    return _CIK_TICKER.get(key)


# Why the store fast-path did or didn't answer, counted per process. The
# frontier change (2026-07-28) was expected to remove ~538 multi-MB EDGAR
# submissions fetches per metrics build; 24 consecutive runs measured 2026-08-02
# show build_metrics UNCHANGED at a 1618s median, so either the fast path is not
# engaging or that fetch was never the bottleneck. These counters answer which,
# instead of a third hypothesis read off the code.
FRONTIER_STATS: dict[str, int] = {
    "hit": 0,            # store answered
    "no_poller": 0,      # liveness gate closed
    "no_ticker": 0,      # CIK didn't resolve to a ticker
    "no_8k_row": 0,      # ticker resolved, no 8-K on record
    "error": 0,          # store read raised
    "edgar": 0,          # fell through to the live submissions index
}


def _accession_from_store(cik) -> str | None:
    """The frontier per the events store, or None when the store cannot PROVE an
    answer (poller not demonstrably live, no ticker mapping, no 8-K on record) —
    in which case the caller falls back to the live submissions index.

    poll-events ingests every 8-K within a minute, INCLUDING the 9.01-only
    filings that used to be dropped, so this replaces a multi-MB submissions
    fetch per bank with one indexed row read. That fetch — ~538 of them,
    serialized, on every metrics build past the 15-min re-check gate — was the
    dominant cost of the 2026-07-27 refresh-home-snapshot timeout incident.

    Every exit increments FRONTIER_STATS so a run reports which branch it took."""
    try:
        from data.events.store import events_ingested_within, latest_8k_accession
        if not events_ingested_within(_POLLER_LIVENESS_S):
            FRONTIER_STATS["no_poller"] += 1
            return None
        tk = _ticker_for_cik(cik)
        if not tk:
            FRONTIER_STATS["no_ticker"] += 1
            return None
        acc = latest_8k_accession(tk)
        if acc is None:
            FRONTIER_STATS["no_8k_row"] += 1
        else:
            FRONTIER_STATS["hit"] += 1
        return acc
    except Exception:
        FRONTIER_STATS["error"] += 1
        return None


def _current_accession(cik) -> str | None:
    """The FRONTIER accession — the newest 8-K that could BE the earnings
    release (Item 2.02, or exhibit-bearing without 2.02: ASB furnishes its
    news release under Item 9.01 only); None when unavailable. Compared against
    the stored record's `frontier`, not its release accession, so a furnished
    non-release 8-K (investor deck) costs at most ONE re-selection pass — never
    a refetch loop.

    Answered from the events store when it can prove an answer (see
    _accession_from_store), else the live submissions index — one request. The
    store path returns the newest 8-K of ANY item set rather than the newest
    earnings CANDIDATE; that is deliberately conservative, since the only effect
    of an over-eager frontier is the single re-selection pass the caller already
    handles at the "frontier moved, release unchanged" branch."""
    acc = _accession_from_store(cik)
    if acc is not None:
        return acc

    FRONTIER_STATS["edgar"] += 1
    from data.ir_provider import _earnings_8k_candidates
    from data.sec_filing_scraper import _get
    try:
        cik10 = str(int(cik)).zfill(10)
        subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    except Exception:
        return None
    cands = _earnings_8k_candidates(subs)
    return cands[0]["accession"] if cands else None


def cached_release_metrics(cik) -> dict | None:
    """The stored release_metrics() extraction for the CIK — CACHE ONLY,
    never a fetch. Built for the 440-bank metrics build (release-first
    increment 3): release_metrics() itself puts a submissions-index
    re-check — and, cold, a full release fetch + extraction — on the call
    path, which is exactly the 2026-07-27 timeout class at build scale. A
    bank with no cached extraction returns None (the column renders '—')
    until the Results board / poll-events warms the cache through the
    normal release_metrics() path.

    No TTL on the read: an old extraction is DATA (the bank's last
    release), and every caller must gate on the stored `qend` itself (the
    _otc_release_ps ~200-day staleness precedent) — never trust this value
    as current.

    COUPLING: the key literal below must match release_metrics()'s key —
    a version bump that misses one of the two silently deadens this
    reader. tests/test_release_first_efficiency.py pins them equal."""
    if not cik:
        return None
    from data import cache as _cache
    try:
        cached = _cache.get(f"release_metrics:v37:{int(cik)}", max_age_s=None)
    except Exception:
        return None
    return (cached or {}).get("value") or None


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

    # v27 (2026-10-12): legacy date / 2Q26 headers under a lone YTD banner
    # refused (NKSH six-month EPS, GBCI six-month NIM served as quarters);
    # prose "basic and diluted … respectively" pairs mapped by order (TCBX
    # basic EPS served as diluted); consolidated beats continuing-operations
    # rows (KEY ROTCE); plain NIM rows beat TE rows release-wide (SBSI).
    # (Re-bumped past v26, which a parallel PR used for the prose guard below.)
    # v37 (2026-10-13): NIM / efficiency basis audit — efficiency follows
    # the plain-over-FTE rule (SBSI, EWBC, VABK); GAAP-marked prose NIM is
    # plain-basis evidence, so FTE-only table columns step aside (CBFV,
    # COFS); rows of a TE-titled table are not plain (CARE); printed
    # precision ("3.90" = 2 decimals) drives table agreement; YTD / LTM
    # rows are never quarter values (WSBF, SFNC, TCBI); split month/year
    # (and ordinal/year) headers pair 1:1 or as the HTML colspans prove,
    # never truncated (FCCO); rows under an in-body period row read against
    # it (EWBC 6M block, FSBC, FFIN, LNKB); "division" headings are segment
    # blocks (CBAN); "$X million or N%" is a change, never a level ratio
    # (BKU); multi-period context = the value's sentence, trailing clause
    # and bullet-list heading, and two-decimal prose values a basis point
    # apart disagree (BSRR, GS, MS, ASRV); "6/30/26" dates (CBFV, AUB).
    # (Re-bumped past v32-v36, which #399 / #401 / #402 / #404 / #405 used.)
    # v36 (2026-10-13): prose basic/diluted EPS pairs map by stated order
    # without "respectively" (NEWT 2Q26 reads its reported $0.47), and a
    # PERIOD pair is never basic/diluted (STLE 1Q26 "for the quarters ended
    # March 31, 2026 and 2025 were $1.43 and $0.97, respectively" shipped
    # last year's 0.97 as diluted EPS).
    # (Re-bumped past v35, which #404 used for Citi's supplement.)
    # v35 (2026-10-13): Citi's supplement — oldest-first split header
    # whose year row runs on into change labels + a YTD block ("3Q | … | 3Q"
    # over "2025 … 2026 | 2Q26 | 3Q25 | 2025 | 2026"), measure-major
    # average-rate tables (NIM / loan yield), NCL / ACLL labels, firmwide
    # page headings after the segment block, "Diluted earnings per share:"
    # → "Net income (loss)"; year cells with footnotes ("2026 (1)"); Tier 1
    # capital "as a percentage of average assets" is leverage (AVBC).
    # v34 (2026-10-13): a NON-2.02 exhibit must also name the results as
    # what it reports (ir_provider._RESULTS_TITLE) — dividend notices
    # (CAC/CFFI/NBHC/QCRH/FKYS/FMCB), a deck (CHMG) and a scheduling notice
    # (SSB) were these banks' frontier records; and a forecast is never a
    # result (NEWT's "EPS guidance range of $0.42-$0.52" read as EPS).
    # (Re-bumped past v33, which #401 used for Citi.)
    # v33 (2026-10-13): Citi-style segment tables named in the header
    # caption ("Services ($ in millions …)") are skipped; "(4)%" change
    # cells hold their column; "total operating expenses" in a label's
    # definition is not an "operating" variant (Citi efficiency 58.5%).
    # v32 (2026-10-13): the finder steps past a NON-RESULTS 2.02 exhibit
    # (NEWT's dividend declarations, deal closings) and never past an
    # Item 4.02 filing — NEWT's record's frontier IS the dividend 8-K.
    # (Re-bumped past v31, which #395 used for exhibit coverage.)
    # v31 (2026-10-13): Results-board exhibit coverage (UNTY/FBK/JPM/WFC/
    # GS audit) — capital / TCE / cost-of-deposits / loan-yield (incl.
    # average-balance groups) / NII+nonII-sum table reads; YTD sections and
    # banners, N/A-dash slots, "% change from" period columns, segment and
    # non-GAAP blocks; GS's nine-month efficiency refused (value-trailing
    # period guard); the EX-99.2 supplement's consolidated tables fill
    # what the release leaves empty; capital merged via merge_table_capital.
    # v30 (2026-10-13): the finder also steps past a call TRANSCRIPT
    # furnished under 2.02 after the release (PEBO served NCO only, BCBP
    # nothing; SBFG's Q4-25 transcript read 2024's $1.72 EPS as the
    # quarter's) — their records' frontier IS the transcript.
    # v29 (2026-10-13): the finder (ir_provider.latest_earnings_release)
    # steps past a call NOTICE to the previous release — WSBC's Item 2.02
    # "to Host … Earnings Conference Call" record (frontier = the notice)
    # shadowed its Q2 release and would otherwise be re-served forever.
    # v28 (2026-10-12): `results_stated` — whether a figure-less exhibit
    # states results (NEWT's Item 2.02 dividend announcement does not).
    # v26 (2026-10-12): prose candidates in a MULTI-QUARTER context (nine/
    # six/twelve months, year-to-date, full year) are refused — WBHC's
    # letter states only nine-month EPS ($5.29), shipped as the quarter's.
    # v25 (2026-10-12): split / comparison period headers — years above
    # bare ordinals over a "Quarter" row (HBAN), a caption cell on a split
    # month/year row (MSBI), quarter + YTD blocks (CUBB), numeric dates and
    # "2Q26 vs. 1Q26" change columns (RF); "excluding …" rows are variants;
    # new header forms under a lone YTD banner are refused. Revenue-basis
    # confirmation 13 -> 23 of the 76 board rows with a revenue actual.
    # v23 (2026-10-06): `rev_basis` — the release's own current-quarter
    # reported / adjusted revenue figures + an adjusted-revenue-mentioned
    # flag, so the Results board scores a revenue surprise only on a
    # confirmed basis (ZION 2Q26 "+29.6%" was GAAP revenue incl. Visa gains
    # vs an ex-items consensus). Also re-selects releases the old 4-row
    # candidate cap skipped (HBAN 2Q26 behind four gated 8-Ks).
    # v22 (2026-10-06): adjusted / ex-items EPS (review P0-1) — prose forms
    # (JPM "excluding significant items … ($6.14 per share)", ZION "(or
    # $1.74 excluding notable items)", HBAN/ONB "adjusted EPS 1 was/of"),
    # widened table labels (ONB "Adjusted EPS, diluted"), and the new
    # `eps_adj_stated` flag the Results board needs to confirm a surprise's
    # GAAP-vs-adjusted basis. Forces a universe-wide re-extraction.
    # v20 (2026-10-01): bv_ps per-COMMON only when the release carries
    # preferred equity (NPB: v19 served $17.69 incl. preferred).
    # v19 (2026-10-01): deterministic bv_ps (prose + table) — BV/share was
    # AI-fill only, so wire banks showed a stale reconstructed BV beside a
    # fresh release TBV (GLBZ). Forces a universe-wide re-extraction.
    # v18 (2026-08-20): EPS tie-out input rows — net income applicable to
    # common ("$K", scale-sniffed) + weighted average diluted shares ("#",
    # raw printed count) so analysis/valuation._release_eps_tie_out can
    # verify the release quarter's extracted EPS arithmetically (NI ÷
    # shares, scale-agnostic — the sec_earnings_8k._internal_tie_out
    # discipline). Plus the span-row fix the ONB ground-truth check forced:
    # a split month/year header under a "Three|Six Months Ended" banner put
    # the banner TWO rows above the year row, so the duplicate-quarter
    # guard looked at the month row, found no span markers, and skipped
    # every such table (ONB's tables were prose-only before this). Forces a
    # universe-wide re-extraction; warmed by poll-events / the Results
    # board as usual.
    # v17 (2026-08-02): TBV/share dollar-change-then-level form —
    # "increased $1.67, or 15.6%, to $12.38" (FSRL 2Q26). Both existing
    # patterns' [^$%] connectors stop at the change amount, so the value
    # was missed entirely; found auditing the 75 smallest banks, where
    # small-bank TBV coverage matters most.
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
    key = f"release_metrics:v37:{int(cik)}"
    try:
        # Freshness is judged below (15-min is_fresh + accession-match
        # re-stamp); the 24h read ceiling dropped `prev` daily, forcing a
        # full re-extraction per SEC filer (2026-07-27 timeout incident).
        cached = _cache.get(key, max_age_s=None)
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
        # Any adjusted / ex-items EPS mention at all (value extracted or
        # not) — the board's surprise-basis check (data/earnings_results).
        "eps_adj_stated": adjusted_eps_stated(rel["html"]),
        "call_notice": is_call_notice(rel["html"]),
        "results_stated": states_results(rel["html"]),
        # The revenue surprise's basis check (data/earnings_results
        # .revenue_basis) — match targets only, never displayed.
        "rev_basis": revenue_basis_facts(rel["html"], qend),
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
    val["capital"] = merge_table_capital(val["metrics"], val["capital"])
    for bucket in ("prior_metrics", "yoy_metrics"):
        _order_capital(val[bucket])
    # The 8-K's financial supplement (EX-99.2): megabanks keep NIM / ROA /
    # credit / capital history there, and the guarded-AI layer used to be
    # the only reader (dead in prod 2026-10-13: API credit exhausted —
    # owner: "why can't you just extract the data"). Same table readers,
    # consolidated section only, gap-fill only.
    try:
        from data.ir_provider import earnings_supplement
        sup = earnings_supplement(cik, rel.get("accession") or "")
    except Exception:
        sup = None
    rel["_sup"] = sup                     # _ai_fill reuses it, no refetch
    if sup and sup.get("html"):
        _supplement_fill(val, sup["html"], rel["html"])
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


_CAP_KEYS = ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio")


def _capital_ordered(d: dict) -> bool:
    """CET1 ≤ Tier 1 ≤ Total (5bp rounding slack) wherever both are present
    — the identity every bank's risk-based ratios satisfy. A violation
    means a row or column mix-up (the ir_provider rule)."""
    c, t1, tot = d.get("cet1_ratio"), d.get("t1_ratio"), d.get("total_ratio")
    return not ((c is not None and t1 is not None and c > t1 + 0.051)
                or (t1 is not None and tot is not None and t1 > tot + 0.051)
                or (c is not None and tot is not None and c > tot + 0.051))


def _order_capital(bucket: dict) -> None:
    """Blank a period bucket's risk-based ratios when they violate the
    ordering identity (never guess which one is wrong)."""
    if bucket and not _capital_ordered(bucket):
        for k in ("cet1_ratio", "t1_ratio", "total_ratio"):
            bucket[k] = None


def merge_table_capital(metrics: dict, capital: dict | None) -> dict:
    """The current quarter's capital dict: ir_provider's prose-AND-table
    corroborated ratios win; the period-proven TABLE reads (extract_table_
    metrics' capital specs) fill the gaps — UNTY 3Q26 states CET1 14.46% in
    prose and in a table row with no "%" of its own, which the corroborator
    requires, so every ratio was refused. Capital keys move out of
    `metrics` (the exhibit merges capital over metrics; a None there must
    not mask a value). Filled values that break CET1 ≤ T1 ≤ Total are
    dropped again. Mutates `metrics`; returns the new capital dict."""
    cap = dict(capital or {})
    filled = []
    for k in _CAP_KEYS:
        v = metrics.pop(k, None)
        if cap.get(k) is None and v is not None:
            cap[k] = v
            filled.append(k)
    if filled and not _capital_ordered(cap):
        for k in filled:
            cap[k] = None
    return cap


# Exhibit keys the supplement may fill. Revenue is handled as one group
# (total_revenue / rev_sum) and per-COMMON book value under preferred.
_SUP_FILL_KEYS = (
    "eps_adj", "eps_diluted", "nim", "efficiency", "roa", "roe", "rotce",
    "loan_yield", "cost_of_deposits", "cet1_ratio", "t1_ratio", "total_ratio",
    "lev_ratio", "tce_ratio", "tbv_ps", "bv_ps", "div_ps", "nco_ratio",
    "npa_assets", "acl_loans")


_FIRMWIDE_TITLE = re.compile(r"(?i)\bcredit[- ]related information\b|"
                             r"\b(?:credit|asset) quality\b|"
                             # Citi's page headings: "ALLOWANCE FOR CREDIT
                             # LOSSES ON LOANS (ACLL) …", "AVERAGE BALANCES
                             # AND INTEREST RATES", "NON-ACCRUAL ASSETS"
                             r"\ballowance for credit losses\b|"
                             r"\baverage balances and interest rates\b|"
                             r"\bnon-?accrual assets\b")


def _consolidated_html(sup_html: str, cap: int = 120_000) -> str:
    """The supplement's FIRMWIDE front section as HTML, cut at the same
    boundary _consolidated_slice finds in text (first segment detail-page
    header past the TOC, else the conservative front cap). The cut falls at
    the start of the table (or text run) holding the boundary, so a segment
    page's tables never enter — JPM's "CONSUMER & COMMUNITY BANKING
    FINANCIAL HIGHLIGHTS" header sits inside the first segment table."""
    segs = re.split(r"(?is)(<table[^>]*>.*?</table>)", sup_html or "")
    starts, flat, tpos, hpos = [], [], 0, 0
    for s in segs:
        f = _flat_text(s)
        starts.append((hpos, tpos))
        flat.append(f)
        tpos += len(f) + 1
        hpos += len(s)
    full = " ".join(flat)
    cut = next((m.start() for m in _SEG_DETAIL.finditer(full)
                if m.start() >= _TOC_FLOOR), None)
    cut = min(cut, cap) if cut is not None else _NO_MARKER_CAP
    end = max((h for h, t in starts if t <= cut), default=0)
    # Past the segment pages, FIRMWIDE credit pages come back (JPM:
    # "CREDIT-RELATED INFORMATION" after Corporate) — a table whose own
    # title names one, and no segment, is kept.
    def _firmwide_page(j, s):
        title = _flat_text(s)[:300]
        if _SEG_DETAIL.search(title):
            return False
        if _FIRMWIDE_TITLE.search(title):
            return True
        heading = flat[j - 1][-160:] if j else ""
        m = _FIRMWIDE_TITLE.search(heading)
        return bool(m) and m.group(0).isupper()

    tail = [s for j, (s, (h, _t)) in enumerate(zip(segs, starts))
            if h >= end and s[:6].lower() == "<table" and _firmwide_page(j, s)]
    return (sup_html or "")[:end] + "".join(tail)


def _supplement_fill(val: dict, sup_html: str, rel_html: str) -> None:
    """Fill exhibit cells the RELEASE left empty from the supplement's
    consolidated tables — per key, all three columns from ONE document: a
    key the release states in any column is never touched (no mixed-basis
    deltas: a release's plain NIM beside a supplement's FTE history). The
    supplement reads through the same period-proven table reader."""
    html = _consolidated_html(sup_html)
    if not html:
        return
    periods = (("metrics", val.get("qend")),
               ("prior_metrics", val.get("prior_qend")),
               ("yoy_metrics", val.get("yoy_qend")))
    tabs = {b: (extract_table_metrics(html, q) if q else {}) for b, q in periods}
    cap = val.setdefault("capital", {})

    def stated(k):
        return (any((val.get(b) or {}).get(k) is not None for b, _ in periods)
                or cap.get(k) is not None)

    def rel_val(b, k):
        v = (val.get(b) or {}).get(k)
        return cap.get(k) if v is None and b == "metrics" else v

    def agrees(a, b):
        tol = max(_AGREE_USD, 0.5 * 10.0 ** -min(_dec(a), _dec(b)))
        return abs(a - b) <= tol

    pref = _release_shows_preferred_equity(rel_html)
    filled_cap = []
    for k in _SUP_FILL_KEYS:
        src_k = "bv_ps_common" if (k == "bv_ps" and pref) else k
        if stated(k):
            # The release states this key somewhere: the supplement may
            # fill its OTHER columns only when every column both documents
            # print AGREES — proof they report one basis (JPM: release TBV
            # $112.64 and dividend $1.65 = supplement's; the supplement
            # adds 2Q26 / 3Q25). Any disagreement → release only.
            both = [(rel_val(b, k), tabs[b].get(src_k)) for b, _ in periods]
            both = [(a, s) for a, s in both if a is not None and s is not None]
            if not both or not all(agrees(a, s) for a, s in both):
                continue
        for b, _ in periods:
            v = tabs[b].get(src_k)
            if v is None or rel_val(b, k) is not None:
                continue
            if b == "metrics" and k in _CAP_KEYS:
                cap[k] = v
                filled_cap.append(k)
            else:
                val.setdefault(b, {})[k] = v
    if filled_cap and not _capital_ordered(cap):
        for k in filled_cap:
            cap[k] = None
    for b, _ in periods[1:]:
        _order_capital(val.get(b) or {})
    if not (stated("total_revenue") or stated("rev_sum")):
        key = ("total_revenue" if any(tabs[b].get("total_revenue") is not None
                                      for b, _ in periods) else "rev_sum")
        for b, _ in periods:
            if tabs[b].get(key) is not None:
                val.setdefault(b, {})[key] = tabs[b][key]


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
        if "_sup" in rel:
            sup = rel["_sup"]             # fetched by the extraction pass
        else:
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
    # The AI can't tell per-common from total-equity BV: with preferred equity
    # in the release, BV/share stays deterministic-only (NPB, see _BV_COMMON).
    pref = _release_shows_preferred_equity(rel.get("html") or "")
    for bucket_key, period in (("metrics", "cur"), ("prior_metrics", "prior"),
                               ("yoy_metrics", "yoy")):
        bucket = val.setdefault(bucket_key, {})
        for k, v in (ai.get(period) or {}).items():
            if k == "bv_ps" and pref and not _bv_ties_to_common(
                    rel.get("html") or "", v):
                continue
            if bucket.get(k) is None:
                bucket[k] = v
    # Current-quarter capital: the dedicated extractor's values win; AI fills
    # the gaps in the capital dict the exhibit merges over `metrics`.
    cap = val.setdefault("capital", {})
    for k in ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio"):
        if cap.get(k) is None and (ai.get("cur") or {}).get(k) is not None:
            cap[k] = ai["cur"][k]
    return "ok"
