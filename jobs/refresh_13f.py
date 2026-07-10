"""Cloud Run Job: warm the universe's 13F holder snapshots (QUARTERLY cadence).

The Ownership section's Holder History and Crossholdings sub-tabs read the
quarter-keyed 13F snapshots ({TICKER}_{YYYYQn}.json) that
fetch_institutional_holdings persists as a side effect. Without a warm pass,
coverage only grows when someone opens each bank's 13F tab — so History
matrices stay sparse and the Crossholdings cross-join sees few banks. This job
walks the whole universe once so every bank's current-quarter snapshot exists.

13F-HRs are filed up to 45 days AFTER quarter-end, so the natural schedule is
quarterly, ~5 days after each deadline (≈ Feb 19 / May 20 / Aug 19 / Nov 19).
Running it more often is harmless (the per-ticker 24h cache makes reruns
idempotent) but pointless between filing seasons.

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

import sys
import time


def main() -> int:
    from data.bank_universe import get_universe_tickers
    from data.bank_mapping import get_name
    from data.form13f_client import fetch_institutional_holdings

    tickers = sorted(get_universe_tickers())
    print(f"▶ Warming 13F snapshots for {len(tickers)} banks", flush=True)

    t0 = time.time()
    covered = failed = 0
    for i, t in enumerate(tickers, 1):
        try:
            holders = fetch_institutional_holdings(t, get_name(t) or "")
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

    print(f"✓ 13F warm pass: {covered}/{len(tickers)} banks with holders, "
          f"{failed} errors in {time.time() - t0:.0f}s", flush=True)
    if covered == 0:
        # Zero coverage across the whole universe is an outage, not sparse
        # small-bank coverage — fail loudly so the #42 alert pages.
        print("✗ zero banks yielded holders — EDGAR/auth outage?", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
