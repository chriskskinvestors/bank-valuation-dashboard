"""
Google News RSS adapter.

Wire services like Issuer Direct / ACCESSWIRE / Newsfile no longer expose clean
public RSS feeds (their sites are JS single-page apps), but their releases are
syndicated into Google News within minutes. We query Google News *per watchlist
bank* by name and keep only items where our shared name-matcher confirms the bank
is the subject — so the firehose noise is filtered out and we catch press
releases the per-wire adapters (Business Wire / PR Newswire / GlobeNewswire) miss.

Per-ticker query, but run across the FULL universe with a throttled thread pool
(IR scraping can't scale to hundreds of bespoke sites; this can — one uniform
query per bank, needs only the name we already have).
"""
from __future__ import annotations
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from data.bank_mapping import get_name
from data.events.base import Event, SourceAdapter
from data.events.store import TOPIC_SOURCE, topic_ticker
from data.events.wire_base import (
    fetch_rss, match_tickers, classify_press_release, is_company_press_release,
    is_safe_news_url, is_junk_news, is_material_regulatory,
)

# A browser UA — Google News returns an empty/blocked feed to obvious bots.
_GN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _search_url(query: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _query_url(name: str) -> str:
    # Quote the exact name so we get items about THIS bank, not loose token hits.
    return _search_url(f'"{name}"')


def _strip_source(title: str) -> tuple[str, str]:
    """Google News titles are 'Headline - Source'. Split off the trailing source."""
    if " - " in title:
        head, src = title.rsplit(" - ", 1)
        return head.strip(), src.strip()
    return title.strip(), ""


def _slug(headline: str) -> str:
    """Stable key from a headline so the same release re-syndicated by another
    outlet (or seen on a later poll) dedups to one event."""
    return re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:90]


class GoogleNewsAdapter(SourceAdapter):
    name = "google_news"
    LOOKBACK_DAYS = 3
    # Modest concurrency — fast enough for the full universe within one poll
    # window, polite enough not to get soft-blocked by Google.
    MAX_WORKERS = 8

    def _fetch_ticker(self, ticker: str, cutoff: datetime) -> list[Event]:
        name = get_name(ticker)
        if not name:
            return []
        try:
            items = fetch_rss(_query_url(name), user_agent=_GN_UA)
        except Exception as e:
            print(f"[google_news] {ticker} error: {type(e).__name__}: {e}")
            return []
        evs: list[Event] = []
        seen: set[str] = set()
        for item in items:
            if item.published and item.published < cutoff:
                continue
            headline, src_name = _strip_source(item.title)
            # Confirm this bank is actually the subject (reuse the wire
            # name-matcher); drops tangential mentions Google returns.
            if ticker not in match_tickers(headline):
                continue
            # Keep the company's OWN releases AND material regulatory/enforcement
            # events (consent orders, fines, written agreements) — the latter are
            # reported by the regulator/press, never carry a company PR verb, and
            # were silently dropped by the first-party-only gate. Then drop
            # third-party articles, analyst notes, structured notes, and
            # foreign-ticker/junk mentions.
            if not (is_company_press_release(headline)
                    or is_material_regulatory(headline)):
                continue
            if is_junk_news(headline, ticker):
                continue
            # Reject content-farm/spam links (messaging, social, shorteners) —
            # a real release links to a wire / IR / outlet, never WhatsApp et al.
            if not is_safe_news_url(item.link):
                continue
            # Dedup by normalized headline so the same release syndicated by
            # multiple outlets collapses to one event (stable across polls).
            ext_id = f"{ticker}::{_slug(headline)}"
            if ext_id in seen:
                continue
            seen.add(ext_id)
            evs.append(Event(
                ticker=ticker,
                source=self.name,
                event_type=classify_press_release(headline),
                headline=headline,
                published_at=item.published or datetime.now(timezone.utc),
                url=item.link,
                summary="",
                external_id=ext_id,
                raw={"via": src_name, "query": name},
            ))
        return evs

    def _blocked(self) -> bool:
        """Google soft-blocks datacenter IPs with a 5xx on news.google.com. It's
        IP-level, so one probe tells us the whole run is blocked — skip the cycle
        instead of grinding 435 per-ticker queries into the 240s cap (each ~4s
        when throttled). A network hiccup (not a 5xx) is NOT treated as a block."""
        import requests
        try:
            r = requests.get(_query_url("JPMorgan Chase"),
                             headers={"User-Agent": _GN_UA}, timeout=8)
            return r.status_code >= 500
        except Exception:
            return False

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        if self._blocked():
            print("[google_news] news.google.com 5xx-blocking this datacenter IP "
                  "— skipping the per-ticker sweep this cycle (catches up when "
                  "Google lifts the soft-block)")
            return []
        # Warm the shared name index once in this thread so the worker threads
        # don't race to build it on first match_tickers() call.
        match_tickers("")
        out: list[Event] = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as ex:
            for evs in ex.map(lambda t: self._fetch_ticker(t, cutoff), tickers):
                out.extend(evs)
        return out


