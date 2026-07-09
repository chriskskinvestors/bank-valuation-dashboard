"""
PR Newswire RSS adapter.

PR Newswire's per-industry RSS feeds. Financial services is most relevant;
we also pull the "Banking & Financial Services" and corporate feeds.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from data.events.base import Event, SourceAdapter
from data.events.wire_base import (
    fetch_rss, match_tickers, classify_press_release, is_routine_noise,
    is_safe_news_url, is_junk_news,
)


PRN_FEEDS = [
    "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
    "https://www.prnewswire.com/rss/banking-financial-services-news/banking-financial-services-news-list.rss",
]


class PRNewswireAdapter(SourceAdapter):
    name = "prnewswire"
    LOOKBACK_DAYS = 7

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        # `since` is deliberately ignored — the feed fetch returns the same
        # items either way, the store dedups on (source, external_id) + the
        # cross-source content key, and a MAX(published_at) cutoff permanently
        # dropped late-syndicated items (AUDIT-2026-07-02 P2 #21).
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)
        in_universe = set(tickers)
        out: list[Event] = []
        seen_guids: set[str] = set()

        for url in PRN_FEEDS:
            items = fetch_rss(url)
            for item in items:
                if item.published and item.published < cutoff:
                    continue
                if item.guid in seen_guids:
                    continue
                seen_guids.add(item.guid)

                if is_routine_noise(item.title) or not is_safe_news_url(item.link):
                    continue
                # Title-only matching — a bank named only in the body (as an
                # underwriter / investor / advisor) is not the story's subject.
                # context=summary disambiguates a shared headline name (First
                # Bancorp → FBP/FNLC) only; it never adds a body-only tag.
                matched = [t for t in match_tickers(item.title, context=item.summary)
                           if t in in_universe]
                if not matched:
                    continue

                for ticker in matched:
                    if is_junk_news(item.title, ticker):
                        continue
                    out.append(Event(
                        ticker=ticker,
                        source=self.name,
                        event_type=classify_press_release(item.title),
                        headline=item.title,
                        published_at=item.published or datetime.now(timezone.utc),
                        url=item.link,
                        summary=item.summary[:2000],
                        external_id=f"{item.guid}::{ticker}",
                        raw={"feed": url, "guid": item.guid},
                    ))
        return out
