"""
Regression tests for the AUDIT-2026-07-02 P3 analysis/data guards:

  - analysis/capital_dynamics.py — compute_capital_screening_metrics early
    returns must use the SAME key/unit as the main path (buyback_capacity_usd,
    raw dollars), never the stale buyback_capacity_k.
  - analysis/screen_engine.py + analysis/peer_groups.py — NaN is MISSING data:
    it must be excluded as no-data, never scored as FAIL / 0th percentile
    (NaN passes `is not None`, and every comparison against NaN is False).
  - data/econ_calendar.py — the cache key must include the window params so
    different (back_days, fwd_days) windows never serve each other's payloads.
  - data/bank_mapping.py — the FDIC phrase filter must be single-encoded
    (requests encodes params once; pre-quoting double-encoded the phrase and
    made the lookup tier dead). Pinned via a mock of requests.get — no network.
"""
import sys
import types
import unittest
from unittest import mock

# Stub streamlit before importing data modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import requests  # noqa: E402  (URL-encoding pin only; no network calls)

from analysis.capital_dynamics import compute_capital_screening_metrics  # noqa: E402
from analysis.peer_groups import (  # noqa: E402
    compute_peer_percentile,
    metric_percentile_context,
)
from analysis.screen_engine import _as_float, evaluate  # noqa: E402

NAN = float("nan")


class TestCapitalScreeningEarlyReturnKeys(unittest.TestCase):
    """P3: early-return dicts used buyback_capacity_k where the main path
    returns buyback_capacity_usd (raw dollars) — a latent key/unit mismatch."""

    MAIN_PATH_KEYS = {
        "cet1_current", "cet1_qoq_pp", "tbv_cagr_1y", "payout_ratio_4q",
        "buyback_capacity_usd", "capital_alerts_count",
    }

    def test_no_records_early_return_matches_main_path_keys(self):
        out = compute_capital_screening_metrics([])
        self.assertEqual(set(out), self.MAIN_PATH_KEYS)
        self.assertNotIn("buyback_capacity_k", out)
        self.assertIsNone(out["buyback_capacity_usd"])

    def test_empty_timeline_early_return_matches_main_path_keys(self):
        # A record with an unparseable REPDTE survives the loop but is dropped
        # by dropna(date) → timeline.empty → second early return.
        out = compute_capital_screening_metrics(
            [{"REPDTE": "not-a-date", "EQTOT": 1000}])
        self.assertEqual(set(out), self.MAIN_PATH_KEYS)
        self.assertNotIn("buyback_capacity_k", out)
        self.assertIsNone(out["buyback_capacity_usd"])

    def test_main_path_key_set_is_the_pinned_contract(self):
        # Guards the pin itself: if the main path gains/renames a key, this
        # fails and the early returns above must be re-aligned with it.
        records = [
            {"REPDTE": "20240331", "EQTOT": 1000, "NETINC": 100, "IDT1CER": 11.0},
            {"REPDTE": "20240630", "EQTOT": 1050, "NETINC": 210, "IDT1CER": 11.2},
        ]
        out = compute_capital_screening_metrics(records)
        self.assertEqual(set(out), self.MAIN_PATH_KEYS)


