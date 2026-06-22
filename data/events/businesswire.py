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
    is_safe_news_url, is_junk_news,
)


# Business Wire's banking + financial-services RSS feeds.
#
# DISABLED 2026-06-15: the two tokens below were verified WRONG against the live
# endpoint — `...GVtRWA==` returns Business Wire's *Technology: Networks* feed
# (which mis-tagged an AI-startup funding release to MS as an investor mention),
# and `...GVtVUw==` is dead ("RSS channel ID is not available"). BW's feed tokens
# are server-generated and obfuscated, so guessing a replacement would risk
# shipping a wrong feed (CLAUDE.md: never ship plausible-wrong / primary sources
# only). Banking releases distributed via Business Wire are still captured —
# they syndicate into Google News within minutes (data/events/google_news.py),
# and PR Newswire / GlobeNewswire / SEC 8-K cover the same material events.
#
# OWNER ACTION NEEDED: supply the correct Banking and Financial-Services feed
# tokens from businesswire.com/portal feed builder, then re-populate BW_FEEDS.
BW_FEEDS: list[str] = []


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
                # Match on the TITLE only — not title+summary. A bank named in
                # the body as an underwriter / investor / advisor (e.g. a startup
                # "Raises $60M" release that credits a bank) is NOT the subject;
                # summary-matching mis-tagged exactly those to the bank. A
                # company's own release always names it in the headline.
                # context=summary is used ONLY to disambiguate a headline name
                # shared by >1 bank (First Bancorp → FBP/FNLC); it never adds a
                # tag the title didn't already name, so the underwriter concern
                # above still holds.
                matched = [t for t in match_tickers(item.title, context=item.summary)
                           if t in in_universe]
                if not matched:
                    continue

                for ticker in matched:
                    # Per-ticker junk gate (the single filter) — also rejects a
                    # headline carrying a DIFFERENT company's $TICKER/(NYSE:XXX).
                    if is_junk_news(item.title, ticker):
                        continue
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
