"""
(2026-08-02) Multi-charter holding companies were represented by ONE cert.

Found from "why does IBOC only have one call report?" — because the platform
maps one ticker to one FDIC cert and IBOC runs five active bank charters. The
same mapping feeds every FDIC metric, so 11 universe banks displayed a fraction
of their real banking operation: WTFC showed $9.3B of $72.4B (16 charters),
IBOC $9.9B of $17.3B, MS $391B of $633B.

Pins:
  1. levels sum across charters (hand-computed on IBOC's real assets);
  2. average-based ratios (ROA/ROE/NIM/NCO rates) go n/a rather than
     silently carrying the LEAD charter's figure onto a consolidated label —
     FDIC computes them against average balances, which period-end levels
     cannot reconstruct;
  3. the exactly-recomputable ratios ARE rebuilt from the sums — incl.
     the CET1 ratio from ΣRBCT1C/ΣRWAJ (2026-09-22; it had been wrongly
     listed as average-based, leaving every multi-charter bank's CET1 blank)
     and, since 2026-09-25, every ratio in _EXACT_QUOTIENTS — before which
     any FDIC ratio listed nowhere was SUMMED across charters;
  4. a single-charter bank is bit-for-bit unchanged (the ~350 other banks);
  5. group resolution degrades to [cert] on failure — never fewer charters
     than we had before.

Pure-function tests; no FDIC calls.

Run: python -m unittest tests.test_cert_group
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.cert_group import (AVERAGE_BASED_RATIOS, aggregate_records,  # noqa: E402
                             get_cert_group)

# IBOC's five active charters, assets in $thousands, largest first (FDIC,
# verified live 2026-08-02).
IBOC = [
    {"CERT": 19629, "REPDTE": "20260630", "ASSET": 9_892_909, "DEP": 7_500_000,
     "NETINC": 100_000, "EQTOT": 1_200_000, "ROA": 1.35, "NIMY": 4.10},
    {"CERT": 25679, "ASSET": 4_520_154, "DEP": 3_400_000, "NETINC": 45_000,
     "EQTOT": 500_000, "ROA": 1.20, "NIMY": 3.90},
    {"CERT": 59093, "ASSET": 1_610_821, "DEP": 1_300_000, "NETINC": 15_000,
     "EQTOT": 180_000, "ROA": 1.10, "NIMY": 3.80},
    {"CERT": 23772, "ASSET": 738_516, "DEP": 600_000, "NETINC": 7_000,
     "EQTOT": 90_000, "ROA": 1.05, "NIMY": 3.70},
    {"CERT": 24961, "ASSET": 527_501, "DEP": 420_000, "NETINC": 5_000,
     "EQTOT": 60_000, "ROA": 1.00, "NIMY": 3.60},
]


class TestLevelsSum(unittest.TestCase):
    def test_iboc_assets_hand_computed(self):
        agg = aggregate_records(IBOC)
        # 9,892,909 + 4,520,154 + 1,610,821 + 738,516 + 527,501
        self.assertEqual(agg["ASSET"], 17_289_901)
        self.assertEqual(agg["DEP"], 13_220_000)
        self.assertEqual(agg["NETINC"], 172_000)
        self.assertEqual(agg["EQTOT"], 2_030_000)

    def test_lead_charter_alone_understates_by_43_percent(self):
        """The size of the bug, asserted so it can't quietly return."""
        agg = aggregate_records(IBOC)
        lead_only = IBOC[0]["ASSET"]
        self.assertAlmostEqual(lead_only / agg["ASSET"] * 100, 57.2, places=1)

    def test_identity_comes_from_the_lead_charter(self):
        agg = aggregate_records(IBOC)
        self.assertEqual(agg["CERT"], 19629)
        self.assertEqual(agg["REPDTE"], "20260630")
        self.assertEqual(agg["_charter_count"], 5)
        self.assertTrue(agg["_aggregated"])

    def test_missing_field_on_one_charter_still_sums_the_rest(self):
        recs = [{"CERT": 1, "ASSET": 100}, {"CERT": 2, "ASSET": 50},
                {"CERT": 3}]
        self.assertEqual(aggregate_records(recs)["ASSET"], 150)


