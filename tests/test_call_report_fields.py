"""Any FDIC call-report field (and formulas over them) as a screener metric.

Owner 2026-09-30: "a way to do a screen and have any call report field be an
option." Pins, with values from the real 6/30/2026 FDIC records:

  * the vendored catalog classifies every audited trap correctly — the
    metadata's "double" tag marks $ items (ALLOTHL, EEFF) and misses ratios
    (LNLSNTV, NIMYQ); "Deposits held in domestic OFFICES" is $, not a count;
  * units: dollar LEVELS ×1000 → raw dollars; ratios/counts as reported;
  * multi-charter groups: levels + counts strict-sum, ratios only via a proven
    exact quotient (LNLSDEPR = ΣLNLSNET/ΣDEP), everything else n/a;
  * formulas: only + − × ÷ over catalog codes, n/a on any missing input or a
    zero divisor, a formula over levels is a ratio of sums;
  * rows are copied (the universe snapshot is never mutated), and a saved
    screen's fields/formulas restore only when they still resolve.

JPM (group [628, 21761]) hand-computed, $K as FDIC reports:
  CDs ≤12m = (157,680,000+500 + 29,689,000 + 43,934,000 + 49,881,000)
             / (2,226,790,000 + 500) × 100 = 12.6273… %  (reference screen 12.6)

Hermetic: FDIC fetches are stubbed. Run: python -m unittest tests.test_call_report_fields
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from data import call_report_fields as crf

# 6/30/2026 values ($K / as reported) — JPM group, BAC group, SBSI single.
TABLE = {
    "CD3LES":  {628: 157680000.0, 21761: 500.0, 3510: 30576000.0, 25178: 500000.0, 3309: 375319.0},
    "CD3LESS": {628: 29689000.0, 21761: 0.0, 3510: 47570000.0, 25178: 763000.0, 3309: 400000.0},
    "CD3T12":  {628: 43934000.0, 21761: 0.0, 3510: 33161000.0, 25178: 0.0, 3309: 300000.0},
    "CD3T12S": {628: 49881000.0, 21761: 0.0, 3510: 76973000.0, 25178: 1509000.0, 3309: 400000.0},
    "DEPDOM":  {628: 2226790000.0, 21761: 500.0, 3510: 1983484000.0, 25178: 12306000.0, 3309: 7377000.0},
    "LNLSDEPR": {628: 54.4497, 21761: 0.0, 3510: 56.4796, 25178: 70.0, 3309: 79.445},
    "LNLSNET": {628: 1535630000.0, 21761: 0.0, 3510: 1137000000.0, 25178: 10000000.0, 3309: 5860000.0},
    "DEP":     {628: 2820284000.0, 21761: 500.0, 3510: 2013000000.0, 25178: 12306000.0, 3309: 7377000.0},
    "NIMY":    {628: 2.88, 21761: 0.1, 3510: 2.52, 25178: 3.0, 3309: 3.09},
    "NUMEMP":  {628: 226800.0, 21761: 46.0, 3510: 133700.0, 25178: 77.0, 3309: 802.0},
}
GROUPS = {"JPM": [628, 21761], "BAC": [3510, 25178], "SBSI": [3309]}


def _stub_fetch(codes, repdte):
    return {c: dict(TABLE.get(c, {})) for c in codes}


class _Stubbed(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(crf, "fetch_quarter", side_effect=_stub_fetch),
            mock.patch("data.cert_group.get_cert_group", side_effect=lambda t: GROUPS.get(t, [])),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class TestCatalogClassification(unittest.TestCase):
    CASES = {
        # $ items the metadata's "double" tag calls ratios
        "ALLOTHL": "level", "EEFF": "level", "OTHBRF": "level", "ELNLOSQ": "level",
        # ratios the metadata leaves untagged
        "LNLSNTV": "ratio", "DEPDASTR": "ratio", "NIMYQ": "ratio", "LNCIT1R": "ratio",
        # "offices" in a $ title is not a count; a count's RATIO is a ratio
        "DEPDOM": "level", "DEPUNINS": "level", "CHITEMR": "ratio", "LNAG1NR": "ratio",
        # plain cases
        "ASSET": "level", "CD3LES": "level", "NTRTIME": "level", "LNLSDEPR": "ratio",
        "NIMY": "ratio", "IDT1CER": "ratio", "NUMEMP": "count", "OFFDOM": "count",
    }

    def test_audited_cases(self):
        cat = crf.catalog()
        got = {k: cat.get(k, {}).get("kind") for k in self.CASES}
        self.assertEqual(got, self.CASES)

    def test_non_measures_are_excluded(self):
        cat = crf.catalog()
        for code in ("CERT", "REPDTE", "SIMS_LAT", "FRSMEM", "SZ1BP", "ACTIVE", "ZIP"):
            self.assertNotIn(code, cat, code)

    def test_every_entry_is_well_formed(self):
        cat = crf.catalog()
        self.assertGreater(len(cat), 2000)
        bad = [k for k, v in cat.items()
               if v.get("kind") not in ("level", "ratio", "count", "asis") or not v.get("title")]
        self.assertEqual(bad, [])

    def test_labels_and_formats_follow_kind(self):
        lvl = crf.metric_def("fdic:CD3LES")
        self.assertEqual((lvl["format"], lvl["category"]), ("dollars_auto", "Call report (FDIC)"))
        self.assertTrue(lvl["label"].startswith("CD3LES · "))
        self.assertEqual(crf.metric_def("fdic:NIMY")["format"], "number")
        self.assertIn("(count)", crf.metric_def("fdic:NUMEMP")["label"])
        self.assertIsNone(crf.metric_def("fdic:NOT_A_FIELD"))


class TestFormulas(unittest.TestCase):
    def test_parse_accepts_arithmetic_over_codes(self):
        _, codes = crf.parse_formula("(cd3les + CD3LESS) / depdom * 100")
        self.assertEqual(codes, ["CD3LES", "CD3LESS", "DEPDOM"])

    def test_parse_rejects_everything_else(self):
        for bad, why in (("", "Enter a formula"), ("ASSET +", "syntax"),
                         ("__import__('os')", "Not allowed"), ("ASSET.real", "Not allowed"),
                         ("FOO / ASSET", "Unknown call report field: FOO"),
                         ("2 * 3", "at least one"), ("ASSET ** 2", "Not allowed"),
                         ("'x' + ASSET", "Only numbers")):
            with self.assertRaises(ValueError, msg=bad) as cm:
                crf.parse_formula(bad)
            self.assertIn(why, str(cm.exception), bad)

    def test_eval_na_on_missing_or_zero_divisor(self):
        tree, _ = crf.parse_formula("ASSET / DEP * 100")
        self.assertEqual(crf.eval_formula(tree, {"ASSET": 50.0, "DEP": 200.0}), 25.0)
        self.assertIsNone(crf.eval_formula(tree, {"ASSET": 50.0, "DEP": None}))
        self.assertIsNone(crf.eval_formula(tree, {"ASSET": 50.0, "DEP": 0.0}))
        neg, _ = crf.parse_formula("-ASSET + 1")
        self.assertEqual(crf.eval_formula(neg, {"ASSET": 3.0}), -2.0)

    def test_slug(self):
        self.assertEqual(crf.slug("CDs repricing ≤12M %"), "fx:cds_repricing_12m")
        self.assertEqual(crf.slug("  "), "")


class TestResolution(_Stubbed):
    F = {"fx:cd12": {"key": "fx:cd12", "name": "CDs 12m",
                     "expr": "(CD3LES + CD3LESS + CD3T12 + CD3T12S) / DEPDOM * 100",
                     "fmt": "Percent"}}

    def _row(self, tk):
        return crf.attach([{"ticker": tk}], ["fdic:CD3LES", "fdic:LNLSDEPR", "fdic:NIMY",
                                             "fdic:NUMEMP", "fx:cd12"], "20260630", self.F)[0]

    def test_single_charter_units(self):
        r = self._row("SBSI")
        self.assertEqual(r["fdic:CD3LES"], 375319.0 * 1000)      # $K → $
        self.assertEqual(r["fdic:LNLSDEPR"], 79.445)             # ratio as reported
        self.assertEqual(r["fdic:NIMY"], 3.09)
        self.assertEqual(r["fdic:NUMEMP"], 802.0)                # count as reported

    def test_group_levels_counts_sum_and_formula_is_ratio_of_sums(self):
        r = self._row("JPM")
        self.assertEqual(r["fdic:CD3LES"], (157680000.0 + 500.0) * 1000)
        self.assertEqual(r["fdic:NUMEMP"], 226846.0)
        expected = ((157680000.0 + 500.0) + 29689000.0 + 43934000.0 + 49881000.0) \
            / (2226790000.0 + 500.0) * 100
        self.assertAlmostEqual(r["fx:cd12"], expected, places=10)
        self.assertAlmostEqual(round(r["fx:cd12"], 1), 12.6)     # reference screen

    def test_group_ratio_exact_quotient_or_na(self):
        r = self._row("BAC")
        # LNLSDEPR is a proven exact quotient: ΣLNLSNET / ΣDEP — not 56.48 + 70.0
        self.assertAlmostEqual(r["fdic:LNLSDEPR"],
                               (1137000000.0 + 10000000.0) / (2013000000.0 + 12306000.0) * 100)
        self.assertIsNone(r["fdic:NIMY"], "average-based ratio: n/a for a group, never a sum")

    def test_missing_charter_value_is_na_not_partial(self):
        with mock.patch.dict(TABLE["CD3LES"], {21761: None}):
            r = self._row("JPM")
        self.assertIsNone(r["fdic:CD3LES"])
        self.assertIsNone(r["fx:cd12"])

    def test_rows_are_copied_and_unknown_bank_is_na(self):
        src = [{"ticker": "SBSI", "price": 1.0}, {"ticker": "ZZZZ"}]
        out = crf.attach(src, ["fdic:CD3LES"], "20260630")
        self.assertNotIn("fdic:CD3LES", src[0], "the snapshot row must not be mutated")
        self.assertEqual(out[0]["price"], 1.0)
        self.assertIsNone(out[1]["fdic:CD3LES"])

    def test_asof_row_uses_its_own_charter(self):
        r = crf.attach([{"ticker": "Some Failed Bank", "_fdic_cert": 3309}],
                       ["fdic:CD3LES"], "20260630")[0]
        self.assertEqual(r["fdic:CD3LES"], 375319.0 * 1000)

    def test_no_quarter_means_na(self):
        r = crf.attach([{"ticker": "SBSI"}], ["fdic:CD3LES"], None)[0]
        self.assertIsNone(r["fdic:CD3LES"])

    def test_series_for_change_trend(self):
        s = crf.series_for({"ticker": "SBSI"}, ["fdic:NIMY"], ["20260630", "20260331"])
        self.assertEqual(s, {"fdic:NIMY": [3.09, 3.09]})

    def test_register_makes_labels_resolvable(self):
        from config import METRICS_BY_KEY
        crf.register(["fdic:CD3LES", "fx:cd12", "price"], self.F)
        self.assertEqual(METRICS_BY_KEY["fx:cd12"]["label"], "ƒ CDs 12m")
        self.assertEqual(METRICS_BY_KEY["fx:cd12"]["format"], "pct")
        self.assertIn("CD3LES", METRICS_BY_KEY["fdic:CD3LES"]["label"])


class TestSavedScreenDynRestore(unittest.TestCase):
    def test_valid_entries_restore_invalid_are_dropped(self):
        import ast
        src = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_screen_valid_dyn")
        ns: dict = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
        got = ns["_screen_valid_dyn"]([
            {"key": "fdic:CD3LES"}, {"key": "fdic:CD3LES"},            # dup dropped
            {"key": "fdic:GONE_FIELD"},                                  # not in catalog
            {"key": "fx:ok", "name": "OK", "expr": "ASSET/DEP", "fmt": "Percent"},
            {"key": "fx:bad", "name": "Bad", "expr": "ASSET/"},         # no longer parses
            {"key": "price"},                                            # not dynamic
        ])
        self.assertEqual(got, [{"key": "fdic:CD3LES"},
                               {"key": "fx:ok", "name": "OK", "expr": "ASSET/DEP",
                                "fmt": "Percent"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
