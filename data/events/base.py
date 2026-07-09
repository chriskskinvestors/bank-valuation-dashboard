"""
Event dataclass + SourceAdapter base class.

Adapters subclass SourceAdapter and implement .poll(tickers) returning a
list of Event objects. The runner (jobs/poll_events.py) handles
persistence and dedup.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Event:
    """One thing that happened for one bank."""

    ticker: str
    source: str            # 'sec_8k', 'businesswire', etc.
    event_type: str        # 'press_release', 'earnings', 'news', 'regulatory'
    headline: str
    published_at: datetime
    url: str = ""
    summary: str = ""
    external_id: str = ""  # source-specific dedupe key
    raw: dict[str, Any] = field(default_factory=dict)


class SourceAdapter:
    """
    Subclass for each ingestion source. Implement .poll().

    Adapters are stateless — they're given a list of tickers and an
    optional cutoff timestamp ("anything newer than this"). They return
    a list of Event objects. The store deduplicates on (source, external_id).
    """

    name: str = ""  # must be set on the subclass — used as the source field

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        """
        Fetch new events for the given tickers.

        Args:
            tickers: List of bank tickers (e.g. ['JPM', 'BAC']).
            since:   Accepted for interface compatibility but IGNORED by every
                     adapter (AUDIT-2026-07-02 P2 #21): each adapter re-scans
                     its own full LOOKBACK window and relies on the store's
                     dedup — a MAX(published_at) cutoff permanently dropped
                     late-syndicated items for zero fetch savings.

        Returns:
            List of Event objects to be inserted. Returning duplicates is
            fine — the store handles dedup. Returning [] is fine.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement poll()"
        )
