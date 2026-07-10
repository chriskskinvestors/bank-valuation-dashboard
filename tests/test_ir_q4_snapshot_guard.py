"""
Pins the Q4-calls snapshot clobber guard (AUDIT-2026-07-02 P3, 2026-07-10):
a transient all-fail scan (found == {}) must NOT overwrite the last good
snapshot with {} — the previous data keeps serving (call dates expire naturally
downstream). A legitimately-empty result persists only when there was no prior
snapshot to protect.
"""
import sys
import types
import unittest
from unittest.mock import patch

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.events.ir_site as irs  # noqa: E402


class TestQ4SnapshotClobberGuard(unittest.TestCase):
    def _run(self, prev):
        with patch.object(irs, "get_ir_endpoints", return_value={}), \
             patch("data.cache.get", return_value=prev), \
             patch("data.cache.put") as put:
            out = irs.refresh_q4_calls_snapshot()
        return out, put

    def test_empty_scan_keeps_previous_snapshot(self):
        prev = {"value": {"JPM": {"call_date": "2026-07-15"}},
                "cached_at": "2026-07-09T18:00:00"}
        out, put = self._run(prev)
        self.assertEqual(out, {})
        put.assert_not_called()              # last good snapshot untouched

    def test_empty_scan_with_no_prior_persists_empty(self):
        out, put = self._run(None)
        self.assertEqual(out, {})
        put.assert_called_once()             # legit empty, nothing to protect


if __name__ == "__main__":
    unittest.main()
