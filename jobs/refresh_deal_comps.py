"""Warm the universe M&A deal caches and compile the Comparable Deal
Analysis snapshot (docs/SNL-BUILD-PLAN.md §14; owner-decided 2026-07-13).

Walks every universe bank building its ma_history (FDIC structure + EDGAR
announcements + termination sweep — all cached 7d in the shared store, so
re-runs and the per-bank Transactions tabs reuse the same entries), then
compiles ONE deal-comps snapshot (data/deal_comps.build_comps_snapshot)
that the Comparable Deal Analysis tab reads instantly.

EDGAR-paced (the data modules sleep between requests); a full cold walk
takes on the order of an hour or two, warm re-runs minutes. A per-bank
failure logs and continues — the snapshot compiles from whatever warmed;
the job fails (rc 1) only when the final snapshot build itself refuses to
cache (lookup failures mid-compile) or coverage is implausibly thin.

Cloud Run job: refresh-deal-comps (in deploy.yml's image-sync loop).
Schedule nightly AFTER refresh-universe (e.g. 7:30am ET) via Cloud
Scheduler — remember the scheduler-invoker run.invoker binding.
"""
import sys
import time


def main() -> int:
    from data.bank_mapping import get_cik, get_fdic_cert, get_name
    from data.bank_universe import get_universe_tickers
    from data.deal_comps import build_comps_snapshot
    from data.ma_history import get_ma_history

    tickers = sorted(get_universe_tickers())
    banks = []
    for t in tickers:
        cert = get_fdic_cert(t)
        if not cert:
            continue
        banks.append({"ticker": t, "name": get_name(t) or t,
                      "cert": int(cert), "cik": get_cik(t)})
    print(f"▶ Warming deal history for {len(banks)} banks", flush=True)

    t0 = time.time()
    warmed = failed = 0
    for i, b in enumerate(banks, 1):
        try:
            deals = get_ma_history(b["cert"], cik=b["cik"], name=b["name"])
            warmed += 1 if deals is not None else 0
        except Exception as e:
            failed += 1
            print(f"  ✗ {b['ticker']}: {type(e).__name__}: {e}", flush=True)
        if i % 25 == 0:
            print(f"  … {i}/{len(banks)} banks ({time.time() - t0:.0f}s)",
                  flush=True)

    print(f"▶ Compiling comps snapshot ({warmed} warmed, {failed} errored, "
          f"{time.time() - t0:.0f}s)", flush=True)
    snap = build_comps_snapshot(banks)
    if not snap:
        print("✗ snapshot build refused to cache (lookup failures) — the "
              "previous snapshot still serves", flush=True)
        return 1
    print(f"✓ snapshot: {snap['deals_total']} deals "
          f"({snap['deals_priced']} priced) across "
          f"{snap['banks_covered']} banks in {time.time() - t0:.0f}s",
          flush=True)
    if snap["deals_total"] < 100:
        # A universe walk yielding under 100 deals means most banks failed
        # silently — don't let a hollow snapshot look like success.
        print("✗ implausibly thin snapshot (<100 deals) — failing the job",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
