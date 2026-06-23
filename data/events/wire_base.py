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
    "INC", "INC.", "INCORPORATED", "CORP", "CORP.", "CORPORATION",
    "BANCORPORATION", "CO", "CO.",
    "LTD", "LTD.", "LLC", "LP", "LLP",
    "N.A.", "NA", "HOLDINGS", "HOLDING", "GROUP", "COMPANY",
    "&", "AND",  # trailing connectors left over after "& Co." stripped
}
# Note: full-word "INCORPORATED" and "BANCORPORATION" are stripped here so the
# SEC legal name matches the common brand used in headlines ("Comerica
# Incorporated"→"Comerica", "Zions Bancorporation"→"Zions"). "BANCORP" is
# deliberately NOT stripped — it shortens too many names to a single generic
# token ("First Bancorp"→"First") and needs prod name-matching verification
# before it's safe to broaden (owner follow-up).

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
    # "JPMorganChase" (one word) is the post-2024 brand BW/Reuters use; it
    # tokenizes as a single word so "JPMorgan Chase" (two words) never matched
    # it — its press releases were silently dropped (live-feed audit 2026-06-15).
    "JPM":  ["JPMorgan", "Chase Bank", "JPMorganChase"],
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
    # Holdco name ("Provident Financial Services") never appears in its own
    # subsidiary-brand releases ("Provident Bank Appoints ..."). Unambiguous: the
    # only other Provident in the universe (PROV) trades as "Provident Savings
    # Bank", so the exact phrase "Provident Bank" can't collide.
    "PFS":  ["Provident Bank"],
}


def build_name_index() -> list[tuple[str, str]]:
    """
    Build a sorted list of (normalized_name, ticker) tuples for unambiguous
    names, and (as a side effect) populate _AMBIGUOUS_INDEX for names shared by
    more than one bank.

    Sources, in precedence order:
      1. BANK_MAP legal names + _BRAND_ALIASES (curated, highest quality).
      2. The FULL tracked universe (get_name per ticker) — so wire/Google
         matching covers every bank the dashboard knows about, not just the
         curated subset. Without this, a universe bank like Regions (RF) is
         polled but its name never matches, so its press releases are dropped.

    A curated (BANK_MAP/alias) ticker always wins a name outright. A name that
    resolves ONLY to universe tickers and lands on >1 of them (e.g. "FIRST
    BANCORP" → FBP + FNLC, or the too-generic names that used to be dropped
    entirely) is AMBIGUOUS: it goes to _AMBIGUOUS_INDEX for the disambiguation
    pass (ticker / geo in the release body) instead of silently tagging one bank
    or none. Sorted longest-first so the most specific name wins.
    """
    global _AMBIGUOUS_INDEX
    curated: dict[str, str] = {}          # normname -> curated ticker (wins outright)
    universe_cands: dict[str, list[str]] = {}  # normname -> [universe tickers]
    curated_tickers: set[str] = set()

    for ticker, info in BANK_MAP.items():
        curated_tickers.add(ticker)
        for name in [info.get("name", "")] + _BRAND_ALIASES.get(ticker, []):
            normalized = _normalize_name(name)
            if not normalized or _is_too_generic(normalized):
                continue
            curated.setdefault(normalized, ticker)

    # Brand aliases apply to EVERY ticker, not just the curated BANK_MAP subset —
    # many universe holdcos (e.g. PFS = "Provident Financial Services") publish
    # under their bank-subsidiary brand ("Provident Bank"), which the legal name
    # never matches. Aliases are hand-picked and unambiguous, so they win outright.
    for ticker, aliases in _BRAND_ALIASES.items():
        for alias in aliases:
            normalized = _normalize_name(alias)
            if normalized and not _is_too_generic(normalized):
                curated.setdefault(normalized, ticker)

    try:
        from data.bank_universe import get_universe_tickers, get_universe
        from data.bank_mapping import get_name
        uni = get_universe()
        for ticker in get_universe_tickers():
            if ticker in curated_tickers:
                continue
            # Index BOTH the SEC holdco name (get_name) AND the FDIC bank-subsidiary
            # brand (bank_name, e.g. "Provident Bank", "Rockland Trust") — holdcos
            # routinely publish news under the subsidiary brand, which the holdco
            # name never matches. The bank_name is absent until refresh-universe
            # repopulates the snapshot, so this no-ops gracefully until then.
            holdco = get_name(ticker) or ""
            sub = (uni.get(ticker) or {}).get("bank_name") or ""
            for nm in (holdco, sub):
                if not nm or nm.strip().upper() == ticker.upper():
                    continue
                normalized = _normalize_name(nm)
                # Keep too-generic names here (unlike before): a generic name
                # shared by >1 bank is recoverable via disambiguation, not noise.
                if len(normalized) < 6:
                    continue
                cands = universe_cands.setdefault(normalized, [])
                if ticker not in cands:
                    cands.append(ticker)
    except Exception as e:
        print(f"[wire] universe name index skipped: {type(e).__name__}: {e}")

    unique: list[tuple[str, str]] = []
    ambiguous: dict[str, list[str]] = {}
    # Curated names: win outright, and are never too-generic (filtered above).
    for normname, ticker in curated.items():
        unique.append((normname, ticker))
    # Universe-only names: 1 candidate → regular index (if not too-generic);
    # >1 candidate → ambiguous, UNLESS the candidates are share-class siblings of
    # one issuer (same CIK, e.g. First Niles common FNFI + preferred FNFPA) — that
    # collapses to the common (shortest) ticker, not a real disambiguation.
    try:
        from data.bank_mapping import get_cik, get_name as _gn
    except Exception:
        get_cik = lambda _t: None  # noqa: E731
        _gn = lambda _t: ""        # noqa: E731
    for normname, cands in universe_cands.items():
        if normname in curated:
            continue                      # a curated ticker already owns it
        if len(cands) > 1:
            names = {(_gn(t) or "").strip().upper() for t in cands}
            distinct_ciks = {get_cik(t) for t in cands} - {None}
            # TRULY ambiguous = candidates with the SAME resolved name but
            # DIFFERENT issuers (FBP/FNLC, both "First Bancorp") — indistinguishable
            # except by ticker/geo, so disambiguate at match time. Everything else
            # keeps the old single-tag behaviour so nothing that matched before
            # regresses: share-class siblings (FNFI/FNFPA, same name + no distinct
            # CIK) and distinguishable-by-name collisions (Citizens vs Citizens
            # Holding) collapse to one — the most-specific resolved name, then the
            # shortest ticker.
            if len(names) == 1 and len(distinct_ciks) >= 2:
                ambiguous[normname] = cands
            else:
                best = sorted(cands, key=lambda t: (-len(_gn(t) or ""), len(t)))[0]
                unique.append((normname, best))
        elif not _is_too_generic(normname):
            unique.append((normname, cands[0]))

    unique.sort(key=lambda x: -len(x[0]))
    _AMBIGUOUS_INDEX = ambiguous
    return unique