# ──────────────────────────────────────────────────────────────────────────
# Topic feeds — Home page categorized overnight news (docs/HOME-MACRO-PLAN.md:
# Macro · Geopolitical · Domestic · Large Markets Events Outside Banks).
# One Google News search per TOPIC per poll cycle (not per-bank).
# ──────────────────────────────────────────────────────────────────────────

# Category label -> Google News RSS search query. Quoted phrases keep the
# boolean parse unambiguous (bare multi-word terms next to OR are AND'd).
TOPIC_QUERIES: dict[str, str] = {
    "macro":        '"Federal Reserve" OR inflation OR CPI OR FOMC',
    "geopolitical": 'sanctions OR "military conflict" OR "trade war"',
    "domestic":     '"US economy" OR "Congress" OR "government shutdown"',
    # Large NON-bank market events — index-level moves, not single names.
    "markets":      '"stock market selloff" OR "stock market rally" OR "S&P 500"',
}


class GoogleNewsTopicAdapter(SourceAdapter):
    """
    General-news topic feeds for the Home page's categorized sections.

    Each stored item is stamped with its category two ways, with NO schema
    change (existing rows untouched): the sentinel ticker 'TOPIC:<CATEGORY>'
    (store.topic_ticker) and raw['category']. Read back via
    data.events.store.get_topic_news(category, hours).
    """

    name = TOPIC_SOURCE
    LOOKBACK_HOURS = 48
    MAX_PER_TOPIC = 15  # cap stored per topic per cycle

    def _fetch_topic(self, category: str, query: str, cutoff: datetime) -> list[Event]:
        try:
            items = fetch_rss(_search_url(query), user_agent=_GN_UA)
        except Exception as e:
            print(f"[{self.name}] {category} error: {type(e).__name__}: {e}")
            return []
        evs: list[Event] = []
        seen: set[str] = set()
        for item in items:
            if item.published and item.published < cutoff:
                continue
            headline, src_name = _strip_source(item.title)
            if not headline:
                continue
            # General-news junk filter — no ticker arg: these aren't bank
            # stories, so the foreign-paren-ticker check must not apply.
            if is_junk_news(headline):
                continue
            if not is_safe_news_url(item.link):
                continue
            # Dedup by normalized headline within the category, stable across
            # polls/outlets (store dedups on (source, external_id)).
            ext_id = f"{category}::{_slug(headline)}"
            if ext_id in seen:
                continue
            seen.add(ext_id)
            evs.append(Event(
                ticker=topic_ticker(category),
                source=self.name,
                event_type="topic_news",
                headline=headline,
                published_at=item.published or datetime.now(timezone.utc),
                url=item.link,
                summary="",
                external_id=ext_id,
                raw={"via": src_name, "category": category, "query": query},
            ))
        # Newest first; cap what we store per topic per cycle.
        evs.sort(key=lambda e: e.published_at, reverse=True)
        return evs[: self.MAX_PER_TOPIC]

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        # `tickers` is ignored — topics aren't per-bank; one query per topic.
        cutoff = since or (datetime.now(timezone.utc) - timedelta(hours=self.LOOKBACK_HOURS))
        out: list[Event] = []
        for category, query in TOPIC_QUERIES.items():
            out.extend(self._fetch_topic(category, query, cutoff))
        return out