class TestAverageBasedRatiosGoNa(unittest.TestCase):
    """The cardinal-rule half: a lead-charter ROA on a consolidated label is a
    plausible-wrong number, so it must be n/a."""

    def test_roa_roe_nim_are_none_not_the_lead_value(self):
        agg = aggregate_records(IBOC)
        for k in ("ROA", "NIMY"):
            self.assertIn(k, agg, f"{k} must be present as an explicit n/a")
            self.assertIsNone(agg[k], f"{k} must not carry the lead charter's value")
        self.assertNotEqual(agg.get("ROA"), 1.35)

    def test_every_average_based_ratio_is_nulled(self):
        recs = [dict({k: 1.0 for k in AVERAGE_BASED_RATIOS}, CERT=1, ASSET=10),
                dict({k: 2.0 for k in AVERAGE_BASED_RATIOS}, CERT=2, ASSET=20)]
        agg = aggregate_records(recs)
        for k in AVERAGE_BASED_RATIOS:
            self.assertIsNone(agg[k], f"{k} must be n/a for a group")
        self.assertEqual(agg["ASSET"], 30)


class TestExactRatiosRecomputed(unittest.TestCase):
    def test_efficiency_from_summed_components(self):
        recs = [
            {"CERT": 1, "INTINC": 1000, "EINTEXP": 400, "NONII": 200, "NONIX": 480},
            {"CERT": 2, "INTINC": 500, "EINTEXP": 200, "NONII": 100, "NONIX": 240},
        ]
        # revenue = (1500-600) + 300 = 1200 ; expense 720 -> 60.00%
        self.assertAlmostEqual(aggregate_records(recs)["EEFFR"], 60.0, places=6)

    def test_capital_ratios_from_summed_dollars(self):
        recs = [
            {"CERT": 1, "RBC": 1000, "RBCT1J": 800, "RBCT1C": 760, "RWAJ": 8000},
            {"CERT": 2, "RBC": 500, "RBCT1J": 400, "RBCT1C": 400, "RWAJ": 4000},
        ]
        agg = aggregate_records(recs)
        self.assertAlmostEqual(agg["RBCRWAJ"], 1500 / 12000 * 100, places=6)
        self.assertAlmostEqual(agg["RBC1RWAJ"], 1200 / 12000 * 100, places=6)
        # CET1 = ΣRBCT1C / ΣRWAJ = 1160 / 12000 = 9.6667% — Tier 1 (10.0%)
        # is NOT reused for it: charter 1 carries 40 of AT1.
        self.assertAlmostEqual(agg["IDT1CER"], 1160 / 12000 * 100, places=6)
        self.assertNotIn("IDT1CER", AVERAGE_BASED_RATIOS)

    def test_cet1_ratio_is_na_when_cet1_dollars_absent(self):
        """Cache rows written before RBCT1C was fetched: n/a, never the lead
        charter's IDT1CER and never Tier 1 standing in for CET1."""
        recs = [
            {"CERT": 1, "RBC": 1000, "RBCT1J": 800, "RWAJ": 8000, "IDT1CER": 9.5},
            {"CERT": 2, "RBC": 500, "RBCT1J": 400, "RWAJ": 4000, "IDT1CER": 10.0},
        ]
        agg = aggregate_records(recs)
        self.assertIsNone(agg["IDT1CER"])
        self.assertAlmostEqual(agg["RBC1RWAJ"], 10.0, places=6)

    def test_missing_rwa_yields_na_not_a_sum_of_ratios(self):
        """The summing loop adds the charters' REPORTED ratios into the key
        before the recompute; with no RWA to divide by, that sum must be
        replaced by n/a, never left as 9.5 + 10.0 = 19.5%."""
        recs = [
            {"CERT": 1, "RBC": 1000, "RBCT1J": 800, "RBCT1C": 760,
             "IDT1CER": 9.5, "RBC1RWAJ": 10.0, "RBCRWAJ": 12.5},
            {"CERT": 2, "RBC": 500, "RBCT1J": 400, "RBCT1C": 400,
             "IDT1CER": 10.0, "RBC1RWAJ": 10.0, "RBCRWAJ": 12.5},
        ]
        agg = aggregate_records(recs)
        for k in ("IDT1CER", "RBC1RWAJ", "RBCRWAJ"):
            self.assertIsNone(agg[k], f"{k} must be n/a without RWA")

    def test_missing_efficiency_component_yields_na_not_a_sum(self):
        recs = [{"CERT": 1, "INTINC": 1000, "EINTEXP": 400, "NONII": 200, "EEFFR": 60.0},
                {"CERT": 2, "INTINC": 500, "EINTEXP": 200, "NONII": 100, "EEFFR": 61.0}]
        self.assertIsNone(aggregate_records(recs)["EEFFR"])   # NONIX absent

    def test_zero_revenue_yields_na_not_a_divide_error(self):
        recs = [{"CERT": 1, "INTINC": 100, "EINTEXP": 150, "NONII": 50, "NONIX": 10},
                {"CERT": 2, "INTINC": 0, "EINTEXP": 0, "NONII": 0, "NONIX": 0}]
        self.assertIsNone(aggregate_records(recs)["EEFFR"])


