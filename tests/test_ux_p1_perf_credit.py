"""UX review P1 wave (2026-09-30), main-thread fixes:

* P1-05 Rates tab: data/treasury_live.live_yields used to call Yahoo four
  times on the render thread whenever its 90 s cache lapsed (~36 s warm on
  prod). It now reads the refresh-live-yields job's snapshot and never fetches.
* Reserves/NPL for a bank with zero noncurrent loans rendered "0%" in
  bottom-quintile red on Compare (AMBK, ALBY): FDIC zero-fills LNRESNCR on a
  zero denominator. Undefined, not zero.
"""
import sys
import time
import types
import unittest
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

from analysis.credit_dynamics import _reserve_coverage  # noqa: E402
from data.fdic_client import null_unreported_capital  # noqa: E402
import data.treasury_live as tl  # noqa: E402


class _FakeCache:
    def __init__(self, store):
        self.store = store

    def get(self, key, *a, **k):
        return self.store.get(key)


class TestTreasuryLiveReadsSnapshot(unittest.TestCase):
    def _run(self, snap):
        from data.live_rates import _SNAP_KEY
        fake = _FakeCache({_SNAP_KEY: snap} if snap is not None else {})
        with mock.patch("data.cache.get", fake.get):
            return tl.live_yields()

    def test_fresh_snapshot_maps_tenors_to_fred_ids(self):
        snap = {"_ts": time.time() - 30,
                "_v": {"3M": [4.07, 4.06, 4.0], "5Y": [5.03, 5.02, 4.9],
                       "10Y": [5.20, 5.16, 5.0], "30Y": [5.51, 5.5, 5.4]}}
        out = self._run(snap)
        self.assertEqual(set(out), {"DGS3MO", "DGS5", "DGS10", "DGS30"})
        self.assertAlmostEqual(out["DGS10"]["yield"], 5.20)
        self.assertIsNotNone(out["DGS10"]["asof"].tzinfo)   # UTC-aware

    def test_stale_or_missing_snapshot_is_empty_never_live(self):
        self.assertEqual(self._run(None), {})
        self.assertEqual(self._run({"_ts": time.time() - 3600,
                                    "_v": {"10Y": [5.2, 5.1, 5.0]}}), {})

    def test_implausible_or_missing_tenor_skipped(self):
        out = self._run({"_ts": time.time(),
                         "_v": {"10Y": [52.0, 5.1, 5.0], "5Y": None}})
        self.assertEqual(out, {})

    def test_never_calls_yahoo(self):
        # A yfinance import at render is the regression — make it explode.
        boom = types.ModuleType("yfinance")

        def _no(*a, **k):
            raise AssertionError("treasury_live fetched Yahoo at render")
        boom.Ticker = _no
        with mock.patch.dict(sys.modules, {"yfinance": boom}):
            out = self._run({"_ts": time.time(), "_v": {"10Y": [5.2, 5.1, 5.0]}})
        self.assertEqual(out["DGS10"]["yield"], 5.2)


class TestReserveCoverageZeroNpl(unittest.TestCase):
    def test_zero_npl_is_undefined(self):
        # AMBK 2026-06-30: zero noncurrent loans, LNRESNCR zero-filled.
        self.assertIsNone(_reserve_coverage(
            {"LNATRESR": 1.05, "NCLNLSR": 0.0, "LNRESNCR": 0.0}))

    def test_real_coverage_unchanged(self):
        self.assertAlmostEqual(_reserve_coverage(
            {"LNATRESR": 1.2, "NCLNLSR": 0.6}), 200.0)

    def test_fdic_scrub_nulls_zero_filled_lnresncr(self):
        rec = null_unreported_capital(
            {"NCLNLS": 0, "LNRESNCR": 0, "LNATRES": 2500})
        self.assertIsNone(rec["LNRESNCR"])

    def test_fdic_scrub_keeps_real_lnresncr(self):
        rec = null_unreported_capital({"NCLNLS": 1200, "LNRESNCR": 208.3})
        self.assertEqual(rec["LNRESNCR"], 208.3)
        # A record without the component says nothing about the denominator.
        rec = null_unreported_capital({"LNRESNCR": 0})
        self.assertEqual(rec["LNRESNCR"], 0)


class TestPeerMedianLoadersAreCached(unittest.TestCase):
    """The peer medians read one cache row per universe bank (~600) — on
    every render when uncached (Capital Adequacy ~9.5 s warm on prod). The
    test stub makes st.cache_data a pass-through, so pin the decorator."""

    def _decorators(self, path, fn):
        import ast
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn:
                return [ast.unparse(d) for d in node.decorator_list]
        self.fail(f"{fn} not found in {path}")

    def test_capital_peer_cet1_median_cached(self):
        self.assertTrue(any(d.startswith("st.cache_data") for d in
                            self._decorators("ui/capital_dynamics.py",
                                             "_load_peer_cet1_median")))

    def test_credit_peer_reserve_coverage_cached(self):
        self.assertTrue(any(d.startswith("st.cache_data") for d in
                            self._decorators("ui/credit_dynamics.py",
                                             "_load_peer_median_reserve_coverage")))


class TestPressReleaseGateRestored(unittest.TestCase):
    """wire_base defined _COMMENTARY_RE twice; the second (aggregator-scoped)
    definition replaced the first-party gate's regex, so analyst coverage
    passed as a company press release (WTFC/WFC mis-tag, UX-P1-31)."""

    def test_analyst_coverage_is_not_a_press_release(self):
        from data.events.wire_base import is_company_press_release
        self.assertFalse(is_company_press_release(
            "WTFC Initiated Coverage by Wells Fargo -- Rating Set to Equal-Weight"))

    def test_real_releases_still_pass(self):
        from data.events.wire_base import is_company_press_release
        for h in ("Fifth Third Bancorp Announces Redemption of Senior Notes",
                  "Northern Trust Corporation to Webcast Third Quarter 2026 "
                  "Earnings Conference Call"):
            self.assertTrue(is_company_press_release(h), h)

    def test_two_regexes_are_distinct_objects(self):
        import data.events.wire_base as wb
        self.assertIsNot(wb._COMMENTARY_RE, wb._AGGREGATOR_COMMENTARY_RE)
        self.assertTrue(wb._COMMENTARY_RE.search("price target raised"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
