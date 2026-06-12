"""
Curation for Home-page topic news (Overnight & Breaking).

User decision 2026-06-12: reputable sources ONLY, plus market-relevance
scoring — Google News topic feeds pass through local-TV/lifestyle chaff
("Abercrombie opens a 'pinnacle' store in SoHo" under MACRO) that has no
place on a trading desk. Fewer items, all signal.

Rules:
  1. Publisher must match the whitelist (substring, case-insensitive).
  2. Headline must hit >= 1 relevance keyword for its category.
  3. Rank by keyword hits, then recency; cap at the caller's limit.
"""
from __future__ import annotations

import re

# Tight by design — adding an outlet is a one-line PR, letting noise
# through is a credibility problem on the first screen of the product.
REPUTABLE_SOURCES = (
    "reuters", "bloomberg", "wall street journal", "wsj",
    "financial times", "associated press", "ap news", "cnbc",
    "axios", "marketwatch", "barron", "the economist", "economist",
    "new york times", "washington post", "politico", "yahoo finance",
    "semafor", "the hill", "fortune",
)

# Headline must hit at least one of its category's keywords.
_RELEVANCE = {
    "macro": (
        "inflation", "cpi", "pce", "ppi", "fed", "fomc", "rate", "rates",
        "treasury", "yield", "gdp", "recession", "payroll", "jobs report",
        "unemployment", "tariff", "central bank", "ecb", "boj",
        "powell", "monetary", "deficit", "stimulus", "economy", "economic",
    ),
    "geopolitical": (
        "sanction", "war", "ceasefire", "china", "russia", "ukraine",
        "iran", "israel", "opec", "nato", "tariff", "trade deal",
        "export control", "missile", "election", "embargo", "summit",
        "treaty", "conflict", "taiwan",
    ),
    "domestic": (
        "congress", "senate", "house", "white house", "supreme court",
        "regulation", "regulator", "fdic", "occ", "federal reserve",
        "treasury", "budget", "shutdown", "tax", "legislation", "doj",
        "sec ", "cfpb", "election", "tariff", "executive order",
    ),
    "markets": (
        "stock", "stocks", "equit", "bond", "credit", "yield", "earnings",
        "ipo", "m&a", "merger", "acquisition", "bank", "rally", "selloff",
        "s&p", "nasdaq", "dow", "futures", "volatility", "vix", "dollar",
        "oil", "spread", "default", "downgrade", "buyback",
    ),
}


# Hard stop-list: a relevance keyword inside a sports/entertainment story
# still isn't desk news ("Paxton warns Big 12 of potential legal action
# over any Texas Tech Sorsby sanctions" passed the 'sanction' keyword).
_STOP_TOPICS = (
    "nfl", "nba", "mlb", "nhl", "ncaa", "big 12", "big ten", "sec football",
    "quarterback", "touchdown", "coach", "playoff", "super bowl",
    "world cup", "olympic", "concert", "box office", "celebrity",
    "bachelor", "kardashian", "grammy", "oscars", "album",
)
# Word boundaries are load-bearing: plain substring matching rejects
# every inflation headline via 'i-NFL-ation'.
_STOP_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _STOP_TOPICS) + r")\b")


def _stopped(headline: str) -> bool:
    return bool(_STOP_RE.search((headline or "").lower()))


def _source_ok(source_name: str) -> bool:
    s = (source_name or "").strip().lower()
    return bool(s) and any(w in s for w in REPUTABLE_SOURCES)


def _relevance_hits(headline: str, category: str) -> int:
    h = (headline or "").lower()
    kws = _RELEVANCE.get((category or "").strip().lower(), ())
    return sum(1 for k in kws if k in h)


def curate_topic_news(items: list[dict], category: str,
                      limit: int = 5) -> list[dict]:
    """Whitelisted-publisher, relevance-ranked subset of topic items.
    Items keep their original fields; ordering is (keyword hits desc,
    original order — which is already newest-first from the store)."""
    scored = []
    for i, it in enumerate(items):
        if not _source_ok(it.get("source_name") or ""):
            continue
        if _stopped(it.get("headline") or ""):
            continue
        hits = _relevance_hits(it.get("headline") or "", category)
        if hits < 1:
            continue
        scored.append((hits, -i, it))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    # Dedup near-identical headlines (same first 8 words) across outlets
    out, seen = [], set()
    for _, _, it in scored:
        key = " ".join(re.sub(r"[^\w\s]", "", (it.get("headline") or "").lower()).split()[:8])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out