class TestSingleCharterUnchanged(unittest.TestCase):
    """~350 banks must be bit-for-bit unaffected."""

    def test_passthrough_is_identical(self):
        one = {"CERT": 628, "ASSET": 100, "ROA": 1.23, "NIMY": 3.21}
        agg = aggregate_records([one])
        self.assertEqual(agg, one)
        self.assertNotIn("_aggregated", agg)

    def test_empty_input(self):
        self.assertEqual(aggregate_records([]), {})
        self.assertEqual(aggregate_records([None]), {})


class TestGroupResolutionDegradesSafely(unittest.TestCase):
    def test_no_cert_returns_empty(self):
        with patch("data.bank_mapping.get_fdic_cert", return_value=None):
            self.assertEqual(get_cert_group("NOPE"), [])

    def test_resolution_failure_falls_back_to_the_mapped_cert(self):
        import data.cert_group as cg
        with patch.object(cg, "_resolve_group", return_value=[]), \
                patch("data.cache.get", return_value=None), \
                patch("data.cache.put", return_value=None):
            self.assertEqual(cg.get_cert_group("X", cert=19629), [19629])

    def test_cached_group_is_served(self):
        # No bulk map in this environment; the legacy per-cert cache row
        # must still be honoured (key-aware mock: the bulk-map read misses).
        import data.cert_group as cg

        def _get(key, max_age_s=None):
            return {"certs": [1, 2, 3]} if key.startswith("cert_group:v1:") else None

        with patch("data.cache.get", side_effect=_get):
            self.assertEqual(cg.get_cert_group("X", cert=1), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()


class TestGroupHistoryAggregation(unittest.TestCase):
    """fetch_group_history is the single seam the wiring goes through: every
    producer of the fdic_hist cache entry calls it, so every consumer becomes
    correct without changing. Aggregating the HISTORY (not one record) is what
    keeps averaged ratios alive — downstream derives ROA/NIM/ROATCE from levels
    plus a prior quarter."""

    def _df(self, rows):
        import pandas as pd
        return pd.DataFrame(rows)

    def test_quarters_are_consolidated_across_charters(self):
        import data.cert_group as cg
        rows = {
            19629: [{"REPDTE": "20260630", "ASSET": 9_892_909, "CERT": 19629},
                    {"REPDTE": "20260331", "ASSET": 9_800_000, "CERT": 19629}],
            25679: [{"REPDTE": "20260630", "ASSET": 4_520_154, "CERT": 25679},
                    {"REPDTE": "20260331", "ASSET": 4_500_000, "CERT": 25679}],
        }
        with patch("data.fdic_client.fetch_financials",
                   side_effect=lambda c, limit=20: self._df(rows[c])), \
                patch.object(cg, "get_cert_group", return_value=[19629, 25679]):
            hist = cg.fetch_group_history("IBOC", limit=20)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["REPDTE"], "20260630")          # newest first
        self.assertEqual(hist[0]["ASSET"], 14_413_063)           # 9,892,909+4,520,154
        self.assertEqual(hist[1]["ASSET"], 14_300_000)
        self.assertEqual(hist[0]["_charter_count"], 2)

    def test_single_charter_takes_the_unaggregated_path(self):
        import data.cert_group as cg
        one = self._df([{"REPDTE": "20260630", "ASSET": 500, "ROA": 1.11,
                         "CERT": 35295}])
        with patch("data.fdic_client.fetch_financials", return_value=one), \
                patch.object(cg, "get_cert_group", return_value=[35295]):
            hist = cg.fetch_group_history("SFST")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["ROA"], 1.11, "single charter keeps its ratios")
        self.assertNotIn("_aggregated", hist[0])

    def test_one_charter_failing_does_not_lose_the_others(self):
        import data.cert_group as cg

        def _fetch(c, limit=20):
            if c == 2:
                raise RuntimeError("FDIC down")
            return self._df([{"REPDTE": "20260630", "ASSET": 100, "CERT": c}])

        with patch("data.fdic_client.fetch_financials", side_effect=_fetch), \
                patch.object(cg, "get_cert_group", return_value=[1, 2]):
            hist = cg.fetch_group_history("X")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["ASSET"], 100)

    def test_no_certs_returns_empty(self):
        import data.cert_group as cg
        with patch.object(cg, "get_cert_group", return_value=[]):
            self.assertEqual(cg.fetch_group_history("NOPE"), [])


