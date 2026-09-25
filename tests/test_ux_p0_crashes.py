"""UX-review P0 crash/freeze regressions (2026-09-25).

UX-P0-06a  Asset Quality Detail / by Loan Type raised TypeError in
           analysis/credit_dynamics.build_credit_timeline when a credit field
           was None in EVERY record (object column of None → .diff()). The
           multi-charter group path (data/cert_group) deliberately sets
           average-based ratios such as NTLNLSR to None, so this is live.
UX-P0-06c  Earnings › Surprise Heat-Map raised TypeError formatting a None
           consensus/actual EPS with :.2f in the hover text.
UX-P0-06b  Branch Proximity for JPM (5,142 branches, 40,842 in-range
           competitor branches at 5 mi) rendered one 53,000 px HTML table and
           froze the tab. Render is capped; the export keeps every pair.
"""
import contextlib
import unittest
from unittest import mock

import pandas as pd


# ── UX-P0-06a: credit timeline with an all-None field ──────────────────

def _credit_records(**overrides):
    """Five quarters, newest first (FDIC order). NTLNLSR (nco_ratio) is None
    in every quarter — the multi-charter-group shape. Past-due rises
    (1.0% → 1.5% of loans) so the migration alert fires with real numbers,
    and C&I NPL (3.6%) is 4x total NPL (0.9%) so a hotspot alert fires."""
    dates = ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31",
             "2024-12-31"]
    p3 = [15_000, 10_000, 10_000, 10_000, 10_000]
    recs = []
    for d, p in zip(dates, p3):
        r = {"REPDTE": d, "NCLNLSR": 0.9, "NTLNLSR": None,
             "IDNCCIR": 3.6, "LNATRESR": 1.8, "P3LNLS": p,
             "P9LNLS": 2_000, "LNLSGR": 1_000_000}
        r.update(overrides)
        recs.append(r)
    return recs


class TestCreditTimelineAllNoneField(unittest.TestCase):

    def test_all_none_field_does_not_raise_and_stays_absent(self):
        from analysis.credit_dynamics import build_credit_timeline
        df = build_credit_timeline(_credit_records())
        self.assertEqual(len(df), 5)
        self.assertTrue(df["nco_ratio"].isna().all(),
                        "absent NCO must stay NaN, never 0")
        self.assertTrue(df["nco_ratio_qoq"].isna().all())
        # present fields still compute: 15,000 / 1,000,000 = 1.5%, +0.5pp QoQ
        self.assertAlmostEqual(float(df["past_due_30_89_pct"].iloc[-1]), 1.5)
        self.assertAlmostEqual(float(df["past_due_30_89_pct_qoq"].iloc[-1]), 0.5)

    def test_summary_alerts_carry_no_nan(self):
        from analysis.credit_dynamics import summarize_bank_credit
        s = summarize_bank_credit(_credit_records())
        codes = {a["code"] for a in s["alerts"]}
        self.assertIn("pd_migration", codes)       # real alerts still fire
        self.assertIn("segment_hotspot", codes)
        for a in s["alerts"]:
            self.assertNotIn("nan", a["message"].lower(), a["message"])
        # consumers get None (absent), never NaN (which prints as "nan%")
        self.assertIsNone(s["latest"]["nco_ratio"])
        self.assertIsNone(s["latest"]["nco_ratio_qoq"])
        self.assertAlmostEqual(s["latest"]["npl_ratio"], 0.9)

    def test_all_none_npl_and_reserves_no_nan_alerts(self):
        # Total NPL and every reserve input absent: no hotspot math on NaN,
        # no reserve alert on NaN, nothing formatted as "nan".
        from analysis.credit_dynamics import (summarize_bank_credit,
                                              compute_credit_screening_metrics)
        recs = _credit_records(NCLNLSR=None, LNATRESR=None, LNRESNCR=None)
        s = summarize_bank_credit(recs, peer_reserve_median=150.0)
        self.assertEqual(s["hotspots"], [])
        codes = {a["code"] for a in s["alerts"]}
        self.assertNotIn("under_reserved", codes)
        self.assertNotIn("thin_reserves_vs_peers", codes)
        for a in s["alerts"]:
            self.assertNotIn("nan", a["message"].lower(), a["message"])
        self.assertIsNone(s["latest"]["reserve_coverage"])
        m = compute_credit_screening_metrics(recs)
        self.assertIsNone(m["nco_4q_trend_bps"])
        self.assertIsNone(m["npl_trend_bps"])
        self.assertIsNone(m["reserve_coverage_pct"])
        self.assertAlmostEqual(m["pd_migration_bps"], 50.0)


# ── UX-P0-06c: heat-map hover with a missing consensus/actual ──────────

