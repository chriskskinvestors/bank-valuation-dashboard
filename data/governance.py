"""Corporate Governance data layer (SNL plan §12).

Charter/bylaw governance provisions extracted from the latest DEF 14A via
the Claude summarizer pipeline (owner-approved 2026-06-12: "extracted from
proxies/charters via the summarizer, labeled + source-linked").

The guard here is EVIDENCE QUOTES: the model must return, for every
non-null provision, a short verbatim quote from the proxy supporting it —
and the quote is verified to actually appear in the source text
(whitespace-normalized). A provision whose quote doesn't verify is nulled,
never trusted. Silence in the proxy renders n/a, not an inference.

Accession-keyed permanent cache (a filed proxy never changes); an API
failure returns None UNCACHED so the next view retries.
"""
from __future__ import annotations

import json
import re

GOVERNANCE_CACHE_PREFIX = "governance_cache"

# provision key → display label (order = display order)
PROVISIONS = [
    ("classified_board", "Classified (staggered) board"),
    ("majority_voting", "Majority voting for directors"),
    ("cumulative_voting", "Cumulative voting"),
    ("proxy_access", "Proxy access"),
    ("supermajority_amendment", "Supermajority charter/bylaw amendment"),
    ("poison_pill", "Shareholder rights plan (poison pill)"),
    ("dual_class", "Dual-class shares"),
    ("exclusive_forum", "Exclusive forum provision"),
    ("special_meeting_right", "Shareholder right to call special meetings"),
    ("written_consent", "Shareholder action by written consent"),
]

_EXTRACT_PROMPT = """\
From the proxy-statement text below for {ticker}, determine each governance \
provision. Respond with a single JSON object, keys exactly:

{keys}

Each value is {{"value": true|false|null, "quote": str|null}}:
- value true/false ONLY when the text states it explicitly; null when the \
text is silent or ambiguous.
- quote: a VERBATIM excerpt from the text (5-25 words) that supports the \
value — copied character-for-character, no paraphrase. null quote requires \
null value.
- majority_voting: true = majority standard (with or without a resignation \
policy), false = plurality standard.

Respond with the JSON object ONLY — no markdown fences, no commentary.

Proxy text:
{text}"""


def _norm(s: str) -> str:
    """Whitespace/quote-normalized form for verbatim-quote matching."""
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def _slice_governance_sections(text: str, max_chars: int = 80_000) -> str:
    """Bound what goes to the model around the governance discussion."""
    if len(text) <= max_chars:
        return text
    anchors = [r"corporate\s+governance", r"governance\s+highlights",
               r"board\s+of\s+directors", r"voting\s+standards?",
               r"majority\s+voting"]
    low = text.lower()
    spans = [max(0, m.start() - 2_000)
             for pat in anchors if (m := re.search(pat, low))]
    if not spans:
        return text[:max_chars]
    start = min(spans)
    return text[start:start + max_chars]


def _parse_governance_json(raw: str) -> dict:
    """Model output → dict of {key: {value, quote}}; {} when unparseable."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        data = json.loads(s[i:j + 1])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _guard_provisions(raw: dict, source_text: str) -> dict:
    """Evidence-quote guard: keep a provision only when its value is a bool
    AND its quote verifies verbatim (normalized) against the source text.
    Everything else → {"value": None, "quote": None}."""
    norm_source = _norm(source_text)
    out = {}
    for key, _label in PROVISIONS:
        entry = raw.get(key)
        value = quote = None
        if isinstance(entry, dict) and isinstance(entry.get("value"), bool):
            q = entry.get("quote")
            if isinstance(q, str):
                q = q.strip()
                words = q.split()
                if 3 <= len(words) <= 40 and _norm(q) in norm_source:
                    value, quote = entry["value"], q
        out[key] = {"value": value, "quote": quote}
    return out


def _extract_via_claude(text: str, ticker: str) -> dict | None:
    """One structured-extraction call. None = API unavailable/failed."""
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get("ANTHROPIC_API_KEY")
        except Exception:
            api_key = None
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        keys = ", ".join(k for k, _ in PROVISIONS)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
                ticker=ticker, keys=keys, text=text)}],
        )
        return _parse_governance_json(msg.content[0].text)
    except Exception as e:
        print(f"[governance] extraction call failed: {type(e).__name__}: {e}")
        return None


def get_governance_provisions(cik: int, ticker: str) -> dict | None:
    """Guarded governance provisions from the latest DEF 14A:
    {"provisions": {key: {value, quote}}, "filed", "accession", "source_url"}.
    None when there is no proxy / text / extraction (never a partial guess)."""
    if not cik:
        return None
    from data.people import _latest_proxy
    proxy = _latest_proxy(int(cik))
    if not proxy or not proxy.get("accession"):
        return None
    accession = proxy["accession"]

    from data.cloud_storage import load_json, save_json
    fname = f"{int(cik)}_{accession.replace('-', '')}.json"
    cached = load_json(GOVERNANCE_CACHE_PREFIX, fname)
    if cached and cached.get("provisions"):
        return cached

    from data.filing_summarizer import fetch_filing_text
    text = fetch_filing_text(proxy.get("url"), max_chars=400_000)
    if not text or len(text) < 2_000:
        print(f"[governance] proxy text unavailable for CIK {cik} {accession}")
        return None
    sliced = _slice_governance_sections(text)

    raw = _extract_via_claude(sliced, ticker)
    if raw is None:
        return None  # API failure — do not cache, retry next view
    provisions = _guard_provisions(raw, text)
    if not any(v["value"] is not None for v in provisions.values()):
        # Nothing verified — treat as extraction failure rather than caching
        # an all-n/a page against this accession forever.
        print(f"[governance] no provision verified for CIK {cik}")
        return None

    result = {
        "provisions": provisions,
        "filed": proxy.get("date"),
        "accession": accession,
        "source_url": proxy.get("url") or proxy.get("index_url"),
    }
    try:
        save_json(GOVERNANCE_CACHE_PREFIX, fname, result)
    except Exception:
        pass
    return result
