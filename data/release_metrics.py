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
index hourly and re-extracts only when a NEW accession appears — earnings-
morning freshness at ~1 request/bank/hour.
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
_NUM = r"(\d{1,2}(?:\.\d{1,2})?)"

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
    "nco_ratio": (r"net charge-offs?(?: ratio)?", (0.0, 5.0)),
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
# prior-period comparisons and non-GAAP variants.
_EXCLUDE_BEFORE = re.compile(
    r"\b(?:adjusted|core|operating|non-?gaap|normalized|underlying|pro forma|"
    r"compared (?:to|with)|versus|vs\.?|year[- ]ago|prior[- ](?:year|quarter)|"
    r"linked[- ]quarter|from|down from|up from)\s*(?:an?\s+|the\s+)?$", re.I)
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


def _pct_metric(text: str, label_re: str, band) -> float | None:
    """Percent metric: '<label> … <verb> X.XX%', first % after the label only."""
    pat = re.compile(label_re + r"[^%]{0,60}?" + _VERB + _NUM + r"\s*%", re.I)
    return _clean(text, ((m.start(), m.group(1), m.end()) for m in pat.finditer(text)),
                  band, _AGREE_PCT)


def _pinned_metric(text: str, label_re: str, denom_re: str, band) -> float | None:
    """Denominator-pinned percent: the base must appear either in the label
    ('<label> to <denominator> … X%') or right after the value ('<label> …
    X% of <denominator>') — a ratio against any other base never qualifies."""
    p_after = re.compile(label_re + r"[^%]{0,80}?" + _NUM +
                         r"\s*%\s*of " + denom_re, re.I)
    p_label = re.compile(label_re + r"\s+to\s+" + denom_re +
                         r"[^%]{0,40}?" + _VERB + _NUM + r"\s*%", re.I)
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


def _dollar_metric(text: str, pats, band) -> float | None:
    def _gen():
        for pat in pats:
            for m in pat.finditer(text):
                yield m.start(), m.group(1), m.end()
    return _clean(text, _gen(), band, _AGREE_USD)


def extract_release_metrics(html: str) -> dict:
    """{nim, efficiency, roa, roe, rotce, nco_ratio, npa_assets, acl_loans,
    tbv_ps, div_ps} from an earnings release — each the agreed prose value or
    None (never guessed). Percent keys are percents (3.42 = 3.42%); per-share
    keys are dollars."""
    text = _flat_text(html)
    out = {}
    for key, (label, band) in _PCT_SPECS.items():
        out[key] = _pct_metric(text, label, band)
    for key, (label, denom, band) in _PINNED_SPECS.items():
        out[key] = _pinned_metric(text, label, denom, band)
    out["tbv_ps"] = _dollar_metric(text, _TBV_PATS, _TBV_BAND)
    out["div_ps"] = _dollar_metric(text, _DIV_PATS, _DIV_BAND)
    return out


# ── Fetch/cache layer ──────────────────────────────────────────────────────

def _current_accession(cik) -> str | None:
    """The accession of the latest earnings 8-K per the (cheap) submissions
    index; None when unavailable. One request."""
    from data.ir_provider import _latest_earnings_8k
    from data.sec_filing_scraper import _get
    try:
        cik10 = str(int(cik)).zfill(10)
        subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    except Exception:
        return None
    hit = _latest_earnings_8k(subs)
    return (hit or {}).get("accession")


def release_metrics(cik) -> dict | None:
    """Extracted metrics for the CIK's latest earnings release:
    {metrics: {...}, capital: {...}, url, filed_date, accession} or None.

    Cached per CIK. An extraction is immutable per accession, so a fresh-
    within-1h cache serves directly; past 1h only the submissions index is
    re-checked and the stored extraction is re-stamped unless a NEW accession
    appeared (then the new release is fetched and extracted). Failures never
    overwrite a good cached extraction."""
    if not cik:
        return None
    from datetime import datetime
    from data import cache as _cache
    from data.freshness import is_fresh

    key = f"release_metrics:v1:{int(cik)}"
    try:
        cached = _cache.get(key)
    except Exception:
        cached = None
    if cached is not None and is_fresh(cached, 3600):
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
    if prev and prev.get("accession") == acc:
        return _stamp(prev)                     # same release — nothing new

    from data.ir_provider import (extract_capital_ratios,
                                  latest_earnings_release)
    try:
        rel = latest_earnings_release(cik)
    except Exception:
        rel = None
    if not rel:
        return _stamp(prev) if prev else None
    val = {
        "metrics": extract_release_metrics(rel["html"]),
        "capital": extract_capital_ratios(rel["html"]),
        "url": rel.get("url"),
        "filed_date": rel.get("filed_date"),
        "accession": rel.get("accession"),
    }
    return _stamp(val)