# Lazy-initialized at first match call
_NAME_INDEX: list[tuple[str, str]] = []
# normname -> [candidate tickers] for names shared by >1 bank (built alongside
# _NAME_INDEX). Resolved at match time by ticker / geo cues in the release body.
_AMBIGUOUS_INDEX: dict[str, list[str]] = {}

# Geo disambiguators for banks whose normalized name collides with another's.
# When the ambiguous brand appears in a headline, the candidate whose ticker or
# one of these location cues also appears (title OR body) wins. Curated only for
# verified cases — an unknown candidate just relies on its ticker symbol (which
# first-party releases almost always print as "(NYSE: XXX)").
_AMBIGUOUS_GEO: dict[str, list[str]] = {
    "FBP":  ["PUERTO RICO", "SAN JUAN"],        # First BanCorp (NYSE: FBP)
    "FNLC": ["MAINE", "DAMARISCOTTA"],          # The First Bancorp (NASDAQ: FNLC)
    "INDB": ["MASSACHUSETTS", "ROCKLAND"],      # Independent Bank Corp / Rockland Trust
}


def _alnum_pad(text: str) -> str:
    """Uppercase, punctuation→space, collapse, and pad — for scanning a release
    for ticker/geo cues (no trailing-suffix stripping, unlike _normalize_name)."""
    t = re.sub(r"[^A-Za-z0-9]+", " ", (text or "").upper())
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _disambiguate(candidates: list[str], haystack_padded: str) -> str | None:
    """Pick the one ambiguous-name candidate the release is actually about, by
    scoring each on its ticker symbol (strong) and curated geo cues (weak) in the
    title+body. Returns the unique top scorer, or None when nothing distinguishes
    them (better no tag than a wrong one — CLAUDE.md)."""
    best_ticker, best_score = None, 0
    tie = False
    for tk in candidates:
        score = 2 if f" {tk.upper()} " in haystack_padded else 0
        for geo in _AMBIGUOUS_GEO.get(tk.upper(), []):
            if f" {geo} " in haystack_padded:
                score += 1
        if score > best_score:
            best_ticker, best_score, tie = tk, score, False
        elif score == best_score and score > 0:
            tie = True
    return None if (best_score == 0 or tie) else best_ticker


