"""
Shared utilities for press-wire RSS adapters (Business Wire, PR Newswire,
GlobeNewswire).

Wire-service feeds aren't ticker-indexed — they're firehoses of every
press release across every industry. We have to:
  1. Pull the feed (RSS / Atom XML)
  2. For each item, search the title + summary text for known bank names
  3. Map matched names to tickers via our BANK_MAP
  4. Return Event objects only for items that match a bank in our universe

Name matching uses a normalized-substring index with a "longest match wins"
rule so 'JPMorgan Chase' wins over 'JPMorgan' and we don't double-count.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import requests

from data.bank_mapping import BANK_MAP


# ──────────────────────────────────────────────────────────────────────────
# Bank name index
# ──────────────────────────────────────────────────────────────────────────

# Generic words that match too broadly when used alone. Phrases that ONLY
# contain these tokens (after stripping punctuation) shouldn't match.
_GENERIC_WORDS = {
    "BANK", "BANC", "BANCORP", "BANCSHARES", "FINANCIAL", "BANKSHARES",
    "TRUST", "SAVINGS", "FEDERAL", "NATIONAL", "FIRST", "THE", "CORP",
    "CORPORATION", "INC", "COMPANY", "CO", "HOLDINGS", "HOLDING", "GROUP",
    "OF", "AND", "&",
}

# Token-level suffixes to strip. We tokenize the name and drop trailing
# tokens that match these. Using token-level matching avoids stripping
# "CORP" out of the middle of "BANCORP" (which would leave "BAN").
_SUFFIX_TOKENS = {
    "INC", "INC.", "CORP", "CORP.", "CORPORATION", "CO", "CO.",
    "LTD", "LTD.", "LLC", "LP", "LLP",
    "N.A.", "NA", "HOLDINGS", "HOLDING", "GROUP", "COMPANY",
    "&", "AND",  # trailing connectors left over after "& Co." stripped
}

# State-suffix tokens like "/MD", "/PA" attached by SEC after company name
_STATE_SUFFIX = re.compile(r"/[A-Z]{2,3}/?$")


def _normalize_name(name: str) -> str:
    """
    Normalize for matching:
      • Upper-case
      • Strip /STATE suffix added by SEC ("/MD", "/PA/", etc.)
      • Drop trailing tokens like 'INC', 'CORP', '& CO', 'HOLDINGS'
      • Strip non-word chars (except & inside the name)
      • Collapse whitespace

    Token-level suffix stripping prevents "BANCORP" from losing its CORP.
    """
    if not name:
        return ""
    n = name.upper().strip()

    # Step 1: drop /STATE marker
    n = _STATE_SUFFIX.sub("", n).strip()

    # Step 2: punctuation → spaces, but keep & for "M & T Bank" style
    n = re.sub(r"[^\w\s&]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()

    # Step 3: drop trailing suffix tokens (repeat for things like "Inc Corp")
    tokens = n.split()
    while tokens and tokens[-1].rstrip(".") in {t.rstrip(".") for t in _SUFFIX_TOKENS}:
        tokens.pop()
    return " ".join(tokens)


def _is_too_generic(name: str) -> bool:
    """A name that's just 'BANK' or 'FIRST BANK' is too generic to match safely."""
    tokens = set(name.split())
    non_generic = tokens - _GENERIC_WORDS
    return len(non_generic) == 0


# Brand-name aliases — press releases use these instead of legal names.
# Map: ticker -> list of additional searchable names (will be normalized).
# Only add aliases that are >= 3 chars and unambiguous in financial context.
_BRAND_ALIASES: dict[str, list[str]] = {
    "C":    ["Citi"],                       # not Citigroup
    "JPM":  ["JPMorgan", "Chase Bank"],     # "Chase" alone is too generic
    "BAC":  ["BofA"],                       # Bank of America abbreviation
    "WFC":  ["Wells Fargo Bank"],           # disambiguates Wells Fargo Bank vs Co
    "USB":  ["US Bank", "U.S. Bank"],       # "USB" the ticker is rarely in PR text
    "PNC":  ["PNC Bank"],
    "TFC":  ["Truist"],
    "FHN":  ["First Horizon"],
    "EWBC": ["East West Bank"],
    "COF":  ["Capital One"],
    "GS":   ["Goldman Sachs"],
    "MS":   ["Morgan Stanley"],
    "HBAN": ["Huntington Bank", "Huntington National"],
    "KEY":  ["KeyBank"],
    "FNB":  ["First National Bank"],
    "ZION": ["Zions Bank"],
}


