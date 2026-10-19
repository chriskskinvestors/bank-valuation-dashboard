"""Cloud Run job timeout audit: configured task timeout vs observed runtimes.

refresh-sod ran under Cloud Run's default task timeout for months and EVERY
monthly full sweep was killed at ~2,800 of 4,232 banks ("Terminating task
because it has reached the maximum timeout") — invisible because upserts
persisted across the timed-out attempts (found 2026-09-22, fixed #138).
refresh-home-snapshot hit the same cliff on 2026-07-27, refresh-universe on
2026-08-04. This audit puts every job's timeout next to what its recent
executions actually took, so the next one is caught before it fails.

For each job in the region:
  timeout       configured task timeout (Cloud Run default 600s when unset)
  n / ok / fail recent executions (up to --limit) and how many succeeded
  max / p50     observed wall time of COMPLETED executions
  last          the newest completed execution's date and verdict
  headroom      timeout ÷ max runtime; < 1.5x is flagged, a timed-out
                execution (runtime ≈ timeout, failed) is flagged loudly

Runs wherever gcloud is authenticated (the "Job timeout audit" GitHub
workflow uses the deployer's WIF credentials). Read-only.

Usage: python tools/job_timeout_audit.py [--region us-central1] [--limit 12]
Exit 1 only on a RED flag (killed at its limit, or the latest run failed);
thin headroom on a succeeding job is a ::warning:: annotation and the run
passes.
"""
from __future__ import annotations
import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_TIMEOUT_S = 600          # Cloud Run's default task timeout
MIN_HEADROOM = 1.5               # timeout must be ≥ 1.5× the slowest recent run


def _gcloud(*args: str) -> list | dict:
    out = subprocess.run(["gcloud", *args, "--format=json"], check=True,
                         capture_output=True, text=True).stdout
    return json.loads(out or "[]")


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _timeout_s(job: dict) -> int:
    tmpl = (job.get("spec", {}).get("template", {}).get("spec", {})
            .get("template", {}).get("spec", {}))
    raw = tmpl.get("timeoutSeconds")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    return int(str(raw).rstrip("s"))


def audit(region: str, limit: int) -> int:
    jobs = _gcloud("run", "jobs", "list", f"--region={region}")
    rows, flagged, warned, notes = [], [], [], []
    for job in sorted(jobs, key=lambda j: j["metadata"]["name"]):
        name = job["metadata"]["name"]
        timeout = _timeout_s(job)
        execs = _gcloud("run", "jobs", "executions", "list", f"--job={name}",
                        f"--region={region}", f"--limit={limit}")
        durations, ok, fail, timed_out, last = [], 0, 0, 0, ""
        for e in execs:
            st = e.get("status", {})
            start, end = _ts(st.get("startTime")), _ts(st.get("completionTime"))
            if not (start and end):
                continue
            secs = (end - start).total_seconds()
            durations.append(secs)
            if st.get("succeededCount"):
                ok += 1
            else:
                fail += 1
                if secs >= timeout * 0.97:
                    timed_out += 1
            # executions list is newest-first: the first completed one is
            # the latest verdict — "is it healthy NOW", not just lately.
            if not last:
                last = f"{end.strftime('%m-%d')} {'ok' if st.get('succeededCount') else 'FAIL'}"
        mx = max(durations) if durations else 0.0
        p50 = statistics.median(durations) if durations else 0.0
        headroom = (timeout / mx) if mx else float("inf")
        # RED (run fails, email) only for a real problem: a run killed at
        # its limit, or the job's latest run failed. Thin headroom on a job
        # that is succeeding is a WARNING (yellow annotation, run passes) —
        # the 2026-10-19 "All jobs have failed" email for two warnings read
        # as "the dashboard is down" (owner).
        flag, severity = "", ""
        if timed_out:
            flag, severity = f"TIMED OUT x{timed_out}", "error"
        elif last.endswith("FAIL"):
            flag, severity = "LATEST RUN FAILED", "error"
        elif mx and headroom < MIN_HEADROOM:
            flag, severity = f"headroom {headroom:.1f}x", "warning"
        if severity == "error":
            flagged.append(name)
        elif severity == "warning":
            warned.append(name)
        if severity:
            notes.append((severity, f"{name}: {flag} (timeout {timeout}s, "
                                    f"max {mx:.0f}s, latest {last})"))
        rows.append((name, timeout, len(durations), ok, fail, mx, p50, last, flag))

    print(f"{'job':<28} {'timeout':>8} {'n':>3} {'ok':>3} {'fail':>4} "
          f"{'max':>8} {'p50':>8}  {'last':<11} flag")
    for name, timeout, n, ok, fail, mx, p50, last, flag in rows:
        print(f"{name:<28} {timeout:>7}s {n:>3} {ok:>3} {fail:>4} "
              f"{mx:>7.0f}s {p50:>7.0f}s  {last:<11} {flag}")
    # GitHub annotations: the reason shows on the run page, not just "failed".
    for severity, text in notes:
        print(f"::{severity}::{text}")
    if warned:
        print(f"\nWARNING ({len(warned)}, run still passes): {', '.join(warned)}")
    if flagged:
        print(f"\nFLAGGED ({len(flagged)}): {', '.join(flagged)}")
        return 1
    print("\nNo job was killed at its limit and every job's latest run succeeded.")
    return 0


def list_executions(region: str, job: str, limit: int) -> int:
    """One line per recent execution of `job` — name, start/end (UTC),
    duration, verdict — so a flagged run can be located and its logs read
    (run-job.yml logs_only with logs_since). Read-only; always exits 0."""
    execs = _gcloud("run", "jobs", "executions", "list", f"--job={job}",
                    f"--region={region}", f"--limit={limit}")
    print(f"{'execution':<34} {'start (UTC)':<17} {'end (UTC)':<17} {'secs':>6}  verdict")
    for e in execs:
        st = e.get("status", {})
        start, end = _ts(st.get("startTime")), _ts(st.get("completionTime"))
        secs = f"{(end - start).total_seconds():.0f}" if (start and end) else "-"
        verdict = ("running" if not end else "ok" if st.get("succeededCount")
                   else "FAIL")
        fmt = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "-"
        print(f"{e['metadata']['name']:<34} {fmt(start):<17} {fmt(end):<17} "
              f"{secs:>6}  {verdict}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--job", help="list this job's executions instead of the audit")
    a = ap.parse_args()
    if a.job:
        return list_executions(a.region, a.job, a.limit)
    return audit(a.region, a.limit)


if __name__ == "__main__":
    sys.exit(main())
