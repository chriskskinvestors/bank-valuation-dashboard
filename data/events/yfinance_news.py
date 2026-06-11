"""
Yahoo Finance news adapter.

yfinance.Ticker(symbol).news returns a list of recent news items per
ticker — aggregated from Reuters, Bloomberg summaries, MarketWatch,
Yahoo's own reporting, etc. Catches third-party coverage that wire
services and SEC filings miss (analyst notes, sector commentary,
M&A speculation).

Pulling per-ticker so we only get news for banks in our universe —
no name-matching needed (Yahoo's index does that for us).
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from data.events.base import Event, SourceAdapter
from data.events.wire_base import classify_press_release, is_company_press_release


class YFinanceNewsAdapter(SourceAdapter):
    """Yahoo Finance per-ticker news."""

    name = "yfinance_news"
    LOOKBACK_DAYS = 14  # Yahoo only returns the last ~10-30 items per ticker

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        out: list[Event] = []

        try:
            import yfinance as yf
        except ImportError:
            print(f"[{self.name}] yfinance not installed; skipping")
            return []

        # Throttle: yfinance hits Yahoo's API directly. ~0.1s per ticker is safe.
        import time
        seen_keys: set[str] = set()

        for ticker in tickers:
            try:
                yt = yf.Ticker(ticker)
                items = getattr(yt, "news", None)
                if not items:
                    continue

                for it in items:
                    # yfinance returns either flat dict or {content: {...}} per
                    # API version. Normalize both shapes.
                    content = it.get("content") if isinstance(it.get("content"), dict) else it

                    title = (content.get("title") or "").strip()
                    if not title:
                        continue
                    # First-party only — drop third-party articles/commentary
                    # (Yahoo is mostly aggregated coverage = junk for our feed).
                    if not is_company_press_release(title):
                        continue

                    # Extract URL and timestamp robustly across yfinance versions
                    url = ""
                    canonical = content.get("canonicalUrl") or {}
                    if isinstance(canonical, dict):
                        url = canonical.get("url", "")
                    if not url:
                        click = content.get("clickThroughUrl") or {}
                        if isinstance(click, dict):
                            url = click.get("url", "")
                    if not url:
                        url = content.get("link") or content.get("url", "")

                    # pubDate can be epoch int, ISO string, or datetime
                    pub = content.get("pubDate") or content.get("providerPublishTime")
                    if isinstance(pub, (int, float)):
                        pub_dt = datetime.fromtimestamp(pub, tz=timezone.utc)
                    elif isinstance(pub, str):
                        try:
                            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                        except Exception:
                            pub_dt = datetime.now(timezone.utc)
                    elif isinstance(pub, datetime):
                        pub_dt = pub if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
                    else:
                        pub_dt = datetime.now(timezone.utc)

                    if pub_dt < cutoff:
                        continue

                    # External id — use Yahoo's UUID if present, else fall back to URL
                    ext_id = content.get("id") or it.get("uuid") or url
                    if not ext_id:
                        continue
                    dedup_key = f"{ext_id}::{ticker}"
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    summary = (content.get("summary") or content.get("description") or "")[:1500]
                    publisher = (
                        (content.get("provider") or {}).get("displayName")
                        if isinstance(content.get("provider"), dict)
                        else content.get("publisher", "")
                    )

                    out.append(Event(
                        ticker=ticker,
                        source=self.name,
                        event_type=classify_press_release(title),
                        headline=title,
                        published_at=pub_dt,
                        url=url,
                        summary=summary,
                        external_id=dedup_key,
                        raw={"publisher": publisher},
                    ))

                # Gentle throttle so we don't get rate-limited
                time.sleep(0.1)

            except Exception as e:
                # Don't let one bank break the whole poll
                print(f"[{self.name}] {ticker} error: {type(e).__name__}: {e}")
                continue

        return out