# Multiword proper nouns that BEGIN with a word also used as a bank-brand token.
# A brand phrase ending in that word must not match when the text is really
# continuing into the proper noun — e.g. brand "First United" (FUNC) vs the
# country in "...opening of first United Arab Emirates office". Keyed by the
# matched phrase's LAST token → the set of following tokens that prove it's the
# non-bank proper noun, not the bank.
_PROPER_NOUN_TRAP = {
    "UNITED": {"STATES", "KINGDOM", "ARAB", "NATIONS", "AIRLINES", "AIRWAYS",
               "HEALTHCARE", "HEALTH", "PARCEL", "METHODIST", "WAY",
               "TECHNOLOGIES", "RENTALS", "NATURAL"},
}


def _first_real_occurrence(haystack_padded: str, name: str) -> int:
    """Index of the first WORD-BOUNDARY occurrence of `name` in `haystack_padded`
    (which must be space-normalized and padded with leading/trailing spaces) that
    is NOT swallowed by a larger proper noun. Returns -1 if there's no genuine
    occurrence. Without a trap entry for the phrase's last word this is a plain
    boundary find; with one it skips occurrences where the next token completes
    the proper noun (so "FIRST UNITED" matches "First United Bank" but not
    "first United Arab Emirates")."""
    if not name:
        return -1
    needle = " " + name + " "
    trap = _PROPER_NOUN_TRAP.get(name.rsplit(" ", 1)[-1])
    pos = haystack_padded.find(needle)
    while pos != -1:
        if not trap:
            return pos
        nxt = haystack_padded[pos + len(needle):].split(" ", 1)[0]
        if nxt not in trap:
            return pos
        pos = haystack_padded.find(needle, pos + 1)
    return -1


def phrase_in_text(haystack_padded: str, phrase: str) -> bool:
    """True if `phrase` occurs as a genuine word-boundary mention in
    `haystack_padded` (space-normalized, space-padded), not swallowed by a larger
    proper noun. Shared by the fmp subject guard so its brand-core check uses the
    same trap as the wire name matcher."""
    return _first_real_occurrence(haystack_padded, phrase) >= 0


# Common English words that are ALSO a bank's whole single-token name core. A
# bare match on one of these collides with unrelated text ("FREEDOM" -> "Freedom
# Boat Club"; "POPULAR" -> "Popular CBD Salve"). (Also imported by fmp_news.)
_COMMON_NAME_WORDS = {
    "FREEDOM", "POPULAR", "CITIZENS", "INDEPENDENT", "COMMERCE", "COMMUNITY",
    "HERITAGE", "PEOPLES", "PREMIER", "PROSPERITY", "PACIFIC", "COLUMBIA",
    "CENTRAL", "LIBERTY", "PROVIDENT", "GENESIS", "SUMMIT", "PINNACLE",
    "BUSINESS", "AMERICAN", "SOUTHERN", "NORTHERN",
    "HORIZON", "ALLIANCE", "CAPITAL", "PARTNERS", "PROGRESS", "PREFERRED",
    "EQUITY", "GUARANTY", "HOMETOWN", "PATRIOT", "SERVICE", "SELECT",
}

# A name whose entire core is a single common word OR a short initialism is
# collision-prone (a French "FNB" = ETF; "Freedom Boat Club"). Confirm those
# only when the issuer's exchange-qualified ticker is present.
_EXCHANGE_RE = (r"(?:NYSE\s*AMERICAN|NYSE|NASDAQ|AMEX|OTCQX|OTCQB|OTCMKTS|OTC|"
                r"CBOE|BATS|ARCA)")


def _is_risky_single(name: str) -> bool:
    toks = name.split()
    return len(toks) == 1 and (toks[0] in _COMMON_NAME_WORDS or len(toks[0]) <= 4)