class TestScreenEngineNanIsMissing(unittest.TestCase):
    """P3: NaN passed `is not None` and scored as FAIL. It must be no-data."""

    def test_as_float_maps_nan_to_none(self):
        self.assertIsNone(_as_float(NAN))
        self.assertIsNone(_as_float("nan"))     # float("nan") via str parse
        self.assertIsNone(_as_float(None))
        self.assertEqual(_as_float("1.5"), 1.5)
        self.assertEqual(_as_float(2), 2.0)

    def test_absolute_spec_nan_excluded_as_nodata_not_fail(self):
        banks = [{"ticker": "NANB", "roaa": NAN},
                 {"ticker": "GOOD", "roaa": 1.2}]
        specs = [{"kind": "absolute", "metric": "roaa", "op": ">", "value": 0.0}]
        kept, n_nodata = evaluate(banks, specs)
        self.assertEqual([b["ticker"] for b in kept], ["GOOD"])
        # Pre-fix: NaN > 0 is False → scored FAIL → n_nodata was 0.
        self.assertEqual(n_nodata, 1)

    def test_peer_relative_nan_excluded_not_zeroth_percentile(self):
        # Pre-fix a NaN bank ranked 0th percentile and PASSED "Bottom 50%".
        banks = [{"ticker": "NANB", "roaa": NAN},
                 {"ticker": "LO", "roaa": 0.5},
                 {"ticker": "MID", "roaa": 1.0},
                 {"ticker": "HI", "roaa": 2.0}]
        specs = [{"kind": "peer_relative", "metric": "roaa",
                  "band": "Bottom", "pct": 50.0}]
        kept, n_nodata = evaluate(banks, specs)
        self.assertNotIn("NANB", [b["ticker"] for b in kept])
        self.assertEqual(n_nodata, 1)
        # The NaN bank must not poison the real banks' percentiles: with 3
        # valid values, LO (lowest) and MID sit in the bottom half and pass.
        self.assertEqual({b["ticker"] for b in kept}, {"LO", "MID"})


class TestPeerGroupsNanGuards(unittest.TestCase):
    def test_percentile_none_for_nan_bank_value(self):
        self.assertIsNone(compute_peer_percentile(NAN, [1.0, 2.0, 3.0]))

    def test_percentile_ignores_nan_peers(self):
        # valid peers = [1.0, 3.0]; Hazen: (1 below + 0) / 2 * 100 = 50.
        self.assertEqual(compute_peer_percentile(2.0, [1.0, NAN, 3.0]), 50.0)

    def test_percentile_none_when_only_nan_peers(self):
        self.assertIsNone(compute_peer_percentile(2.0, [NAN, NAN]))

    @staticmethod
    def _cohort(self_roaa, peer_roaas):
        banks = [{"ticker": "SELF", "total_assets": 5e9, "roaa": self_roaa}]
        for i, v in enumerate(peer_roaas):
            banks.append({"ticker": f"P{i}", "total_assets": 5e9, "roaa": v})
        return banks

    def test_context_skips_metric_when_self_value_is_nan(self):
        banks = self._cohort(NAN, [0.8, 0.9, 1.0, 1.1, 1.2])
        out = metric_percentile_context("SELF", banks, metric_keys=["roaa"])
        self.assertNotIn("roaa", out)  # pre-fix: present at 0th percentile

    def test_context_excludes_nan_peers_from_cohort(self):
        banks = self._cohort(1.0, [0.8, 0.9, NAN, 1.1, 1.2, 1.3])
        out = metric_percentile_context("SELF", banks, metric_keys=["roaa"])
        self.assertIn("roaa", out)
        # 7 in cohort (self + 6 peers) minus the NaN peer = 6 populated values.
        self.assertEqual(out["roaa"]["n"], 6)
        self.assertEqual(out["roaa"]["out_of"], 6)


class TestEconCalendarCacheKeyWindow(unittest.TestCase):
    """P3: the cache key omitted (back_days, fwd_days), so different windows
    could serve each other's cached payloads."""

    @staticmethod
    def _run(back, fwd):
        with mock.patch("data.cache.get", return_value=None) as get, \
             mock.patch("data.cache.put") as put, \
             mock.patch("data.freshness.is_fresh", return_value=False), \
             mock.patch("data.econ_calendar._get", return_value=[]):
            from data.econ_calendar import get_us_calendar
            get_us_calendar(back_days=back, fwd_days=fwd)
        return get.call_args[0][0], put.call_args[0][0]

    def test_key_includes_window_params(self):
        from data.econ_calendar import CACHE_KEY
        get_key, put_key = self._run(3, 5)
        self.assertEqual(get_key, put_key)
        self.assertTrue(get_key.startswith(CACHE_KEY))
        self.assertIn("3", get_key)
        self.assertIn("5", get_key)

    def test_different_windows_use_different_keys(self):
        key_a, _ = self._run(3, 5)
        key_b, _ = self._run(10, 14)
        self.assertNotEqual(key_a, key_b)


