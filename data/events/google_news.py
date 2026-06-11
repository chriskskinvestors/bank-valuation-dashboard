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
from data.events.wire_base import fetch_rss, match_tickers, classify_press_release

# A browser UA — Google News returns an empty/blocked feed to obvious bots.
_GN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _query_url(name: str) -> str:
    # Quote the exact name so we get items about THIS bank, not loose token hits.
    q = urllib.parse.quote(f'"{name}"')
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


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

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        # Warm the shared name index once in this thread so the worker threads
        # don't race to build it on first match_tickers() call.
        match_tickers("")
        out: list[Event] = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as ex:
            for evs in ex.map(lambda t: self._fetch_ticker(t, cutoff), tickers):
                out.extend(evs)
        return out