class TestSurpriseHover(unittest.TestCase):

    def test_missing_estimate_renders_na(self):
        from ui.earnings import _surprise_hover
        h = _surprise_hover("JPM", "2025Q4", None, 4.81, 5.2)
        self.assertIn("Consensus: n/a", h)
        self.assertIn("Actual: $4.81", h)
        self.assertIn("Surprise: +5.2%", h)

    def test_missing_actual_renders_na(self):
        from ui.earnings import _surprise_hover
        h = _surprise_hover("JPM", "2025Q4", 4.57, None, -1.0)
        self.assertIn("Consensus: $4.57", h)
        self.assertIn("Actual: n/a", h)
        self.assertIn("Surprise: -1.0%", h)

    def test_no_surprise_is_blank(self):
        from ui.earnings import _surprise_hover
        self.assertEqual(_surprise_hover("JPM", "2025Q4", 4.57, 4.81, None), "")

    def test_heatmap_renders_with_none_estimate(self):
        # The page itself: a row with surprise + actual but no estimate must
        # not raise (it did, at the hover f-string).
        import ui.earnings as E
        est = {"AAA": {"earnings_history": [
            {"date": "2026-01-15", "surprise_pct": 5.2, "eps_actual": 1.10,
             "eps_estimate": None},
            {"date": "2025-10-15", "surprise_pct": -2.0, "eps_actual": 0.98,
             "eps_estimate": 1.00},
        ]}}
        st = mock.MagicMock()
        with mock.patch("data.estimates.fetch_all_estimates",
                        lambda t: est), \
                mock.patch("data.bank_mapping.get_name", lambda t: "Alpha"), \
                mock.patch.object(E, "st", st), \
                mock.patch.object(E, "table_export", mock.MagicMock()), \
                mock.patch.object(E, "_df_ticker_linkcol", dict), \
                mock.patch.object(E, "_skeleton", contextlib.nullcontext):
            E._render_surprise_heatmap(["AAA"])
        fig = st.plotly_chart.call_args[0][0]
        hover = [c for row in fig.data[0].customdata for c in row if c]
        self.assertTrue(any("Consensus: n/a" in c for c in hover), hover)
        self.assertTrue(any("Consensus: $1.00" in c for c in hover), hover)


# ── UX-P0-06b: Branch Proximity render caps ────────────────────────────

def _pairs(n_subj, n_comp, n_banks):
    """Synthetic get_branch_competitors pairs: n_subj subject branches ×
    n_comp competitor branches each (every competitor branch unique), sorted
    (subj_brnum, distance) like the store. Subject deposits are a scrambled
    permutation so 'largest' ≠ brnum order; competitor distances are a
    scrambled permutation of 0.5..n_comp-0.5 per branch. Competitor bank k
    (cert 1000+k) has branch deposits k+1, so bank rank is by k."""
    rows = []
    for i in range(n_subj):
        for j in range(n_comp):
            idx = i * n_comp + j
            k = idx % n_banks
            rows.append({
                "subj_brnum": i, "subj_branch_name": f"S{i}",
                "subj_address": "addr", "subj_city": "City",
                "subj_state": "PA", "subj_lat": 40.0, "subj_lng": -75.0,
                "subj_deposits": ((i * 37) % n_subj + 1) * 1000,
                "cert": 1000 + k, "brnum": idx, "ticker": None,
                "bank_name": f"Bank{k}", "branch_name": f"C{idx}",
                "address": "a", "city": "Town", "state": "PA",
                "zip": "19000", "deposits": k + 1, "lat": 40.01,
                "lng": -75.01, "serv_type": "11",
                "distance_miles": ((j * 7) % n_comp) + 0.5,
            })
    return (pd.DataFrame(rows).sort_values(["subj_brnum", "distance_miles"])
            .reset_index(drop=True))


class TestBranchProximitySelection(unittest.TestCase):

    def test_select_branch_rows_caps_and_picks_largest_nearest(self):
        from ui.branch_proximity import _select_branch_rows
        pairs = _pairs(100, 30, 60)
        before = pairs.copy()
        got = _select_branch_rows(pairs, 25, 10)
        pd.testing.assert_frame_equal(pairs, before)   # export input untouched
        self.assertEqual(len(pairs), 3000)
        self.assertEqual(len(got), 250)
        self.assertEqual(got["subj_brnum"].nunique(), 25)
        # the 25 largest by subject deposits: deposits (i*37 % 100 + 1)*1000
        # is a permutation of 1..100 ×1000, so the kept ones are 76..100 ×1000
        self.assertEqual(sorted(got["subj_deposits"].unique()),
                         [d * 1000 for d in range(76, 101)])
        # each kept branch shows its 10 nearest (distances 0.5..9.5)
        for _k, g in got.groupby("subj_brnum"):
            self.assertEqual(sorted(g["distance_miles"]),
                             [d + 0.5 for d in range(10)])
            self.assertTrue(g["distance_miles"].is_monotonic_increasing)

    def test_select_no_cap_when_small(self):
        from ui.branch_proximity import _select_branch_rows
        pairs = _pairs(3, 4, 5)
        self.assertEqual(len(_select_branch_rows(pairs, 25, 10)), 12)

    def test_bank_rollup_and_map_cap(self):
        from ui.branch_proximity import _bank_rollup, _map_competitors
        uniq = _pairs(100, 60, 120).drop_duplicates(subset=["cert", "brnum"])
        self.assertEqual(len(uniq), 6000)
        ro = _bank_rollup(uniq)
        self.assertEqual(len(ro), 120)
        top = ro.head(50)
        # bank k: 50 branches × deposits (k+1) → top 50 are k = 70..119
        self.assertEqual(sorted(top["cert"]), list(range(1070, 1120)))
        self.assertEqual(int(top["deposits"].iloc[0]), 50 * 120)
        m = _map_competitors(uniq, 5000, 50)
        self.assertEqual(len(m), 2500)
        self.assertTrue(m["cert"].isin(top["cert"]).all())
        # under the threshold nothing is dropped
        self.assertEqual(len(_map_competitors(uniq, 6000, 50)), 6000)