def _word_after(haystack: str, name: str) -> str:
    """The token immediately following `name` in the space-normalized haystack
    ('CITIZENS HOLDING ...' -> 'HOLDING'); '' if none. Used to confirm a risky
    single-token match sits inside the bank's real name ('Citizens Holding')
    rather than an unrelated phrase ('Freedom Boat Club')."""
    m = re.search(r"(?:^|\s)" + re.escape(name) + r"\s+(\S+)", haystack)
    return m.group(1).rstrip(".") if m else ""


def _has_exchange_ticker(text: str, context: str, ticker: str) -> bool:
    """True if the issuer's exchange-qualified ticker ('(NYSE: FNB)', 'Nasdaq:
    FRHC') appears in the title/body — the discriminator between a first-party
    release and FMP's polluted symbol index."""
    blob = f"{text} {context}".upper()
    return re.search(_EXCHANGE_RE + r"\s*:?\s*" + re.escape(ticker.upper()) + r"\b",
                     blob) is not None


def match_tickers(text: str, context: str = "") -> list[str]:
    """
    Find which bank tickers are mentioned in a piece of text (the headline).

    `context` (e.g. the release body) is used ONLY to disambiguate a candidate
    when the headline carries a name shared by >1 bank — it never introduces a
    new tag on its own, so the title-only safety property is preserved.

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
        # Word-boundary search for " NAME " in " ...HAYSTACK... ", skipping
        # occurrences swallowed by a larger proper noun ("First United" must not
        # match "first United Arab Emirates").
        needle = " " + name + " "
        idx = _first_real_occurrence(haystack, name)
        if idx < 0:
            continue
        end = idx + len(needle)
        # Don't double-count: if this match overlaps a longer earlier match, skip.
        if any(s <= idx < e or s < end <= e for (s, e) in consumed):
            continue
        # A bare common-word / short-initialism core ("FREEDOM", "FNB") collides
        # with unrelated text. Accept it only when it sits inside the bank's real
        # name (followed by a corporate word: "Citizens Holding", "Freedom
        # Holding") OR the issuer's exchange-qualified ticker confirms it. Kills
        # the "Freedom Boat Club"→FRHC and French-"FNB AGF"→FNB mis-tags while
        # keeping "Citizens Holding Company"→CIZN.
        if _is_risky_single(name) and not (
                _word_after(haystack, name) in _SUFFIX_TOKENS
                or _has_exchange_ticker(text, context, ticker)):
            continue
        consumed.append((idx, end))
        if ticker not in found:
            found.append(ticker)

    # Ambiguous pass: a name shared by >1 bank (e.g. "First Bancorp" → FBP/FNLC,
    # which the regular index drops as too generic) tags a bank ONLY when the
    # headline names it and the title+body carries that bank's ticker or geo cue.
    if _AMBIGUOUS_INDEX:
        disamb_hay = _alnum_pad(f"{text} {context}")
        for name, cands in _AMBIGUOUS_INDEX.items():
            if _first_real_occurrence(haystack, name) < 0:
                continue                  # the ambiguous brand must be in the headline
            winner = _disambiguate(cands, disamb_hay)
            if winner and winner not in found:
                found.append(winner)

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
    r"structured\s+notes?|fixed\s+to\s+floating|exchange[\s-]?traded\s+notes?|"
    r"\betns?\b)\b",
    re.IGNORECASE,
)


def is_routine_noise(headline: str) -> bool:
    """True for structured-note / retail-product issuances — high-volume filler a
    bank analyst doesn't track. Applied to ALL news sources (wires included)."""
    return bool(_NOISE_RE.search(headline or ""))


# Headlines that mention a bank but aren't ABOUT it — third-party SEO/
# aggregator spam (broker forecasts, funding stories about someone else, and
# non-material branch/staffing trivia). These slip past name-matching because
# the bank's name appears in another company's story.
# (Structured-note issuance is covered by _NOISE_RE above; 13F-ownership and
# insider-trivia spam by the dedicated regexes below.)
_THIRD_PARTY_RE = re.compile(
    # NOTE: 'price\s+targets?' (plural) — the old pattern's trailing word-
    # boundary missed "Price Targets" (the live MS/JEF headline). Each branch
    # carries its own boundaries so a plural 's' can't defeat the group's \b.
    r"\bissues?\s+(optimistic|pessimistic|bullish|bearish)\s+forecast\b"
    r"|\bprice\s+targets?\b|\btarget\s+price\b|\bforecast\s+for\b"
    r"|\b(funding|investment)\s+from\b|\bto\s+(buy|sell)\s+\$?\d"
    r"|\boffice\s+leader\b|\bbranch\s+manager\b|\brelationship\s+manager\b"
    r"|\bnew\s+\w+\s+(branch|location|office)\b",
    re.IGNORECASE,
)

