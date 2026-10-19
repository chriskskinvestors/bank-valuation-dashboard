"""Warm the universe M&A deal caches and compile the Comparable Deal
Analysis snapshot (docs/SNL-BUILD-PLAN.md §14; owner-decided 2026-07-13).

Re-walks the universe banks whose ma_history (FDIC structure + EDGAR
announcements + termination sweep — cached 7d in the shared store, which
the per-bank Transactions tabs reuse) is missing or expired, OLDEST FIRST
and at most ceil(banks/5) / 40 min per run, then compiles ONE deal-comps
snapshot (data/deal_comps.build_comps_snapshot) from every bank's cached
history at any age — the Comparable Deal Analysis tab reads it instantly.

Why the budget (2026-10-19): a cold walk is ~12s/bank (~2h for the
universe). The v15 bump cached every bank within a day, so all of them
expired together a week later and the 10-14 run hit the 7200s task
timeout mid-walk — and that re-walk re-synchronized them. Capping the
re-walk spreads the entries over ~5 daily cohorts that never re-merge; an
expired entry keeps serving until its turn (a bank's deals never drop off
to stay under budget). While banks with NO cached history at all remain
unreached (a key bump, see data/ma_history._history_key) the snapshot is
not recompiled — the served one holds and only the pending pass runs —
until the backlog drains over the following runs.

A per-bank failure logs and continues; the job fails (rc 1) only when the
final snapshot build itself refuses to cache (lookup failures mid-compile)
or coverage is implausibly thin.

``python -m jobs.refresh_deal_comps pending`` runs the PENDING fast pass
instead (owner 2026-10-06): only the pending leg per bank, spliced into
the served snapshot (data/deal_comps.refresh_pending_snapshot) — minutes,
not hours, so a same-day announcement reaches the board the same day.
Dispatch: run-job.yml with args "-m,jobs.refresh_deal_comps,pending".

Cloud Run job: refresh-deal-comps (in deploy.yml's image-sync loop).
Schedule nightly AFTER refresh-universe (e.g. 7:30am ET) via Cloud
Scheduler — remember the scheduler-invoker run.invoker binding.
"""
import sys
import time

# A full-universe walk yielding fewer deals than this means most banks failed
# in a way no single lookup reported — treated as a failed run, never cached.
_MIN_UNIVERSE_DEALS = 100

# Per-run re-walk budget: at most ceil(banks / _WARM_SHARE) banks and
# _WARM_BUDGET_S of walking (~12s/bank cold: ~25 min for 595 banks / 5).
_WARM_SHARE = 5
_WARM_BUDGET_S = 40 * 60


def _universe_banks() -> list[dict]:
    from data.bank_mapping import get_cik, get_fdic_cert, get_name
    from data.bank_universe import get_universe_tickers
    banks = []
    for t in sorted(get_universe_tickers()):
        cert = get_fdic_cert(t)
        if not cert:
            continue
        banks.append({"ticker": t, "name": get_name(t) or t,
                      "cert": int(cert), "cik": get_cik(t)})
    return banks


def _refresh_spreads(snap: dict) -> None:
    """Rebuild the spread-tracker history from the snapshot just written
    (data/deal_spreads). A failure here never fails the snapshot job."""
    try:
        from data.deal_spreads import refresh_spread_histories
        built = refresh_spread_histories(snap)
        if built is not None:
            print(f"✓ spread tracker: {len(built['deals'])} deals charted, "
                  f"{len(built['skipped'])} not chartable", flush=True)
    except Exception as e:
        print(f"✗ spread tracker build failed: {type(e).__name__}: {e}", flush=True)


def main_pending(banks: list[dict] | None = None) -> int:
    from data.deal_comps import refresh_pending_snapshot
    banks = banks or _universe_banks()
    print(f"▶ Pending fast pass over {len(banks)} banks", flush=True)
    t0 = time.time()
    snap = refresh_pending_snapshot(banks)
    if not snap:
        print("✗ pending pass wrote nothing — the previous snapshot still "
              "serves", flush=True)
        return 1
    _refresh_spreads(snap)
    pending = sum(1 for r in snap["deals"] if r.get("status") == "pending")
    print(f"✓ pending pass: {pending} pending deals spliced into the "
          f"{snap['deals_total']}-deal snapshot in {time.time() - t0:.0f}s "
          f"({len(snap.get('pending_banks_failed') or [])} banks kept their "
          "previous pending rows)", flush=True)
    return 0