def build_name_index() -> list[tuple[str, str]]:
    """
    Build a sorted list of (normalized_name, ticker) tuples.

    Sources, in precedence order:
      1. BANK_MAP legal names + _BRAND_ALIASES (curated, highest quality).
      2. The FULL tracked universe (get_name per ticker) — so wire/Google
         matching covers every bank the dashboard knows about, not just the
         curated subset. Without this, a universe bank like Regions (RF) is
         polled but its name never matches, so its press releases are dropped.

    Sorted longest-first so the most specific name wins ('BANK OF AMERICA'
    beats 'BANK OF'); BANK_MAP entries win ties over universe-derived names.
    """
    pairs: list[tuple[str, str]] = []
    curated_tickers: set[str] = set()
    for ticker, info in BANK_MAP.items():
        curated_tickers.add(ticker)
        names = [info.get("name", "")] + _BRAND_ALIASES.get(ticker, [])
        for name in names:
            normalized = _normalize_name(name)
            if not normalized or _is_too_generic(normalized):
                continue
            pairs.append((normalized, ticker))

    # 2. Universe names not already curated.
    try:
        from data.bank_universe import get_universe_tickers
        from data.bank_mapping import get_name
        for ticker in get_universe_tickers():
            if ticker in curated_tickers:
                continue
            nm = get_name(ticker) or ""
            # Skip placeholders (name == ticker) and anything too short/generic
            # to match safely.
            if not nm or nm.strip().upper() == ticker.upper():
                continue
            normalized = _normalize_name(nm)
            if len(normalized) < 6 or _is_too_generic(normalized):
                continue
            pairs.append((normalized, ticker))
    except Exception as e:
        print(f"[wire] universe name index skipped: {type(e).__name__}: {e}")

    # Deduplicate (same normalized name → first/most-specific ticker wins).
    # Stable sort keeps BANK_MAP entries (added first) ahead of universe ones
    # on equal length.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    pairs.sort(key=lambda x: -len(x[0]))
    for name, ticker in pairs:
        if name not in seen:
            seen.add(name)
            unique.append((name, ticker))
    return unique


# Lazy-initialized at first match call
_NAME_INDEX: list[tuple[str, str]] = []


def match_tickers(text: str) -> list[str]:
    """
    Find which bank tickers are mentioned in a piece of text.

    Returns a deduplicated list of tickers (preserving order of appearance).
    """
    global _NAME_INDEX
    if not _NAME_INDEX:
        _NAME_INDEX = build_name_index()

    haystack = " " + _normalize_name(text) + " "
    if not haystack.strip():
        return []

    found: list[str] = []
    consumed: list[tuple[int, int]] = []  # (start, end) ranges already matched

    for name, ticker in _NAME_INDEX:
        # Use word-boundary check: search for " NAME " in " ...HAYSTACK... "
        needle = " " + name + " "
        idx = haystack.find(needle)
        if idx < 0:
            continue
        end = idx + len(needle)
        # Don't double-count: if this match overlaps a longer earlier match, skip.
        if any(s <= idx < e or s < end <= e for (s, e) in consumed):
            continue
        consumed.append((idx, end))
        if ticker not in found:
            found.append(ticker)

    return found


# ──────────────────────────────────────────────────────────────────────────
# Generic RSS fetcher
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class RSSItem:
    title: str
    summary: str
    link: str
    published: datetime | None
    guid: str  # unique ID from the feed


def _parse_pubdate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        pass
    # ISO 8601 fallback
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_rss(url: str, user_agent: str = "BankValuationDashboard/1.0",
              timeout: int = 15) -> list[RSSItem]:
    """
    Pull and parse an RSS / Atom feed. Returns a list of RSSItem.

    Uses stdlib xml.etree — no external deps. Handles both <rss><channel><item>
    and Atom <feed><entry> forms.
    """
    import xml.etree.ElementTree as ET

    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/xml"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        print(f"[wire] Fetch error {url}: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"[wire] Parse error {url}: {e}")
        return []

    # Strip Atom namespace prefix from tags for easy access
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[RSSItem] = []

    # RSS path
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag != "item" and tag != "entry":
            continue

        def child_text(*names):
            for n in names:
                for sub in it:
                    if sub.tag.split("}")[-1] == n and sub.text:
                        return sub.text.strip()
                # Also try with namespace
                for n_full in (f"{{http://www.w3.org/2005/Atom}}{n}", n):
                    el = it.find(n_full)
                    if el is not None and el.text:
                        return el.text.strip()
            return ""

        title = child_text("title")
        summary = child_text("description", "summary", "content")
        link = child_text("link")
        if not link:
            # Atom <link href="..."/>
            for sub in it:
                if sub.tag.split("}")[-1] == "link":
                    href = sub.attrib.get("href")
                    if href:
                        link = href
                        break
        pub = child_text("pubDate", "published", "updated")
        guid = child_text("guid", "id") or link

        if not (title and (link or guid)):
            continue
        items.append(RSSItem(
            title=title,
            summary=re.sub(r"<[^>]+>", "", summary)[:2000] if summary else "",
            link=link,
            published=_parse_pubdate(pub),
            guid=guid,
        ))
    return items


