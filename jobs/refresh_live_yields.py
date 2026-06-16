"""
Cloud Run Job: warm the live Treasury-yield snapshot.

The Home Rates pane reads live intraday yields (yfinance CBOE indices +
the 2Y yield future) from a persisted snapshot — READ-ONLY at render, so
the page never makes a network call for rates. This job does the single
yfinance fetch and persists the snapshot; run it every ~2 min during US
market hours via Cloud Scheduler. If it stalls, the pane falls back to
FRED daily (a stale yield is never shown as live).

Exit codes:
  0  — wrote ≥4 of the 5 tenors
  1  — partial (pane falls back to FRED for the missing tenors)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    from data import live_rates

    print(f"[{time.strftime('%H:%M:%S')}] live-yields refresh starting", flush=True)
    data = live_rates.refresh()
    got = sum(1 for v in (data or {}).values() if v and v[0] is not None)
    total = len(live_rates.LIVE_YIELD_SYMBOLS)
    print(f"[{time.strftime('%H:%M:%S')}] wrote {got}/{total} live tenors", flush=True)
    return 0 if got >= 4 else 1


if __name__ == "__main__":
    sys.exit(main())
