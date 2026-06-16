"""
FMP press-release adapter — per-ticker first-party company press releases.

Replaces the dead Business Wire direct RSS feed (its tokens broke; one had
silently become BW's Technology feed, the other 404'd). FMP's press-release
index aggregates Business Wire / PR Newswire / IR-site releases and is indexed
BY TICKER, so unlike the wire firehose there's no name-matching and therefore no
wrong-company mis-tagging — the whole bug class the 2026-06-15 pass fixed.

Provenance note (owner-approved exception, 2026-06-16): CLAUDE.md scopes FMP to
the verify-oracle ("never display"). Here FMP is the DISCOVERY index only — every
emitted event links to the PRIMARY source URL (businesswire.com / prnewswire.com
/ the IR site), exactly the role Google News already plays in this pipeline. No
FMP-sourced *value* is displayed.

Watchlist-scoped + throttled (per-ticker REST calls), and 30-min cached in
fmp_client, so it stays well under the Starter plan's rate cap. Releases are
first-party by construction, so the company-PR gate the aggregators need isn't
applied — but the SAME is_junk_news / is_safe_news_url filter runs, dropping the
structured-note / ETN-coupon filler FMP also carries.
"""

from __future__ import annotations
import re
import time
from datetime import datetime, timezone, timedelta

from data.events.base import Event, SourceAdapter
from data.events.wire_base import (
    classify_press_release, is_junk_news, is_safe_news_url, match_tickers,
    _normalize_name, _is_too_generic,
)


def _is_subject(ticker: str, text_blob: str) -> bool:
    """Confirm `ticker`'s bank is the SUBJECT of `text_blob` (title + body).

    FMP's symbol index is reliable for normal tickers but polluted for short,
    ambiguous ones (symbols=CMA returns "...CMA Fest" / a mining "CMA
    Underground", neither about Comerica). A first-party release always names
    its issuer, so we require either:

      (a) the shared name index matches the ticker — this carries the curated
          brand aliases ("JPMorganChase"→JPM) the legal name alone misses; or
      (b) the bank's resolved legal name (data.bank_mapping.get_name) appears
          verbatim — this covers banks absent from the universe-snapshot index
          (e.g. ABCB "Ameris Bancorp", CFBK "CF Bankshares") that (a) would drop.

    When the name can't be resolved (placeholder == ticker) we can't confirm and
    return False — better a missed item than a wrong-company tag (CLAUDE.md:
    prefer n/a over a guess)."""
    if ticker in match_tickers(text_blob):
        return True
    from data.bank_mapping import get_name
    name = (get_name(ticker) or "").strip()
    if not name or name.upper() == ticker.upper():
        return False
    norm = _normalize_name(name)
    if not norm or _is_too_generic(norm):
        return False
    return f" {norm} " in " " + _normalize_name(text_blob) + " "


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
