"""Observe-only report of secret coverage on the Cloud Run Jobs.

Why: the 2026-06-24 outage was the *service* losing its secret mounts (see
docs/AUDIT-PRODUCTION-READINESS.md Gap F). The Jobs are separate resources with
their own secret configs and are what WARM the caches the dashboard reads - a
job that silently loses e.g. FMP_API_KEY produces nothing and the cached panes
go stale/blank with no error. The deploy already fails loudly if a scheduled job
loses its invoker IAM binding; this is the secret-side twin of that guard, but
**observe-only for now**: it prints each job's mounted secret env vars and warns
(never fails) when a job is missing a secret its primary function needs.

Flip to blocking later: once a few deploys show a clean baseline, change
`OBSERVE_ONLY = False` (or move the EXPECTED check into a step that `exit 1`s).
Run in CI (where gcloud is authed); locally it just reports "describe failed".

EXPECTED is deliberately CONSERVATIVE - only secrets a job's core purpose
provably needs (verified against the job code), so a warning is always real.
Jobs absent from EXPECTED are still listed, just not flagged.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

REGION = "us-central1"
OBSERVE_ONLY = True  # never fail the deploy while we establish a clean baseline

# Every Cloud Run job kept in sync by deploy.yml's image-sync loop.
JOBS = [
    "refresh-universe", "refresh-sod", "refresh-ffiec", "poll-events",
    "live-audit", "refresh-prices", "refresh-avg-volume", "refresh-insider",
    "refresh-live-yields", "verify-metrics", "refresh-capital",
    "refresh-company-financials", "refresh-home-snapshot", "refresh-macro",
    "refresh-premarket", "refresh-trends",
]

# Conservative high-confidence map: secret env vars a job's primary function
# provably needs (verified against the job's code path, not just its imports).
EXPECTED = {
    "poll-events": {"FMP_API_KEY", "ANTHROPIC_API_KEY"},
    "refresh-avg-volume": {"FMP_API_KEY"},
    "refresh-prices": {"FMP_API_KEY"},
    "refresh-premarket": {"FMP_API_KEY"},
    "refresh-home-snapshot": {"FMP_API_KEY"},
    "refresh-universe": {"FMP_API_KEY"},
    "verify-metrics": {"FMP_API_KEY"},
    "refresh-macro": {"FRED_API_KEY"},
    "refresh-ffiec": {"FFIEC_USERNAME", "FFIEC_JWT_TOKEN"},
}

_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _env_names(job: str):
    """The container env-var names mounted on the job, or None if describe fails."""
    try:
        r = subprocess.run(
            ["gcloud", "run", "jobs", "describe", job, "--region", REGION,
             "--format", "json"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
    except Exception:
        return None
    names = set()

    def walk(o):
        if isinstance(o, dict):
            n = o.get("name")
            if isinstance(n, str) and ("value" in o or "valueFrom" in o) and _SECRET_NAME.match(n):
                names.add(n)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return names


def main() -> int:
    print("## Cloud Run Jobs - secret coverage (observe-only)")
    problems = []
    for job in JOBS:
        names = _env_names(job)
        if names is None:
            print(f"  {job}: (not deployed / describe unavailable)")
            continue
        shown = sorted(names) or ["none"]
        missing = EXPECTED.get(job, set()) - names
        flag = f"   !! MISSING {', '.join(sorted(missing))}" if missing else ""
        print(f"  {job}: [{', '.join(shown)}]{flag}")
        if missing:
            problems.append((job, sorted(missing)))
    if problems:
        detail = "; ".join(f"{j} -> {', '.join(m)}" for j, m in problems)
        print(f"::warning::Cloud Run job(s) missing an expected secret: {detail}")
    else:
        print("  All jobs carry their expected secrets.")
    # Observe-only: surface, never block.
    return 0 if OBSERVE_ONLY else (1 if problems else 0)


if __name__ == "__main__":
    sys.exit(main())
