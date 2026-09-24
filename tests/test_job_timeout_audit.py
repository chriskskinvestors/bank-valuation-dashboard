"""tools/job_timeout_audit.py — hermetic (gcloud stubbed).

Hand-computed: a job with timeout 1800 whose failed execution ran 1799s is a
timeout kill; one with timeout 600 and a 500s success has 1.2x headroom
(flagged); one with 7200 and a 2700s success has 2.67x (clean). Unset
timeout reads as Cloud Run's 600s default; executions without a completion
time (running) are ignored.
"""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import job_timeout_audit as jta


def _job(name, timeout=None):
    spec = {} if timeout is None else {"timeoutSeconds": f"{timeout}s"}
    return {"metadata": {"name": name},
            "spec": {"template": {"spec": {"template": {"spec": spec}}}}}


def _exec(start, end, ok):
    st = {"startTime": start, "completionTime": end}
    if ok:
        st["succeededCount"] = 1
    else:
        st["failedCount"] = 1
    return {"status": st}


_EXECS = {
    "sod": [_exec("2026-09-22T10:30:59Z", "2026-09-22T11:00:58Z", False),   # 1799s
            _exec("2026-09-21T02:15:15Z", "2026-09-21T02:17:49Z", True)],   # 154s
    "tight": [_exec("2026-09-24T06:00:00Z", "2026-09-24T06:08:20Z", True)],  # 500s
    "roomy": [_exec("2026-09-24T06:00:00Z", "2026-09-24T06:45:00Z", True),   # 2700s
              {"status": {"startTime": "2026-09-24T07:00:00Z"}}],           # running
    "idle": [],
}
_JOBS = [_job("sod", 1800), _job("tight"), _job("roomy", 7200), _job("idle", 900)]


def _fake_gcloud(*args):
    if args[:3] == ("run", "jobs", "list"):
        return _JOBS
    if args[:4] == ("run", "jobs", "executions", "list"):
        name = next(a for a in args if a.startswith("--job=")).split("=", 1)[1]
        return _EXECS[name]
    raise AssertionError(args)


class TestAudit(unittest.TestCase):
    def _run(self):
        buf = io.StringIO()
        with patch.object(jta, "_gcloud", side_effect=_fake_gcloud), redirect_stdout(buf):
            rc = jta.audit("us-central1", 12)
        return rc, buf.getvalue()

    def test_flags_timeout_kill_and_thin_headroom_only(self):
        rc, out = self._run()
        self.assertEqual(rc, 1)
        lines = {l.split()[0]: l for l in out.splitlines() if l and l[0].isalpha()}
        self.assertIn("TIMED OUT x1", lines["sod"])
        self.assertIn("1799s", lines["sod"])
        self.assertIn("headroom 1.2x", lines["tight"])
        self.assertIn("600s", lines["tight"])                # default timeout
        self.assertNotIn("headroom", lines["roomy"])
        self.assertIn("2700s", lines["roomy"])
        self.assertIn("  1   1    0", lines["roomy"])        # running one ignored
        self.assertIn("09-22 FAIL", lines["sod"])             # newest completed verdict
        self.assertIn("09-24 ok", lines["roomy"])
        self.assertIn("FLAGGED (2): sod, tight", out)

    def test_clean_when_nothing_flagged(self):
        clean = {**_EXECS, "sod": [_EXECS["sod"][1]], "tight": []}
        with patch.dict(_EXECS, clean):
            rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("All jobs have >= 1.5x headroom", out)

    def test_timeout_parsing(self):
        self.assertEqual(jta._timeout_s(_job("x")), 600)
        self.assertEqual(jta._timeout_s(_job("x", 3600)), 3600)
        j = _job("x"); j["spec"]["template"]["spec"]["template"]["spec"]["timeoutSeconds"] = 5400
        self.assertEqual(jta._timeout_s(j), 5400)


if __name__ == "__main__":
    unittest.main()