class TestBulkGroupMap(unittest.TestCase):
    """(2026-08-04) The per-cert resolution path costs 2 FDIC API calls per
    bank; inside the nightly's 8-worker burst those get 429-throttled,
    get_with_retry returns None, and every bank SILENTLY degraded to its lead
    charter — a 'successful' run wrote lead-charter data universe-wide. The
    bulk map is built from the universe snapshot's full-institutions walk
    (zero extra API calls) and consulted before any API path."""

    _INSTS = [
        {"cert": 19629, "rssdhcr": 1114912, "asset": 9_892_909},
        {"cert": 25679, "rssdhcr": 1114912, "asset": 4_520_154},
        {"cert": 59093, "rssdhcr": 1114912, "asset": 1_610_821},
        {"cert": 628,   "rssdhcr": 1039502, "asset": 3_600_000_000},
        {"cert": 35295, "rssdhcr": 3557916, "asset": 4_500_000},   # single
        {"cert": 90001, "rssdhcr": 0,       "asset": 100},         # no holdco
        {"cert": 90002, "rssdhcr": None,    "asset": 100},
    ]

    def test_map_lists_only_multi_charter_groups_largest_first(self):
        from data.cert_group import build_group_map
        m = build_group_map(self._INSTS)
        self.assertEqual(m["19629"], [19629, 25679, 59093])
        self.assertEqual(m["25679"], [19629, 25679, 59093],
                         "every member cert must key the SAME group")
        self.assertNotIn("35295", m, "single-charter certs are omitted")
        self.assertNotIn("628", m, "a holdco with one active charter is single")
        self.assertNotIn("90001", m, "RSSDHCR 0 = no holdco")
        self.assertNotIn("90002", m)

    def test_get_cert_group_serves_the_map_without_any_api_call(self):
        import data.cert_group as cg
        with patch("data.cache.get",
                   return_value={"19629": [19629, 25679, 59093]}), \
                patch.object(cg, "_resolve_group",
                             side_effect=AssertionError("API path must not run")):
            self.assertEqual(cg.get_cert_group("IBOC", cert=19629),
                             [19629, 25679, 59093])

    def test_absence_from_the_map_means_single_charter_no_api_call(self):
        import data.cert_group as cg
        with patch("data.cache.get", return_value={"19629": [19629, 25679]}), \
                patch.object(cg, "_resolve_group",
                             side_effect=AssertionError("API path must not run")):
            self.assertEqual(cg.get_cert_group("SFST", cert=35295), [35295])

    def test_no_map_falls_back_to_legacy_resolution(self):
        import data.cert_group as cg
        with patch("data.cache.get", return_value=None), \
                patch("data.cache.put", return_value=None), \
                patch.object(cg, "_resolve_group", return_value=[1, 2]):
            self.assertEqual(cg.get_cert_group("X", cert=1), [1, 2])

    def test_throttled_resolution_is_loud_not_silent(self):
        """The cardinal-adjacent rule for ops: a degradation must never be
        invisible. get_with_retry returns None ONLY on exhausted 429s."""
        import contextlib
        import io
        import data.cert_group as cg
        buf = io.StringIO()
        with patch("data.http.get_with_retry", return_value=None), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(cg._resolve_group(19629), [])
        self.assertIn("rate-limited", buf.getvalue())

    def test_universe_walk_feeds_the_map(self):
        """Structural: the snapshot build must request RSSDHCR and warm the
        map, or the bulk path silently never exists and every environment
        falls back to the throttle-prone API resolution."""
        src = (REPO / "data/bank_universe.py").read_text(encoding="utf-8")
        self.assertIn("RSSDHCR", src)
        self.assertIn("warm_group_map", src)


