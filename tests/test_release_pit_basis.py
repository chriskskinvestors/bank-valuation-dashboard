"""Release metrics: point-in-time columns + the current-vs-table basis guard.

Fixtures are verbatim EDGAR EX-99.1 excerpts (cell text + colspan/rowspan
only) from 2Q26 releases, under tests/fixtures/release_pit_basis/.

1. PBFS prints its capital and ACL rows ONLY under the "At or For the Six
   Months Ended June 30" columns. "At" states the balance sheet at June 30,
   2026 / 2025, which is the quarter-end value, so it serves the quarter
   (point-in-time keys only; a "For the" banner proves nothing).
2. _comparative_basis_guard: when the release's own table states the
   current quarter at a different value than the one served, comparatives
   go n/a, and the current value too unless it is a declared dividend or a
   proven-annualized NCO rate (WNEB).
"""
import inspect
import os
import unittest
from unittest import mock

from data import release_metrics as rm

Q, PQ, YQ = "2026-06-30", "2026-03-31", "2025-06-30"


def _fx(name):
    p = os.path.join(os.path.dirname(__file__), "fixtures", "release_pit_basis",
                     name + ".html")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _val(html):
    """The deterministic part of release_metrics(): current (prose first,
    table fill), prior / year-ago table reads, then the basis guard."""
    val = {"qend": Q,
           "metrics": rm.extract_release_metrics(html, expected_qend=Q),
           "prior_metrics": rm.extract_table_metrics(html, PQ),
           "yoy_metrics": rm.extract_table_metrics(html, YQ)}
    rm._comparative_basis_guard(val, rm.extract_table_metrics(html, Q))
    return val


def _cols(val, key):
    return tuple(val[b].get(key) for b in ("metrics", "prior_metrics",
                                           "yoy_metrics"))


class TestPointInTimeColumns(unittest.TestCase):
    CAP = ("cet1_ratio", "t1_ratio", "total_ratio", "lev_ratio", "acl_loans")

    def test_pbfs_capital_reads_the_at_columns(self):
        html = _fx("PBFS")
        cur = rm.extract_table_metrics(html, Q)
        ya = rm.extract_table_metrics(html, YQ)
        self.assertEqual(tuple(cur[k] for k in self.CAP),
                         (13.22, 13.22, 14.48, 9.97, 1.48))
        self.assertEqual(tuple(ya[k] for k in self.CAP),
                         (17.28, 17.28, 18.54, 11.93, 1.52))

    def test_no_prior_quarter_column_no_value(self):
        prior = rm.extract_table_metrics(_fx("PBFS"), PQ)
        self.assertTrue(all(prior[k] is None for k in self.CAP))

    def test_flow_ratio_under_the_six_month_columns_stays_unread(self):
        # "Net charge-offs to average outstanding loans during the period |
        # 0.17% | 0.01%" sits under the same six-month columns — a flow
        # ratio, never the quarter's.
        for q in (Q, YQ):
            self.assertIsNone(rm.extract_table_metrics(_fx("PBFS"), q)["nco_ratio"])

    def test_quarter_columns_unchanged(self):
        html = _fx("PBFS")
        self.assertEqual((rm.extract_table_metrics(html, Q)["roa"],
                          rm.extract_table_metrics(html, YQ)["roa"]), (0.60, 1.26))
        self.assertEqual(rm.extract_table_metrics(html, Q)["efficiency"], 78.33)

    def test_banner_without_at_proves_nothing(self):
        html = _fx("PBFS").replace("At or For the", "For the")
        self.assertIn("For the", html)
        cur = rm.extract_table_metrics(html, Q)
        self.assertTrue(all(cur[k] is None for k in self.CAP))
        self.assertEqual(cur["roa"], 0.60)


class TestComparativeBasisGuard(unittest.TestCase):
    def test_wneb_annualized_current_kept_quarterly_comparatives_dropped(self):
        # Prose: "net charge-offs of $1.8 million, or 0.33% of average loans,
        # on an annualized basis"; table row: 0.08% | 0.00% | … | (0.03)% —
        # quarterly (0.08 × 4 ≈ 0.33). Pre-change: 0.33 / 0.00 / -0.03.
        val = _val(_fx("WNEB"))
        self.assertEqual(_cols(val, "nco_ratio"), (0.33, None, None))
        self.assertEqual(val["basis_split"], ["nco_ratio"])

    def test_unproven_nco_disagreement_is_na(self):
        # Same release, a prose rate that is NOT 4 × the table's 0.08.
        html = _fx("WNEB").replace("or 0.33% of average loans",
                                   "or 0.50% of average loans")
        self.assertEqual(_cols(_val(html), "nco_ratio"), (None, None, None))

    def test_bhb_compared_to_value_never_serves(self):
        # "55.76% efficiency ratio (Non-GAAP), compared to 56.92%" served the
        # PRIOR quarter's 56.92 as current; the table says 55.76.
        self.assertEqual(_cols(_val(_fx("BHB")), "efficiency"), (None, None, None))

    def test_pkbk_six_month_prose_never_serves(self):
        # "Return on average assets increased to 2.18% from 1.52%" and
        # "efficiency ratio improved to 30.16% … for the first six months" —
        # Table 3's quarter columns: 2.17 / 28.98 / ROE 14.33.
        val = _val(_fx("PKBK"))
        for k in ("roa", "efficiency", "roe"):
            self.assertEqual(_cols(val, k), (None, None, None), k)

    def test_efsc_declared_dividend_kept_comparatives_dropped(self):
        # "Increased quarterly dividend $0.01 to $0.35 per common share for
        # the third quarter 2026"; the table's 2Q26 column is $0.34, 1Q26
        # $0.33 — a Q/Q of +0.02 against a +0.01 raise.
        self.assertEqual(_cols(_val(_fx("EFSC")), "div_ps"), (0.35, None, None))

    def test_agreeing_values_untouched(self):
        val = {"metrics": {"nim": rm._Num(3.6, 1), "roa": 1.31},
               "prior_metrics": {"nim": 3.54, "roa": 1.2},
               "yoy_metrics": {"nim": 3.23, "roa": 1.0}}
        rm._comparative_basis_guard(val, {"nim": rm._Num(3.61, 2), "roa": 1.31})
        self.assertEqual(val["prior_metrics"], {"nim": 3.54, "roa": 1.2})
        self.assertNotIn("basis_split", val)

    def test_release_metrics_runs_the_guard(self):
        self.assertIn("_comparative_basis_guard(", inspect.getsource(rm.release_metrics))

    def test_ai_fill_never_refills_a_split_key(self):
        val = {"metrics": {"efficiency": None, "roa": None},
               "prior_metrics": {"nco_ratio": None}, "yoy_metrics": {},
               "capital": {}, "basis_split": ["efficiency", "nco_ratio"]}
        ai = {"cur": {"efficiency": 56.92, "roa": 1.31},
              "prior": {"nco_ratio": 0.0}, "yoy": {}}
        with mock.patch("data.release_ai.has_api_key", return_value=True), \
                mock.patch("data.release_ai.release_ai_metrics", return_value=ai):
            self.assertEqual(rm._ai_fill(val, 1, {"html": "", "_sup": None}), "ok")
        self.assertIsNone(val["metrics"]["efficiency"])
        self.assertIsNone(val["prior_metrics"]["nco_ratio"])
        self.assertEqual(val["metrics"]["roa"], 1.31)


if __name__ == "__main__":
    unittest.main()
