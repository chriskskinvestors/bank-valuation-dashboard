"""
Cloud Run Job: poll all configured event sources, insert new events.

Runs every ~30 min during market hours via Cloud Scheduler. Idempotent —
duplicate detection happens in the store via (source, external_id).

If ANTHROPIC_API_KEY is set, freshly-ingested events with empty summaries
get a short LLM summary written back. Skipped when the key isn't
configured (e.g., dev environments).

Exit code:
  0  — at least one adapter ran without crashing
  1  — every adapter crashed (transient API issues that warrant alerting)
"""

from __future__ import annotations
import os
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")

    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST
    from data.events import init_schema, insert_events, last_seen_published
    from data.events.sec_8k import SEC8KAdapter
    from data.events.businesswire import BusinessWireAdapter
    from data.events.prnewswire import PRNewswireAdapter
    from data.events.globenewswire import GlobeNewswireAdapter
    from data.events.yfinance_news import YFinanceNewsAdapter
    from data.events.ir_site import IRSiteAdapter

    init_schema()

    watchlist = sorted(set(DEFAULT_WATCHLIST))
    universe = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))

    # SEC 8-K + wire feeds run against the full universe (cheap: one feed
    # call covers all banks via name-matching).
    # YFinance news runs against watchlist only: it's per-ticker, so 50
    # API calls (manageable) instead of 370 (Yahoo would rate-limit).
    broad_adapters = [
        SEC8KAdapter(),
        BusinessWireAdapter(),
        PRNewswireAdapter(),
        GlobeNewswireAdapter(),
    ]
    narrow_adapters = [YFinanceNewsAdapter(), IRSiteAdapter()]
    adapters = broad_adapters + narrow_adapters

    print(f"▶ Polling — broad: {len(broad_adapters)} sources × {len(universe)} tickers, "
          f"narrow: {len(narrow_adapters)} sources × {len(watchlist)} tickers")
    t0 = time.time()
    crashes = 0
    total_new = 0

    for adapter in adapters:
        try:
            # Narrow adapters (per-ticker APIs) only run against watchlist
            scope = watchlist if adapter in narrow_adapters else universe
            since = last_seen_published(adapter.name)
            print(f"  [{adapter.name}] scope={len(scope)} tickers since={since}")
            events = adapter.poll(scope, since=since)
            n = insert_events(events)
            total_new += n
            print(f"  [{adapter.name}] {len(events)} fetched, {n} new")
        except Exception as e:
            crashes += 1
            print(f"  [{adapter.name}] CRASH {type(e).__name__}: {e}")
            traceback.print_exc()

    # Optional: LLM-summarize any events with empty summaries (most recent first).
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            n_summarized = _summarize_recent_events(limit=20)
            print(f"▶ Summarized {n_summarized} recent events via Claude")
        except Exception as e:
            print(f"  [summarizer] failed: {type(e).__name__}: {e}")

    elapsed = time.time() - t0
    print(f"✓ Done in {elapsed:.1f}s — {total_new} new events, {crashes} adapter crashes")
    return 0 if crashes < len(adapters) else 1


def _summarize_recent_events(limit: int = 20) -> int:
    """
    Backfill summaries on the most-recently-ingested events that don't have
    one yet. Uses a small Claude call per event; cheap and idempotent.
    """
    from sqlalchemy import text
    from data.events.store import _get_engine

    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, ticker, source, headline, url
            FROM events
            WHERE summary IS NULL OR summary = ''
            ORDER BY published_at DESC
            LIMIT :n
        """), {"n": limit}).mappings().all()
    if not rows:
        return 0

    try:
        import anthropic
        from data.filing_summarizer import fetch_filing_text
    except ImportError:
        return 0

    client = anthropic.Anthropic()
    n = 0

    for r in rows:
        try:
            # Fetch the filing text (filing_summarizer caches via TTL)
            text_body = fetch_filing_text(r["url"]) if r["url"] else ""
            if not text_body or len(text_body) < 200:
                continue
            # 8-K filings can be huge; truncate to first ~10K chars
            text_body = text_body[:10000]

            msg = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are summarizing a SEC 8-K filing for {r['ticker']}.\n"
                        f"Headline: {r['headline']}\n\n"
                        "In 2-3 sentences, summarize the substance for a bank analyst. "
                        "Focus on dollar amounts, dates, and impact. Skip boilerplate.\n\n"
                        f"FILING TEXT:\n{text_body}"
                    ),
                }],
            )
            summary = "".join(b.text for b in msg.content if b.type == "text").strip()
            if not summary:
                continue
            with eng.begin() as conn:
                conn.execute(text("UPDATE events SET summary = :s WHERE id = :id"),
                             {"s": summary[:20000], "id": r["id"]})
            n += 1
        except Exception as e:
            print(f"    [summarize {r['ticker']}] {type(e).__name__}: {e}")
            continue
    return n


if __name__ == "__main__":
    sys.exit(main())
