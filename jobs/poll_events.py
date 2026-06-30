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
import re
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Wall-clock budgets under the 900s Cloud Run task cap. _TASK_BUDGET_S is the
# overall ceiling for polling; _PER_ADAPTER_S caps any single source so one slow
# adapter (e.g. Google News over the full ~440-ticker universe) can't run past
# the kill — it's abandoned and we commit what completed. Tuned to leave room
# for the post-loop summarize/purge and still finish well under 900s.
_TASK_BUDGET_S = 780
_PER_ADAPTER_S = 240

# High-signal 8-K items whose headlines are opaque AND material — these benefit
# most from an LLM summary, so they jump the summarizer queue when budget is
# tight (M&A 1.01/2.01, officer change 5.02, restatement 4.02, impairment 2.06,
# control change 5.01, and the catch-all "Other Material Event" 8.01 which is
# opaque by definition). Earnings (2.02) are deprioritized: their numbers are
# already in the wire/FMP press release that accompanies them.
_HIGH_SIGNAL_8K_ITEMS = {"1.01", "2.01", "8.01", "5.02", "4.02", "2.06", "5.01"}


def _is_high_signal_8k(raw_json) -> bool:
    """True if a stored 8-K event's items include a high-signal type — used to
    prioritize the summarizer queue. Tolerant of missing/garbled raw_json."""
    import json
    try:
        items = json.loads(raw_json or "{}").get("items") or []
    except (TypeError, ValueError):
        return False
    return bool(set(items) & _HIGH_SIGNAL_8K_ITEMS)


