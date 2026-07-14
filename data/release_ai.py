"""
Guarded AI extraction for earnings-release metrics (coverage layer).

The deterministic extractors (data/release_metrics.py) are prose/table-strict
by design and top out well short of what a release actually states — megabank
supplement-style tables especially. This layer sends the release text to
Claude ONCE per accession and fills ONLY the cells the deterministic pass
left None, under the house guarded-AI pattern (data/governance.py), hardened
for NUMBERS — every value must survive ALL of:

  1. VERBATIM EVIDENCE: the model returns a short quote per value; the quote
     must appear verbatim (whitespace/quote-normalized) in the release text.
  2. NUMBER-IN-QUOTE: the quote must contain the claimed number as printed
     (comma/paren/%-tolerant) — the model cannot report a number it didn't see.
  3. BANDS: per-metric plausibility bands (a 34% NIM never renders).
  4. VARIANT/SEGMENT REJECTION: quotes carrying adjusted/core/segment language
     are dropped (non-GAAP-by-definition keys tolerate a bare non-GAAP tag).
  5. PERIOD CUE: a prior/year-ago value's quote must itself carry a period cue
     (quarter/year token or comparison phrase) — the model cannot silently
     re-label the current quarter as history.

Only the unit-safe metric set is extracted: percent ratios and per-share
dollars. Scale-ambiguous big-dollar lines (revenue, NII, provisions) stay
deterministic-only — a millions/billions misread is exactly the
plausible-wrong number this platform must never ship.

Results are cached permanently per accession (GCS, like governance); an API
failure returns None UNCACHED so the next build retries. No key configured →
None (prod-only; local dev has no ANTHROPIC key).
"""

from __future__ import annotations

import json
import re

RELEASE_AI_CACHE_PREFIX = "release_ai_cache"
_MODEL = "claude-sonnet-4-6"          # matches the governance extractor

# key → (band_lo, band_hi, unit). '%' values are percents (4.56 = 4.56%);
# '$' are per-share dollars. Bands mirror/extend data/release_metrics.
METRIC_SPECS = {
    "nim":              (0.5, 8.0, "%"),
    "efficiency":       (20.0, 110.0, "%"),
    "roa":              (0.05, 4.0, "%"),
    "roe":              (0.5, 40.0, "%"),
    "rotce":            (0.5, 60.0, "%"),
    "cet1_ratio":       (4.0, 30.0, "%"),
    "t1_ratio":         (5.0, 32.0, "%"),
    "total_ratio":      (7.0, 35.0, "%"),
    "lev_ratio":        (3.0, 20.0, "%"),
    "tce_ratio":        (2.0, 25.0, "%"),
    "nco_ratio":        (-1.0, 5.0, "%"),
    "npa_assets":       (0.0, 10.0, "%"),
    "acl_loans":        (0.1, 6.0, "%"),
    "cost_of_deposits": (0.0, 7.0, "%"),
    "loan_yield":       (1.0, 12.0, "%"),
    "eps_diluted":      (-10.0, 60.0, "$"),
    "tbv_ps":           (1.0, 600.0, "$"),
    "bv_ps":            (1.0, 900.0, "$"),
    "div_ps":           (0.005, 10.0, "$"),
}

_PERIODS = ("cur", "prior", "yoy")

# Non-GAAP-by-definition keys: a bare "non-GAAP" footnote tag in the quote is
# the conventional labeling, not a variant. "Adjusted/core/…" still rejects.
_NONGAAP_OK = {"tbv_ps", "tce_ratio", "rotce"}
_VARIANT_RE = re.compile(
    r"\b(?:adjusted|core|operating|normalized|underlying|pro forma)\b", re.I)
_NONGAAP_RE = re.compile(r"non-?gaap", re.I)
_SEGMENT_RE = re.compile(
    r"\b(?:card(?: services)?|consumer bank|community bank|wholesale|wealth|"
    r"asset management|investment bank|mortgage bank|segment)\b", re.I)
# A prior/year-ago quote must carry its own period cue.
_PERIOD_CUE_RE = re.compile(
    r"(?:[1-4]Q\s?\d{2,4}|Q[1-4]\s?'?\d{2,4}|\b(?:19|20)\d{2}\b|"
    r"first|second|third|fourth|prior|previous|preceding|linked|year[- ]ago|"
    r"a year earlier|last year|compared|versus|vs\.?|from)", re.I)

