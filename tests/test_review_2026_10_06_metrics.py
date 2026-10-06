"""REVIEW 2026-10-06 — the per-bank metrics row (Screen & Compare / Overview).

Every case pins the reviewer's real numbers (data as of 2026-06-30; FDIC
financials API + SEC companyfacts, captured 2026-10-06) and fails on the
pre-fix code:

  P1-1  holdco ROATCE over AVERAGE TCE; merger in the TTM window → n/a + flag
  P1-2  ROAA 4Q / NIM 4Q from the single-quarter ROAQ / NIMYQ
  P1-3  CRE / total risk-based capital (the regulatory concentration)
  P1-4  cache-miss paths through the charter-group seam; keep last good
  P2    AOCI+HTM period match, Div Yield relabel, P/TBV share-basis
        mismatch, same-entity earnings normalizer, acquisition-driven TCE
        CAGR, absent intangibles n/a, "(Bank)" entity labels

Run: python -m unittest tests.test_review_2026_10_06_metrics
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import analysis.valuation as val  # noqa: E402
from analysis.metrics import _aoci_metrics  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# ── Real inputs (2026-06-30) ────────────────────────────────────────────
# SEC holdco, $: NI to common TTM and common TCE at the five window ends.
ONB_SEC = {"book_value_total": 8_583_843_000.0, "preferred_present": True,
           "preferred_stock": 230_500_000.0, "intangible_adjustment": 2_862_427_000.0,
           "net_income_to_common_ttm": 870_141_000.0, "tce_holdco": 5_490_916_000.0,
           "sec_as_of": "2026-06-30"}
ONB_TCE = {"2026-03-31": 5_393.7e6, "2025-12-31": 5_356.3e6,
           "2025-09-30": 5_151.8e6, "2025-06-30": 4_951.5e6}
PNFP_SEC = {"book_value_total": 14_828_000_000.0, "preferred_present": True,
            "preferred_stock": 781_000_000.0, "intangible_adjustment": 4_524_000_000.0,
            "net_income_to_common_ttm": 783_673_000.0, "tce_holdco": 9_523_000_000.0,
            "sec_as_of": "2026-06-30"}
# Legacy Pinnacle pre-merger points; 2025-09-30 unresolvable in companyfacts.
PNFP_TCE = {"2026-03-31": 9_244.0e6, "2025-12-31": 4_958.7e6,
            "2025-09-30": None, "2025-06-30": 4_229.2e6}
HBAN_TCE = {"2026-03-31": 19_158e6, "2025-12-31": 15_469e6,
            "2025-09-30": 13_874e6, "2025-06-30": 13_282e6}


def _window(ticker, sec, pts, bank_goodwill_k=None):
    """holdco_tce_window with the companyfacts seam stubbed to `pts`."""
    with patch("data.bank_mapping.get_cik", return_value=1), \
         patch("data.sec_client.fetch_company_facts", return_value={"facts": {}}), \
         patch("data.sec_client._balance_sheet_date", return_value="2026-06-30"), \
         patch.object(val, "_holdco_tce_at", side_effect=lambda f, d: pts[d]):
        return val.holdco_tce_window(ticker, sec, bank_goodwill_k)


class TestP1HoldcoRoatceAverageTce(unittest.TestCase):
    def test_onb_average_of_five_quarter_ends(self):
        w = _window("ONB", ONB_SEC, ONB_TCE)
        self.assertEqual([d for d, _ in w["points"]],
                         ["2026-06-30", "2026-03-31", "2025-12-31",
                          "2025-09-30", "2025-06-30"])
        # (5,490.916 + 5,393.7 + 5,356.3 + 5,151.8 + 4,951.5) / 5 = 5,268.843
        self.assertAlmostEqual(w["avg"] / 1e6, 5268.8432, places=3)
        self.assertFalse(w["merger"])
        got = val.compute_roatce_holdco(ONB_SEC, w)
        self.assertAlmostEqual(got, 870.141 / 5268.8432 * 100, places=4)   # 16.515
        # The pre-fix figure (period-end TCE) was 15.847 — no longer served.
        self.assertAlmostEqual(val.compute_roatce_holdco(ONB_SEC), 15.8469, places=3)

    def test_pnfp_merger_is_na_and_flagged(self):
        w = _window("PNFP", PNFP_SEC, PNFP_TCE)
        self.assertTrue(w["merger"])
        self.assertIn("merger in TTM window", w["reason"])
        self.assertIsNone(w["avg"])
        self.assertIsNone(val.compute_roatce_holdco(PNFP_SEC, w))
        # Pre-fix: 783.673 / 9,523 = 8.23% → Fair Price $48.14.
        self.assertAlmostEqual(val.compute_roatce_holdco(PNFP_SEC), 8.2293, places=3)

    def test_hban_veritex_cadence_merger(self):
        # 19,158 (3/31) − 13,282 = 5,876 > 25% × 13,282 + TTM NI-to-common 2,246 = 5,566.5
        sec = dict(ONB_SEC, tce_holdco=19_301e6, net_income_to_common_ttm=2_246e6)
        w = _window("HBAN", sec, HBAN_TCE)
        self.assertTrue(w["merger"])
        self.assertIn("TCE rose 44%", w["reason"])

    def test_fitb_comerica_merger(self):
        sec = dict(ONB_SEC, tce_holdco=21_057e6, net_income_to_common_ttm=2_198e6)
        self.assertTrue(_window("FITB", sec, {
            "2026-03-31": 20_725e6, "2025-12-31": 14_938e6,
            "2025-09-30": 14_314e6, "2025-06-30": 14_015e6})["merger"])

    def test_organic_growth_is_not_a_merger(self):
        # LARK: 113.7 → 132.8 (+16.8%) is earnings, not a deal.
        sec = dict(ONB_SEC, tce_holdco=132.756e6, preferred_present=False,
                   net_income_to_common_ttm=None, net_income=20.128e6)
        w = _window("LARK", sec, {"2026-03-31": 127.4e6, "2025-12-31": 126.3e6,
                                  "2025-09-30": 121.2e6, "2025-06-30": 113.7e6})
        self.assertFalse(w["merger"])
        self.assertAlmostEqual(w["avg"] / 1e6, (132.756 + 127.4 + 126.3 + 121.2 + 113.7) / 5,
                               places=3)

    def test_retained_earnings_growth_is_not_a_merger(self):
        # CARE: 403.4 → 537.1 (+33% raw) on $126.9M NI-to-common, no deal —
        # 133.7 < 25% × 403.4 + 126.9 = 227.8. A raw >25% rule flagged it.
        sec = dict(ONB_SEC, tce_holdco=537.141e6, net_income_to_common_ttm=126.928e6)
        w = _window("CARE", sec, {"2026-03-31": 502.8e6, "2025-12-31": 417.6e6,
                                  "2025-09-30": 410.6e6, "2025-06-30": 403.4e6})
        self.assertFalse(w["merger"])
        self.assertAlmostEqual(val.compute_roatce_holdco(sec, w),
                               126.928 / ((537.141 + 502.8 + 417.6 + 410.6 + 403.4) / 5) * 100,
                               places=4)

    def test_window_begin_unresolvable_is_na(self):
        # BAFN: preferred unresolved at every pre-recap quarter-end → the
        # window's begin has no common TCE → n/a, never period-end −239%.
        sec = {"book_value_total": 115_901_000.0, "preferred_present": True,
               "preferred_stock": 90_238_000.0, "intangible_adjustment": 0.0,
               "net_income_to_common_ttm": -61_251_000.0, "tce_holdco": 25_663_000.0}
        w = _window("BAFN", sec, {d: None for d in PNFP_TCE})
        self.assertIsNone(w["avg"])
        self.assertIsNone(val.compute_roatce_holdco(sec, w))
        self.assertAlmostEqual(val.compute_roatce_holdco(sec), -238.674, places=2)

    def test_untagged_holdco_goodwill_is_na(self):
        w = _window("X", dict(ONB_SEC, intangible_adjustment=0.0), ONB_TCE,
                    bank_goodwill_k=2_000_000)
        self.assertIsNone(w["avg"])
        self.assertIn("goodwill untagged", w["reason"])
        # Immaterial bank goodwill (TRST: $553K beside ~$0.8B TCE) stays.
        trst = dict(ONB_SEC, intangible_adjustment=0.0, tce_holdco=800e6)
        w = _window("TRST", trst, {d: 800e6 for d in ONB_TCE}, bank_goodwill_k=553)
        self.assertAlmostEqual(w["avg"], 800e6)

    def test_merger_blanks_the_fair_value_chain(self):
        merger = {"avg": None, "points": [], "merger": True,
                  "reason": "merger in TTM window — test"}
        row = _row("PNFP", PNFP_SEC, price=100.0, tbvps=63.02, window=merger,
                   fdic_hist=_hist_with_netinc())
        for k in ("roatce_holdco", "roatce_blended", "fair_ptbv", "fair_price",
                  "ptbv_discount"):
            self.assertIsNone(row[k], k)
            self.assertIn("merger in TTM window", row["_notes"][k])
        self.assertIs(row["roatce_holdco_merger"], True)

    def test_unresolved_window_falls_back_labelled(self):
        # WTFC/BAFN class: SEC data present, window begin unresolvable
        # (preferred changed inside it) → holdco n/a with its reason, and the
        # FDIC-blend fallback says it is bank-sub basis.
        unresolved = {"avg": None, "points": [], "merger": False,
                      "reason": "TCE not resolvable at the window's begin and end"}
        row = _row("WTFC", ONB_SEC, price=None, tbvps=92.13, window=unresolved,
                   fdic_hist=_hist_with_netinc())
        self.assertIsNone(row["roatce_holdco"])
        self.assertEqual(row["_notes"]["roatce_holdco"], unresolved["reason"])
        self.assertIsNotNone(row["roatce_blended"])
        self.assertIn("bank-subsidiary (FDIC) basis", row["_notes"]["roatce_blended"])

    def test_holdco_tce_at_carries_fiscal_year_goodwill(self):
        def usd(*pairs):
            return {"units": {"USD": [{"end": e, "val": v, "form": f, "filed": e}
                                      for e, v, f in pairs]}}
        facts = {"facts": {"us-gaap": {
            "StockholdersEquity": usd(("2026-03-31", 1_000.0, "10-Q")),
            "Goodwill": usd(("2025-12-31", 150.0, "10-K")),
            "FiniteLivedIntangibleAssetsNet": usd(("2025-12-31", 20.0, "10-K")),
        }}}
        # Goodwill tagged only at FY-end → carried (snapshot convention).
        self.assertEqual(val._holdco_tce_at(facts, "2026-03-31"), 1_000.0 - 170.0)


def _hist_with_netinc():
    """Four consecutive quarters + a fifth (ONB ROA/ROAQ/NIMY/NIMYQ)."""
    rows = [("2026-06-30", 1.4064154686484507, 1.46, 3.6045296882860427, 3.61),
            ("2026-03-31", 1.3521781685960255, 1.35, 3.603931805805133, 3.60),
            ("2025-12-31", 1.164154127517348, 1.32, 3.6339160574679585, 3.69),
            ("2025-09-30", 1.093577857493333, 1.13, 3.584130389305689, 3.66),
            ("2025-06-30", 1.0, 1.1, 3.5, 3.5)]
    return [{"REPDTE": d, "ROA": a, "ROAQ": aq, "NIMY": n, "NIMYQ": nq,
             "NETINC": 100.0 * (i + 1), "EQTOT": 1000.0, "INTAN": 100.0, "CERT": 3832}
            for i, (d, a, aq, n, nq) in enumerate(rows)]


def _row(ticker, sec, price, tbvps, window=None, fdic_hist=None, fdic=None,
         capital_return=None, acquisitions=None):
    cap = dict(val._CAPITAL_RETURN_DEFAULTS, **(capital_return or {}))
    hist = fdic_hist if fdic_hist is not None else []
    with patch.object(val, "_resolve_eps", return_value=(None, None, False)), \
         patch.object(val, "_resolve_tbvps", return_value=(tbvps, "reconstructed", False)), \
         patch.object(val, "_resolve_bvps", return_value=(tbvps, "reconstructed", False)), \
         patch.object(val, "_sec_facts_lag", return_value={
             "lag": None, "facts_as_of": None, "filed_period": None,
             "filed_date": None, "filed_form": None}), \
         patch.object(val, "_resolve_release_efficiency", return_value=(None, None)), \
         patch.object(val, "_otc_release_shares", return_value=None), \
         patch.object(val, "holdco_tce_window", return_value=window or {
             "avg": None, "points": [], "merger": False, "reason": "stub"}), \
         patch.object(val, "_compute_capital_return_for_ticker", return_value=cap), \
         patch("ui.history_range.group_acquisitions", return_value=acquisitions or []):
        return val.compute_all_valuations({"price": price}, sec,
                                          fdic if fdic is not None else (hist[0] if hist else {}),
                                          hist, ticker)


class TestP1SingleQuarterRatios(unittest.TestCase):
    def test_onb_roaa_and_nim_4q(self):
        row = _row("ONB", {}, 22.0, None, fdic_hist=_hist_with_netinc())
        # (1.46 + 1.35 + 1.32 + 1.13) / 4 — pre-fix YTD mean was 1.2541
        self.assertAlmostEqual(row["roaa_4q"], 1.315, places=10)
        # (3.61 + 3.60 + 3.69 + 3.66) / 4 — pre-fix 3.6066
        self.assertAlmostEqual(row["nim_4q"], 3.64, places=10)

    def test_bafn_roaa_4q(self):
        hist = _hist_with_netinc()
        for r, q in zip(hist, (-11.15, -1.84, -1.07, -5.55)):
            r["ROAQ"] = q
        row = _row("BAFN", {}, 12.0, None, fdic_hist=hist)
        self.assertAlmostEqual(row["roaa_4q"], -4.9025, places=10)   # pre-fix −2.98


class TestP1CreConcentration(unittest.TestCase):
    # FDIC 6/30/2026, $K. Reviewer's figures exclude RC-C Memo 3 (LNCOMRE):
    # ONB 252 / PNFP 231 / LARK 126 / BAFN 116 — reproduced below as the
    # three secured legs ÷ RBC; the guidance's total CRE adds Memo 3.
    CASES = {
        "ONB": (2_445_540, 5_855_437, 8_754_318, 358_260, 6_777_660, 6_252_432, 256.9258, 251.6),
        "PNFP": (5_890_023, 4_864_508, 16_581_377, 1_437_223, 11_849_111, 10_754_634, 242.8294, 230.7),
        "LARK": (23_357, 45_039, 148_937, 0, 172_867, 159_899, 125.7227, 125.7),
        "BAFN": (38_094, 5_029, 86_430, 0, 112_208, 101_140, 115.4579, 115.5),
    }

    def test_real_banks(self):
        for t, (cons, mult, nrot, memo3, rbc, t1, want, reviewer) in self.CASES.items():
            rec = {"LNRECONS": cons, "LNREMULT": mult, "LNRENROT": nrot,
                   "LNCOMRE": memo3, "RBC": rbc, "RBCT1": t1}
            self.assertAlmostEqual(val.compute_cre_concentration(rec), want, places=3, msg=t)
            self.assertAlmostEqual((cons + mult + nrot) / rbc * 100, reviewer, places=1, msg=t)

    def test_onb_row_replaces_nonfarm_over_equity(self):
        cons, mult, nrot, memo3, rbc, t1, want, _ = self.CASES["ONB"]
        fdic = {"LNRECONS": cons, "LNREMULT": mult, "LNRENROT": nrot, "LNCOMRE": memo3,
                "RBC": rbc, "RBCT1": t1, "LNRENRES": 14_719_960, "EQTOT": 8_356_262}
        row = _row("ONB", {}, 22.0, None, fdic=fdic)
        self.assertAlmostEqual(row["cre_to_capital"], want, places=3)   # pre-fix 176.15

    def test_preconditions_are_na(self):
        base = {"LNRECONS": 1.0, "LNREMULT": 1.0, "LNRENROT": 1.0, "LNCOMRE": 0.0,
                "RBC": 10.0, "RBCT1": 9.0}
        self.assertIsNone(val.compute_cre_concentration(dict(base, RBC=0)),
                          "CBLR elector: RBC not reported")
        self.assertIsNone(val.compute_cre_concentration(dict(base, RBC=8.0)),
                          "group RBC < Tier 1: a CBLR charter inside the sum")
        self.assertIsNone(val.compute_cre_concentration(
            {k: v for k, v in base.items() if k != "LNCOMRE"}), "pre-field cached record")
        self.assertEqual(val.compute_cre_concentration(dict(
            base, LNRECONS=0, LNREMULT=0, LNRENROT=0)), 0.0, "a $0 CRE book is data")

    def test_fields_are_fetched(self):
        from config import get_fdic_fields
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        have = _BASE_FINANCIALS_FIELDS | get_fdic_fields()
        for f in ("LNRECONS", "LNREMULT", "LNRENROT", "LNCOMRE", "RBC", "RBCT1",
                  "ROAQ", "NIMYQ", "INTANGW"):
            self.assertIn(f, have)

    def test_components_sum_across_a_charter_group(self):
        from data.cert_group import aggregate_records
        a = {"CERT": 1, "REPDTE": "20260630", "ASSET": 2.0, "LNRECONS": 10.0,
             "LNREMULT": 20.0, "LNRENROT": 30.0, "LNCOMRE": 5.0, "RBC": 40.0, "RBCT1": 35.0}
        b = {"CERT": 2, "REPDTE": "20260630", "ASSET": 1.0, "LNRECONS": 1.0,
             "LNREMULT": 2.0, "LNRENROT": 3.0, "LNCOMRE": 0.0, "RBC": 10.0, "RBCT1": 9.0}
        g = aggregate_records([a, b])
        self.assertAlmostEqual(val.compute_cre_concentration(g), 71.0 / 50.0 * 100)


class TestP1CacheMissSeam(unittest.TestCase):
    def test_parallel_group_fetch_limit_8_and_absent_on_failure(self):
        from data.loaders import fetch_group_histories_parallel
        calls = []

        def fake(t, limit, cert):
            calls.append((t, limit, cert))
            if t == "BAD":
                return []
            if t == "BOOM":
                raise RuntimeError("429")
            return [{"REPDTE": "20260630", "CERT": cert}]
        with patch("data.cert_group.fetch_group_history", side_effect=fake):
            got = fetch_group_histories_parallel({"WTFC": 33935, "BAD": 1, "BOOM": 2})
        self.assertEqual(set(got), {"WTFC"})
        self.assertEqual(sorted(calls), [("BAD", 8, 1), ("BOOM", 8, 2), ("WTFC", 8, 33935)])

    def test_cache_miss_paths_use_the_group_seam(self):
        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        loader = app_src[app_src.index("def load_fdic_data"):app_src.index("def load_sec_data")]
        self.assertIn("fetch_group_histories_parallel", loader)
        self.assertNotIn("fdic_client.fetch_multiple_banks_parallel", app_src)
        from jobs import refresh_home_snapshot as rhs
        src = inspect.getsource(rhs._load_fdic)
        self.assertIn("fetch_group_histories_parallel", src)
        self.assertNotIn("fetch_multiple_banks_parallel", src)

    def test_refresh_universe_keeps_last_good_fdic_copy(self):
        from jobs import refresh_universe as ru
        src = inspect.getsource(ru)
        self.assertNotIn('cache.invalidate(f"fdic:{ticker}")', src)
        self.assertNotIn('cache.invalidate(f"fdic_hist:{ticker}")', src)

    def test_refresh_one_failed_group_fetch_writes_nothing(self):
        from jobs import refresh_universe as ru
        cache = MagicMock()
        with patch("data.bank_mapping.get_cik", return_value=None), \
             patch("data.bank_mapping.get_fdic_cert", return_value=33935), \
             patch("data.cache.invalidate", cache.invalidate), \
             patch("data.cache.put", cache.put), \
             patch("data.cache.put_fdic", cache.put_fdic), \
             patch("data.cert_group.fetch_group_history", return_value=[]), \
             patch("data.cert_group.get_cert_group", return_value=[33935]), \
             patch("data.fdic_structure.get_acquisition_history", return_value=[]):
            row = ru.refresh_one("WTFC")
        invalidated = [c.args[0] for c in cache.invalidate.call_args_list]
        self.assertNotIn("fdic:WTFC", invalidated)
        self.assertNotIn("fdic_hist:WTFC", invalidated)
        cache.put.assert_not_called()
        cache.put_fdic.assert_not_called()
        self.assertIn("fdic_group_fetch_failed:kept_last_good", row["warnings"])


class TestP2(unittest.TestCase):
    JPM_FDIC = {"REPDTE": "20260630", "SCHF": 250313000, "SCHA": 268516000,
                "EQTOT": 341610000, "INTAN": 50505000}
    JPM_SEC = {"aoci_holdco": -7_693e6, "tce_holdco": 299_547e6,
               "tce_goodwill_prior": False, "sec_as_of": "2026-06-30"}

    def test_aoci_htm_requires_same_period(self):
        ok = _aoci_metrics(self.JPM_FDIC, self.JPM_SEC, None)
        self.assertAlmostEqual(ok["aoci_htm_holdco_pct_tce"], -8.6450, places=3)
        lag = _aoci_metrics(self.JPM_FDIC, dict(self.JPM_SEC, sec_as_of="2026-03-31"), None)
        self.assertIsNone(lag["aoci_htm_holdco_pct_tce"])
        self.assertIn("period mismatch", lag["_aoci_htm_note"])
        self.assertAlmostEqual(lag["aoci_holdco_pct_tce"], -2.5682, places=3,
                               msg="AOCI alone stays — no cross-date sum in it")

    def test_div_yield_sec_relabelled(self):
        from config import METRICS_BY_KEY
        self.assertEqual(METRICS_BY_KEY["dividend_yield"]["label"], "Div Yield")
        self.assertNotEqual(METRICS_BY_KEY["dividend_yield_sec"]["label"], "Div Yield")
        self.assertIn("incl. pref", METRICS_BY_KEY["dividend_yield_sec"]["label"])

    def test_bafn_share_basis_mismatch(self):
        # 4,106,905 quarter-end vs 26,962,815 cover (preferred converted
        # after 6/30): |4.107 − 26.963| / 26.963 = 84.8% divergence.
        sec = {"shares_cover_divergence_pct": 84.76826325441168,
               "shares_outstanding": 4_106_905, "shares_for_market_cap": 26_962_815}
        row = _row("BAFN", sec, 12.0, 6.248744492507131)
        for k in ("ptbv_ratio", "tbvps", "pb_ratio", "bvps", "fair_price", "ptbv_discount"):
            self.assertIsNone(row[k], k)
            self.assertIn("share-basis mismatch", row["_notes"][k])
        self.assertIs(row["ptbv_basis_mismatch"], True)
        self.assertAlmostEqual(row["market_cap"], 12.0 * 26_962_815)
        # Under the threshold the multiple stands (PNFP 0.07%).
        ok = _row("PNFP", {"shares_cover_divergence_pct": 0.0745}, 100.0, 63.02)
        self.assertAlmostEqual(ok["ptbv_ratio"], 100.0 / 63.02)

    def test_holdco_roatce_uses_holdco_earnings_factor(self):
        # Bank-sub NI with a one-time spike (last quarter 10× the rest) would
        # have scaled the holdco ROATCE by 0.4; holdco NI is steady → 1.0.
        hist = _hist_with_netinc()
        ytd = {"2026-06-30": 1100.0, "2026-03-31": 100.0, "2025-12-31": 400.0,
               "2025-09-30": 300.0, "2025-06-30": 200.0}
        hist += [{"REPDTE": "2025-03-31", "NETINC": 100.0, "EQTOT": 1000.0, "INTAN": 100.0},
                 {"REPDTE": "2024-12-31", "NETINC": 400.0, "EQTOT": 1000.0, "INTAN": 100.0}]
        for r in hist:
            r["NETINC"] = ytd.get(r["REPDTE"], r["NETINC"])
        self.assertLess(val._normalized_earnings_factor(hist), 0.85)
        window = {"avg": 5_268.8432e6, "points": [], "merger": False, "reason": None}
        row = _row("ONB", ONB_SEC, 22.0, 14.32, window=window, fdic_hist=hist,
                   capital_return={"_holdco_ni_q": [230e6, 220e6, 215e6, 205e6, 210e6, 200e6]})
        self.assertEqual(row["earnings_norm_factor"], 1.0)
        self.assertAlmostEqual(row["roatce_normalized"], row["roatce_holdco"])
        self.assertAlmostEqual(row["roatce_holdco"], 16.5148, places=3)
        self.assertNotIn("_holdco_ni_q", row)
        # FDIC-basis fallback keeps the FDIC factor.
        fb = _row("X", {}, 22.0, 14.32, fdic_hist=hist)
        self.assertLess(fb["earnings_norm_factor"], 0.85)

    def test_winsorized_factor_matches_the_fdic_path(self):
        self.assertEqual(val._winsorized_ttm_factor([1.0] * 3), 1.0)
        self.assertAlmostEqual(val._winsorized_ttm_factor([1000, 100, 100, 100, 100, 100]),
                               (300 + 300) / 1300)

    def test_tce_cagr_acquisition_note(self):
        hist = _hist_with_netinc()
        deal = {"date": "2026-01-02", "target_name": "Synovus Bank", "target_cert": 873}
        with patch.object(val, "_compute_capital_dynamics",
                          return_value={"tbv_cagr_1y": 111.6}):
            row = _row("PNFP", {}, 100.0, 63.02, fdic_hist=hist, acquisitions=[deal])
            self.assertIs(row["tbv_cagr_1y_acq"], True)
            self.assertIn("Synovus Bank (2026-01-02)", row["_notes"]["tbv_cagr_1y"])
            self.assertEqual(row["tbv_cagr_1y"], 111.6, "the figure stays; only color/note change")
            old = dict(deal, date="2024-06-30")   # before the window
            row = _row("PNFP", {}, 100.0, 63.02, fdic_hist=hist, acquisitions=[old])
            self.assertIs(row["tbv_cagr_1y_acq"], False)
            self.assertNotIn("tbv_cagr_1y", row["_notes"])

    def test_absent_intangibles_are_na(self):
        rec = {"NETINC": 100.0, "EQTOT": 1000.0, "REPDTE": "20261231"}
        self.assertIsNone(val.compute_roatce(rec))                       # was 10.0
        self.assertAlmostEqual(val.compute_roatce(dict(rec, INTAN=0)), 10.0)
        hist = [{k: v for k, v in r.items() if k != "INTAN"} for r in _hist_with_netinc()]
        self.assertIsNone(val.compute_roatce_4q(hist))
        sec = {"book_value_total": 1000.0, "net_income": 100.0}   # no intangible fields
        self.assertIsNone(val.compute_roatce_holdco(sec))            # was 10.0
        self.assertAlmostEqual(val.compute_roatce_holdco(dict(sec, goodwill=0.0)), 10.0)

    def test_bank_entity_labels(self):
        from config import METRICS_BY_KEY
        for k in ("roaa", "roaa_4q", "nim", "nim_4q", "efficiency_ratio",
                  "npl_ratio", "cet1_ratio", "cre_to_capital"):
            self.assertIn("(Bank)", METRICS_BY_KEY[k]["label"], k)
        self.assertIn("(HoldCo)", METRICS_BY_KEY["roatce_holdco"]["label"])

    def test_screen_renders_flagged_cells(self):
        import ui.generic_table as gt
        rows = [{"ticker": "PNFP", "roatce_holdco": None, "tbv_cagr_1y": 111.6,
                 "_notes": {"roatce_holdco": "merger in TTM window — x",
                            "tbv_cagr_1y": "acquisition-driven — y"}},
                {"ticker": "ONB", "roatce_holdco": 16.5, "tbv_cagr_1y": 12.3}]
        st = MagicMock()
        with patch.object(gt, "st", st), \
             patch.object(gt, "_fast_name_lookup", side_effect=lambda s: s):
            gt.render_generic_table(rows, ["roatce_holdco", "tbv_cagr_1y"])
        html = st.markdown.call_args_list[-1].args[0]
        self.assertIn('title="merger in TTM window — x">n/a †</td>', html)
        cagr_cell = html.split('title="acquisition-driven — y"')[0].rsplit("<td", 1)[1]
        self.assertNotIn("background", cagr_cell, "acquisition-driven growth is unshaded")
        self.assertIn("111.6% †", html)
        self.assertIn("16.50%", html)
        st.caption.assert_called_once()


if __name__ == "__main__":
    unittest.main()
