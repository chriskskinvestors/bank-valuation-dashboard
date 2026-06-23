"""
One-shot: create the refresh-premarket Cloud Run job + its pre-market Cloud
Scheduler, cloning the refresh-universe job config (image, SA, Cloud SQL volume,
DATABASE_URL) and the existing scheduler pattern. Mirrors create_price_jobs.py.

Run AFTER a deploy that includes jobs/refresh_premarket.py (so the cloned image
digest contains it). Uses ADC. Idempotent: skips anything that already exists.

  PYTHONIOENCODING=utf-8 python -X utf8 tools/create_premarket_job.py
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

# (job_id, module, (cron, tz)). Pre-market window only: every 5 min, 4-9 a.m. ET
# on weekdays. The Home panes gate display on the 4:00-9:30 ET window, so the
# 9:30-9:59 tail (cron hour 9) is harmless.
NEW_JOBS = [
    ("refresh-premarket", "jobs.refresh_premarket",
     ("*/5 4-9 * * 1-5", "America/New_York")),
]


def _session():
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _job_exists(s, job_id) -> bool:
    return s.get(f"{RUN}/jobs/{job_id}").status_code == 200


def _create_job(s, job_id, module, template):
    tmpl = copy.deepcopy(template)
    container = tmpl["template"]["containers"][0]
    container["command"] = ["python"]
    container["args"] = ["-m", module]
    env = container.setdefault("env", [])
    if not any(e.get("name") == "FMP_API_KEY" for e in env):
        env.append({
            "name": "FMP_API_KEY",
            "valueSource": {"secretKeyRef": {"secret": "fmp-api-key", "version": "latest"}},
        })
    r = s.post(f"{RUN}/jobs?jobId={job_id}", json={"template": tmpl})
    if r.status_code not in (200, 201):
        print(f"  [ERROR] create {job_id}: {r.status_code} {r.text[:300]}")
        return False
    op = r.json().get("name")
    for _ in range(30):
        if not op:
            break
        d = s.get(f"https://run.googleapis.com/v2/{op}").json()
        if d.get("done"):
            if d.get("error"):
                print(f"  [ERROR] {job_id} op: {d['error']}")
                return False
            break
        time.sleep(3)
    print(f"  ✓ created job {job_id} ({module})")
    return True


def _create_scheduler(s, job_id, cron, tz):
    sid = f"{job_id}-sched"
    if s.get(f"{SCHED}/jobs/{sid}").status_code == 200:
        print(f"  • scheduler {sid} already exists")
        return True
    body = {
        "name": f"projects/{PROJ}/locations/{LOC}/jobs/{sid}",
        "schedule": cron,
        "timeZone": tz,
        "httpTarget": {
            "uri": f"https://{LOC}-run.googleapis.com/apis/run.googleapis.com/v1/"
                   f"namespaces/{PROJ}/jobs/{job_id}:run",
            "httpMethod": "POST",
            "headers": {"User-Agent": "Google-Cloud-Scheduler"},
            "oauthToken": {
                "serviceAccountEmail": SCHED_SA,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            },
        },
        "retryConfig": {"maxRetryDuration": "0s", "minBackoffDuration": "5s",
                        "maxBackoffDuration": "3600s", "maxDoublings": 5},
    }
    r = s.post(f"{SCHED}/jobs", json=body)
    if r.status_code not in (200, 201):
        print(f"  [ERROR] scheduler {sid}: {r.status_code} {r.text[:300]}")
        return False
    print(f"  ✓ created scheduler {sid} ('{cron}' {tz})")
    return True


def _fire(s, job_id):
    r = s.post(f"{RUN}/jobs/{job_id}:run")
    if r.status_code in (200, 201):
        print(f"  ✓ fired {job_id}: {r.json().get('metadata', {}).get('name', '').split('/')[-1]}")
        return True
    print(f"  [ERROR] fire {job_id}: {r.status_code} {r.text[:200]}")
    return False


def main() -> int:
    s = _session()
    base = s.get(f"{RUN}/jobs/refresh-universe").json()
    template = base.get("template")
    if not template:
        print("[FATAL] could not read refresh-universe template")
        return 1
    img = template["template"]["containers"][0].get("image", "")
    print(f"Cloning config from refresh-universe (image …{img[-12:]})")

    for job_id, module, sched in NEW_JOBS:
        print(f"\n{job_id}:")
        if _job_exists(s, job_id):
            print(f"  • job {job_id} already exists — skipping create")
        else:
            if not _create_job(s, job_id, module, template):
                continue
        if sched:
            _create_scheduler(s, job_id, *sched)

    # Smoke-fire once so the blob exists (outside pre-market it writes an empty
    # map — harmless; the Home panes gate on the ET window anyway).
    print("\nSmoke-firing refresh-premarket:")
    _fire(s, "refresh-premarket")
    return 0


if __name__ == "__main__":
    sys.exit(main())