def _run_with_timeout(label: str, fn, timeout_s: float):
    """Run fn() in a daemon worker and return its value, raising TimeoutError if
    it overruns timeout_s. The worker can't be force-killed (a blocking network
    call owns the thread), but being a daemon it never blocks process exit — so
    abandoning it lets the job finish and commit the rest. Re-raises fn's own
    exception."""
    import threading
    box: dict = {}

    def _work():
        try:
            box["value"] = fn()
        except Exception as e:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = e

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"{label} exceeded {timeout_s:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")
    # Line-buffer stdout so progress logs survive a hard task kill (block
    # buffering loses them) — this is why earlier timeouts showed no progress.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    from data.bank_universe import get_universe, coverage_excluded
    from config import DEFAULT_WATCHLIST
    from data.events import init_schema, insert_events_returning_new, last_seen_published
    from data.events.sec_8k import SEC8KAdapter, SEC8KRecentAdapter
    from data.events.businesswire import BusinessWireAdapter
    from data.events.prnewswire import PRNewswireAdapter
    from data.events.globenewswire import GlobeNewswireAdapter
    from data.events.yfinance_news import YFinanceNewsAdapter
    from data.events.ir_site import IRSiteAdapter
    from data.events.fmp_news import FMPPressReleaseAdapter
    from data.events.google_news import GoogleNewsAdapter, GoogleNewsTopicAdapter

    init_schema()

    watchlist = sorted(set(DEFAULT_WATCHLIST))
    # File-based universe (no per-ticker FDIC calls). get_universe_tickers()
    # fires a live, rate-limited cert_is_active per ticker; on a cold job FDIC
    # throttles the burst and the build alone blew the 900s task timeout before
    # any adapter ran (news froze 2026-06-12). The broad adapters name-match, so
    # the unfiltered superset is correct — same fix refresh_prices already uses.
    #
    # BUT drop non-common share classes + out-of-scope ETNs/ADRs (coverage_
    # excluded() is offline — CIK clustering + the static skip set, no network).
    # They share their registrant's CIK, so an 8-K adapter would attribute the
    # COMMON's filing to whichever sibling won the CIK race — JPMorgan's CIK
    # 19617 carries the VYLD/AMJB ETNs, which tagged JPM's 8-Ks ">VYLD". The
    # common stock still polls the same CIK and name-matches the same wire
    # releases, so nothing is lost; the sibling tickers are pure mis-tag risk.
    universe = sorted((set(get_universe().keys()) - coverage_excluded())
                      | set(DEFAULT_WATCHLIST))

    # POLL_PROFILE selects the source mix so a single job image can serve two
    # very different cadences (Cloud Scheduler passes the env override):
    #   • "fast" — sub-minute, WATCHLIST-scoped, high-signal low-latency sources
    #     only (SEC 8-K + the cheap single-feed wires + FMP press releases). It
    #     skips the slow full-universe Google News / Yahoo / IR scrape, so it's
    #     safe to schedule every 1–5 min without piling up overlapping runs.
    #   • "full" (default) — every source over the full universe; the heavy run
    #     that catches the long tail, scheduled less often (~30 min).
    profile = (os.environ.get("POLL_PROFILE") or "full").strip().lower()

    if profile == "fast":
        # FULL-universe coverage, still sub-minute. SEC 8-K comes from EDGAR's
        # recent-filings feed (SEC8KRecentAdapter) — ONE call returns every
        # bank's latest 8-Ks. FMP press releases are now batched (symbols=...),
        # so they cover the WHOLE universe in ~18 calls — broad, not watchlist.
        narrow_adapters = []
        deferred_adapters = []
        broad_adapters = [SEC8KRecentAdapter(), PRNewswireAdapter(),
                          GlobeNewswireAdapter(), FMPPressReleaseAdapter()]
    else:
        # Order matters: cheap, high-yield, RELIABLE sources first so they always
        # fit in budget. FMP (batched, ~18 calls, the broadest press-release
        # source) runs right after SEC 8-K.
        broad_adapters = [
            SEC8KAdapter(),
            # FMP press releases: batched over the universe (symbols=...), the
            # broadest first-party press-release source (BW/PRN/IR aggregated).
            FMPPressReleaseAdapter(),
            BusinessWireAdapter(),
            PRNewswireAdapter(),
            GlobeNewswireAdapter(),
            # Topic feeds for the Home page's categorized overnight news (Macro /
            # Geopolitical / Domestic / Markets) — ONE query per topic, not per-bank.
            GoogleNewsTopicAdapter(),
        ]
        narrow_adapters = [YFinanceNewsAdapter(), IRSiteAdapter()]
        # Google News is DEFERRED to dead-last — AFTER the first-party IR/Yahoo
        # narrow adapters. It's per-ticker, often 503-rate-limited from datacenter
        # IPs, and burns its full 240s cap; running it last means a task-budget
        # shortfall abandons THIS flaky third-party source, not the first-party IR
        # feed (which previously sat behind it and got starved — the CBSH gap).
        deferred_adapters = [GoogleNewsAdapter()]

    adapters = broad_adapters + narrow_adapters + deferred_adapters

    print(f"▶ Polling [{profile}] — broad: {len(broad_adapters)} sources × {len(universe)} tickers, "
          f"narrow: {len(narrow_adapters)} sources × {len(watchlist)} tickers, "
          f"deferred: {len(deferred_adapters)}")
    t0 = time.time()
    crashes = 0
    timeouts = 0
    total_new = 0
    new_events = []

    for adapter in adapters:
        # Overall wall-clock budget: stop polling new sources before the hard
        # 900s task kill so we always reach the commit/summarize tail. Events
        # commit per-adapter, so skipped sources just catch up next cycle.
        remaining = _TASK_BUDGET_S - (time.time() - t0)
        if remaining <= 0:
            print(f"  [budget] task budget reached — skipping {adapter.name} "
                  "and any remaining sources (caught up next cycle)")
            break
        # Narrow adapters (per-ticker APIs) only run against the watchlist.
        scope = watchlist if adapter in narrow_adapters else universe
        since = last_seen_published(adapter.name)
        cap = min(_PER_ADAPTER_S, remaining)
        print(f"  [{adapter.name}] scope={len(scope)} tickers since={since} cap={cap:.0f}s")
        try:
            # Hard per-adapter cap: one slow/hanging source (e.g. Google News at
            # full universe) is abandoned, never blocking past the task kill.
            events = _run_with_timeout(
                adapter.name, lambda: adapter.poll(scope, since=since), cap)
            newly = insert_events_returning_new(events)
            new_events.extend(newly)
            total_new += len(newly)
            print(f"  [{adapter.name}] {len(events)} fetched, {len(newly)} new")
        except TimeoutError:
            timeouts += 1
            print(f"  [{adapter.name}] TIMEOUT after {cap:.0f}s — abandoned, "
                  "committing the rest (catches up next cycle)")
        except Exception as e:
            crashes += 1
            print(f"  [{adapter.name}] CRASH {type(e).__name__}: {e}")
            traceback.print_exc()

    # A new 10-K/10-Q means the company's XBRL facts changed — drop the cached
    # SEC facts for those banks so the next dashboard load re-pulls fresh data
    # (new figures AND new source-document links) instead of serving up to 24h
    # of staleness. FDIC data is already fetched live on every render.
    _invalidate_fundamentals_for_filings(new_events)

    # Purge any junk that slipped in before the safety filters existed —
    # spam/social URLs (e.g. a WhatsApp-group link) and structured-note noise.
    try:
        n_purged = _purge_junk_events()
        if n_purged:
            print(f"▶ Purged {n_purged} junk events (unsafe URL / structured-note noise)")
    except Exception as e:
        print(f"  [purge] failed: {type(e).__name__}: {e}")

    # Re-clean summaries stored before the cleanup shipped — strips markdown
    # titles / "Summary:" labels and drops refusal / metadata-only answers so
    # the feed self-heals on the next poll (and any future stragglers) instead
    # of waiting for them to age out.
    try:
        n_clean = _reclean_summaries()
        if n_clean:
            print(f"▶ Re-cleaned {n_clean} noisy summaries")
    except Exception as e:
        print(f"  [reclean] failed: {type(e).__name__}: {e}")

    # Optional: LLM-summarize events with empty summaries (most recent first),
    # but only with budget left — and under a hard cap so the summarize pass
    # can't push the run past the 900s task kill on its own.
    if os.environ.get("ANTHROPIC_API_KEY"):
        sum_budget = _TASK_BUDGET_S + 60 - (time.time() - t0)
        if sum_budget < 30:
            print(f"  [summarizer] skipped — only {sum_budget:.0f}s left in budget")
        else:
            try:
                n_summarized = _run_with_timeout(
                    "summarizer", lambda: _summarize_recent_events(limit=40),
                    sum_budget)
                print(f"▶ Summarized {n_summarized} recent events via Claude")
            except TimeoutError:
                print(f"  [summarizer] hit {sum_budget:.0f}s cap — stopped "
                      "(remaining events summarize next cycle)")
            except Exception as e:
                print(f"  [summarizer] failed: {type(e).__name__}: {e}")

    # Rebuild the upcoming-earnings CALL-DETAIL snapshots from the now-fresh
    # events store — here, every ~30 min, rather than only in the heavy nightly
    # refresh-universe job (whose validation-gate exit code and image-pin churn
    # kept call data stale). Cheap (Q4 events + a handful of PR detail fetches),
    # never fails the poll, uses the IR endpoints discovered nightly.
    try:
        from data.events.ir_site import refresh_q4_calls_snapshot
        from data.earnings_call import refresh_pr_call_snapshot
        tq = time.time()
        n_q4 = len(refresh_q4_calls_snapshot())
        n_pr = len(refresh_pr_call_snapshot())
        print(f"▶ Call details refreshed — Q4 {n_q4} banks, PR {n_pr} banks "
              f"({time.time()-tq:.0f}s)", flush=True)
    except Exception as e:
        print(f"  [calls] snapshot refresh failed: {type(e).__name__}: {e}",
              flush=True)

    elapsed = time.time() - t0
    print(f"✓ Done in {elapsed:.1f}s — {total_new} new events, "
          f"{crashes} crashes, {timeouts} timeouts")
    # Success unless every adapter hard-crashed (a timeout is an expected,
    # non-fatal skip of a slow source, not a failure).
    return 0 if crashes < len(adapters) else 1


