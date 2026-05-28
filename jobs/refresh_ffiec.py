"""
Cloud Run Job: refresh FFIEC Call Report Schedule RC-B (debt securities
by remaining maturity) for every active FDIC bank.

Runs quarterly (1st of Feb / May / Aug / Nov — ~45 days after each
quarter-end gives banks time to file). For each active bank: pull the
Call Report from FFIEC, extract the securities maturity ladder, upsert
into call_report_securities table. Bank-specific NIM repricing pace
then reads from this table instead of using the generic ~29%/yr default.

Auth: requires FFIEC_USERNAME + FFIEC_JWT_TOKEN env vars (mounted from
Google Secret Manager). If missing, the job exits early with code 2.

Exit codes:
  0  — successful ingest (≥80% of attempted banks)
  1  — partial failure (worth investigating, dashboard still works)
  2  — auth/config error (FFIEC creds missing or invalid)
"""

from __future__ import annotations
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _refresh_one(
    cert: int, rssd_id: int, period: str,
) -> tuple[int, int, str]:
    """Fetch + store one bank. Returns (cert, n_buckets_written, err)."""
    from data.ffiec_client import (
        fetch_call_report, get_securities_maturity_ladder,
    )
    from data.call_report_store import upsert_securities_ladder

    if not cert or not rssd_id:
        return cert, 0, "missing_ids"
    try:
        df = fetch_call_report(rssd_id, period)
        if df is None or df.empty:
            return cert, 0, "empty_call_report"
        ladder = get_securities_maturity_ladder(rssd_id, period, call_report_df=df)
        if ladder is None:
            return cert, 0, "no_securities_data"
        # Stash floating-loan-share derivation as None for now — would need
        # additional RC-K parsing per bank, future work.
        n = upsert_securities_ladder(cert, rssd_id, ladder, floating_loan_share=None)
        return cert, len(ladder.get("buckets", {})), "" if n else "upsert_failed"
    except Exception as e:
        return cert, 0, f"{type(e).__name__}: {str(e)[:80]}"


def main() -> int:
    import warnings; warnings.filterwarnings("ignore")

    from data.ffiec_client import is_configured, health_check, latest_reporting_period
    from data.call_report_store import init_call_report_schema
    from data.fdic_client import list_all_active_institutions

    print(f"[{time.strftime('%H:%M:%S')}] FFIEC refresh starting", flush=True)

    if not is_configured():
        print("⚠ FFIEC creds not configured (FFIEC_USERNAME / FFIEC_JWT_TOKEN).",
              flush=True)
        print("  Add them to Secret Manager and re-run.", flush=True)
        return 2

    hc = health_check()
    if not hc.get("ok"):
        print(f"⚠ FFIEC health-check failed: {hc.get('reason')}", flush=True)
        return 2
    if hc.get("warning"):
        print(f"⚠ {hc['warning']} — please rotate the FFIEC JWT", flush=True)
    if "days_until_expiry" in hc:
        print(f"  Token has {hc['days_until_expiry']:.0f} days until expiry",
              flush=True)

    init_call_report_schema()
    period = latest_reporting_period()
    print(f"[{time.strftime('%H:%M:%S')}] Using reporting period: {period}",
          flush=True)

    print(f"[{time.strftime('%H:%M:%S')}] Enumerating active FDIC banks...",
          flush=True)
    institutions = list_all_active_institutions()
    candidates = [
        i for i in institutions
        if i.get("cert") and i.get("rssd_id")
    ]
    print(f"[{time.strftime('%H:%M:%S')}] {len(institutions)} active banks, "
          f"{len(candidates)} with both CERT + RSSD", flush=True)

    if not candidates:
        print("⚠ No candidates with RSSD — institutions endpoint may be stale.",
              flush=True)
        return 1

    # Conservative concurrency: FFIEC REST API rate-limits and we don't
    # want to look hostile. 3 workers @ ~1s/call ≈ 25 min for 4,500 banks.
    t0 = time.time()
    success = 0
    no_data = 0
    errors: list[tuple[int, str]] = []
    workers = 3

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(_refresh_one, int(i["cert"]), int(i["rssd_id"]), period): i
            for i in candidates
        }
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            cert, n_buckets, err = fut.result()
            done += 1
            if err == "":
                if n_buckets > 0:
                    success += 1
                else:
                    no_data += 1
            elif err in ("empty_call_report", "no_securities_data"):
                no_data += 1
            else:
                errors.append((cert, err))
            if done % 200 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} ({elapsed:.0f}s, "
                      f"ok={success} no_data={no_data} err={len(errors)} "
                      f"~{eta:.0f}s remaining)", flush=True)

    elapsed = time.time() - t0
    print()
    print(f"✓ Done in {elapsed:.0f}s")
    print(f"  Ladders ingested:    {success}/{len(candidates)}")
    print(f"  Banks with no data:  {no_data}")
    print(f"  Errors:              {len(errors)}")

    if errors:
        print("\nError sample (first 10):")
        for cert, err in errors[:10]:
            print(f"  cert={cert} {err}")

    success_rate = success / max(1, len(candidates))
    if success_rate >= 0.80:
        return 0
    if success_rate >= 0.50:
        return 1
    return 2  # mostly broken — likely auth issue


if __name__ == "__main__":
    sys.exit(main())
