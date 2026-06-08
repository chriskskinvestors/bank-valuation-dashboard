"""
Cloud Run Job: independent ground-truth metric verification.

Re-derives every key valuation/profitability metric for the full bank
universe directly from primary FDIC/SEC fields and diffs it against what
the dashboard computes. Any divergence beyond tolerance is reported and
makes the job exit non-zero (so a scheduler / alert can catch regressions
like a unit slip or a wrong field mapping the moment they appear).

The actual logic lives in tools/verify_metrics.py — this is just the
universe-wide entrypoint for scheduling.

Exit codes:
  0  — every metric within tolerance across the universe
  1  — one or more banks diverged (investigate the CSV / logs)
  2  — harness error
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    from tools.verify_metrics import run
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    tickers = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    try:
        return run(tickers)
    except Exception as e:
        import traceback
        print(f"[FATAL] {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