class TestProducersUseTheSeam(unittest.TestCase):
    """Structural: the cache entry every tab reads must be written from the
    group path, or a consumer silently gets lead-charter figures again."""

    def test_shared_loader_uses_group_history(self):
        src = (REPO / "data/loaders.py").read_text(encoding="utf-8")
        self.assertIn("fetch_group_history", src)
        self.assertNotIn("fdic_client.fetch_financials(cert", src)

    def test_nightly_refresh_uses_group_history(self):
        src = (REPO / "jobs/refresh_universe.py").read_text(encoding="utf-8")
        self.assertIn("fetch_group_history", src)

    def test_no_ui_module_reads_fdic_per_cert(self):
        """(2026-08-04, third fix of the day) Six UI call sites bypassed the
        seam via fdic_client.get_latest_financials / get_historical_financials
        and kept rendering lead-charter figures — IBOC's Corporate Profile
        showed $9.89B of $17.3B THROUGH two correct nightly-side fixes,
        because this panel never read what the nightly writes. Ticker-scoped
        display code must go through data.loaders (load_fdic_latest /
        load_fdic_hist_df / load_fdic_hist).

        ui/data_quality.py is exempt: it is a raw-source diagnostic that
        compares the per-cert FDIC record against SEC deliberately."""
        offenders = []
        for p in sorted((REPO / "ui").glob("*.py")):
            if p.name == "data_quality.py":
                continue
            src = p.read_text(encoding="utf-8")
            for needle in ("get_latest_financials", "get_historical_financials",
                           "fdic_client.fetch_financials("):
                if needle in src:
                    offenders.append(f"{p.name}: {needle}")
        self.assertEqual(offenders, [])


