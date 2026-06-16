"""
FMP press-release adapter — per-ticker first-party company press releases.

Replaces the dead Business Wire direct RSS feed (its tokens broke; one had
silently become BW's Technology feed, the other 404'd). FMP's press-release
index aggregates Business Wire / PR Newswire / IR-site releases by ticker.

Provenance note (owner-approved exception, 2026-06-16): CLAUDE.md scopes FMP to
the verify-oracle ("never display"). Here FMP is the DISCOVERY index only — every
emitted event links to the PRIMARY source URL (businesswire.com / prnewswire.com
/ the IR site), exactly the role Google News already plays in this pipeline. No
FMP-sourced *value* is displayed.

Watchlist-scoped + throttled (per-ticker REST calls), and 30-min cached in
fmp_client, so it stays well under the Starter plan's rate cap. The SAME
is_junk_news / is_safe_news_url filter runs (dropping structured-note / ETN
filler), plus a deterministic _is_subject guard — FMP's symbol index is polluted
for short/ambiguous tickers (symbols=CMA returns "...CMA Fest"; symbols=CHCO
returns "Kansas City"/"Québec City" stories), so we confirm the bank is actually
named before tagging it.
"""

from __future__ import annotations
import re
import time
from datetime import datetime, timezone, timedelta

from data.events.base import Event, SourceAdapter
from data.events.wire_base import (
    classify_press_release, is_junk_news, is_safe_news_url,
    _GENERIC_WORDS, _BRAND_ALIASES, _STATE_SUFFIX,
)

# Trailing tokens dropped to reduce a bank's legal name to its distinctive
# "subject phrase" core. Corporate-form + entity-type words: the legal name
# ("Eastern Bankshares", "UMB Financial", "ACNB Corp") and the brand used in
# headlines ("Eastern Bank", "UMB", "ACNB Corporation") differ only in this
# trailing word, so stripping it lets them match. The >=2-token / distinctive
# guard in _subject_phrase keeps this from collapsing a name to a bare generic
# word ("CITY HOLDING" never becomes "CITY").
_SUBJ_SUFFIX = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
    "LTD", "LLC", "LP", "LLP", "NA", "HOLDINGS", "HOLDING", "GROUP",
    "BANCORPORATION", "&", "AND",
    # entity-type words
    "BANK", "BANKS", "BANCORP", "BANCSHARES", "BANKSHARES", "BANCSHARE",
    "BANKING", "FINANCIAL", "SAVINGS", "TRUST", "NATIONAL", "FIRST",
}
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def _ticker_related(tok: str, ticker: str) -> bool:
    """A single-token core is trustworthy if it ties back to the ticker — covers
    short acronym brands (UMB/UMBF, FNB/FNB, WSFS/WSFS, ACNB/ACNB) while leaving
    common words ("CITY" vs CHCO) unprivileged. The >=3 prefix overlap guards
    against an empty/short ticker matching everything via startswith("")."""
    t, k = tok.upper(), (ticker or "").upper()
    if len(t) < 3:
        return False
    return t == k or (len(k) >= 3 and (k.startswith(t) or t.startswith(k)))


def _canon_tokens(text: str) -> list[str]:
    """Canonical token list used for BOTH the bank's name phrase and the release
    text, so they compare on equal footing. Upper-cases, strips the SEC "/MD/"
    state suffix, maps "&" -> "AND" ("Farmers & Merchants" vs the wire's
    "Farmers AND Merchants"), and turns all other punctuation into spaces.

    Applying the identical transform to both sides is the fix for the recall
    collapse: previously the name kept "/MS/" and "&" while the text didn't, so
    nothing matched."""
    n = (text or "").upper()
    n = _STATE_SUFFIX.sub("", n)
    n = n.replace("&", " AND ")
    n = re.sub(r"[^\w\s]", " ", n)
    return n.split()


def _subject_phrase(name: str, ticker: str = "") -> str:
    """The distinctive name-phrase core to look for. Strips a trailing 2-letter
    state code ("Independent Bank Corp (MI)" -> ...) then trailing corporate /
    entity-type words, KEEPING at least two tokens — unless the single survivor
    is distinctive (>=6 chars OR ticker-related). So:
      "Eastern Bankshares"   -> "EASTERN"          (len>=6)
      "UMB Financial Corp"   -> "UMB"              (ticker UMBF)
      "First Citizens Bancshares" -> "FIRST CITIZENS"
      "CITY HOLDING CO"      -> "CITY HOLDING"     (CITY: short + unrelated to CHCO)
    The last line is the precision guard against FMP's "Kansas City" pollution."""
    toks = _canon_tokens(name)
    if len(toks) > 1 and toks[-1] in _US_STATES:   # drop "(MI)"/"(NJ)" tail
        toks.pop()
    while len(toks) > 1 and toks[-1].rstrip(".") in _SUBJ_SUFFIX:
        if len(toks) >= 3:                         # dropping still leaves >=2
            toks.pop()
        elif ((len(toks[0]) >= 6 or _ticker_related(toks[0], ticker))
              and toks[0] not in _GENERIC_WORDS):
            toks.pop()                             # 2->1 only if survivor distinctive
        else:
            break
    return " ".join(toks)


