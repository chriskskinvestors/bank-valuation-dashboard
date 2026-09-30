"""
Cloud Run Job: refresh the universe's Form 4 insider cache.

The Home news feed shows recent OPEN-MARKET insider buys/sells across all
covered banks (form4_client.recent_open_market_transactions), which reads
ONLY cached Form 4 JSON — it never fetches live on render. This nightly job
populates that cache for the whole universe so the feed has real coverage,
not just the handful of banks a user happened to open.

Form 4 fetching is heavy (per bank: 1 submissions call + up to ~30 filing
XMLs), so the loop runs sequentially and politely. Calls are force=True: the
render path serves a per-CIK file for 4 days, so an unforced call would skip a
file written yesterday (and a 24h check flipped on run-to-run jitter).

Exit codes:
  0  — refreshed/confirmed-fresh ≥70% of resolvable CIKs
  1  — partial (feed still reads last good cache)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# A file this young was written by this run (or a failed attempt earlier today):
# skip it so a retry or manual rerun resumes instead of recrawling everything.
# Yesterday's 04:30 run is ~24h old, so it is always refreshed. The intraday
# firehose keeps a file's existing cached_at when it merges, so it never makes
# the job skip a bank it should sweep.
_RESUME_WINDOW_S = 12 * 3600


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    from data.cloud_storage import load_json
    from data.form4_client import FORM4_CACHE_PREFIX, fetch_insider_trades
    from data.freshness import is_fresh
    from data.bank_mapping import get_cik
    from data.bank_universe import get_universe
    from config import DEFAULT_WATCHLIST

    print(f"[{time.strftime('%H:%M:%S')}] insider (Form 4) refresh starting", flush=True)

    tickers = sorted(set(get_universe().keys()) | set(DEFAULT_WATCHLIST))
    ciks = {t: get_cik(t) for t in tickers}
    resolvable = {t: c for t, c in ciks.items() if c}
    print(f"[{time.strftime('%H:%M:%S')}] {len(resolvable)} CIKs to refresh "
          f"(of {len(tickers)} tickers)...", flush=True)

    t0 = time.time()
    ok = 0
    for i, (ticker, cik) in enumerate(resolvable.items()):
        done = load_json(FORM4_CACHE_PREFIX, f"{cik}.json")
        if is_fresh(done, _RESUME_WINDOW_S) and "transactions" in done:
            ok += 1
            continue
        try:
            # Populates/refreshes the per-CIK cloud cache as a side effect;
            # returns [] cleanly on any SEC error (already logged inside).
            fetch_insider_trades(cik, force=True)
            ok += 1
        except Exception as e:
            print(f"⚠ {ticker} (CIK {cik}) failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(0.3)  # be polite to SEC across the universe
        if (i + 1) % 50 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {i + 1}/{len(resolvable)}...", flush=True)

    elapsed = time.time() - t0
    coverage = ok / max(1, len(resolvable))
    print(f"[{time.strftime('%H:%M:%S')}] done in {elapsed:.0f}s — "
          f"{ok}/{len(resolvable)} refreshed ({coverage*100:.0f}%)", flush=True)
    return 0 if coverage >= 0.70 else 1


if __name__ == "__main__":
    sys.exit(main())