class TestFdicPhraseSearchSingleEncoded(unittest.TestCase):
    """P3: the phrase filter was urllib.parse.quote()d BEFORE being handed to
    requests, which encodes params again → '%2522...' double-encoding → the
    lookup tier matched nothing. Pins the built query only (HTTP mocked)."""

    @staticmethod
    def _capture_calls(json_payload):
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = json_payload
        with mock.patch("data.bank_mapping.requests.get",
                        return_value=resp) as rget:
            from data.bank_mapping import search_fdic_by_name
            result = search_fdic_by_name("Atlantic Union Bankshares")
        return result, rget.call_args_list

    def test_filters_param_is_raw_quoted_phrase_not_pre_encoded(self):
        result, calls = self._capture_calls({"data": []})
        self.assertIsNone(result)  # None-safe on no match
        self.assertGreater(len(calls), 0)
        for c in calls:
            filt = c.kwargs["params"]["filters"]
            # Raw quoted phrase, encoded exactly once by requests later.
            self.assertNotIn("%", filt)
            self.assertRegex(filt, r'^NAMEHCR:"[A-Z ]+"$')
        # Most specific phrase first.
        first = calls[0].kwargs["params"]["filters"]
        self.assertEqual(first, 'NAMEHCR:"ATLANTIC UNION BANKSHARES"')

    def test_prepared_url_is_single_encoded(self):
        _, calls = self._capture_calls({"data": []})
        url = calls[0].args[0] if calls[0].args else calls[0].kwargs["url"]
        pr = requests.models.PreparedRequest()
        pr.prepare_url(url, calls[0].kwargs["params"])
        self.assertIn("%22", pr.url)        # quotes encoded once
        self.assertNotIn("%2522", pr.url)   # never twice
        self.assertNotIn("%2520", pr.url)

    def test_match_flows_through_to_cert(self):
        payload = {"data": [{"data": {
            "CERT": 33011, "NAME": "ATLANTIC UNION BANK",
            "NAMEHCR": "ATLANTIC UNION BANKSHARES CORPORATION",
            "ASSET": 20_000_000, "ACTIVE": 1,
        }}]}
        result, _ = self._capture_calls(payload)
        self.assertEqual(result, 33011)


class TestCleanNameTokenBoundary(unittest.TestCase):
    """(2026-07-10, surfaced during the P3 batch) _clean_name used SUBSTRING
    replaces, so " CORP" ate the middle of " CORPORATION"
    ("…BANKSHARESORATION") and " CO" the head of " COMMUNITY"/" COMPANY"/
    " COLUMBIA" ("MMUNITY…"). Suffix words must be dropped at TOKEN boundaries
    only — non-suffix words survive intact."""

    def test_corporation_not_glued(self):
        from data.bank_mapping import _clean_name
        self.assertEqual(_clean_name("Atlantic Union Bankshares Corporation"),
                         "ATLANTIC UNION BANKSHARES")

    def test_community_and_columbia_survive(self):
        from data.bank_mapping import _clean_name
        self.assertEqual(_clean_name("First Community Corp"), "FIRST COMMUNITY")
        self.assertEqual(_clean_name("Columbia Banking System, Inc."),
                         "COLUMBIA BANKING SYSTEM")

    def test_suffix_words_still_dropped(self):
        from data.bank_mapping import _clean_name
        self.assertEqual(_clean_name("CVB Financial Corp."), "CVB")
        self.assertEqual(_clean_name("Southern Financial Corp"), "SOUTHERN")
        self.assertEqual(_clean_name("White River Bancshares Co /DE"),
                         "WHITE RIVER BANCSHARES")


if __name__ == "__main__":
    unittest.main()
