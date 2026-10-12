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
        if (_MULTI_PERIOD.search(text[start:end]) or _MULTI_PERIOD.search(
                text[max(0, start - 70):start].rsplit(". ", 1)[-1])):
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
                plain_only: bool = False) -> float | None:
    """Percent metric: '<label> … <verb> X.XX%', the basis-point form
    '<label> … <verb> N bps' (÷100), or either side of an
    'A and B were X% and Y%, respectively' pair. `plain_only` drops a
    candidate marked tax-equivalent (_NIM_TE) in its label→value span or
    earlier in its sentence ("Fully taxable equivalent net interest margin
    was 3.38%", FRME) — the plain-basis NIM release (see _plain_nim_basis)."""
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
        net interest margin of 3.49%" marks the income, not the margin."""
        before = re.split(r"\.\s|[■•;$%]", text[max(0, start - 80):start])[-1]
        return plain_only and bool(_NIM_TE.search(before + " " +
                                                  text[start:label_end]))

    def _lab_end(m, lead=""):
        return m.start() + re.match(lead + label_re, text[m.start():],
                                    re.I).end()

    def _gen():
        for m in pat.finditer(text):
            if _RESP_TAIL.match(text[m.end():m.end() + 60]):
                continue                  # the pair parser owns this value
            if not _te(m.start(), m.end()):
                yield m.start(), m.group(1), m.end()
        for m in bps.finditer(text):
            if not _te(m.start(), m.end()):
                yield m.start(), str(float(m.group(1)) / 100), m.end()
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
_EPS_PAIR = re.compile(
    r"\b(basic|diluted)\s+(?:and|&)\s+(diluted|basic)\s+"
    r"(?:earnings per (?:common )?share|eps\b)(?:\s*\(“?\"?eps”?\"?\))?"
    r"[^$%]{0,60}?(?::|\b(?:of|was|were|at))\s*\$\s?(\d{1,2}\.\d{2})"
    + _PER_SH + r"\s*,?\s*and\s+\$\s?(\d{1,2}\.\d{2})" + _PER_SH +
    r"\s*,?\s*respectively", re.I)


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
            if first != second:
                yield (m.start(), m.group(3) if first == "diluted"
                       else m.group(4), m.end())
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
                    or _ADJ_EPS_PAIR.match(after)):
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
        out[key] = _pct_metric(text, label, band)
    # Plain-basis NIM release (_plain_nim_basis): a narrated NIM serves only
    # when dropping its tax-equivalent candidates leaves the SAME value — a
    # TE narration (FRME "Net interest margin on a fully taxable equivalent
    # basis of 3.38%"; its GAAP row is 3.25%) would sit beside plain prior /
    # year-ago columns, a mixed-basis delta. The full candidate set still
    # decides first, so dropping a TE candidate can never unmask a lone
    # disagreeing one (ATLO's "3.10% for the six months").
    plain_nim = _plain_nim_basis(html)
    if plain_nim and out["nim"] is not None and out["nim"] != _pct_metric(
            text, *_PCT_SPECS["nim"], plain_only=True):
        out["nim"] = None
    for key, (label, denom, band) in _PINNED_SPECS.items():
        out[key] = _pinned_metric(text, label, denom, band)
    out["tbv_ps"] = _dollar_metric(text, _TBV_PATS, _TBV_BAND)
    out["bv_ps"] = _dollar_metric(text, _BV_PATS, _BV_BAND)
    out["bv_ps_common"] = _dollar_metric(text, _BV_COMMON_PATS, _BV_BAND)
    out["div_ps"] = _dollar_metric(text, _DIV_PATS, _DIV_BAND)
    out["eps_diluted"] = _eps_metric(text)
    out["eps_adj"] = _adj_eps_metric(text)
    if plain_nim and out["nim"] is not None and expected_qend:
        # …and a narrated NIM that IS the release's own tax-equivalent row
        # value is the TE figure under a bare label: WTFC 2Q26 "Net interest
        # margin … at 3.52%" = its "fully taxable-equivalent (non-GAAP)"
        # row; its GAAP row (which then serves) is 3.50%.
        te = extract_table_metrics(html, expected_qend, specs={
            "nim_te": (r"(?=.*?(?:" + _NIM_TE.pattern + r"))"
                       + _TABLE_SPECS["nim"][0], "%", _TABLE_SPECS["nim"][2])},
            adj_specs={}).get("nim_te")
        if te is not None and abs(out["nim"] - te) < 0.005:
            out["nim"] = None
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
_NUMDATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
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
_CHANGE_COL = re.compile(r"(?i)\b(?:seq|yr/yr|yoy|qoq|lq|vs|versus|change|"
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
        try:
            return _date(int(m.group(3)), int(m.group(1)),
                         int(m.group(2))).isoformat()
        except ValueError:
            return None
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
    "bv_ps": (r"book value per (?:common )?share", "$", (1.0, 900.0)),
    "bv_ps_common": (r"book value per common share", "$", (1.0, 900.0)),
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


def _period_header(rows: list[list[str]]) -> tuple | None:
    """The table's PERIOD HEADER: (qends, hdr_i, span_i, n_change) — the ISO
    quarter-end per period column, the header row index, the row where a
    "Three Months Ended | Six Months Ended" span banner would sit, and the
    count of trailing %-change columns value rows must also carry — or None
    when no row proves the layout (bare years, lone dates). Shared by the
    single-column reader (extract_table_metrics) and the period-series
    reader (extract_table_series): one header proof, two consumers.
    """
    # span_i: the row where a "Three Months Ended | Six Months Ended"
    # SPAN banner would sit for this table's header shape — directly
    # above the period row for a single-row header, but above the MONTH
    # row for a split month/year header (ONB 2Q26: span, then "June 30,"
    # cells, then years — three rows; found 2026-08-20 wiring the EPS
    # tie-out, whose NI/share rows live in exactly those tables).
    for i, cells in enumerate(rows[:8]):
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
            return found, i, i - 1, n_change
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
                    if _ytd_only_head(rows, i, i + 1):
                        return None       # FITB "Year to Date" over "June | June"
                    from datetime import date as _date
                    qends = [_date(y, m, _QE_MONTH[m]).isoformat()
                             for m, y in zip(months, years)]
                    # span banner sits above the months row
                    return qends, i + 1, i - 1, len(extras)
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
_YEAR_CELL = re.compile(r"^20\d{2}$")
_MON_CELL = re.compile(
    r"(?i)^(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|"
    r"dec)\.?(?:\s+(\d{1,2}))?,?$")
_ORD_CELL = re.compile(
    r"(?i)^(?:(first|second|third|fourth|1st|2nd|3rd|4th)"
    r"(\s+(?:quarter|qtr\.?))?|q([1-4]))$")
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
        if m.group(3):
            return int(m.group(3)), True
        return _QNUM[m.group(1).lower()], bool(m.group(2))
    return None


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
        if y and y[0] and is_change(y[1]) and not ytd_only(last + 1):
            years = [int(c) for c in y[0]]
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
_SCOPE_CONS = re.compile(r"(?i)\bconsolidated\b")
_SCOPE_CONT = re.compile(r"(?i)\bcontinuing operations\b")


def _row_scope(text: str) -> str | None:
    cons, cont = _SCOPE_CONS.search(text), _SCOPE_CONT.search(text)
    return "cons" if cons and not cont else "cont" if cont and not cons else None


# A tax-equivalent NIM label ("Net interest margin (FTE)", "- TE", "tax-
# equivalent basis", "fully taxable equivalent"). There is no separate FTE
# metric: where the release ALSO prints a readable PLAIN NIM row in any
# period-proven table (SBSI 2Q26: "Net interest margin 2.80 %" over "Net
# interest margin (FTE) (1) 2.90 %" — one rule matched both and the
# disagreement refused both), only plain rows serve the NIM — release-wide,
# so the current / prior / year-ago columns all read one basis (prose too:
# see extract_release_metrics). A release printing no plain row keeps its
# TE rows (the conventional reported basis — see _CONNECTOR_EXCLUDE).
_NIM_TE = re.compile(r"\b(?:fte|te|teq)\b|(?<!non )(?<!non-)\b(?:fully )?"
                     r"tax(?:able)?[- ]equivalent", re.I)
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


def _nim_plain_label(label: str) -> bool:
    """A PLAIN NIM row label: the NIM label plus only _NIM_PLAIN_TAIL."""
    m = re.match(r"\s*" + _TABLE_SPECS["nim"][0], label, re.I)
    return bool(m) and bool(_NIM_PLAIN_TAIL.fullmatch(label[m.end():]))


def _plain_nim_basis(html: str) -> bool:
    """True when any period-proven table prints a READABLE plain NIM row (one
    value per period column, + any counted change columns) — the release's
    NIM basis is then the plain one: only plain rows serve NIM (TE rows and
    variants step aside, in every column alike). An unreadable row is no
    evidence (BUSE "Net interest margin (i)(v)" interleaves $ amounts)."""
    for thtml in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(thtml)
        hdr = _period_header(rows)
        if not hdr:
            continue
        qends, hdr_i, _span_i, n_change = hdr
        for cells in rows[hdr_i + 1:]:
            j = next((i for i, c in enumerate(cells) if c), None)
            if j is not None and _nim_plain_label(cells[j]) and len(
                    _row_values(cells[j:])) in (len(qends),
                                                len(qends) + n_change):
                return True
    return False


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
    plain_nim = "nim" in specs and _plain_nim_basis(html)
    for thtml in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(thtml)
        hdr = _period_header(rows)
        if not hdr:
            continue                      # no period row → skip table
        qends, hdr_i, span_i, n_change = hdr
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

        def _try(specs, label, row_text, vals, scope):
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
                elif kind == "#" and ("$" in row_text or "%" in row_text):
                    # A share COUNT row carries neither symbol; a $/% on the
                    # row marks a dollar or ratio line mismatched into it.
                    continue
                else:
                    v = vals[col]
                if band[0] <= v <= band[1]:
                    cands[key].append((v, scope, _nim_plain_label(label)))

        section = None
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
            if sum(1 for c in cells if c) == 1:
                section = _row_scope(label)       # label-only section row
            scope = _row_scope(label) or section
            row_text = " ".join(cells)
            # _row_values itself skips its first cell (the label slot), so
            # hand it the row FROM the label cell onward.
            vals = _row_values(cells[label_idx:])
            if len(vals) == len(qends) + n_change and n_change:
                vals = vals[:len(qends)]  # trailing change cols (header-counted)
            elif cmp and _cmp_proves(vals, len(qends), cmp):
                vals = vals[:len(qends)]  # "$ chg | % chg" per comparison
            if len(vals) != len(qends):
                continue                  # colspan drift → alignment unproven
            if _CONNECTOR_EXCLUDE.search(label) or _ROW_VARIANT.search(label):
                # adjusted/core variant row — only the opt-in specs may read it
                _try(adj_specs, label, row_text, vals, scope)
                continue
            _try(specs, label, row_text, vals, scope)
    out = {}
    for key, cs in cands.items():
        if (any(sc == "cons" for _, sc, _ in cs)
                and any(sc == "cont" for _, sc, _ in cs)):
            cs = [c for c in cs if c[1] != "cont"]
        if key == "nim" and plain_nim:
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
    return out


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
                      r"|\bq[1-4]\b")


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
        for cells in rows[hdr_i + 1:]:
            label_idx = next((i for i, c in enumerate(cells) if c), None)
            if label_idx is None:
                continue
            label = cells[label_idx]
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
        cached = _cache.get(f"release_metrics:v28:{int(cik)}", max_age_s=None)
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
    key = f"release_metrics:v28:{int(cik)}"
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
