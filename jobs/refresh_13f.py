"""Cloud Run Job: warm the universe's 13F holder snapshots (QUARTERLY cadence).

The Ownership section's Holder History and Crossholdings sub-tabs read the
quarter-keyed 13F snapshots ({TICKER}_{YYYYQn}.json) that
fetch_institutional_holdings persists as a side effect. Without a warm pass,
coverage only grows when someone opens each bank's 13F tab — so History
matrices stay sparse and the Crossholdings cross-join sees few banks. This job
walks the whole universe once so every bank's current-quarter snapshot exists.

13F-HRs are filed up to 45 days AFTER quarter-end, so the natural schedule is
quarterly, ~5 days after each deadline (≈ Feb 19 / May 20 / Aug 19 / Nov 19).
Running it more often is harmless but pointless between filing seasons. Each
call is force=True: the render path serves the per-ticker file for 7 days, so an
unforced call would hand a fresh file back instead of refreshing it.

Cost: ~1 EDGAR full-text search + up to ~25 filer info-table fetches (plus
prior-quarter lookups for QoQ change status) per bank — tens of thousands of
SEC requests universe-wide. The shared data/http retry policy paces individual
calls; the inter-ticker sleep below keeps the aggregate under SEC's guidance.
Create the Cloud Run job with a LONG task timeout (e.g. --task-timeout=5400);
the default 900s will not fit a full pass (the refresh-capital lesson).

Exit codes:
  0 — pass completed; per-ticker failures are tolerated (small banks with no
      13F coverage are NORMAL, not errors)
  1 — catastrophic: zero banks yielded holders (auth/network/EDGAR outage) —
      pages via the job-failure alert
"""
from __future__ import annotations

import argparse
import sys
import time


def backfill(quarters: list[str]) -> int:
    """EDGAR 13F backfill (plan §13 phase 2): one pass over the universe per
    past quarter, searching that quarter's own filing season and persisting
    through the merge-only snapshot writer. Idempotent + resumable: quarters
    a ticker already has are skipped, so re-running after a timeout only
    does the remaining work. Fire with an args override, one or more
    quarters, oldest last (each quarter ≈ a full warm pass — size the
    execution accordingly, e.g. one quarter per execution):

      gcloud.cmd run jobs execute refresh-13f --region=us-central1 --args="-m,jobs.refresh_13f,--backfill,2026Q1"
    """
    from data.bank_universe import get_universe_tickers
    from data.bank_mapping import get_name
    from data.form13f_client import backfill_quarter

    tickers = sorted(get_universe_tickers())
    t0 = time.time()
    grand_found = grand_skipped = 0
    for quarter in quarters:
        found = skipped = failed = 0
        print(f"▶ Backfilling {quarter} 13F snapshots for {len(tickers)} banks",
              flush=True)
        for i, t in enumerate(tickers, 1):
            try:
                n = backfill_quarter(t, get_name(t) or "", quarter)
                if n is None:
                    skipped += 1
                elif n > 0:
                    found += 1
            except Exception as e:
                failed += 1
                print(f"  {t} {quarter}: {type(e).__name__}: {str(e)[:80]}",
                      flush=True)
            if i % 25 == 0:
                print(f"  {quarter} {i}/{len(tickers)} — {found} with holders, "
                      f"{skipped} already stored, {failed} errors, "
                      f"{time.time() - t0:.0f}s", flush=True)
            time.sleep(0.3)  # same EDGAR pacing as the warm pass
        print(f"✓ {quarter}: {found} banks backfilled, {skipped} already "
              f"stored, {failed} errors", flush=True)
        grand_found += found
        grand_skipped += skipped
    from data.form13f_client import SEARCH_FAILURES
    print(f"✓ Backfill done in {time.time() - t0:.0f}s — "
          f"{SEARCH_FAILURES[0]} failed EDGAR searches", flush=True)
    # A failed search leaves that quarter EMPTY (re-runnable: merge-only skips
    # only stored quarters); >10% failing is an outage — fail so it pages.
    if SEARCH_FAILURES[0] > 0.10 * len(tickers) * len(quarters):
        print("✗ widespread EDGAR search failures — re-run the backfill", flush=True)
        return 1
    if grand_found == 0 and grand_skipped < len(tickers) * len(quarters) * 0.5:
        # Nothing found AND little was pre-stored — outage, not completion.
        print("✗ backfill yielded nothing — EDGAR outage?", flush=True)
        return 1
    return 0


# A file this young was written by this execution (or its failed first
# attempt): skip it, so the Cloud Run retry after a 5400s timeout resumes
# where the first attempt stopped instead of recrawling from the top and
# timing out at the same point. force=True applies to everything older.
_RESUME_WINDOW_S = 12 * 3600


def main() -> int:
    from data.bank_universe import get_universe_tickers
    from data.bank_mapping import get_name
    from data.cloud_storage import load_json
    from data.form13f_client import FORM13F_CACHE_PREFIX, fetch_institutional_holdings
    from data.freshness import is_fresh

    tickers = sorted(get_universe_tickers())
    print(f"▶ Warming 13F snapshots for {len(tickers)} banks", flush=True)

    t0 = time.time()
    covered = failed = resumed = 0
    for i, t in enumerate(tickers, 1):
        done = load_json(FORM13F_CACHE_PREFIX, f"{t.upper()}.json")
        if is_fresh(done, _RESUME_WINDOW_S) and "holders" in done:
            resumed += 1
            covered += bool(done["holders"])
            continue
        try:
            holders = fetch_institutional_holdings(t, get_name(t) or "",
                                                   force=True)
            if holders:
                covered += 1
        except Exception as e:
            failed += 1
            print(f"  {t}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        if i % 25 == 0:
            print(f"  {i}/{len(tickers)} — {covered} with holders, "
                  f"{failed} errors, {time.time() - t0:.0f}s", flush=True)
        # Aggregate pacing on top of per-call retry pacing: EDGAR full-text
        # search is the sensitive endpoint; stay well under SEC guidance.
        time.sleep(0.3)

    from data.form13f_client import SEARCH_FAILURES
    print(f"✓ 13F warm pass: {covered}/{len(tickers)} banks with holders, "
          f"{failed} errors, {SEARCH_FAILURES[0]} failed EDGAR searches, "
          f"{resumed} already done this run, in {time.time() - t0:.0f}s", flush=True)
    # A failed search reads as "no holders" for that bank; more than 10% of
    # the universe failing is an EDGAR outage, not sparse coverage — fail the
    # pass so it pages and gets re-run (2026-10-04: 65/597, exit 0).
    if SEARCH_FAILURES[0] > 0.10 * len(tickers):
        print(f"✗ {SEARCH_FAILURES[0]} EDGAR full-text searches failed — outage; "
              "re-run the job", flush=True)
        return 1
    if covered == 0:
        # Zero coverage across the whole universe is an outage, not sparse
        # small-bank coverage — fail loudly so the #42 alert pages.
        print("✗ zero banks yielded holders — EDGAR/auth outage?", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", default=None,
                        help="comma-separated past quarters, e.g. 2026Q1,2025Q4")
    args = parser.parse_args()
    if args.backfill:
        sys.exit(backfill([q.strip().upper()
                           for q in args.backfill.split(",") if q.strip()]))
    sys.exit(main())