def _invalidate_fundamentals_for_filings(new_events) -> None:
    """For each newly-detected 10-K/10-Q, drop the bank's cached SEC facts so
    the next dashboard render re-pulls fresh figures + source-doc links."""
    periodic = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
    try:
        from data import cache
    except Exception:
        return
    seen = set()
    for e in new_events:
        raw = getattr(e, "raw", None) or {}
        if raw.get("form") not in periodic:
            continue
        cik = raw.get("cik")
        if cik is None or cik in seen:
            continue
        seen.add(cik)
        # Invalidate both key spellings (callers pass cik as int or str).
        for kf in (f"sec_facts:{cik}", f"sec_facts:{int(cik)}"):
            try:
                cache.invalidate(kf)
            except Exception:
                pass
        print(f"  ↻ fundamentals cache invalidated for {e.ticker} ({raw.get('form')}) "
              f"— next load re-pulls SEC facts")


def _purge_junk_events() -> int:
    """Delete events with spam/social URLs or junk headlines (structured notes,
    third-party mentions, foreign-ticker tags). Idempotent; reuses exactly the
    filters the adapters apply at ingest."""
    from sqlalchemy import text
    from data.events.store import _get_engine, TOPIC_SOURCE
    from data.events.wire_base import is_safe_news_url, is_junk_news, match_tickers
    from data.events.fmp_news import _is_subject

    # Wire/aggregator sources that assign a ticker by name-matching the headline
    # (match_tickers). Re-running the matcher purges historical mis-tags that the
    # current matcher would no longer make — e.g. "First United" tagged onto a
    # Century 21 PR before the proper-noun trap landed. Self-consistent (the
    # adapters matched on the same headline), so it only removes what wouldn't be
    # ingested today; no over-purge of legitimately-named rows.
    _NAME_MATCHED = {"businesswire", "globenewswire", "prnewswire", "google_news"}

    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, ticker, source, url, headline, summary FROM events"
        )).mappings().all()

    def _is_bad(r) -> bool:
        if not is_safe_news_url(r["url"]):
            return True
        # Topic rows have a sentinel ticker ('TOPIC:MACRO'), so the foreign-
        # paren-ticker check must not apply — pass ticker=None for those.
        tk = None if r["source"] == TOPIC_SOURCE else r["ticker"]
        if is_junk_news(r["headline"], tk):
            return True
        # FMP's symbol index mis-tags common-word banks (Popular/Freedom/Citizens)
        # onto unrelated PRs whose text never names the bank — re-apply the
        # subject guard so those get purged, not just blocked at ingest.
        if r["source"] == "fmp_news":
            blob = f"{r['headline']}. {(r['summary'] or '')[:1000]}"
            if not _is_subject(r["ticker"], blob):
                return True
        # Name-matched wire/aggregator rows: drop if the current matcher would no
        # longer tag this ticker from the same headline (a corrected mis-tag).
        if r["source"] in _NAME_MATCHED and r["ticker"]:
            matched = {t.upper() for t in match_tickers(r["headline"] or "")}
            if r["ticker"].upper() not in matched:
                return True
        return False

    bad = [r["id"] for r in rows if _is_bad(r)]
    if not bad:
        return 0
    with eng.begin() as conn:
        for _id in bad:
            conn.execute(text("DELETE FROM events WHERE id = :id"), {"id": _id})
    return len(bad)


