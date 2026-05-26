"""
Unified event ingestion + storage.

Every source (SEC 8-K, wire services, news APIs, IR sites) writes into a
single `events` table via the SourceAdapter interface. UI reads from this
table to render "Recent Activity" panels per-bank and across the universe.

Adapters in this package:
  • sec_8k        — SEC EDGAR 8-K filings (material press releases by law)
  • businesswire  — Business Wire RSS (financial-services + banking feeds)
  • prnewswire    — PR Newswire RSS (financial-services feed)
  • globenewswire — GlobeNewswire RSS (financial-services feed)
  • yfinance_news — (planned) Yahoo Finance third-party news
"""

from data.events.base import Event, SourceAdapter
from data.events.store import (
    init_schema,
    insert_events,
    get_recent_events,
    get_universe_recent,
    last_seen_published,
)

__all__ = [
    "Event",
    "SourceAdapter",
    "init_schema",
    "insert_events",
    "get_recent_events",
    "get_universe_recent",
    "last_seen_published",
]