def _is_subject(ticker: str, text_blob: str) -> bool:
    """Confirm `ticker`'s bank is the SUBJECT of `text_blob` (title + body).

    FMP's symbol index is polluted for short/ambiguous tickers (symbols=CMA
    returns "...CMA Fest" / a mining "CMA Underground"; symbols=CHCO returns
    "Kansas City"/"Québec City" stories — none about the bank), so a first-party
    release must name its issuer. Confirm DETERMINISTICALLY — no dependence on
    the universe name index, whose completeness varies run-to-run and caused
    mass over-dropping of legitimate releases:

      (a) the bank's resolved-legal-name phrase (get_name) appears contiguously;
      (b) a curated brand alias appears (covers one-word brands like
          "JPMorganChase" and "BofA" that the legal name alone misses).

    When the name resolves only to a placeholder (== ticker) and there's no
    alias, we can't confirm → reject (CLAUDE.md: prefer n/a over a guess)."""
    blob = " " + " ".join(_canon_tokens(text_blob)) + " "

    from data.bank_mapping import get_name
    name = (get_name(ticker) or "").strip()
    if name and name.upper() != ticker.upper():
        phrase = _subject_phrase(name, ticker)
        toks = phrase.split()
        # Require a non-trivial phrase: not empty, and not a single generic word.
        if toks and not (len(toks) == 1 and toks[0] in _GENERIC_WORDS):
            if f" {phrase} " in blob:
                return True

    for alias in _BRAND_ALIASES.get(ticker.upper(), []):
        a = " ".join(_canon_tokens(alias))
        if a and f" {a} " in blob:
            return True
    return False


def _slug(headline: str) -> str:
    """Stable key from a headline so the same release dedups across polls and,
    via the store's cross-source content key, against the wire/Google copies."""
    return re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:90]


def _parse_dt(s: str) -> datetime | None:
    """FMP timestamps read 'YYYY-MM-DD HH:MM:SS' (naive). Treat as UTC — a few
    hours' tz drift is immaterial against the multi-day lookback, and ordering
    stays correct."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class FMPPressReleaseAdapter(SourceAdapter):
    """Per-ticker company press releases via FMP. Watchlist-only (narrow)."""

    name = "fmp_news"
    LOOKBACK_DAYS = 14
    MAX_PER_TICKER = 25

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        from data.fmp_client import get_press_releases, _has_key
        if not _has_key():
            print(f"[{self.name}] FMP_API_KEY not set; skipping")
            return []

        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        out: list[Event] = []
        seen: set[str] = set()

        for ticker in tickers:
            try:
                rows = get_press_releases(ticker, limit=self.MAX_PER_TICKER)
            except Exception as e:
                # One bank must never break the whole poll.
                print(f"[{self.name}] {ticker} error: {type(e).__name__}: {e}")
                continue

            for r in rows:
                title = (r.get("title") or "").strip()
                if not title:
                    continue
                url = (r.get("url") or "").strip()
                body = (r.get("text") or "")

                # Confirm THIS bank is actually the subject (see _is_subject):
                # FMP's symbol index is polluted for short/ambiguous tickers, so
                # without this an unrelated "CMA Fest" release would be tagged to
                # Comerica — re-introducing wrong-company mis-tagging.
                if not _is_subject(ticker, f"{title}. {body[:1000]}"):
                    continue

                pub = _parse_dt(r.get("published_at", ""))
                if pub is None:
                    pub = datetime.now(timezone.utc)
                elif pub < cutoff:
                    continue

                # THE single junk filter (drops structured-note / ETN-coupon
                # filler, wrong-company $tags) + spam-URL guard.
                if is_junk_news(title, ticker) or not is_safe_news_url(url):
                    continue

                # Stable dedup key — collapses re-syndication across polls; the
                # store's cross-source content key collapses it against the
                # wire/Google copies of the same release.
                ext_id = f"{ticker}::{_slug(title)}"
                if ext_id in seen:
                    continue
                seen.add(ext_id)

                out.append(Event(
                    ticker=ticker,
                    source=self.name,
                    event_type=classify_press_release(title),
                    headline=title,
                    published_at=pub,
                    url=url,
                    summary=(r.get("text") or "")[:1500],
                    external_id=ext_id,
                    raw={"publisher": (r.get("publisher") or "").strip()},
                ))

            # Gentle throttle — per-ticker REST calls; keeps us under the cap.
            time.sleep(0.05)

        return out
