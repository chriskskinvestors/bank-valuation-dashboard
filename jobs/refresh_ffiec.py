"""
Cloud Run Job: refresh FFIEC Call Report Schedule RC-B (debt securities
by remaining maturity) for every active FDIC bank.

Runs quarterly (1st of Feb / May / Aug / Nov — ~45 days after each
quarter-end gives banks time to file). For each active bank: pull the
Call Report from FFIEC, extract the securities maturity ladder, upsert
into call_report_securities table. Bank-specific NIM repricing pace
then reads from this table instead of using the generic ~29%/yr default.
The same fetched call report also yields Schedule RI income detail
(SNL Income Statement rows), upserted into ri_income_detail per
bank-quarter, and Schedule RC-N past-due/nonaccrual loan detail
(SNL Asset Quality by Loan Type matrix), upserted into rcn_detail —
RI/RC-N failures are logged but never fatal to the job.

Auth: requires FFIEC_USERNAME + FFIEC_JWT_TOKEN env vars (mounted from
Google Secret Manager). If missing, the job exits early with code 2.

Rate limit: FFIEC PWS allows ~2,500 requests/hour per user. With ~4,500
active banks the job needs ≥2 hourly windows. We pace ourselves at 2,400
requests/hour (small safety margin) and sleep when we hit the cap.

Exit codes:
  0  — successful ingest (≥80% of attempted banks)
  1  — partial failure (worth investigating, dashboard still works)
  2  — auth/config error (FFIEC creds missing or invalid)
"""

from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stay just below FFIEC's 2,500/hr cap to avoid 429s killing the long run.
_HOURLY_REQUEST_BUDGET = 2400


def _persist_ri_detail(cert: int, rssd_id: int, period: str, df) -> str:
    """
    Extract + store Schedule RI income detail from an already-fetched
    Call Report (no extra HTTP call). Returns 'ok', 'no_data', or
    'fail:<reason>' — never raises (RI failures must not affect the
    ladder accounting or kill the job).
    """
    from data.ffiec_client import get_ri_income_detail
    from data.call_report_store import upsert_ri_income_detail
    try:
        detail = get_ri_income_detail(rssd_id, period, call_report_df=df)
        if detail is None:
            return "no_data"
        # Every RI code absent (even total interest income / net income,
        # which every filer reports) means the parse produced nothing —
        # don't store a row of all-Nones as if it were real data.
        codes = [k for k in detail
                 if k not in ("reporting_period", "rssd_id")
                 and not k.endswith("_usd")]
        if all(detail.get(k) is None for k in codes):
            return "no_data"
        n = upsert_ri_income_detail(cert, rssd_id, detail)
        return "ok" if n else "fail:upsert_failed"
    except Exception as e:
        return f"fail:{type(e).__name__}: {str(e)[:80]}"


def _persist_rcn_detail(cert: int, rssd_id: int, period: str, df) -> str:
    """
    Extract + store Schedule RC-N past-due/nonaccrual loan detail from an
    already-fetched Call Report (no extra HTTP call). Returns 'ok',
    'no_data', or 'fail:<reason>' — never raises (RC-N failures must not
    affect the ladder accounting or kill the job).
    """
    from data.ffiec_client import get_rcn_detail
    from data.call_report_store import upsert_rcn_detail
    try:
        # get_rcn_detail returns None (not an all-None matrix) when the
        # report has no RC-N content, so no extra emptiness check here.
        detail = get_rcn_detail(rssd_id, period, call_report_df=df)
        if detail is None:
            return "no_data"
        n = upsert_rcn_detail(cert, rssd_id, detail)
        return "ok" if n else "fail:upsert_failed"
    except Exception as e:
        return f"fail:{type(e).__name__}: {str(e)[:80]}"


def _persist_rcr_detail(cert: int, rssd_id: int, period: str, df) -> str:
    """Extract + store the Schedule RC-R Part I capital walk from an
    already-fetched Call Report. 'ok' / 'no_data' / 'fail:<reason>';
    never raises."""
    from data.ffiec_client import get_rcr_capital_detail
    from data.call_report_store import upsert_rcr_detail
    try:
        # get_rcr_capital_detail returns None (not all-Nones) when the
        # report has no RC-R Part I content.
        detail = get_rcr_capital_detail(rssd_id, period, call_report_df=df)
        if detail is None:
            return "no_data"
        n = upsert_rcr_detail(cert, rssd_id, detail)
        return "ok" if n else "fail:upsert_failed"
    except Exception as e:
        return f"fail:{type(e).__name__}: {str(e)[:80]}"