_PROMPT = """You are extracting metrics from a bank's quarterly earnings \
release for {bank} (ticker {ticker}). The current reporting quarter ends \
{qend}; the prior (linked) quarter ends {prior_qend}; the year-ago quarter \
ends {yoy_qend}.

Extract ONLY these metric keys (skip any the release does not state):
{keys}

Definitions: values are FIRMWIDE/consolidated, as-reported for the stated \
period — never a business segment's figure and never an "adjusted"/"core" \
variant (tangible book value, TCE ratio and ROTCE are conventionally \
non-GAAP; those are fine). Percent metrics are percents (net interest margin \
3.42% -> 3.42). Per-share metrics are dollars per share.

For EVERY value return the period it belongs to — "cur" (quarter ending \
{qend}), "prior" ({prior_qend}) or "yoy" ({yoy_qend}) — and a SHORT verbatim \
quote (3-25 words, keep it minimal) copied EXACTLY from the document that \
contains the number and enough context to identify the metric. Values whose \
quote you cannot copy verbatim must be omitted.

Numbers printed in comparative table rows are fine — quote the row text. For \
a "prior" or "yoy" value taken from a TABLE COLUMN, additionally return \
"period_quote": the column's period header copied EXACTLY as printed (e.g. \
"1Q26" or "March 31, 2026" or "Second Quarter 2025") so the column can be \
verified. Narrated history ("compared with 3.38% in the prior quarter") \
needs no period_quote — the sentence itself carries the period.

Return ONLY a JSON array, no prose:
[{{"key": "nim", "period": "cur", "value": 3.42, "quote": "..."}},
 {{"key": "nim", "period": "prior", "value": 3.38, "quote": "...",
   "period_quote": "1Q26"}}, ...]

DOCUMENT:
{text}"""


def _norm(s: str) -> str:
    """Whitespace/quote-normalized form for verbatim matching (as governance)."""
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def _number_renderings(value: float) -> list[str]:
    """The plausible printed forms of `value` — a quote must contain one.
    Covers 2/1/0-decimal prints, thousands commas, and -X / (X) negatives.
    A rendering is kept only when it round-trips to the value (a 0-decimal
    print of 4.56 would read "5" — never accepted as evidence for 4.56)."""
    a = abs(value)
    forms = []
    for fmt in (f"{a:,.2f}", f"{a:,.1f}", f"{a:,.0f}", f"{a:g}"):
        if abs(float(fmt.replace(",", "")) - a) < 0.006 and fmt not in forms:
            forms.append(fmt)
    if value < 0:
        return [f"-{f}" for f in forms] + [f"({f})" for f in forms]
    return forms


def _history_period_ok(it: dict, period: str, quote: str, norm_source: str,
                       prior_qend, yoy_qend) -> bool:
    """A prior/yoy claim must prove its period one of two ways:
    - the quote itself carries a period cue (narrated history), OR
    - `period_quote` (a table column's period header) exists verbatim in the
      document AND parses to EXACTLY the expected quarter-end for that bucket
      (table history — the cue lives in the header, not the row; the Jul-14
      megabank panels lost ALL history to the cue-only rule)."""
    if _PERIOD_CUE_RE.search(quote):
        return True
    pq = it.get("period_quote")
    if not isinstance(pq, str):
        return False
    pq = pq.strip()
    if not pq or _norm(pq) not in norm_source:
        return False
    from data.release_metrics import _period_qend
    expected = prior_qend if period == "prior" else yoy_qend
    return expected is not None and _period_qend(pq) == expected


