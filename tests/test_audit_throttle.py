"""Pin the audit harness's per-request timeout (tools/_audit_common).

Regression context (2026-06-22): a transient SEC socket stall hung a full-universe
Company Reported audit for hours — the per-bank try/except never fired because a
blocked read never raises. install_throttle now caps every _get at a wall-clock
timeout so a stall becomes a per-bank error the loop skips, not a dead run.

Run: python -m unittest tests.test_audit_throttle
"""
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools._audit_common import install_throttle


def _fake_sfs(get_fn):
    m = types.SimpleNamespace()
    m._get = get_fn
    return m


class TestAuditThrottle(unittest.TestCase):
    def test_fast_call_passes_through(self):
        sfs = _fake_sfs(lambda url, *a, **k: {"url": url})
        install_throttle(sfs, req_per_sec=1000, timeout=5)
        self.assertEqual(sfs._get("x"), {"url": "x"})

    def test_stalled_call_raises_timeout_not_hang(self):
        sfs = _fake_sfs(lambda url, *a, **k: time.sleep(30))  # would hang the run
        install_throttle(sfs, req_per_sec=1000, timeout=0.3)
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            sfs._get("slow")
        self.assertLess(time.time() - t0, 3, "must abandon the stall quickly, not block")

    def test_underlying_error_propagates(self):
        def boom(url, *a, **k):
            raise ValueError("fetch failed")
        sfs = _fake_sfs(boom)
        install_throttle(sfs, req_per_sec=1000, timeout=5)
        with self.assertRaises(ValueError):
            sfs._get("x")

    def test_throttle_spaces_requests(self):
        sfs = _fake_sfs(lambda url, *a, **k: 1)
        install_throttle(sfs, req_per_sec=20, timeout=5)  # 50ms min gap
        t0 = time.time()
        for _ in range(4):
            sfs._get("x")
        self.assertGreaterEqual(time.time() - t0, 0.12, "4 calls @20/s should take >=150ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