def _due_oldest_first(banks: list[dict]) -> list[tuple]:
    """[(cached_at | None, bank)] for every bank whose history is missing or
    past the TTL — missing first, then oldest cached first."""
    from datetime import datetime, timedelta
    from data.ma_history import CACHE_TTL_SECONDS, history_cached_at
    expired = datetime.now() - timedelta(seconds=CACHE_TTL_SECONDS)
    due = []
    for b in banks:
        at = history_cached_at(b["cert"], b["cik"])
        if at is None or at <= expired:
            due.append((at, b))
    return sorted(due, key=lambda d: d[0] or datetime.min)


def main() -> int:
    from data.deal_comps import build_comps_snapshot
    from data.ma_history import get_ma_history

    banks = _universe_banks()
    due = _due_oldest_first(banks)
    cap = -(-len(banks) // _WARM_SHARE)
    cold = sum(1 for at, _ in due if at is None)
    print(f"▶ Re-walking deal history: {len(due)} of {len(banks)} banks due "
          f"({cold} never cached); this run walks up to {cap} oldest-first "
          f"within {_WARM_BUDGET_S}s", flush=True)

    t0 = time.time()
    walked = failed = 0
    for at, b in due[:cap]:
        if time.time() - t0 >= _WARM_BUDGET_S:
            print(f"  ⏱ warm budget spent after {walked} banks", flush=True)
            break
        walked += 1
        try:
            get_ma_history(b["cert"], cik=b["cik"], name=b["name"],
                           ticker=b["ticker"])
        except Exception as e:
            failed += 1
            print(f"  ✗ {b['ticker']}: {type(e).__name__}: {e}", flush=True)
        if walked % 25 == 0:
            print(f"  … {walked}/{min(cap, len(due))} banks "
                  f"({time.time() - t0:.0f}s)", flush=True)

    unreached = due[walked:]
    cold_left = [b["ticker"] for at, b in unreached if at is None]
    print(f"▶ Re-walked {walked} ({failed} errored) in {time.time() - t0:.0f}s; "
          f"{len(unreached)} still due serve their cached history "
          f"({len(cold_left)} have none)", flush=True)
    if cold_left:
        # Compiling now would drop these banks' deals from the board; the
        # served snapshot holds until the backlog drains (a key bump).
        print(f"▶ {len(cold_left)} banks never cached yet "
              f"({', '.join(cold_left[:10])}{' …' if len(cold_left) > 10 else ''})"
              " — snapshot NOT recompiled this run; previous snapshot serves, "
              "pending rows refreshed", flush=True)
        return main_pending(banks)

    print(f"▶ Compiling comps snapshot ({time.time() - t0:.0f}s)", flush=True)
    # The <100-deal plausibility floor is passed INTO the build so it gates the
    # cache.put. It used to be checked here, after the fact — by which point the
    # hollow snapshot had already replaced the last good one and the UI served
    # it (AUDIT-2026-07-27 P2), making the "previous snapshot still serves"
    # message below untrue for exactly the case it was describing.
    snap = build_comps_snapshot(banks, min_deals=_MIN_UNIVERSE_DEALS,
                                allow_stale=True)
    if not snap:
        print("✗ snapshot build refused to cache (lookup failures, or a walk "
              f"yielding <{_MIN_UNIVERSE_DEALS} deals) — the previous snapshot "
              "still serves", flush=True)
        return 1
    print(f"✓ snapshot: {snap['deals_total']} deals "
          f"({snap['deals_priced']} priced) across "
          f"{snap['banks_covered']} banks in {time.time() - t0:.0f}s",
          flush=True)
    # The walk's PENDING rows come from the per-bank ma_history cache, which
    # can predate today's code: the 2026-10-08 walk put Bank of Hawaii, HBT
    # "acquiring" Tri-County at 0.28x and other long-fixed rows back on the
    # board. The pending pass recomputes every bank's pending leg with the
    # current code and splices it in (a bank whose leg fails keeps the walk's
    # row, as in any pending pass).
    from data.deal_comps import refresh_pending_snapshot
    fresh = refresh_pending_snapshot(banks)
    if fresh:
        snap = fresh
        print(f"✓ pending rows recomputed ({len(fresh.get('pending_banks_failed') or [])} "
              "banks kept the walk's rows)", flush=True)
    else:
        print("✗ pending recompute wrote nothing — the walk's pending rows serve",
              flush=True)
    _refresh_spreads(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main_pending() if "pending" in sys.argv[1:] else main())