def _reclean_summaries(days: int = 21) -> int:
    """Re-apply _clean_summary to summaries stored in the last `days`, nulling
    the markdown-title / refusal noise that pre-dates the cleanup. Idempotent:
    already-clean rows are unchanged, so it's safe to run every poll."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text as _sql
    from data.events.store import _get_engine

    eng = _get_engine()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with eng.connect() as conn:
        rows = conn.execute(_sql(
            "SELECT id, summary FROM events "
            "WHERE summary IS NOT NULL AND summary <> '' AND published_at >= :c"
        ), {"c": cutoff}).mappings().all()
    n = 0
    with eng.begin() as conn:
        for r in rows:
            cleaned = _clean_summary(r["summary"])
            if cleaned != (r["summary"] or "").strip():
                conn.execute(_sql("UPDATE events SET summary = :s WHERE id = :id"),
                             {"s": cleaned, "id": r["id"]})
                n += 1
    return n


# Any EDGAR archives URL exposes the CIK and the 18-digit accession-directory,
# whether it's an "-index.htm" page (recent-feed adapter) or a primary-document
# link (per-CIK adapter, e.g. ".../000162828026044499/pnc-20260622.htm"). Both
# resolve to the same filing, so we can find the EX-99.1 from either.
_ARCHIVE_URL_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})(?:/|$)", re.IGNORECASE)


def _resolve_8k_doc_url(url: str) -> str:
    """Resolve an EDGAR 8-K filing URL to the document that carries its substance
    so the summarizer reads real body text instead of the EDGAR metadata index
    page. Works for BOTH the recent-feed adapter's "-index.htm" URL and the
    per-CIK adapter's primary-document URL: extract the CIK + accession and ask
    find_8k_body_url for the EX-99.1 press release (earnings / Reg-FD / M&A) or,
    when there's no exhibit, the primary 8-K cover document (which holds the
    narrative for officer-change / vote / bylaw / other-event items). Falls back
    to the original URL if nothing resolves; non-EDGAR-archive URLs pass through."""
    m = _ARCHIVE_URL_RE.search(url or "")
    if not m:
        return url
    d = m.group(2)  # 18-digit accession with dashes stripped
    accession = f"{d[:10]}-{d[10:12]}-{d[12:]}"
    try:
        from data.filing_summarizer import find_8k_body_url
        body = find_8k_body_url(int(m.group(1)), accession)
    except Exception:
        body = None
    return body or url


def _primary_doc_url(url: str) -> str:
    """The 8-K's primary cover-document URL, used as a fallback body when the
    resolved exhibit is a too-thin stub (a forward-looking-statements fragment
    or an image-only press release that doesn't extract to text)."""
    m = _ARCHIVE_URL_RE.search(url or "")
    if not m:
        return ""
    d = m.group(2)
    accession = f"{d[:10]}-{d[10:12]}-{d[12:]}"
    try:
        from data.filing_summarizer import find_8k_primary_doc_url
        return find_8k_primary_doc_url(int(m.group(1)), accession) or ""
    except Exception:
        return ""


def _clean_summary(text: str) -> str:
    """Normalize a model summary to a clean 1-2 sentence string, or "" to skip.

    Haiku sometimes prefixes a markdown title ("# CFFI 8-K Summary",
    "# Summary for Bank Analyst") or a "Summary:" label, and answers a
    content-free filing with a refusal sentence — all of which leaked into the
    feed. Strip the headers/labels and drop refusals / the NONE sentinel so the
    UI falls back to the clean item-based headline instead of showing noise."""
    import re
    if not text:
        return ""
    # Drop leading markdown headers + blank lines, join the rest to one block.
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    out = " ".join(lines).strip()
    # Strip a leading "Summary[ for ...]:" / "Here's ...:" label.
    out = re.sub(r"^(summary\b[^:]*:|here'?s\b[^:]*:)\s*", "", out,
                 flags=re.IGNORECASE).strip()
    low = out.lower()
    # Drop refusal / content-free answers (model couldn't summarize because the
    # fetched text was only the filing index/metadata). Precise so it never
    # catches a real summary that happens to say "unable to <do X>".
    if not out or low == "none" or low.startswith("none") or _REFUSAL_RE.search(out):
        return ""
    # Reject extraction garbage (filing-form scaffolding, exhibit/file headers,
    # contact lines, mid-sentence fragments) — noise, not a summary. Also nulls
    # any such rows already stored, via the _reclean_summaries pass, so they fall
    # back to the clean item label (or re-summarize once Claude is back).
    if _GARBAGE_SUMMARY_RE.search(out) or out[0] in "☐•(),.;:-–—":
        return ""
    return out


# Extraction garbage that must never reach the feed AS a summary: 8-K cover-form
# scaffolding, EX-99 exhibit / document-filename headers, and leaked contact
# phone numbers. (A real summary is a sentence that starts with a name/date.)
_GARBAGE_SUMMARY_RE = re.compile(
    r"^\s*item\s+\d+\.\d{2}\b"                       # raw "Item 5.02 ..." header
    r"|\bex-?99(?:\.\d)?\b"                          # exhibit header "EX-99.1 2 ..."
    r"|\.html?\b"                                    # a document filename leaked in
    r"|pursuant\s+to\s+section\s+13\s+or\s+15\(d\)"  # 8-K cover boilerplate
    r"|\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b",       # a contact phone number
    re.IGNORECASE,
)


# Phrasings the summarizer uses when it can't summarize (the fetched 8-K text
# was just the index page / metadata). Targeted at "can't produce a summary" /
# "text not included", NOT a company being "unable to <do something>".
_REFUSAL_RE = re.compile(
    r"\bunable to (provide|summari[sz]|generate|create|produce|give)"
    r"|\bcannot (provide|summari[sz]|generate|produce)"
    r"|\bi (can'?t|cannot)\b"
    r"|\b(filing|document|text|content)\b[^.]{0,50}\bnot (included|provided|available)"
    r"|\bnot included in[^.]{0,40}(text|filing|document|provided)"
    r"|\bcontains only\b[^.]*\b(metadata|exhibit)"
    r"|\bonly (the )?(sec )?(filing )?(metadata|exhibits)"
    r"|\bno substantive (content|information|details?)"
    r"|\bprovided text (contains|is |only)",
    re.IGNORECASE,
)


def _is_auth_error(e: Exception) -> bool:
    """True for an Anthropic auth/permission rejection (invalid/revoked key) —
    distinguishes 'rotate the key' from a transient rate-limit/timeout, so the
    summarizer stops retrying Claude per-event and degrades to extractive."""
    if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError"):
        return True
    code = (getattr(e, "status_code", None)
            or getattr(getattr(e, "response", None), "status_code", None))
    return code in (401, 403)


def _summarize_recent_events(limit: int = 40, max_seconds: float = 180.0) -> int:
    """
    Backfill summaries on the most-recently-ingested events that don't have
    one yet. Uses a small Claude call per event; cheap and idempotent.

    Only SEC filings (8-K) are summarized — their item codes ("Other Events")
    are opaque so a summary adds real value, and fetch_filing_text returns the
    EX-99.1 cleanly. Wire/news headlines are already self-explanatory and their
    URLs (often redirects) fetch slowly, so we skip them. Bounded by a hard time
    budget so the summarizer can never dominate a poll run.
    """
    import time as _t
    from sqlalchemy import text
    from data.events.store import _get_engine

    eng = _get_engine()
    with eng.connect() as conn:
        # Pull a window of the most recent unsummarized 8-Ks, then re-rank so the
        # material-but-opaque ones get summarized first within the per-run cap.
        rows = conn.execute(text("""
            SELECT id, ticker, source, headline, url, raw_json
            FROM events
            WHERE (summary IS NULL OR summary = '') AND source = 'sec_8k'
            ORDER BY published_at DESC
            LIMIT :w
        """), {"w": max(limit * 3, limit)}).mappings().all()
    if not rows:
        return 0

    # Stable sort keeps the existing newest-first order within each tier, so
    # material-but-opaque filings (M&A, officer, regulatory) jump the queue.
    rows = sorted(rows, key=lambda r: 0 if _is_high_signal_8k(r["raw_json"]) else 1)
    rows = rows[:limit]

    try:
        import anthropic
        from data.filing_summarizer import fetch_filing_text
    except ImportError:
        return 0

    # Short per-call timeout — the SDK default is 600s, which would let one hung
    # call wedge the whole job.
    client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    n = 0
    auth_failed = False   # set once a 401/403 proves the key is dead this run
    deadline = _t.monotonic() + max_seconds

    for r in rows:
        if _t.monotonic() > deadline:
            print(f"    [summarizer] time budget ({max_seconds:.0f}s) reached after {n}")
            break
        try:
            # Resolve the filing URL to its body document (EX-99.1 or primary
            # cover) and fetch the text.
            doc_url = _resolve_8k_doc_url(r["url"]) if r["url"] else ""
            text_body = fetch_filing_text(doc_url) if doc_url else ""
            # Stub exhibit (e.g. a forward-looking-statements fragment or an
            # image-only release that doesn't extract): fall back to the primary
            # cover doc, which may carry the event narrative.
            if doc_url and len(text_body) < 200:
                alt = _primary_doc_url(r["url"])
                if alt and alt != doc_url:
                    alt_body = fetch_filing_text(alt)
                    if len(alt_body) > len(text_body):
                        text_body = alt_body
            if not text_body or len(text_body) < 200:
                continue
            # 8-K filings can be huge; truncate to first ~10K chars
            text_body = text_body[:10000]

            summary = ""
            if not auth_failed:
                try:
                    msg = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=300,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"You are summarizing a company news item / SEC filing for {r['ticker']}.\n"
                                f"Source: {r['source']}. Headline: {r['headline']}\n\n"
                                "In 1-2 tight sentences, summarize the substance for a bank "
                                "analyst — dollar amounts, dates, people, and impact. Skip "
                                "boilerplate, disclaimers, and forward-looking-statement language.\n\n"
                                "Reply with ONLY the summary sentences — no title, no heading, "
                                "no markdown, no bullet points, and no 'Summary:' label. If the "
                                "text has no substantive content (only filing metadata/exhibits), "
                                "reply with exactly: NONE\n\n"
                                f"TEXT:\n{text_body}"
                            ),
                        }],
                    )
                    summary = _clean_summary(
                        "".join(b.text for b in msg.content if b.type == "text"))
                except Exception as ce:
                    if _is_auth_error(ce):
                        if not auth_failed:   # LOUD, once — a dead key, not a blip
                            print("  [summarizer] ⚠️  ANTHROPIC_API_KEY REJECTED "
                                  f"({type(ce).__name__}) — the key is invalid/revoked. "
                                  "No summaries this run; rotate the 'anthropic-api-key' "
                                  "secret. 8-Ks show their item label until it's fixed.",
                                  flush=True)
                        auth_failed = True
                    else:
                        print(f"    [summarize {r['ticker']}] claude "
                              f"{type(ce).__name__}: {ce}")
            # Write ONLY a real LLM summary. When Claude is unavailable or returns
            # NONE, leave the row empty — the feed shows the clean item label,
            # which beats a garbage extractive guess (filing scaffolding, contact
            # lines, mid-sentence fragments).
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
