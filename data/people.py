"""People Summary data layer (SNL plan §12).

Two sources, honestly separated:

1. get_proxy_people(cik) — directors & executive officers extracted from the
   latest DEF 14A via the Claude summarizer pipeline (owner-approved
   2026-06-12: "extracted via summarizer pipeline, labeled + source-linked").
   Extraction is guarded, not trusted: every returned person's surname must
   appear verbatim in the proxy text (hallucination guard), ages/years are
   range-checked, and anything not explicitly in the filing comes back None.
   Results persist in the cloud-storage JSON store keyed by proxy ACCESSION —
   a proxy never changes after filing, so one Claude call per bank per proxy
   season, ever.

2. get_insider_roster(cik) — Section 16 insiders aggregated from the existing
   Form 4 cache (name, role, latest filing). Coverage-honest: only insiders
   with Form 4 activity in the trailing window appear.
"""
from __future__ import annotations

import json
import re

PEOPLE_CACHE_PREFIX = "people_cache"

_EXTRACT_PROMPT = """\
From the proxy-statement text below, extract every DIRECTOR and EXECUTIVE \
OFFICER of {ticker} into a JSON array. For each person:

{{"name": str, "age": int|null, "position": str|null,
  "role": "director"|"officer"|"both", "director_since": int|null,
  "independent": true|false|null, "committees": [str]|null,
  "bio": str|null}}

STRICT RULES:
- Include ONLY facts stated explicitly in the text. Use null for anything \
absent — never infer or estimate an age, year, or committee.
- "independent" only if the text states independence (or non-independence) \
for that person; otherwise null.
- "bio" is at most 20 words, from the person's biography in the text.
- Names exactly as printed (no honorifics like Mr./Ms./Dr.).
- Respond with the JSON array ONLY — no markdown fences, no commentary.

Proxy text:
{text}"""


def _slice_people_sections(text: str, max_chars: int = 80_000) -> str:
    """The people content sits in the board/nominee and executive-officer
    sections — locate them and bound what goes to the model. Falls back to
    the document head when no anchor matches (small banks' proxies are short
    enough that the head IS the board section)."""
    if len(text) <= max_chars:
        return text
    anchors = [r"election\s+of\s+directors", r"nominees?\s+for\s+director",
               r"board\s+of\s+directors", r"executive\s+officers"]
    spans = []
    low = text.lower()
    for pat in anchors:
        m = re.search(pat, low)
        if m:
            spans.append(max(0, m.start() - 2_000))
    if not spans:
        return text[:max_chars]
    start = min(spans)
    return text[start:start + max_chars]


def _parse_people_json(raw: str) -> list[dict]:
    """Model output → list of dicts; tolerant of stray fences, strict on
    shape. [] when unparseable (callers treat as extraction failure)."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        data = json.loads(s[i:j + 1])
    except (ValueError, TypeError):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


def _guard_people(people: list[dict], source_text: str) -> list[dict]:
    """Hallucination + range guards. A person survives only if their surname
    appears verbatim in the proxy text; out-of-range ages/years become None
    (n/a over a guess), and committees normalize to a list of strings."""
    low = source_text.lower()
    out = []
    for p in people:
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        surname = name.split()[-1].strip(".,").lower()
        if len(surname) < 3 or surname not in low:
            continue
        age = p.get("age")
        age = age if isinstance(age, int) and 21 <= age <= 100 else None
        since = p.get("director_since")
        since = since if isinstance(since, int) and 1900 <= since <= 2030 else None
        committees = p.get("committees")
        if isinstance(committees, list):
            committees = [str(c) for c in committees if c] or None
        else:
            committees = None
        role = p.get("role")
        role = role if role in ("director", "officer", "both") else None
        independent = p.get("independent")
        independent = independent if isinstance(independent, bool) else None
        out.append({
            "name": name, "age": age,
            "position": (str(p["position"]).strip() or None) if p.get("position") else None,
            "role": role, "director_since": since,
            "independent": independent, "committees": committees,
            "bio": (str(p["bio"]).strip() or None) if p.get("bio") else None,
        })
    return out


def _latest_proxy(cik: int) -> dict | None:
    from data.sec_client import get_filing_info
    info = get_filing_info(cik, max_filings=200) or {}
    for f in info.get("recent_filings", []):
        if f.get("form") == "DEF 14A":
            return f
    return None


def _extract_via_claude(text: str, ticker: str) -> list[dict] | None:
    """One structured-extraction call. None = API unavailable/failed
    (callers must NOT cache that — retry next view)."""
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
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
                ticker=ticker, text=text)}],
        )
        return _parse_people_json(msg.content[0].text)
    except Exception as e:
        print(f"[people] extraction call failed: {type(e).__name__}: {e}")
        return None


def get_proxy_people(cik: int, ticker: str) -> dict | None:
    """Directors & officers from the latest DEF 14A:
    {"people": [...guarded rows...], "filed", "accession", "source_url"}.
    None when there is no proxy, the text fetch fails, or extraction is
    unavailable (never a partial guess). Accession-keyed permanent cache."""
    if not cik:
        return None
    proxy = _latest_proxy(int(cik))
    if not proxy or not proxy.get("accession"):
        return None
    accession = proxy["accession"]

    from data.cloud_storage import load_json, save_json
    fname = f"{int(cik)}_{accession.replace('-', '')}.json"
    cached = load_json(PEOPLE_CACHE_PREFIX, fname)
    if cached and cached.get("people"):
        return cached

    from data.filing_summarizer import fetch_filing_text
    text = fetch_filing_text(proxy.get("url"), max_chars=400_000)
    if not text or len(text) < 2_000:
        print(f"[people] proxy text unavailable for CIK {cik} {accession}")
        return None
    sliced = _slice_people_sections(text)

    raw = _extract_via_claude(sliced, ticker)
    if raw is None:
        return None  # API failure — do not cache, retry next view
    people = _guard_people(raw, text)
    if not people:
        print(f"[people] extraction yielded no guarded rows for CIK {cik}")
        return None

    result = {
        "people": people,
        "filed": proxy.get("date"),
        "accession": accession,
        "source_url": proxy.get("url") or proxy.get("index_url"),
    }
    try:
        save_json(PEOPLE_CACHE_PREFIX, fname, result)
    except Exception:
        pass
    return result


def get_insider_roster(cik: int) -> list[dict]:
    """Distinct Section 16 insiders from the Form 4 cache (trailing window
    the cache holds): [{name, role, latest_date}] sorted officers-first,
    then by latest activity."""
    if not cik:
        return []
    from data.form4_client import fetch_insider_trades
    by_name: dict[str, dict] = {}
    for t in fetch_insider_trades(int(cik)):
        name = t.get("insider")
        if not name:
            continue
        cur = by_name.get(name)
        date = t.get("date") or ""
        if cur is None or date > cur["latest_date"]:
            by_name[name] = {"name": name, "role": t.get("role") or "Insider",
                             "latest_date": date}
    # Officers (any role beyond a bare Director/Insider) first; newest
    # activity first within each group (two-pass stable sort).
    rows = sorted(by_name.values(), key=lambda r: r["latest_date"], reverse=True)
    rows.sort(key=lambda r: 1 if r["role"] in ("Director", "Insider") else 0)
    return rows
