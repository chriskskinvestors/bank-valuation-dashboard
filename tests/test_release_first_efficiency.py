"""(2026-08-19) Release-first increment 3: efficiency ratio, ADD-ALONGSIDE
(owner decision — the FDIC bank-sub EEFFR column stays THE efficiency for all
banks; a NEW "Eff (Rel)" column carries the bank's own released holdco
efficiency where disclosed; the bases legitimately differ, so NO conflict
flag and NO cross-band gate between them).

Pinned here:
- CACHE-ONLY / NO-FETCH (the perf contract): cached_release_metrics never
  touches the frontier/fetch path, and the resolver never calls
  release_metrics() (the fetching entrypoint) nor the OTC transport for a
  bank with XBRL — call-counted via mocks, so the 440-bank build gains
  zero cold fetches by construction.
- Reader/writer key coupling: cached_release_metrics and release_metrics()
  carry ONE versioned key literal — a bump that misses one fails here.
- Staleness gate: a release quarter-end older than ~200 days -> (None, None)
  (the _otc_release_ps precedent — a stopped-publishing bank never drifts).
- OTC path: a cikless (or empty-XBRL) bank serves from its wire release,
  same 200-day gate.
- Config: efficiency_release declared (computed/pct/lower_better, thresholds
  copied from efficiency_ratio); efficiency_ratio UNTOUCHED (still fdic/EEFFR).
- Wiring: compute-side keys emitted; build_bank_metrics passes both through.

Run: python -m unittest tests.test_release_first_efficiency
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import analysis.valuation as va  # noqa: E402
import data.release_metrics as drm  # noqa: E402


FRESH_QEND = (date.today() - timedelta(days=45)).isoformat()
STALE_QEND = (date.today() - timedelta(days=250)).isoformat()


def _envelope(qend=FRESH_QEND, efficiency=57.3):
    return {"cached_at": "2026-08-19T06:00:00",
            "value": {"qend": qend, "metrics": {"efficiency": efficiency},
                      "accession": "0000000001-26-000001"}}


class _CikBank(unittest.TestCase):
    """get_cik resolves; each test controls the cache/OTC seams."""

    def setUp(self):
        import data.bank_mapping as bm
        self.bm = bm
        self._orig_get_cik = bm.get_cik
        bm.get_cik = lambda t: 999

    def tearDown(self):
        self.bm.get_cik = self._orig_get_cik


class TestCacheOnlyNoFetch(_CikBank):
    def test_resolver_serves_from_cache_without_any_fetch_path(self):
        """The build-path pin: a warm cache answers, and neither
        release_metrics() (the fetching entrypoint) nor the frontier check
        nor the OTC transport is ever called."""
        with patch("data.cache.get", return_value=_envelope()) as cget, \
                patch.object(drm, "release_metrics") as rm, \
                patch.object(drm, "_current_accession") as acc, \
                patch("data.otc_release.otc_release_metrics") as otc:
            out = va._resolve_release_efficiency("JPM", sec_has_xbrl=True)
        self.assertEqual(out, (57.3, FRESH_QEND))
        rm.assert_not_called()
        acc.assert_not_called()
        otc.assert_not_called()
        cget.assert_called_once()
        # The read must be ceiling-free: an old-but-valid extraction is
        # DATA, gated by qend — not dropped by a TTL.
        self.assertIsNone(cget.call_args.kwargs.get("max_age_s", "missing"))

    def test_cold_cache_is_na_never_a_fetch(self):
        """No cached extraction -> (None, None) ('—' until the Results board
        / poll-events warms it) — never a live fetch, never the OTC wire for
        a bank that has XBRL."""
        with patch("data.cache.get", return_value=None), \
                patch.object(drm, "release_metrics") as rm, \
                patch.object(drm, "_current_accession") as acc, \
                patch("data.otc_release.otc_release_metrics") as otc:
            out = va._resolve_release_efficiency("JPM", sec_has_xbrl=True)
        self.assertEqual(out, (None, None))
        rm.assert_not_called()
        acc.assert_not_called()
        otc.assert_not_called()

    def test_reader_and_writer_share_one_versioned_key(self):
        """cached_release_metrics duplicates release_metrics()'s key literal
        (the writer's line is itself pinned by test_cache_read_ceilings, so
        it cannot move into a shared constant) — a version bump that misses
        one of the two would silently deaden the reader; exactly one version
        may appear in the module."""
        src = (REPO / "data" / "release_metrics.py").read_text(encoding="utf-8")
        versions = set(re.findall(r'"release_metrics:(v\d+):', src))
        self.assertEqual(len(versions), 1, f"key versions diverged: {versions}")


class TestStalenessGate(_CikBank):
    def test_stale_release_qend_is_none(self):
        with patch("data.cache.get",
                   return_value=_envelope(qend=STALE_QEND)):
            self.assertEqual(
                va._resolve_release_efficiency("JPM", sec_has_xbrl=True),
                (None, None))

    def test_missing_qend_is_none(self):
        with patch("data.cache.get", return_value=_envelope(qend=None)):
            self.assertEqual(
                va._resolve_release_efficiency("JPM", sec_has_xbrl=True),
                (None, None))

    def test_missing_efficiency_metric_is_none(self):
        with patch("data.cache.get",
                   return_value=_envelope(efficiency=None)):
            self.assertEqual(
                va._resolve_release_efficiency("JPM", sec_has_xbrl=True),
                (None, None))


class TestOtcPath(unittest.TestCase):
    def setUp(self):
        import data.bank_mapping as bm
        self.bm = bm
        self._orig_get_cik = bm.get_cik
        bm.get_cik = lambda t: None

    def tearDown(self):
        self.bm.get_cik = self._orig_get_cik

    def test_cikless_bank_serves_wire_release(self):
        with patch("data.otc_release.otc_release_metrics",
                   return_value={"qend": FRESH_QEND,
                                 "metrics": {"efficiency": 61.2}}):
            self.assertEqual(
                va._resolve_release_efficiency("PBAM", sec_has_xbrl=False),
                (61.2, FRESH_QEND))

    def test_stale_wire_release_is_none(self):
        with patch("data.otc_release.otc_release_metrics",
                   return_value={"qend": STALE_QEND,
                                 "metrics": {"efficiency": 61.2}}):
            self.assertEqual(
                va._resolve_release_efficiency("PBAM", sec_has_xbrl=False),
                (None, None))

    def test_nothing_available_is_none(self):
        with patch("data.otc_release.otc_release_metrics", return_value=None):
            self.assertEqual(
                va._resolve_release_efficiency("PBAM", sec_has_xbrl=False),
                (None, None))


class TestConfig(unittest.TestCase):
    def test_release_column_declared(self):
        from config import METRICS_BY_KEY
        m = METRICS_BY_KEY.get("efficiency_release")
        self.assertIsNotNone(m, "efficiency_release column missing")
        self.assertEqual(m["source"], "computed")
        self.assertEqual(m["label"], "Eff (Rel)")
        self.assertEqual(m["format"], "pct")
        fdic = METRICS_BY_KEY["efficiency_ratio"]
        self.assertEqual(m["color_rule"], fdic["color_rule"])
        self.assertEqual(m["thresholds"], fdic["thresholds"])
        self.assertEqual(m["category"], fdic["category"])

    def test_fdic_column_untouched(self):
        """The owner decision: EEFFR stays THE efficiency — uniform for all
        538 banks, screener-stable. The release column ADDS, never replaces."""
        from config import METRICS_BY_KEY
        fdic = METRICS_BY_KEY["efficiency_ratio"]
        self.assertEqual(fdic["source"], "fdic")
        self.assertEqual(fdic["fdic_field"], "EEFFR")
        self.assertEqual(fdic["label"], "Efficiency")

    def test_valuation_screen_lists_release_column(self):
        from config import TABS
        val_tab = next(t for t in TABS if t["key"] == "valuation")
        cols = val_tab["columns"]
        self.assertIn("efficiency_release", cols)
        self.assertIn("efficiency_ratio", cols)  # alongside, not instead


class TestWiring(unittest.TestCase):
    def test_metrics_row_carries_value_and_qend(self):
        import analysis.metrics as am
        resolved = {"efficiency_release": 57.3,
                    "efficiency_release_qend": FRESH_QEND}
        with patch.object(am, "compute_all_valuations", return_value=resolved):
            row = am.build_bank_metrics("X", {}, {}, {"price": None}, [])
        self.assertEqual(row.get("efficiency_release"), 57.3)
        self.assertEqual(row.get("efficiency_release_qend"), FRESH_QEND)

    def test_compute_emits_both_keys_even_when_absent(self):
        """n/a is a value: both keys always present in the computed dict."""
        with patch.object(va, "_resolve_release_efficiency",
                          return_value=(None, None)):
            out = va.compute_all_valuations({"price": None}, {}, {}, [],
                                            ticker=None)
        self.assertIn("efficiency_release", out)
        self.assertIn("efficiency_release_qend", out)
        self.assertIsNone(out["efficiency_release"])


if __name__ == "__main__":
    unittest.main()
