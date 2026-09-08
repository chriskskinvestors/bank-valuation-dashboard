"""Backfill + nightly append for the deep FDIC history store.

DEEP-HISTORY-PLAN.md foundation. Two modes:

  python -m jobs.backfill_fdic_history backfill      # one-time, resumable
  python -m jobs.backfill_fdic_history incremental   # nightly append

BACKFILL walks every cert in every universe ticker's charter group and
pulls its full call-report history (limit=160 quarters reaches past 1992)
in ONE API call per cert, paced under FDIC courtesy limits. Resumable by
checkpoint: a cert whose stored min_repdte is already ≤ DEEP_ENOUGH is
skipped, so a rerun continues where a kill left off.

INCREMENTAL fetches only a handful of recent quarters per cert and
upserts — a no-op most nights, one new row per cert each quarter-end.

Exit codes: 0 healthy coverage, 1 degraded (logged), 2 hard failure.
"""
from __future__ import annotations

import sys
import time

# Reaches comfortably past 1992 (135 quarters 1992Q1→2026); headroom for
# banks with pre-1992 records the API happens to carry.
_BACKFILL_LIMIT = 160
_DEEP_ENOUGH = "19940101"   # stored min ≤ this ⇒ cert already backfilled
_INCR_LIMIT = 6             # incremental: last ~18 months, idempotent
_PACE_S = 0.25              # ~4 certs/s ceiling against the FDIC API


def _all_certs() -> list[int]:
    """Every cert in every universe ticker's charter group (the
    fetch_group_history seam's own membership — multi-charter complete)."""
    from data.bank_universe import get_universe
    from data.cert_group import get_cert_group
    certs: set[int] = set()
    for ticker in sorted(get_universe().keys()):
        try:
            certs.update(get_cert_group(ticker) or [])
        except Exception as e:
            print(f"[deep-hist] {ticker}: cert group failed: "
                  f"{type(e).__name__}: {e}", flush=True)
    return sorted(certs)


def main(mode: str = "incremental") -> int:
    from data import fdic_client
    from data.fdic_history_store import (init_history_schema, min_repdte,
                                         store_counts, upsert_history)

    t0 = time.time()
    init_history_schema()
    certs = _all_certs()
    if not certs:
        print("[deep-hist] no certs resolved — universe snapshot missing?",
              flush=True)
        return 2
    limit = _BACKFILL_LIMIT if mode == "backfill" else _INCR_LIMIT
    print(f"[deep-hist] {mode}: {len(certs)} certs, limit={limit}",
          flush=True)

    done = skipped = failed = rows = 0
    for cert in certs:
        if mode == "backfill":
            oldest = min_repdte(cert)
            if oldest is not None and oldest <= _DEEP_ENOUGH:
                skipped += 1
                continue
        try:
            df = fdic_client.fetch_financials(cert, limit=limit)
            recs = [] if df is None or df.empty else df.to_dict("records")
            n = upsert_history(cert, recs)
            rows += n
            done += 1
            if mode == "backfill" and done % 50 == 0:
                print(f"[deep-hist] {done} fetched / {skipped} skipped / "
                      f"{failed} failed · {rows} rows · "
                      f"{time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            failed += 1
            print(f"[deep-hist] cert {cert}: {type(e).__name__}: {e}",
                  flush=True)
        time.sleep(_PACE_S)

    n_certs, n_rows = store_counts()
    print(f"[deep-hist] {mode} done in {time.time()-t0:.0f}s — "
          f"{done} fetched, {skipped} skipped, {failed} failed; store now "
          f"{n_certs} certs / {n_rows} rows", flush=True)
    if failed and failed >= max(5, len(certs) // 20):
        print(f"[deep-hist] degraded: {failed}/{len(certs)} certs failed",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "incremental"))