class _Recorder:
    """Records the page's st.markdown / st.caption output. Patched onto the
    page module's own `st` binding (not sys.modules["streamlit"]) so a stub
    swapped by an earlier suite under discovery can't bypass it."""

    def __init__(self):
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.st = mock.MagicMock()
        self.st.markdown.side_effect = \
            lambda text, **k: self.markdowns.append(str(text))
        self.st.caption.side_effect = \
            lambda text, **k: self.captions.append(str(text))
        self.st.selectbox.side_effect = \
            lambda label, options, index=0, **k: list(options)[index]

    def md(self) -> str:
        return "\n".join(self.markdowns)

    def caption_text(self) -> str:
        return "\n".join(self.captions)


class TestBranchProximityRenderCaps(unittest.TestCase):
    """The page end-to-end on a JPM-shaped (scaled) result: 100 subject
    branches × 60 unique competitors across 120 banks = 6,000 pairs."""

    def setUp(self):
        import data.branches_store as bs
        import ui.branch_proximity as bp
        import ui.geo_view as gv
        self.pairs = _pairs(100, 60, 120)
        self.pairs_before = self.pairs.copy()
        subj = pd.DataFrame({
            "bank_name": "Alpha", "branch_name": [f"S{i}" for i in range(100)],
            "city": "City", "state": "PA", "deposits": 1,
            "lat": 40.0, "lng": -75.0})
        res = {"pairs": self.pairs, "n_subject_branches": 100,
               "n_subject_missing_coords": 0,
               "n_competitor_missing_coords": 0, "year": 2025,
               "reason": None}
        self.exported, self.mapped = [], []
        self.ui = _Recorder()
        self._patches = [
            mock.patch.object(bp, "st", self.ui.st),
            mock.patch.object(bp, "title_bar", lambda *a, **k: None),
            mock.patch.object(bp, "stat_pill",
                              lambda label, value, **k: f"{label}: {value}"),
            mock.patch.object(bp, "pill_row",
                              lambda pills, **k: self.ui.markdowns.append(
                                  " | ".join(pills))),
            mock.patch.object(bs, "get_branch_competitors",
                              lambda cert, radius_miles=5.0, year=None: res),
            mock.patch.object(bs, "get_owner_branches",
                              lambda cert, year=None: subj),
            mock.patch.object(bp, "get_fdic_cert", lambda t: 1),
            mock.patch.object(bp, "get_name", lambda t: "Alpha Bancorp"),
            mock.patch.object(bp, "table_export",
                              lambda df, *a, **k: self.exported.append(df)),
            mock.patch.object(gv, "_render_map",
                              lambda df, **k: self.mapped.append(df)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_caps_render_not_export(self):
        from ui.branch_proximity import render_branch_proximity, _EXPORT_COLS
        render_branch_proximity("AAA")
        # export: every pair, unmodified
        self.assertEqual(len(self.exported), 1)
        pd.testing.assert_frame_equal(
            self.exported[0],
            self.pairs_before.rename(columns=_EXPORT_COLS))
        # map: all 100 subject branches + only the top-50 banks' 2,500
        m = self.mapped[0]
        self.assertEqual(int((m["Role"] == "AAA branches").sum()), 100)
        self.assertEqual(int((m["Role"] == "Competitors in range").sum()), 2500)
        blob = self.ui.md()
        # pills keep the full counts
        self.assertIn("COMPETITOR BRANCHES IN RANGE: 6,000", blob)
        self.assertIn("COMPETITOR BANKS IN RANGE: 120", blob)
        # per-branch: 25 headers, each 60 in range with nearest 10 shown
        self.assertEqual(blob.count("(60 in range, nearest 10 shown)"), 25)
        # rollup: 50 bank rows
        rollup = next(x for x in self.ui.markdowns
                      if "Branches in Range" in x)
        self.assertEqual(rollup.count("<tr>") - 1, 50)
        caps = self.ui.caption_text()
        self.assertIn("Showing the 25 largest (by branch deposits) of 100 "
                      "branches with competitors in range, up to the 10 "
                      "nearest competitors each — the export has all 6,000 "
                      "pairs.", caps)
        self.assertIn("Showing the top 50 of 120 competitor banks", caps)
        self.assertIn("Map shows 2,500 of 6,000 competitor branches", caps)


if __name__ == "__main__":
    unittest.main()