# 13F / institutional-ownership SEO spam. Stock-ownership churn — "X Acquires N
# Shares of Y", "N Shares of Y Acquired by X", "X Boosts Holdings in Y", "X Has
# $N Position in Y", "New Stake in Y" — is not material company news. It's
# auto-generated filler from MarketBeat / ETF-Daily-News-style content farms and
# was the single biggest junk source in the live feed (2026-06-15). It also
# mis-tags: "...Shares of Target Corporation $TGT ... by State Street" landed
# under STT because the holder's name matched. Examples this catches:
#   "66,617 Shares in SoFi Technologies, Inc. $SOFI Acquired by Blue Jean ... LLC"
#   "State Street Corp Acquires 394,198 Shares of The Goldman Sachs Group $GS"
#   "JPMorgan Chase & Co. Has $2.1 Billion Position in Apple Inc."
#   "Bank of America Boosts Holdings in Tesla" / "New Stake in Zions ... by Vanguard"
_INSTITUTIONAL_RE = re.compile(
    r"\d[\d,]*\s+shares?\s+(in|of)\b"
    r"|\bshares?\s+(in|of)\s+\d"
    # "<Holder> reports 3.42M-share stake in <Bank>" — compact 13F-stake form
    # the live feed surfaced under the bank's OWN ticker (so the cross-ticker
    # guard can't catch it). Distinct from "Takes Stake in a fintech" (no
    # leading share-count → not matched).
    r"|\b\d[\d.,]*\s*[mkb]?[\s-]?shares?\s+(stake|position|holding|interest)\b"
    # "<Holder> Acquires/Sells/Trims/Boosts ... N shares / its holdings /
    # its position". 'take\w+ stake' is deliberately NOT here — a bank *taking
    # a stake* in a fintech is real news; only the "takes position in" content-
    # farm phrasing (handled below) is junk.
    r"|\b(acquir\w+|sells?|sold|buys?|bought|purchas\w+|trim\w+|boost\w+|"
    r"lower\w+|reduc\w+|divest\w+)\s+"
    r"(its\s+|a\s+|new\s+)?(stake|position|holding|shares?|\$?\d)"
    # broker/13F "Has $N (million|billion) Position/Holdings/Stake in <Co>"
    r"|\bhas\s+(a\s+)?\$[\d.,]+\s*(million|billion|thousand)?\s*"
    r"(stock\s+|equities?\s+)?(position|holding|stake)"
    # content-farm ownership openers
    r"|\bnew\s+(stake|position)\s+in\b"
    r"|\btakes?\s+(a\s+)?position\s+in\b"
    r"|\b(boost\w+|trim\w+|lower\w+|reduc\w+|grow\w+|grew|cut\w+|rais\w+|"
    r"increas\w+|lift\w+)\s+(its\s+|a\s+)?(stake|holdings?|position)\s+in\b"
    r"|\bshares?\s+(sold|bought|purchased|acquired)\s+by\b",
    re.IGNORECASE,
)

# Foreign / EU-style major-shareholding & transparency notifications — a holder
# (often a big bank) filing a regulatory ownership notice ABOUT another company.
# The bank's name sits in the title, so the name-match + cross-ticker guards keep
# it, but it's third-party ownership plumbing, not bank news.
# Examples: "Umicore - Transparency notifications by Bank of America Corporation",
# "...Transparantieverklaringen...", "REG - X JPMorgan Chase - Holding(s) in Company".
_SHAREHOLDER_NOTICE_RE = re.compile(
    r"\btransparency\s+notification"
    r"|\btransparantieverklaring"
    r"|\bd[eé]clarations?\s+de\s+transparence"
    r"|\bnotification\s+of\s+major\s+holding"
    r"|\bmajor\s+(shareholding|holding)s?\b"
    r"|\bholding\(s\)\s+in\s+company"
    r"|\btotal\s+voting\s+rights\b"
    r"|\btr-1\b",
    re.IGNORECASE,
)

