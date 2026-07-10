"""
One-shot: create the refresh-13f Cloud Run job + its quarterly Cloud
Scheduler, cloning the existing refresh-universe job config (image, SA,
Cloud SQL volume, DATABASE_URL) — same approach as tools/create_home_jobs.py.

Run AFTER a deploy that includes jobs/refresh_13f.py (so the cloned image
digest contains the module). Uses ADC. Idempotent: skips anything that exists.

Two deliberate overrides vs the cloned template:
  • task timeout 5400s — a full universe 13F pass will not fit the 900s
    default (the refresh-capital timeout lesson);
  • quarterly cron (19th of Feb/May/Aug/Nov, 6am ET) — 13F-HRs are due 45
    days after quarter-end (≈ the 14th), so the 19th catches the season with
    a buffer. Running between seasons is pointless, not harmful.

  PYTHONIOENCODING=utf-8 python -X utf8 tools/create_13f_job.py
"""
from __future__ import annotations
import copy
import sys
import time

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJ = "ace-beanbag-486220-a8"
LOC = "us-central1"
RUN = f"https://run.googleapis.com/v2/projects/{PROJ}/locations/{LOC}"
SCHED = f"https://cloudscheduler.googleapis.com/v1/projects/{PROJ}/locations/{LOC}"
SCHED_SA = "scheduler-invoker@ace-beanbag-486220-a8.iam.gserviceaccount.com"

JOB_ID, MODULE = "refresh-13f", "jobs.refresh_13f"
CRON, TZ = "0 6 19 2,5,8,11 *", "America/New_York"
TASK_TIMEOUT = "5400s"


def _session():
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def main() -> int:
    s = _session()
    base = s.get(f"{RUN}/jobs/refresh-universe").json()
    template = base.get("template")
    if not template:
        print("[FATAL] could not read refresh-universe template")
        return 1

    # Job (idempotent)
    if s.get(f"{RUN}/jobs/{JOB_ID}").status_code == 200:
        print(f"  • job {JOB_ID} already exists")
    else:
        tmpl = copy.deepcopy(template)
        tmpl["taskCount"] = 1
        tmpl["template"]["timeout"] = TASK_TIMEOUT
        tmpl["template"]["maxRetries"] = 1
        container = tmpl["template"]["containers"][0]
        container["command"] = ["python"]
        container["args"] = ["-m", MODULE]
        r = s.post(f"{RUN}/jobs?jobId={JOB_ID}", json={"template": tmpl})
        if r.status_code not in (200, 201):
            print(f"  [ERROR] create {JOB_ID}: {r.status_code} {r.text[:300]}")
            return 1
        op = r.json().get("name")
        for _ in range(30):
            if not op:
                break
            d = s.get(f"https://run.googleapis.com/v2/{op}").json()
            if d.get("done"):
                if d.get("error"):
                    print(f"  [ERROR] {JOB_ID} op: {d['error']}")
                    return 1
                break
            time.sleep(3)
        print(f"  ✓ created job {JOB_ID} ({MODULE}, timeout {TASK_TIMEOUT})")

    # Scheduler (idempotent) — oauthToken + scheduler-invoker SA, exactly like
    # every other trigger (the invoker binding is auto-granted by deploy.yml's
    # Ensure step on the next deploy; the tool's fire below proves it works).
    sid = f"{JOB_ID}-sched"
    if s.get(f"{SCHED}/jobs/{sid}").status_code == 200:
        print(f"  • scheduler {sid} already exists")
    else:
        body = {
            "name": f"projects/{PROJ}/locations/{LOC}/jobs/{sid}",
            "schedule": CRON, "timeZone": TZ,
            "httpTarget": {
                "uri": f"https://{LOC}-run.googleapis.com/apis/run.googleapis.com/v1/"
                       f"namespaces/{PROJ}/jobs/{JOB_ID}:run",
                "httpMethod": "POST",
                "headers": {"User-Agent": "Google-Cloud-Scheduler"},
                "oauthToken": {"serviceAccountEmail": SCHED_SA,
                               "scope": "https://www.googleapis.com/auth/cloud-platform"},
            },
            "retryConfig": {"maxRetryDuration": "0s", "minBackoffDuration": "5s",
                            "maxBackoffDuration": "3600s", "maxDoublings": 5},
        }
        r = s.post(f"{SCHED}/jobs", json=body)
        if r.status_code not in (200, 201):
            print(f"  [ERROR] scheduler {sid}: {r.status_code} {r.text[:300]}")
            return 1
        print(f"  ✓ created scheduler {sid} ('{CRON}' {TZ})")

    # Seed pass now (don't wait until the next 13F season for coverage).
    r = s.post(f"{RUN}/jobs/{JOB_ID}:run")
    if r.status_code in (200, 201):
        print(f"  ✓ fired {JOB_ID} — seed pass running (~60-90 min); "
              "watch: gcloud.cmd run jobs executions list --region=us-central1")
    else:
        print(f"  [WARN] could not fire {JOB_ID}: {r.status_code} {r.text[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
