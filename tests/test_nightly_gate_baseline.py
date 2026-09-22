"""refresh-universe growth gate: the previous-run baseline must survive the
job's own run-to-run jitter.

The gate compares tonight's validation failures against the map the previous
run persisted under cache key ``nightly_validation_lastrun``. That key is
written once per run at the END of the refresh phase and read at the same
point ~24h later, so under cache.get's default 24h TTL it was alive or expired
by the SECONDS by which tonight's gate ran later in the day than last night's.
An expired read came back {} and the whole stable exception list printed as
NEW, failing the job — 2026-09-09..22: 8 of 14 nights failed, always the same
6 tickers (5 deregistered filers with years-stale XBRL + PCB's release-basis
BVPS conflict), and on every failing night the reconstructed gate time was
>86,400s after the previous gate, on every passing night <86,400s.

Pins:
  1. the exact flip: a baseline aged TTL+55s reads as MISSING through the old
     default-TTL path (the failure mode), and as the full map through
     load_previous_run — so the stable set yields zero NEW failures;
  2. a baseline days old still serves (a skipped night is not a reset);
  3. no baseline at all is an honest {} (never an exception);
  4. structural: the job reads and writes the baseline only via PREV_RUN_KEY /
     load_previous_run — no default-TTL read of the key can creep back.

All DB access runs on an isolated in-memory SQLite engine; no network.
Run: python -m unittest tests.test_nightly_gate_baseline
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import data.cache as cache  # noqa: E402
from jobs import refresh_universe as ru  # noqa: E402
from tests.test_cache_stale_fallback import _IsolatedCache  # noqa: E402

# The stable exception list exactly as the 2026-09-21 run printed it.
STABLE_FAILURES = {
    "CCNB": ["sec_filings:Data is 4922 days old (max expected: 200)."],
    "FOTB": ["sec_filings:Data is 5287 days old (max expected: 200)."],
    "OSBK": ["sec_filings:Data is 5287 days old (max expected: 200)."],
    "PCB": ["bvps:Release-reported BVPS and the reconstruction disagree"],
    "UBOH": ["sec_filings:Data is 1360 days old (max expected: 200)."],
    "VWFB": ["sec_filings:Data is 539 days old (max expected: 200)."],
}


class TestBaselineSurvivesRunJitter(_IsolatedCache):
    def _persist_run(self, failed: dict, date: str):
        """Write the baseline with the exact shape the job persists."""
        cache.put(ru.PREV_RUN_KEY, {
            "date": date, "failed": failed, "warnings": 0, "universe_size": 657,
        })

    def test_gate_55s_later_than_last_night_sees_no_new_failures(self):
        """The 2026-09-21 shape: last night's gate ran at T, tonight's at
        T + 24h + 55s. Same six failures both nights."""
        self._persist_run(STABLE_FAILURES, "2026-09-20 06:16:09")
        self._age(ru.PREV_RUN_KEY, cache.TTL_SECONDS + 55)

        # The old read path — cache.get at the default TTL — is the failure
        # mode: the baseline reads as missing, so every failure is "new".
        self.assertIsNone(cache.get(ru.PREV_RUN_KEY))

        prev_run = ru.load_previous_run()
        self.assertEqual(prev_run.get("date"), "2026-09-20 06:16:09")
        prev = prev_run.get("failed", {})
        current = dict(STABLE_FAILURES)
        self.assertEqual(sorted(set(current) - set(prev)), [],
                         "a stable exception list must never print as NEW")
        self.assertEqual(sorted(set(prev) - set(current)), [])

    def test_gate_earlier_than_last_night_unchanged(self):
        """The passing-night shape still passes (no regression in the
        under-TTL case)."""
        self._persist_run(STABLE_FAILURES, "2026-09-19 06:18:03")
        self._age(ru.PREV_RUN_KEY, cache.TTL_SECONDS - 90)
        self.assertEqual(ru.load_previous_run()["failed"], STABLE_FAILURES)

    def test_days_old_baseline_still_serves(self):
        """A skipped or failed night must not reset the baseline: the last
        run that completed is the comparison point, whatever its age."""
        self._persist_run(STABLE_FAILURES, "2026-09-15 06:16:15")
        self._age(ru.PREV_RUN_KEY, 4 * cache.TTL_SECONDS)
        self.assertEqual(ru.load_previous_run()["failed"], STABLE_FAILURES)

    def test_genuinely_new_failure_still_detected(self):
        """The gate's purpose survives the fix: a ticker absent from the
        baseline IS new, even when the baseline is past the default TTL."""
        self._persist_run(STABLE_FAILURES, "2026-09-20 06:16:09")
        self._age(ru.PREV_RUN_KEY, cache.TTL_SECONDS + 55)
        prev = ru.load_previous_run().get("failed", {})
        current = dict(STABLE_FAILURES, WTFC=["total_assets:cross-source gap"])
        self.assertEqual(sorted(set(current) - set(prev)), ["WTFC"])

    def test_no_baseline_is_empty_dict(self):
        self.assertEqual(ru.load_previous_run(), {})


class TestJobWiring(unittest.TestCase):
    def test_job_reads_and_writes_baseline_only_via_named_key(self):
        src = (REPO / "jobs/refresh_universe.py").read_text(encoding="utf-8")
        self.assertIn("prev_run = load_previous_run()", src)
        self.assertIn("cache.put(PREV_RUN_KEY, {", src)
        # No default-TTL read of the baseline key anywhere in the job.
        self.assertIsNone(
            re.search(r'cache\.get\(\s*"nightly_validation_lastrun"', src),
            "baseline must be read TTL-free through load_previous_run")
        self.assertEqual(src.count('"nightly_validation_lastrun"'), 1,
                         "the key literal lives only in PREV_RUN_KEY")

    def test_loader_is_ttl_free(self):
        src = (REPO / "jobs/refresh_universe.py").read_text(encoding="utf-8")
        self.assertIn("cache.get(PREV_RUN_KEY, max_age_s=None)", src)


if __name__ == "__main__":
    unittest.main()