def _persist_rie_detail(cert: int, rssd_id: int, period: str, df) -> str:
    """Extract + store Schedule RI-E itemizations from an already-fetched
    Call Report. 'ok' / 'no_data' / 'fail:<reason>'; never raises."""
    from data.ffiec_client import get_ri_e_detail
    from data.call_report_store import upsert_rie_detail
    try:
        # get_ri_e_detail returns None when no line was itemized at all.
        detail = get_ri_e_detail(rssd_id, period, call_report_df=df)
        if detail is None:
            return "no_data"
        n = upsert_rie_detail(cert, rssd_id, detail)
        return "ok" if n else "fail:upsert_failed"
    except Exception as e:
        return f"fail:{type(e).__name__}: {str(e)[:80]}"


def _refresh_one(
    cert: int, rssd_id: int, period: str,
) -> tuple[int, int, str, str, str]:
    """Fetch + store one bank.
    Returns (cert, n_buckets_written, err, ri_status, rcn_status)."""
    from data.ffiec_client import (
        fetch_call_report, get_securities_maturity_ladder, get_loan_repricing,
    )
    from data.call_report_store import upsert_securities_ladder

    if not cert or not rssd_id:
        return cert, 0, "missing_ids", "no_data", "no_data"
    try:
        df = fetch_call_report(rssd_id, period)
        if df is None or df.empty:
            return cert, 0, "empty_call_report", "no_data", "no_data"
    except Exception as e:
        return cert, 0, f"{type(e).__name__}: {str(e)[:80]}", "no_data", "no_data"

    # Schedules RI, RC-N, RC-R Part I, and RI-E all ride on the same fetched
    # call report (never fetch twice) — persisted even when the bank has no
    # RC-B securities ladder. RC-R/RI-E statuses are logged per-bank below
    # but don't widen the return tuple (callers track ri/rcn).
    ri_status = _persist_ri_detail(cert, rssd_id, period, df)
    rcn_status = _persist_rcn_detail(cert, rssd_id, period, df)
    rcr_status = _persist_rcr_detail(cert, rssd_id, period, df)
    rie_status = _persist_rie_detail(cert, rssd_id, period, df)
    if rcr_status.startswith("fail") or rie_status.startswith("fail"):
        print(f"  [warn] cert {cert}: rcr={rcr_status} rie={rie_status}",
              flush=True)

    try:
        ladder = get_securities_maturity_ladder(rssd_id, period, call_report_df=df)
        if ladder is None:
            return cert, 0, "no_securities_data", ri_status, rcn_status
        # Derive floating-loan share from the same Call Report (RC-C Memo 2
        # loan repricing buckets) — no extra HTTP call. None if the bank
        # didn't report the loan-repricing memoranda.
        loan_rp = get_loan_repricing(rssd_id, period, call_report_df=df)
        floating_share = loan_rp.get("floating_loan_share") if loan_rp else None
        n = upsert_securities_ladder(
            cert, rssd_id, ladder, floating_loan_share=floating_share)
        return (cert, len(ladder.get("buckets", {})),
                "" if n else "upsert_failed", ri_status, rcn_status)
    except Exception as e:
        return cert, 0, f"{type(e).__name__}: {str(e)[:80]}", ri_status, rcn_status


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

    # Build the universe: only banks in our published watchlist + the
    # OTC-discovered set. ~400-500 banks vs. ~4,500 active FDIC institutions.
    # Saves ~90% of the FFIEC PWS request budget and drops runtime to ~10 min.
    from data.bank_mapping import BANK_MAP
    universe_certs: set[int] = set()
    for ticker, info in BANK_MAP.items():
        cert = info.get("fdic_cert")
        if cert:
            universe_certs.add(int(cert))
    try:
        from data.bank_mapping import _RESOLVED_FROM_JSON
        for t, info in _RESOLVED_FROM_JSON.items():
            cert = info.get("fdic_cert")
            if cert:
                universe_certs.add(int(cert))
    except Exception:
        pass
    print(f"[{time.strftime('%H:%M:%S')}] Universe size: "
          f"{len(universe_certs)} certs (BANK_MAP + resolved JSON)",
          flush=True)

    print(f"[{time.strftime('%H:%M:%S')}] Enumerating active FDIC banks "
          "(to map CERT → FFIEC RSSD)...", flush=True)
    institutions = list_all_active_institutions()
    candidates = [
        i for i in institutions
        if i.get("cert") and i.get("rssd_id")
        and int(i["cert"]) in universe_certs
    ]
    print(f"[{time.strftime('%H:%M:%S')}] {len(institutions)} active banks, "
          f"{len(candidates)} in universe with both CERT + RSSD",
          flush=True)

    if not candidates:
        print("⚠ No universe candidates with RSSD — check BANK_MAP / resolved JSON.",
              flush=True)
        return 1

    # Sanity: warn if we're missing universe certs (acquired / inactive)
    found_certs = {int(i["cert"]) for i in candidates}
    missing = universe_certs - found_certs
    if missing:
        print(f"  [warn] {len(missing)} universe certs absent from active "
              f"FDIC list (likely acquired/inactive)", flush=True)

    # Sequential pacing: FFIEC PWS caps each user at ~2,500 requests/hour.
    # We pace at 2,400/hr and sleep to top-of-next-hour when we hit the cap.
    # ~4,500 banks ÷ 2,400/hr = 2 hourly windows (~90 min total wall-clock).
    t0 = time.time()
    success = 0
    no_data = 0
    errors: list[tuple[int, str]] = []
    ri_ok = 0
    ri_no_data = 0
    ri_failures: list[tuple[int, str]] = []
    rcn_ok = 0
    rcn_no_data = 0
    rcn_failures: list[tuple[int, str]] = []
    requests_in_window = 0
    window_start = time.time()

    for i in candidates:
        # Rate-limit window enforcement
        if requests_in_window >= _HOURLY_REQUEST_BUDGET:
            elapsed_in_window = time.time() - window_start
            sleep_until_next_window = max(0, 3600 - elapsed_in_window) + 5
            print(f"[{time.strftime('%H:%M:%S')}] Hit hourly cap "
                  f"({requests_in_window} req). Sleeping "
                  f"{sleep_until_next_window:.0f}s for next window...",
                  flush=True)
            time.sleep(sleep_until_next_window)
            requests_in_window = 0
            window_start = time.time()

        cert, n_buckets, err, ri_status, rcn_status = _refresh_one(
            int(i["cert"]), int(i["rssd_id"]), period,
        )
        requests_in_window += 1

        if ri_status == "ok":
            ri_ok += 1
        elif ri_status == "no_data":
            ri_no_data += 1
        else:
            ri_failures.append((cert, ri_status))

        if rcn_status == "ok":
            rcn_ok += 1
        elif rcn_status == "no_data":
            rcn_no_data += 1
        else:
            rcn_failures.append((cert, rcn_status))

        if err == "":
            if n_buckets > 0:
                success += 1
            else:
                no_data += 1
        elif err in ("empty_call_report", "no_securities_data"):
            no_data += 1
        else:
            errors.append((cert, err))

        done = success + no_data + len(errors)
        total = len(candidates)
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
    print(f"  RI detail stored:    ok={ri_ok} fail={len(ri_failures)} "
          f"no_data={ri_no_data}")
    print(f"  RC-N detail stored:  ok={rcn_ok} fail={len(rcn_failures)} "
          f"no_data={rcn_no_data}")

    if errors:
        print("\nError sample (first 10):")
        for cert, err in errors[:10]:
            print(f"  cert={cert} {err}")

    if ri_failures:
        print("\nRI detail failure sample (first 10):")
        for cert, ri_err in ri_failures[:10]:
            print(f"  cert={cert} {ri_err}")

    if rcn_failures:
        print("\nRC-N detail failure sample (first 10):")
        for cert, rcn_err in rcn_failures[:10]:
            print(f"  cert={cert} {rcn_err}")

    # Exit-code logic measures errors per attempt — banks with no
    # securities (legitimate empty RC-B Memo 2) are not failures.
    # Auth/network failures populate `errors`.
    error_rate = len(errors) / max(1, len(candidates))
    no_data_rate = no_data / max(1, len(candidates))

    # If we got literally zero ladders AND no errors, the schema lookup
    # is silently broken (every bank fetched but parser produced nothing).
    if success == 0 and len(errors) == 0 and no_data > 10:
        print("\n⚠ Zero ladders ingested with no errors — schema mismatch?")
        return 2

    if error_rate < 0.05:
        return 0
    if error_rate < 0.20:
        return 1
    return 2  # mostly broken — likely auth issue


if __name__ == "__main__":
    sys.exit(main())
