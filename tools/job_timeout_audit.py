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
  headroom      timeout ÷ max runtime; < 1.5x is flagged, a timed-out
                execution (runtime ≈ timeout, failed) is flagged loudly

Runs wherever gcloud is authenticated (the "Job timeout audit" GitHub
workflow uses the deployer's WIF credentials). Read-only.

Usage: python tools/job_timeout_audit.py [--region us-central1] [--limit 12]
Exit 1 when any job is flagged, so the workflow run is red when there is
something to look at.
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
    rows, flagged = [], []
    for job in sorted(jobs, key=lambda j: j["metadata"]["name"]):
        name = job["metadata"]["name"]
        timeout = _timeout_s(job)
        execs = _gcloud("run", "jobs", "executions", "list", f"--job={name}",
                        f"--region={region}", f"--limit={limit}")
        durations, ok, fail, timed_out = [], 0, 0, 0
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
        mx = max(durations) if durations else 0.0
        p50 = statistics.median(durations) if durations else 0.0
        headroom = (timeout / mx) if mx else float("inf")
        flag = ""
        if timed_out:
            flag = f"TIMED OUT x{timed_out}"
        elif mx and headroom < MIN_HEADROOM:
            flag = f"headroom {headroom:.1f}x"
        if flag:
            flagged.append(name)
        rows.append((name, timeout, len(durations), ok, fail, mx, p50, flag))

    print(f"{'job':<28} {'timeout':>8} {'n':>3} {'ok':>3} {'fail':>4} "
          f"{'max':>8} {'p50':>8}  flag")
    for name, timeout, n, ok, fail, mx, p50, flag in rows:
        print(f"{name:<28} {timeout:>7}s {n:>3} {ok:>3} {fail:>4} "
              f"{mx:>7.0f}s {p50:>7.0f}s  {flag}")
    if flagged:
        print(f"\nFLAGGED ({len(flagged)}): {', '.join(flagged)}")
        return 1
    print("\nAll jobs have >= 1.5x headroom and no timeout kills in the window.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args()
    return audit(a.region, a.limit)


if __name__ == "__main__":
    sys.exit(main())