# Form-4 / insider micro-events auto-posted by content farms — tax-withholding
# share events and "N-share" vesting/award trivia. Non-material.
# Examples: "CF Bankshares (NASDAQ: CFBK) CEO reports 1,932-share tax
# withholding event"; "EVP sells 4,000 shares".
_INSIDER_TRIVIA_RE = re.compile(
    r"\btax[\s-]?withholding\b"
    r"|\d[\d,]*[\s-]share\s+(tax|withholding|vesting|forfeiture|award|grant|"
    r"disposition|acquisition)\b"
    r"|\b(ceo|cfo|director|evp|svp|president|insider)\s+sells?\s+\d"
    r"|\bsells?\s+\d[\d,]*\s+shares?\b"
    r"|\bshares?\s+sold\s+by\s+(insider|ceo|cfo|director|evp|svp|president)\b"
    r"|\bform\s+144\b",   # notice of proposed sale of restricted stock — plumbing
    re.IGNORECASE,
)

# SEO / stock-analysis profile pages that aggregators (simplywall.st, marketbeat,
# stockanalysis, "Risk Zones" trade-signal sites) auto-generate per ticker. They
# name the bank but carry no news — pure click-bait reference pages. Patterns are
# specific to those page titles; real dividend/M&A/earnings/officer releases never
# read this way (pinned in tests/test_news_junk_filter.py).
_SEO_RE = re.compile(
    r"\bshareholder\s+structure\b"
    r"|\binstitutional\s+(holdings?|ownership)\b"
    r"|\bmajor\s+shareholders?\b"
    r"|\bvaluation\s*[:\-]"                         # "Valuation: PE, PB & Fair Value..."
    r"|\bfair\s+value\s+analysis\b"
    r"|\bpe,?\s*pb\b|\bp/?e\b[^.]{0,10}\bp/?b\b"    # "PE, PB" / "P/E ... P/B"
    r"|\bprecision\s+trading\b|\brisk\s+zones?\b"
    r"|\bintrinsic\s+value\b|\bdcf\s+(valuation|analysis|value)\b",
    re.IGNORECASE,
)

# Content-farm editorializing tacked onto an earnings-shaped headline.
# Example: "...Q1 2026 Earnings: EPS Falls Short ... - Earnings Manipulation Risk"
_FARM_RE = re.compile(
    r"\bearnings\s+manipulation\s+risk\b"
    r"|\b(you\s+)?should\s+you?\s+buy\b|\bis\s+it\s+a\s+buy\b"
    r"|\bhere'?s\s+(why|what)\b",
    re.IGNORECASE,
)

# Promotional / listicle / SEO-spam headlines — "Top 5 Bank Stocks", "3 Reasons
# to Buy", "Best Dividend Stocks to Buy Now", "... Should Be On Your Radar",
# "Stock Moves -1.1%: What You Should Know", plus content-farm publisher tags
# that only ever wrap aggregator chaff. These mention a bank but sell clicks,
# not news. (Earnings/M&A/dividend releases never read this way.)
# Branches stay narrow on purpose: the bare "<N> <noun>" form only fires on
# listicle nouns (best/top/reasons/cheap/undervalued), never on "bank"/"stocks"/
# "dividend" (which appear with leading numbers in legit M&A/earnings headlines —
# "$2.4 Billion Community Bank", "Dividend by 6%"). The real "N stocks" listicle
# still gets caught by the "stocks to buy/watch" branch.
_PROMO_RE = re.compile(
    r"\b(top|best|worst)\s+\d+\b"
    r"|\b\d+\s+(best|top|worst|reasons?|cheap|undervalued|high[\s-]?yield)\b"
    r"|\bstocks?\s+to\s+(buy|watch|sell|avoid|consider)\b"
    r"|\b(buy|watch|sell)\s+(now|today)\b"
    r"|\bshould\s+be\s+on\s+your\s+(radar|watchlist|list)\b"
    r"|\bwhat\s+you\s+should\s+know\b"
    r"|\bstock\s+moves?\s+[+-]?\d"
    r"|\b(premarket|pre-market|midday|mid-day|after[\s-]?hours)\s+(movers|"
    r"gainers|losers|trading)\b"
    r"|\bbiggest\s+(movers|gainers|losers)\b"
    r"|\btrending\s+stocks?\b"
    r"|\b52[\s-]?week\s+(high|low)\b"
    r"|\bmoving\s+average\b"
    r"|\bheavy\s+(trading\s+)?volume\b"
    r"|\b(motley\s+fool|zacks|insider\s+monkey|simply\s+wall)\b",
    re.IGNORECASE,
)