class TestRatioClassNotSummed(unittest.TestCase):
    """(2026-09-25) Every FDIC ratio neither dropped as average-based nor
    rebuilt fell through to the summing loop: WFC's loans/deposits rendered
    233.3% (its charters' LNLSDEPR added). Two charters whose reported ratios
    differ, so a sum, a lead-charter carry and a simple average of the ratios
    are all distinguishable from the right answer. Values hand-computed."""

    A = {"CERT": 1, "ASSET": 900, "LNLSNET": 540, "DEPDOM": 700, "EQ": 90,
         "EQTOT": 95, "NUMEMP": 3, "NCRERES": 6, "LNRERES": 200,
         "LNATRESJ": 12, "NCLNLS": 8, "RBCT1": 80, "AVASSETJ": 880,
         "ELNLOS": 11, "NTTOT": 10, "CHFLA": 50, "NTLNLSA": 10,
         # the charters' own FDIC-reported ratios (what used to be summed)
         "LNLSNTV": 60.0, "DEPDASTR": 77.78, "EQV": 10.0, "ASTEMPM": 0.3,
         "NCRERESR": 3.0, "LNRESNCR": 150.0, "RBC1AAJ": 9.09,
         "ELNANTR": 110.0, "IDERNCVR": 5.0,
         "NOIJY": 1.2, "NTRER": 0.10, "NTCOMRER": 0.20, "ELNATRY": 0.30,
         "IDNTCIR": 0.40}
    B = {"CERT": 2, "ASSET": 100, "LNLSNET": 80, "DEPDOM": 60, "EQ": 12,
         "EQTOT": 12, "NUMEMP": 1, "NCRERES": 4, "LNRERES": 50,
         "LNATRESJ": 1, "NCLNLS": 4, "RBCT1": 10, "AVASSETJ": 95,
         "ELNLOS": 3, "NTTOT": 5, "CHFLA": 6, "NTLNLSA": 3,
         "LNLSNTV": 80.0, "DEPDASTR": 60.0, "EQV": 12.0, "ASTEMPM": 0.1,
         "NCRERESR": 8.0, "LNRESNCR": 25.0, "RBC1AAJ": 10.53,
         "ELNANTR": 60.0, "IDERNCVR": 2.0,
         "NOIJY": 1.4, "NTRER": 0.30, "NTCOMRER": 0.40, "ELNATRY": 0.50,
         "IDNTCIR": 0.60}

    def test_level_quotients_are_ratio_of_sums(self):
        g = aggregate_records([self.A, self.B])
        # (540 + 80) / (900 + 100) = 620 / 1000        (sum would be 140.0)
        self.assertAlmostEqual(g["LNLSNTV"], 62.0, places=12)
        # (700 + 60) / 1000                            (sum 137.78)
        self.assertAlmostEqual(g["DEPDASTR"], 76.0, places=12)
        # (90 + 12) / 1000 — EQ, not EQTOT (which gives 10.7)
        self.assertAlmostEqual(g["EQV"], 10.2, places=12)
        # 1000 $K = $1.0M over 3 + 1 employees          (sum 0.4)
        self.assertAlmostEqual(g["ASTEMPM"], 0.25, places=12)
        # (6 + 4) / (200 + 50) = 10 / 250              (sum 11.0)
        self.assertAlmostEqual(g["NCRERESR"], 4.0, places=12)
        # (12 + 1) / (8 + 4) = 13 / 12                 (sum 175.0)
        self.assertAlmostEqual(g["LNRESNCR"], 13 / 12 * 100, places=12)
        # (80 + 10) / (880 + 95) = 90 / 975            (sum 19.62)
        self.assertAlmostEqual(g["RBC1AAJ"], 90 / 975 * 100, places=12)

    def test_same_period_flow_quotients_are_ratio_of_sums(self):
        g = aggregate_records([self.A, self.B])
        # provision / NCO: (11 + 3) / (10 + 5) = 14 / 15    (sum 170.0)
        self.assertAlmostEqual(g["ELNANTR"], 14 / 15 * 100, places=12)
        # earnings coverage (x): (50 + 6) / (10 + 3) = 56 / 13  (sum 7.0)
        self.assertAlmostEqual(g["IDERNCVR"], 56 / 13, places=12)

    def test_average_balance_ratios_are_na(self):
        """annualized flow ÷ 5-point AVERAGE balance (dictionary: ASSET5,
        LNRE5, LNCOMRE5, LNCI5) — n/a for a group, never a sum."""
        g = aggregate_records([self.A, self.B])
        for k in ("NOIJY", "NTRER", "NTCOMRER", "ELNATRY", "IDNTCIR"):
            self.assertIn(k, AVERAGE_BASED_RATIOS)
            self.assertIsNone(g[k], f"{k} must be n/a for a group")

    def test_component_missing_on_one_charter_is_na_not_partial(self):
        """A cache/deep-store row written before the component was fetched:
        the sum over the other charters is a partial numerator — n/a."""
        b = {k: v for k, v in self.B.items() if k != "NCRERES"}
        g = aggregate_records([self.A, b])
        self.assertIsNone(g["NCRERESR"])        # not 6 / 250 = 2.4%
        self.assertEqual(g["NCRERES"], 6)       # the level loop is unchanged
        self.assertAlmostEqual(g["LNLSNTV"], 62.0, places=12)
        nan_b = dict(self.B, EQ=float("nan"))
        self.assertIsNone(aggregate_records([self.A, nan_b])["EQV"])

    def test_efficiency_needs_every_charter_too(self):
        a = {"CERT": 1, "INTINC": 1000, "EINTEXP": 400, "NONII": 200, "NONIX": 480}
        b = {"CERT": 2, "INTINC": 500, "EINTEXP": 200, "NONII": 100}
        self.assertIsNone(aggregate_records([a, b])["EEFFR"])

    def test_non_positive_denominator_is_na(self):
        # net recoveries: ΣNTTOT = 10 + (-12) = -2
        g = aggregate_records([self.A, dict(self.B, NTTOT=-12)])
        self.assertIsNone(g["ELNANTR"])

    def test_single_charter_passthrough_keeps_fdic_value(self):
        self.assertEqual(aggregate_records([self.A])["LNRESNCR"], 150.0)


