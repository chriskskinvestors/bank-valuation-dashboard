"""
GlobeNewswire RSS adapter.

GlobeNewswire (owned by Notified / Intrado) publishes industry RSS feeds.
The Financial Services subject feed catches most US bank releases.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from data.events.base import Event, SourceAdapter
from data.events.wire_base import (
    fetch_rss, match_tickers, classify_press_release, is_routine_noise,
    is_safe_news_url,
)


# GlobeNewswire's financial-services subject feed
# The feed URL pattern is /RssFeed/orgclass/<id>/feedTitle/<title>
GN_FEEDS = [
    # Financial Services subject (orgclass 9)
    "https://www.globenewswire.com/RssFeed/subjectcode/9-Banking-And-Financial-Services/feedTitle/GlobeNewswire-Banking-and-Financial-Services",
    # Generic press releases (broad — let the name matcher filter to banks)
    "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire-News-on-Public-Companies",
]


class GlobeNewswireAdapter(SourceAdapter):
    name = "globenewswire"
    LOOKBACK_DAYS = 7

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        in_universe = set(tickers)
        out: list[Event] = []
        seen_guids: set[str] = set()

        for url in GN_FEEDS:
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
                matched = [t for t in match_tickers(text) if t in in_universe]
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
                        external_id=f"{item.guid}::{ticker}",
                        raw={"feed": url, "guid": item.guid},
                    ))
        return out