# Structured-product prospectus / boilerplate auto-posts (StockTitan / Street-
# Insider firehose). 424B* prospectus supplements, pricing/free-writing
# prospectuses, and "Guarantor:" stubs are filings plumbing, not company news.
# (Distinct from _NOISE_RE, which catches the note PRODUCTS by name.)
_PROSPECTUS_RE = re.compile(
    r"\b424b\d?\b"
    r"|\bfree\s+writing\s+prospectus\b"
    r"|\b(prospectus|pricing|prospectus\s+supplement)\s+supplement\b"
    r"|\bform\s+(fwp|424b\d?)\b"
    r"|^\s*guarantor\s*:",
    re.IGNORECASE,
)

# Dividend-CALENDAR filler — "X to Trade Ex-Dividend", "Ex-Dividend Reminder",
# "Upcoming Dividend Calendar". Auto-generated scheduling chaff. Must NOT catch
# the real corporate action "<Bank> Declares/Increases ... Dividend".
_DIV_CALENDAR_RE = re.compile(
    r"\bex[\s-]?dividend\b"
    r"|\bdividend\s+(calendar|reminder|schedule)\b"
    r"|\bupcoming\s+dividend\b"
    r"|\bto\s+go\s+ex[\s-]?dividend\b",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────
# Coverage: material third-party-voiced events the first-party PR gate would
# otherwise drop. Regulatory/enforcement actions against a bank are reported BY
# the regulator/press, not the bank, so they never carry a company PR verb —
# yet they're highly material. Let them through the Google/Yahoo first-party
# gate (still subject to is_junk_news + name-matching).
# ──────────────────────────────────────────────────────────────────────────
_REGULATORY_RE = re.compile(
    r"\b(consent\s+order|cease[\s-]and[\s-]desist|enforcement\s+action|"
    r"civil\s+money\s+penalt\w+|deferred\s+prosecution|"
    r"matter\s+requiring\s+attention|"
    r"\bmra\b|written\s+agreement\s+with\s+(the\s+)?(fed|occ|fdic)|"
    r"\b(fined|fines|penaliz\w+|sanction\w+|charg\w+|sued|lawsuit|settl\w+|"
    r"indict\w+|prob\w+)\s+\w*\s*(by\s+)?(the\s+)?"
    r"(occ|fdic|federal\s+reserve|cfpb|\bsec\b|doj|justice\s+department|"
    r"regulators?|prosecutors?))\b"
    r"|\b(occ|fdic|federal\s+reserve|cfpb|doj)\s+(fines?|orders?|charges?|"
    r"sanctions?|penaliz\w+|sues?|issues?\s+\w+\s+order)\b",
    re.IGNORECASE,
)


def is_material_regulatory(headline: str) -> bool:
    """True for regulatory/enforcement events (consent orders, fines, written
    agreements, lawsuits by a regulator). Material but third-party-voiced, so
    the first-party PR gate drops them — this lets them back in. Still gated by
    is_junk_news + name-matching downstream, so spam can't ride in on it."""
    return bool(_REGULATORY_RE.search(headline or ""))


# A parenthetical exchange tag like (NYSE:CHWY) — if it's NOT this bank's
# ticker, the story is about another company. The bare "$TGT" form (no exchange
# prefix) is the dominant style in 13F-spam headlines, so we check both.
_PAREN_TICKER_RE = re.compile(
    r"\((?:NYSE|NASDAQ|NYSEAMERICAN|NYSEARCA|AMEX|OTC|CBOE)[:\s]+([A-Z.]{1,6})\)",
    re.IGNORECASE,
)
# Bare cashtag like "$TGT" / "$GS". Upper-case only (so "$500" or "$2.1" can't
# match) and 2–6 letters (skips single-letter false hits).
_CASHTAG_RE = re.compile(r"\$([A-Z]{2,6})\b")

# Bank named only PERIPHERALLY (it isn't the subject) or marketing/sponsorship
# fluff. The bank's name matched the headline, but the STORY is someone else's:
#   • underwriter / placement-agent / advisor on another company's deal —
#     "Macerich ... Public Offering ... Goldman Sachs acting as underwriter"
#   • custodian/holder on an EU transparency filing — "Umicore - Transparency
#     notification by Bank of America Corporation"
#   • sponsorship / sports / sweepstakes — "Monster Energy's ... UFC ..."
#   • content-marketing "study/survey finds" pieces — "BofA Study Finds ..."
# Narrow on purpose: real dividend/M&A/earnings/buyback/officer headlines never
# read this way (pinned in tests/test_news_junk_filter.py).
_OFFSUBJECT_RE = re.compile(
    r"\bunderwrit"                                       # underwriter/-ing
    r"|\bbook-?runn|\bbookrunner\b"
    r"|\b(joint|sole|lead|co-?)\s*(?:book|lead\s+manager|bookrunner|manager)\b"
    r"|\bplacement\s+agent\b|\bsales\s+agent\b"
    r"|\bacting\s+as\b|\bas\s+(?:lead|sole|joint|exclusive)\b"
    r"|\b(?:financial|exclusive|legal)\s+advisor\b|\badvisor\s+to\b"
    r"|\btransparency\s+notification\b|\bshareholding\s+notification\b"
    r"|\bnotification\s+of\s+major\b|\bvoting\s+rights\b"
    r"|\bUFC\b|\bchampionship\b|\bdefeats\b|\btitle\s+fight\b"
    r"|\bgrand\s+prix\b|\btournament\b|\bsweepstakes\b"
    r"|\b(?:study|survey|poll|report|index)\s+(?:finds|reveals|shows)\b"
    r"|\bsurvey\s+of\b",
    re.IGNORECASE,
)


def is_junk_news(headline: str, ticker: str | None = None) -> bool:
    """ONE junk filter for ingest AND display. Rejects:
      • third-party broker forecasts / branch-hire trivia (_THIRD_PARTY_RE)
      • 13F institutional-ownership churn (_INSTITUTIONAL_RE) — the biggest junk
        source in the live feed: "X Acquires N Shares of Y", "Y Has $N Position
        in Z", "New Stake in W"
      • Form-4 / insider tax-withholding & vesting micro-events (_INSIDER_TRIVIA_RE)
      • content-farm editorializing (_FARM_RE)
      • promotional / listicle / SEO-spam + market-roundup/movers (_PROMO_RE) —
        "Top 5 Bank Stocks", "3 Reasons to Buy", "Stock Moves -1%: What You
        Should Know", "Midday Movers"
      • structured-product prospectus boilerplate (_PROSPECTUS_RE) — 424B*
        supplements, free-writing prospectuses, "Guarantor:" stubs
      • dividend-CALENDAR filler (_DIV_CALENDAR_RE) — ex-dividend reminders /
        upcoming-dividend schedules (NOT real "Declares Dividend" actions)
      • off-subject / marketing (_OFFSUBJECT_RE) — bank named only as
        underwriter/advisor/custodian on someone else's deal, EU transparency
        notifications, sponsorship/sports fluff, "study finds" content-marketing
      • structured-note issuance (is_routine_noise)
      • (when ``ticker`` is given) headlines tagged with a DIFFERENT company's
        exchange ticker, both "(NYSE:XXX)" and bare "$XXX" cashtag forms.

    Conservative by construction: every rule targets a documented junk phrasing
    and the test suite pins legitimate press releases (dividends, M&A, earnings,
    buybacks, officer changes) as must-pass. This logic previously lived split
    between here and ui/home.py, where the two regexes drifted."""
    h = headline or ""
    if (_THIRD_PARTY_RE.search(h) or _INSTITUTIONAL_RE.search(h)
            or _INSIDER_TRIVIA_RE.search(h) or _FARM_RE.search(h)
            or _PROMO_RE.search(h) or _PROSPECTUS_RE.search(h)
            or _DIV_CALENDAR_RE.search(h) or _OFFSUBJECT_RE.search(h)
            or _SHAREHOLDER_NOTICE_RE.search(h) or _SEO_RE.search(h)
            or is_routine_noise(h)):
        return True
    if ticker:
        t = ticker.upper()
        for other in _PAREN_TICKER_RE.findall(h):
            if other.upper() != t:
                return True
        for other in _CASHTAG_RE.findall(h):
            if other.upper() != t:
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