def guard_items(items, source_text: str, prior_qend=None, yoy_qend=None) -> dict:
    """{period: {key: value}} for items surviving EVERY guard (see module
    docstring). Anything else is dropped — never partially trusted."""
    norm_source = _norm(source_text)
    out = {p: {} for p in _PERIODS}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        key, period = it.get("key"), it.get("period")
        spec = METRIC_SPECS.get(key)
        if spec is None or period not in _PERIODS:
            continue
        try:
            value = float(it.get("value"))
        except (TypeError, ValueError):
            continue
        lo, hi, _unit = spec
        if not (lo <= value <= hi):
            continue
        q = it.get("quote")
        if not isinstance(q, str):
            continue
        q = q.strip()
        words = q.split()
        if not (3 <= len(words) <= 60) or _norm(q) not in norm_source:
            continue
        if not any(r in q for r in _number_renderings(value)):
            continue                      # number not printed in the evidence
        if _VARIANT_RE.search(q):
            continue                      # adjusted/core/… variant
        if key not in _NONGAAP_OK and _NONGAAP_RE.search(q):
            continue
        if _SEGMENT_RE.search(q):
            continue                      # a segment's figure, not firmwide
        if period != "cur" and not _history_period_ok(
                it, period, q, norm_source, prior_qend, yoy_qend):
            continue                      # history claim without period proof
        if key in out[period]:            # duplicate claims must agree
            if abs(out[period][key] - value) > 0.011:
                out[period][key] = None   # conflicting — poison then drop
            continue
        out[period][key] = value
    for p in _PERIODS:
        out[p] = {k: v for k, v in out[p].items() if v is not None}
    return out


def _parse_items(raw: str) -> list:
    """Model output → item list. A max_tokens-truncated array (the Jul-14
    megabank responses) is SALVAGED to its complete items rather than parsed
    to [] — losing the tail must not lose the whole extraction."""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    i = s.find("[")
    if i < 0:
        return []
    j = s.rfind("]")
    if j > i:
        try:
            data = json.loads(s[i:j + 1])
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            pass
    k = s.rfind("}")                       # salvage complete items
    if k <= i:
        return []
    try:
        data = json.loads(s[i:k + 1] + "]")
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _api_key() -> str | None:
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


def has_api_key() -> bool:
    """Whether an ANTHROPIC key is configured — callers distinguish 'no key
    ever' (final; nothing to retry) from an extraction failure (retryable)."""
    return bool(_api_key())


def _call_model(text: str, ticker: str, bank: str, qend, prior_qend, yoy_qend):
    """One extraction call. None = API unavailable/failed (never cached)."""
    api_key = _api_key()
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=8000,     # 21 keys × 3 periods × quotes overran 4000
            messages=[{"role": "user", "content": _PROMPT.format(
                bank=bank or ticker, ticker=ticker or "?", qend=qend or "?",
                prior_qend=prior_qend or "?", yoy_qend=yoy_qend or "?",
                keys=", ".join(METRIC_SPECS), text=text[:200_000])}],
        )
        return _parse_items(msg.content[0].text)
    except Exception as e:
        print(f"[release_ai] extraction call failed: {type(e).__name__}: {e}")
        return None


def release_ai_metrics(cik, accession: str, text: str, ticker: str = "",
                       bank: str = "", qend=None, prior_qend=None,
                       yoy_qend=None) -> dict | None:
    """Guarded AI metric fill for one release: {"cur": {...}, "prior": {...},
    "yoy": {...}} (guarded values only), permanently cached per accession.
    None on API failure/empty verification — NOT cached, so the next board
    build retries (governance pattern)."""
    if not cik or not accession or not text:
        return None
    from data.cloud_storage import load_json, save_json
    fname = f"{int(cik)}_{accession.replace('-', '')}_v1.json"
    cached = load_json(RELEASE_AI_CACHE_PREFIX, fname)
    if cached and isinstance(cached.get("periods"), dict):
        return cached["periods"]

    items = _call_model(text, ticker, bank, qend, prior_qend, yoy_qend)
    if items is None:
        return None                       # API failure — retry next build
    periods = guard_items(items, text, prior_qend=prior_qend, yoy_qend=yoy_qend)
    if not any(periods[p] for p in _PERIODS):
        # Nothing verified. For a real release that's an extraction failure —
        # don't cache an empty result against the accession forever.
        print(f"[release_ai] nothing verified for CIK {cik} {accession}")
        return None
    try:
        save_json(RELEASE_AI_CACHE_PREFIX, fname, {"periods": periods})
    except Exception as e:
        print(f"[release_ai] cache write failed: {type(e).__name__}: {e}")
    return periods
