"""The nightly refresh-universe job appends the newest quarters to the deep
FDIC history store (DEEP-HISTORY-PLAN.md "Nightly append", wired
2026-09-22). Pins: it runs the backfill job in INCREMENTAL mode (never the
one-time deep backfill), it never fails or aborts the nightly (the growth
gate's exit code is the job's contract), and its degraded exit is logged.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: F401

from jobs import refresh_universe as ru


class TestDeepHistoryAppend(unittest.TestCase):
    def test_runs_incremental_mode_and_reports_code(self):
        calls = []
        with patch("jobs.backfill_fdic_history.main",
                   side_effect=lambda mode: calls.append(mode) or 0):
            self.assertEqual(ru.append_deep_history(), 0)
        self.assertEqual(calls, ["incremental"],
                         "the nightly must append, never re-run the deep backfill")

    def test_degraded_code_is_returned_not_raised(self):
        with patch("jobs.backfill_fdic_history.main", return_value=1):
            self.assertEqual(ru.append_deep_history(), 1)

    def test_crash_is_swallowed_never_aborts_the_nightly(self):
        def boom(mode):
            raise ConnectionError("FDIC offline")
        with patch("jobs.backfill_fdic_history.main", side_effect=boom):
            self.assertIsNone(ru.append_deep_history())


if __name__ == "__main__":
    unittest.main()