# ──────────────────────────────────────────────────────────────────────────
# Helper: classify event_type from headline
# ──────────────────────────────────────────────────────────────────────────

def classify_press_release(headline: str) -> str:
    """Coarse-grained event_type tag for wire press releases."""
    h = (headline or "").lower()
    if any(k in h for k in ("earnings", "quarterly results", "fourth quarter", "first quarter",
                              "second quarter", "third quarter", "reports results", "q1 ", "q2 ",
                              "q3 ", "q4 ", "fiscal")):
        return "earnings"
    if any(k in h for k in ("acquir", "merger", "to merge", "to acquire", "combining with",
                              "definitive agreement")):
        return "m_and_a"
    if any(k in h for k in ("dividend", "declares quarterly", "share repurchase", "buyback")):
        return "capital_return"
    if any(k in h for k in ("appoint", "named", "joins", "resignation", "steps down",
                              "chief executive", "ceo", "chairman", "board of directors")):
        return "executive_change"
    if any(k in h for k in ("offering", "prices", "underwritten", "senior notes", "debt")):
        return "capital_raise"
    return "press_release"


# ──────────────────────────────────────────────────────────────────────────
# First-party filter — keep the COMPANY's own releases, drop third-party
# articles/commentary (analyst notes, roundups, opinion). Used to gate news
# aggregators (Google News, Yahoo) so the feed isn't full of junk.
# ──────────────────────────────────────────────────────────────────────────

# Verbs/phrases that mark a first-party company release (third person, e.g.
# "<Bank> Announces …", "<Bank> Reports …", "<Bank> Declares a Dividend").
_PR_VERB_RE = re.compile(
    r"\b(announc\w+|report\w+|declar\w+|complet\w+|names?|appoint\w+|provid\w+|"
    r"sets? |schedul\w+|pric\w+|clos\w+|authoriz\w+|increas\w+|reduc\w+|"
    r"present\w*|participat\w+|to host|hosts?|acquir\w+|launch\w+|introduc\w+|"
    r"post\w+|releas\w+|issu\w+|elect\w+|approv\w+|rais\w+|adopt\w+|expand\w+|"
    r"to report|earnings release|conference call|business update|to merge|"
    r"enters? into|to be acquired|prices? offering|commences?)\b",
    re.IGNORECASE,
)

# Markers of third-party coverage/commentary — if present, it's NOT a company
# release, no matter what.
_COMMENTARY_RE = re.compile(
    r"\b(analyst|price target|upgrad\w+|downgrad\w+|\brating\b|\bbuy\b|\bsell\b|"
    r"\bhold\b|should you|how |why |\d+\s+reasons|\bvs\.?\b|compared to|"
    r"best place to work|motley fool|zacks|seeking alpha|simply ?wall|"
    r"insider monkey|benzinga|the globe and mail|market cap of|stock to|"
    r"is it a|here's (what|why)|what to know|outperform|underperform|"
    r"top \d+|best \d+|moving average|short interest|hedge fund)\b",
    re.IGNORECASE,
)


def is_company_press_release(headline: str) -> bool:
    """True if a headline reads like the company's OWN press release (not a
    third-party article about it). Conservative: commentary markers always
    reject; otherwise require a press-release verb."""
    h = headline or ""
    if _COMMENTARY_RE.search(h):
        return False
    return bool(_PR_VERB_RE.search(h))


