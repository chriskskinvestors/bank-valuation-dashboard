"""
Cloud Run Job: refresh Summary-of-Deposits branch data for the universe.

Runs nightly. Walks every bank in the universe (+ default watchlist),
fetches SOD branches per cert, upserts into the `branches` table.

The table powers the new Geographic UI view (multi-bank map + state/MSA
lookup). It's read-only at request time so the dashboard renders fast
regardless of FDIC API health.

Exit code: 0 if at least one bank was successfully upserted, 1 if every
bank failed (which would indicate an FDIC outage worth alerting on).
"""

from __future__ import annotations
import sys
import time
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _refresh_one(ticker: str) -> tuple[str, int, str]:
    """Refresh SOD for one ticker. Returns (ticker, n_branches, err)."""
    from data.bank_mapping import get_fdic_cert
    from data.sod_client import fetch_branches
    from data.branches_store import upsert_branches

    cert = get_fdic_cert(ticker)
    if not cert:
        return ticker, 0, "no_cert"
    try:
        df = fetch_branches(cert)
        n = upsert_branches(ticker, cert, df)
        return ticker, n, ""
    except Exception as e:
        return ticker, 0, f"{type(e).__name__}: {e}"


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST
    from data.branches_store import init_branches_schema

    init_branches_schema()
    tickers = sorted(set(get_universe_tickers()) | set(DEFAULT_WATCHLIST))
    print(f"▶ Refreshing SOD for {len(tickers)} banks (workers=4 to respect FDIC rate limit)")

    t0 = time.time()
    no_cert = 0
    errors: list[tuple[str, str]] = []
    total_rows = 0
    success = 0

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_refresh_one, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            t, n, err = fut.result()
            done += 1
            if err == "no_cert":
                no_cert += 1
            elif err:
                errors.append((t, err))
            elif n > 0:
                success += 1
                total_rows += n
            if done % 25 == 0:
                print(f"  {done}/{len(tickers)} ({time.time()-t0:.0f}s, "
                      f"ok={success} no_cert={no_cert} err={len(errors)})")

    elapsed = time.time() - t0
    print()
    print(f"✓ Done in {elapsed:.0f}s")
    print(f"  Banks with branches: {success}/{len(tickers)}")
    print(f"  No cert (foreign):   {no_cert}")
    print(f"  Errors:              {len(errors)}")
    print(f"  Total branch rows upserted: {total_rows:,}")

    if errors:
        print("\n  Error sample:")
        for t, err in errors[:10]:
            print(f"    {t:<6} {err[:80]}")

    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
