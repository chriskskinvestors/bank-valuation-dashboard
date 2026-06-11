"""
Business Wire RSS adapter.

Business Wire publishes regional + industry RSS feeds. The financial
services / banking feed is the most relevant for us.

Feed URLs reference:
  https://www.businesswire.com/portal/site/home/news/industries/
  (Direct RSS at /portal/site/home/rss/ per industry slug)
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from data.events.base import Event, SourceAdapter
from data.events.wire_base import (
    fetch_rss, match_tickers, classify_press_release, is_routine_noise,
    is_safe_news_url,
)


# Business Wire's banking + financial-services RSS feeds
BW_FEEDS = [
    # Banking - all
    "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA==",
    # Financial Services - all
    "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtVUw==",
]


class BusinessWireAdapter(SourceAdapter):
    name = "businesswire"
    LOOKBACK_DAYS = 7

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        in_universe = set(tickers)
        out: list[Event] = []
        seen_guids: set[str] = set()

        for url in BW_FEEDS:
            items = fetch_rss(url)
            for item in items:
                if item.published and item.published < cutoff:
                    continue
                if item.guid in seen_guids:
                    continue
                seen_guids.add(item.guid)

                if is_routine_noise(item.title) or not is_safe_news_url(item.link):
                    continue
                text = f"{item.title}. {item.summary}"
                matched = match_tickers(text)
                # Keep only matches that are in the user's universe
                matched = [t for t in matched if t in in_universe]
                if not matched:
                    continue

                for ticker in matched:
                    out.append(Event(
                        ticker=ticker,
                        source=self.name,
                        event_type=classify_press_release(item.title),
                        headline=item.title,
                        published_at=item.published or datetime.now(timezone.utc),
                        url=item.link,
                        summary=item.summary[:1500],
                        # External id includes ticker so the same release matched
                        # to multiple banks (rare but possible) gets one row per
                        # bank without falsely overwriting.
                        external_id=f"{item.guid}::{ticker}",
                        raw={"feed": url, "guid": item.guid},
                    ))
        return out