# Domains a legitimate press release / filing NEVER lives on — messaging apps,
# social media, link shorteners, forums. Spam/content-farm "news" links to these
# (e.g. a fake earnings article whose link is a WhatsApp group invite). Reject
# any event URL on these hosts, at ingest AND display.
_BLOCKED_URL_DOMAINS = {
    "whatsapp.com", "chat.whatsapp.com", "wa.me",
    "t.me", "telegram.me", "telegram.org", "telegram.dog",
    "facebook.com", "fb.com", "fb.me", "fb.watch", "m.facebook.com",
    "instagram.com", "tiktok.com", "twitter.com", "x.com",
    "youtube.com", "youtu.be", "reddit.com", "discord.gg", "discord.com",
    "linktr.ee", "linktree.com", "medium.com",
    "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "is.gd",
    "cutt.ly", "rebrand.ly", "shorturl.at", "lnkd.in", "t.co", "rb.gy",
}


# Structured-product / retail-note issuances — big banks (esp. JPM, GS, MS, C)
# file hundreds of these. They're technically press releases but pure noise for a
# bank investor. Reject them across every news source.
_NOISE_RE = re.compile(
    r"\b(buffered|auto-?call\w*|autocallable|contingent[\s-]?interest|barrier|"
    r"uncapped|capped\s+\w*\s*notes?|leveraged\s+notes?|digital\s+notes?|"
    r"review\s+notes?|market[\s-]?linked|dual\s+directional|trigger\s+\w+\s+notes?|"
    r"principal\s+at\s+risk|callable\s+\w*\s*notes?|step[\s-]?up\s+notes?|"
    r"range\s+accrual|phoenix\s+notes?|buffer\s+notes?|return\s+notes?|"
    r"index[\s-]?linked\s+notes?|notes?\s+linked\s+to|notes?\s+tied\s+to|"
    r"structured\s+notes?|fixed\s+to\s+floating)\b",
    re.IGNORECASE,
)


def is_routine_noise(headline: str) -> bool:
    """True for structured-note / retail-product issuances — high-volume filler a
    bank analyst doesn't track. Applied to ALL news sources (wires included)."""
    return bool(_NOISE_RE.search(headline or ""))


# Headlines that mention a bank but aren't ABOUT it — third-party SEO/
# aggregator spam (broker forecasts, shares-sold-by articles), funding stories
# about someone else, and non-material branch/staffing trivia. These slip past
# name-matching because the bank's name appears in another company's story.
# (Structured-note issuance is covered by _NOISE_RE above.)
_THIRD_PARTY_RE = re.compile(
    r"\b(issues?\s+(optimistic|pessimistic|bullish|bearish)\s+forecast|"
    r"price\s+target|forecast\s+for|target\s+price|"
    r"shares?\s+(sold|bought|purchased|acquired)\s+by|"
    r"(funding|investment)\s+from|to\s+(buy|sell)\s+\$?\d|"
    r"office\s+leader|branch\s+manager|relationship\s+manager|"
    r"new\s+\w+\s+(branch|location|office))\b",
    re.IGNORECASE,
)
# A parenthetical exchange tag like (NYSE:CHWY) — if it's NOT this bank's
# ticker, the story is about another company.
_PAREN_TICKER_RE = re.compile(
    r"\((?:NYSE|NASDAQ|NYSEAMERICAN|NYSEARCA|AMEX|OTC|CBOE)[:\s]+([A-Z.]{1,6})\)",
    re.IGNORECASE,
)


def is_junk_news(headline: str, ticker: str | None = None) -> bool:
    """ONE junk filter for ingest AND display: third-party mentions, routine
    structured-note issuance, and (when ``ticker`` is given) headlines tagged
    with a different company's exchange ticker. This logic previously lived
    split between here and ui/home.py, where the two regexes drifted."""
    h = headline or ""
    if _THIRD_PARTY_RE.search(h) or is_routine_noise(h):
        return True
    if ticker:
        for other in _PAREN_TICKER_RE.findall(h):
            if other.upper() != ticker.upper():
                return True
    return False


def is_safe_news_url(url: str) -> bool:
    """False for URLs that can't be a real press release / filing — messaging,
    social, shorteners. A real release links to a wire, an IR site, EDGAR, or a
    news outlet; never to a WhatsApp/Telegram/social/shortener host."""
    if not url:
        return True  # no link is fine (the headline still stands)
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower().lstrip(".")
    except Exception:
        return False
    if not host:
        return False
    # Match the host or any parent domain (covers subdomains).
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in _BLOCKED_URL_DOMAINS:
            return False
    return True