class TestEveryRegistryRatioIsClassified(unittest.TestCase):
    """The structural guard: a registry FDIC ratio that is neither dropped nor
    rebuilt is SUMMED for multi-charter banks. That is how 18 of them shipped
    wrong until 2026-09-25 — adding a pct/ratio metric must now classify it."""

    def test_registry_pct_and_ratio_fields_are_classified(self):
        import config
        from data.cert_group import _EXACT_QUOTIENTS
        handled = AVERAGE_BASED_RATIOS | set(_EXACT_QUOTIENTS) | {"EEFFR"}
        unclassified = sorted(
            f"{m['key']}={m['fdic_field']}" for m in config.METRICS
            if m.get("source") == "fdic" and m.get("fdic_field")
            and m.get("format") in ("pct", "ratio")
            and m["fdic_field"] not in handled)
        self.assertEqual(unclassified, [])

    def test_quotient_components_are_fetched_and_classes_disjoint(self):
        import config
        from data.cert_group import _EXACT_QUOTIENTS
        from data.fdic_client import _BASE_FINANCIALS_FIELDS
        fetched = _BASE_FINANCIALS_FIELDS | config.get_fdic_fields()
        missing = sorted({c for n, d, _ in _EXACT_QUOTIENTS.values()
                          for c in (n, d)} - fetched)
        self.assertEqual(missing, [], "a component that is never fetched "
                         "makes its ratio permanently n/a for groups")
        self.assertEqual(AVERAGE_BASED_RATIOS & set(_EXACT_QUOTIENTS), set())


class TestRegistryLabelsMatchFields(unittest.TestCase):
    """(2026-09-25) Five registry metrics displayed a DIFFERENT FDIC quantity
    than their label (risview dictionary titles). Keys are kept for saved
    screens; the field/label pairs are pinned here."""

    def test_corrected_pairs(self):
        import config
        m = {x["key"]: x for x in config.METRICS}
        self.assertEqual(m["npl_resi"]["fdic_field"], "NCRERESR")  # not construction
        self.assertEqual(m["nco_ci"]["fdic_field"], "IDNTCIR")     # not CRE NCOs
        self.assertEqual(m["npl_cre"]["label"], "NPL RE %")        # NCRER = all RE
        self.assertEqual(m["reserve_coverage"]["fdic_field"], "LNRESNCR")
        self.assertEqual(m["reserve_nco_coverage"]["fdic_field"], "IDERNCVR")
        self.assertEqual(m["reserve_nco_coverage"]["format"], "ratio")
        self.assertEqual(m["nco_to_reserve"]["label"], "Loans/Core Dep")
        self.assertEqual(m["nco_to_reserve"]["category"], "Composition")
