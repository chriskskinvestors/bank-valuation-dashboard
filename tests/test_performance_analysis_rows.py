"""Performance Analysis / capital rows vs the FDIC API (REVIEW 2026-10-05).

Every fixture value below is the FDIC financials API's own figure ($000 /
percent), pulled 2026-10-05; every expected cell is hand-computed here.

  * P0-1  Quarterly columns printed YTD-annualized FDIC ratios (ROA, ROE,
          NIMY, INTINCY, INTEXPY, EEFFR, NONIIAY, NONIXAY, NTLNLSR) under
          single-quarter labels, and Profit margin / Net nonrecurring divided
          YTD flows. ONB "Q4 '25": ROAA 1.16 (YTD) vs ROAQ 1.32.
  * P0-2  Core income was YTD in the Quarterly view (ONB "Q2 '26" $511.9M vs
          single-quarter $267.5M); Core EPS divided BANK-SUB core income by
          HOLDCO shares — now n/a.
  * P0-5  Loan yield used ILNDOM (domestic loans only) over LNLSGR (loans +
          leases, all offices): JPM Q2'26 5.82% vs 6.41% on ILNLS.
  * P0-6  IB-deposit cost: EDEP (all offices) over DEPIDOM (domestic only):
          JPM Q2'26 2.91% vs 2.18% over DEPIDOM + DEPIFOR.
  * P1-8  Tier 1 leverage showed RBCT1JR (÷ period-end assets), not RBC1AAJ
          (÷ quarter-average assets): HBAN Q1'26 8.92% vs 10.24%.
  * P1-9  "Net interest spread" was INTINCY − INTEXPY = NIMY (same base) —
          removed.
  * P1-10 Annual averages were Dec-to-Dec 2-point: ONB FY2022 (First Midwest
          merger) ROACE 11.67% vs 10.37% on the 5 quarter-ends (= FDIC ROE).
"""
import re
import unittest

import pandas as pd

import ui.financials_statements as FS
from tests.test_statement_units_p2 import _render, _row_cells


def _texts(html, label):
    return [t for _, t in _row_cells(html, label)]


def _live(html, label):
    """True per column when the cell carries a click-through (a value or an
    explained n/a), False for a dead cell."""
    return ["data-cid" in tag for tag, _ in _row_cells(html, label)]


def _export_row(ws, label):
    for r in ws.iter_rows():
        if r[0].value == label:
            return [c.value for c in r[1:]]
    raise AssertionError(f"export row {label!r} missing")


# ONB (cert 3832) — 9/30/2025 and 12/31/2025 as FDIC reports them.
ONB_Q3_25 = {
    "REPDTE": "2025-09-30",
    "ROA": 1.093577857493333, "ROAQ": 1.13, "ROE": 9.55, "ROEQ": 10.08,
    "NIMY": 3.584130389305689, "NIMYQ": 3.66,
    "INTINCY": 5.721729126992976, "INTINCYQ": 5.81,
    "INTEXPY": 2.137598737687287, "INTEXPYQ": 2.15,
    "EEFFR": 52.87601741891347, "EEFFQR": 56.11,
    "NONIIAY": 0.74, "NONIIAYQ": 0.71, "NONIXAY": 2.1818357607713383, "NONIXAYQ": 2.36,
    "NTLNLSR": 0.2470093409416542, "NTLNLSQR": 0.25,
    "NETINC": 509_587, "INTINC": 2_363_060, "EINTEXP": 882_823, "NONII": 343_987,
    "NONIX": 1_016_695, "IGLSEC": 367, "EXTRA": 0, "PTAXNETINC": 641_861, "ITAX": 132_641,
}
ONB_Q4_25 = {
    "REPDTE": "2025-12-31",
    "ROA": 1.164154127517348, "ROAQ": 1.32, "ROE": 10.2, "ROEQ": 11.67,
    "NIMY": 3.6339160574679585, "NIMYQ": 3.69,
    "INTINCY": 5.737863271644431, "INTINCYQ": 5.66,
    "INTEXPY": 2.1039472141764723, "INTEXPYQ": 1.97,
    "EEFFR": 51.79982063977641, "EEFFQR": 48.96,
    "NONIIAY": 0.71, "NONIIAYQ": 0.61, "NONIXAY": 2.1568257350415543, "NONIXAYQ": 2.04,
    "NTLNLSR": 0.2533998358904637, "NTLNLSQR": 0.27,
    "NETINC": 745_732, "INTINC": 3_259_679, "EINTEXP": 1_195_252, "NONII": 452_296,
    "NONIX": 1_381_616, "IGLSEC": 630, "EXTRA": 0, "PTAXNETINC": 935_911, "ITAX": 190_809,
}

# (row label, Q4'25 single-quarter field value, FY2025 YTD field value)
RATIO_ROWS = [
    ("Return on avg assets (ROAA)", "1.32%", "1.16%"),
    ("Return on avg equity (ROAE)", "11.67%", "10.20%"),
    ("Net interest margin (reported)", "3.69%", "3.63%"),
    ("Yield on earning assets", "5.66%", "5.74%"),
    ("Cost of funding earning assets", "1.97%", "2.10%"),
    ("Efficiency ratio", "48.96%", "51.80%"),
    ("Non-interest income / avg assets", "0.61%", "0.71%"),
    ("Non-interest expense / avg assets", "2.04%", "2.16%"),
    ("Yield: interest-earning assets", "5.66%", "5.74%"),
    ("Cost: funding (earning-asset basis)", "1.97%", "2.10%"),
    ("Net charge-offs / loans", "0.27%", "0.25%"),
]


class TestQuarterlyRatioFields(unittest.TestCase):
    """P0-1: a Quarterly column shows FDIC's single-quarter ratio field."""

    HIST = pd.DataFrame([ONB_Q3_25, ONB_Q4_25])

    def test_quarterly_columns_use_single_quarter_variants(self):
        html, _ = _render(FS._PERFORMANCE, self.HIST, "Quarterly")
        for label, q_val, ytd_val in RATIO_ROWS:
            with self.subTest(row=label):
                self.assertEqual(_texts(html, label)[1], q_val)
        self.assertIn("ROAQ (single quarter, as filed)", html)

    def test_annual_columns_keep_the_ytd_fy_fields(self):
        html, _ = _render(FS._PERFORMANCE, self.HIST, "Annual")
        for label, q_val, ytd_val in RATIO_ROWS:
            with self.subTest(row=label):
                self.assertEqual(_texts(html, label), [ytd_val])

    def test_multi_charter_group_average_based_rows_are_na(self):
        # cert_group SUMS fields it does not classify: INTINCYQ etc. arrive as
        # the charters' sum (5.66 + 5.47 = 11.13). Efficiency is flow ÷ flow —
        # cert_group rebuilds it exactly, so it stays.
        grp = dict(ONB_Q4_25, _charter_count=2, ROA=None, ROAQ=None,
                   INTINCYQ=11.13, INTEXPYQ=3.84, NONIIAYQ=1.39, NONIXAYQ=3.98)
        hist = pd.DataFrame([dict(ONB_Q3_25, _charter_count=2), grp])
        html, ws = _render(FS._PERFORMANCE, hist, "Quarterly")
        for label in ("Yield on earning assets", "Cost of funding earning assets",
                      "Non-interest income / avg assets",
                      "Non-interest expense / avg assets",
                      "Return on avg assets (ROAA)"):
            with self.subTest(row=label):
                self.assertEqual(_texts(html, label)[1], "—")
                self.assertTrue(_live(html, label)[1])          # explained n/a
                self.assertEqual(_export_row(ws, label)[1], "n/a")
        self.assertNotIn("11.13%", html)
        self.assertEqual(_texts(html, "Efficiency ratio")[1], "48.96%")
        self.assertIn("can't be combined across the group", html)


class TestFlowRowsSpan(unittest.TestCase):
    """P0-1 / P0-2: computed flow rows de-cumulate in the Quarterly view."""

    HIST = pd.DataFrame([ONB_Q3_25, ONB_Q4_25])

    def test_profit_margin_single_quarter(self):
        # NI_q = 745,732 − 509,587 = 236,145
        # rev_q = (3,259,679 − 2,363,060) − (1,195,252 − 882,823)
        #         + (452,296 − 343,987) = 896,619 − 312,429 + 108,309 = 692,499
        # 236,145 / 692,499 = 34.100%  (YTD-on-YTD rendered 29.63%)
        html, _ = _render(FS._PERFORMANCE, self.HIST, "Quarterly")
        self.assertEqual(_texts(html, "Profit margin")[1], "34.10%")
        # NONIX_q = 1,381,616 − 1,016,695 = 364,921 / 692,499 = 52.70%
        self.assertEqual(_texts(html, "Overhead ratio (non-int exp / revenue)")[1],
                         "52.70%")
        # First column: no Q2'25 to de-cumulate Q3 → dead, never a YTD figure.
        self.assertEqual(_texts(html, "Profit margin")[0], "—")

    def test_profit_margin_annual_unchanged(self):
        # FY: 745,732 / (3,259,679 − 1,195,252 + 452,296 = 2,516,723) = 29.63%
        html, _ = _render(FS._PERFORMANCE, self.HIST, "Annual")
        self.assertEqual(_texts(html, "Profit margin"), ["29.63%"])

    def test_nonrecurring_single_quarter(self):
        # (IGLSEC 630 − 367) / (PTAXNETINC 935,911 − 641,861) = 263 / 294,050
        # = 0.0894% → 0.09%   (YTD: 630 / 935,911 = 0.07%)
        html, _ = _render(FS._PERFORMANCE, self.HIST, "Quarterly")
        self.assertEqual(_texts(html, "Net nonrecurring income / pre-tax income")[1],
                         "0.09%")

    def test_core_income_single_quarter(self):
        # ONB 3/31/2026 + 6/30/2026 (YTD):
        #   NI_q  = 511,569 − 244,045 = 267,524
        #   IGL_q = −364 − (−453) = 89; EXTRA 0
        #   t = ITAX / PTAXNETINC (YTD) = 141,217 / 653,150 = 0.216209
        #   core = 267,524 − 89 × (1 − 0.216209) = 267,454.2 ($000) → $267.5M
        # (the YTD record gave $511.9M under "Q2 '26").
        # Q1 is as filed: 244,045 − (−453) × (1 − 67,951 / 312,449)
        #   = 244,045 + 453 × 0.782522 = 244,399.5 → $244.4M

        q1 = {"REPDTE": "2026-03-31", "NETINC": 244_045, "IGLSEC": -453, "EXTRA": 0,
              "PTAXNETINC": 312_449, "ITAX": 67_951}
        q2 = {"REPDTE": "2026-06-30", "NETINC": 511_569, "IGLSEC": -364, "EXTRA": 0,
              "PTAXNETINC": 653_150, "ITAX": 141_217}
        html, ws = _render(FS._PERFORMANCE, pd.DataFrame([q1, q2]), "Quarterly")
        self.assertEqual(_texts(html, "Core income"), ["$244.4M", "$267.5M"])
        self.assertAlmostEqual(_export_row(ws, "Core income ($K)")[1],
                               267_524 - 89 * (1 - 141_217 / 653_150), places=6)

    def test_core_eps_is_na_mixed_entities(self):
        html, ws = _render(FS._PERFORMANCE, self.HIST, "Annual")
        self.assertEqual(_texts(html, "Core EPS"), ["—"])
        self.assertEqual(_live(html, "Core EPS"), [True])
        self.assertEqual(_export_row(ws, "Core EPS"), ["n/a"])
        self.assertIn("would mix two entities", html)


# JPM (cert 628) as FDIC reports, $000.
JPM_Q1_26 = {"REPDTE": "2026-03-31", "ILNLS": 24_117_000, "ILNDOM": 22_061_000,
             "LNLSGR": 1_519_711_000, "EDEP": 11_157_000, "DEPIDOM": 1_611_537_000,
             "DEPIFOR": 532_709_000, "DEP": 2_787_994_000, "DEPDOM": 2_209_823_000}
JPM_Q2_26 = {"REPDTE": "2026-06-30", "ILNLS": 48_791_000, "ILNDOM": 44_471_000,
             "LNLSGR": 1_561_577_000, "EDEP": 22_829_000, "DEPIDOM": 1_597_995_000,
             "DEPIFOR": 549_733_000, "DEP": 2_820_284_000, "DEPDOM": 2_226_790_000}


class TestLoanYieldAndDepositCost(unittest.TestCase):

    def test_loan_yield_uses_total_loan_and_lease_interest(self):
        # (48,791,000 − 24,117,000) × 4 / ((1,519,711,000 + 1,561,577,000) / 2)
        # = 98,696,000 / 1,540,644,000 = 6.406%   (ILNDOM gave 5.82%)
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame([JPM_Q1_26, JPM_Q2_26]),
                          "Quarterly")
        self.assertEqual(_texts(html, "Yield: total loans")[1], "6.41%")

    def test_ib_deposit_cost_includes_foreign_offices(self):
        # (22,829,000 − 11,157,000) × 4
        #   / ((1,611,537,000 + 532,709,000 + 1,597,995,000 + 549,733,000) / 2)
        # = 46,688,000 / 2,145,987,000 = 2.1756%   (DEPIDOM alone: 2.91%)
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame([JPM_Q1_26, JPM_Q2_26]),
                          "Quarterly")
        self.assertEqual(_texts(html, "Cost: interest-bearing deposits")[1], "2.18%")

    def test_missing_depifor_with_foreign_offices_is_na(self):
        rows = [{k: v for k, v in r.items() if k != "DEPIFOR"}
                for r in (JPM_Q1_26, JPM_Q2_26)]
        html, ws = _render(FS._PERFORMANCE, pd.DataFrame(rows), "Quarterly")
        self.assertEqual(_texts(html, "Cost: interest-bearing deposits")[1], "—")
        self.assertEqual(_export_row(ws, "Cost: interest-bearing deposits")[1], "n/a")
        self.assertNotIn("2.91%", html)

    def test_domestic_only_null_depifor_counts_zero(self):
        # ONB 2026 (DEP == DEPDOM, FDIC null DEPIFOR):
        # (470,834 − 233,330) × 4 / ((42,681,440 + 43,423,577) / 2)
        # = 950,016 / 43,052,508.5 = 2.2067%
        q1 = {"REPDTE": "2026-03-31", "EDEP": 233_330, "DEPIDOM": 42_681_440,
              "DEPIFOR": float("nan"), "DEP": 56_500_217, "DEPDOM": 56_500_217}
        q2 = {"REPDTE": "2026-06-30", "EDEP": 470_834, "DEPIDOM": 43_423_577,
              "DEPIFOR": float("nan"), "DEP": 56_985_995, "DEPDOM": 56_985_995}
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame([q1, q2]), "Quarterly")
        self.assertEqual(_texts(html, "Cost: interest-bearing deposits")[1], "2.21%")


class TestLeverageRatio(unittest.TestCase):
    """P1-8: the regulatory Tier 1 leverage ratio is RBC1AAJ."""

    HBAN = pd.DataFrame([{"REPDTE": "2026-03-31", "RBC1AAJ": 10.235658787785283,
                          "RBCT1JR": 8.920782246563922}])

    def test_both_leverage_rows_read_rbc1aaj(self):
        for spec, label in ((FS._CAPITAL_ADEQUACY, "Tier 1 Leverage Ratio"),
                            (FS._CAPITAL_STRUCTURE, "Tier 1 leverage ratio")):
            with self.subTest(row=label):
                html, _ = _render(spec, self.HBAN, "Quarterly")
                self.assertEqual(_texts(html, label), ["10.24%"])
                self.assertNotIn("8.92%", html)


class TestNetInterestSpreadRemoved(unittest.TestCase):
    """P1-9: INTINCY − INTEXPY is NIMY (both over earning assets) — not a
    spread. The row and its now-unused kind are gone."""

    def test_row_and_kind_gone(self):
        labels = [r[0] for _, rows in FS._PERFORMANCE for r in rows]
        self.assertNotIn("Net interest spread", labels)
        self.assertNotIn("pctdiff", {r[1] for _, rows in FS._PERFORMANCE for r in rows})


# ONB (cert 3832) through the First Midwest merger (closed 2/15/2022).
ONB_FY22 = [
    {"REPDTE": "2021-12-31", "NETINC": 291_312, "EQTOT": 3_053_575, "EQPP": 0,
     "INTAN": 1_101_712, "ROE": 9.56},
    {"REPDTE": "2022-03-31", "NETINC": -11_233, "EQTOT": 4_966_364, "EQPP": 0,
     "INTAN": 2_098_504, "ROE": -1.12},
    {"REPDTE": "2022-06-30", "NETINC": 110_683, "EQTOT": 4_857_545, "EQPP": 0,
     "INTAN": 2_085_807, "ROE": 5.16},
    {"REPDTE": "2022-09-30", "NETINC": 258_618, "EQTOT": 4_766_758, "EQPP": 0,
     "INTAN": 2_089_298, "ROE": 7.82},
    {"REPDTE": "2022-12-31", "NETINC": 469_735, "EQTOT": 5_000_153, "EQPP": 0,
     "INTAN": 2_072_970, "ROE": 10.37},
]


class TestAnnualFivePointAverage(unittest.TestCase):
    """P1-10: Annual average balances span the year's 5 quarter-ends."""

    def test_fy2022_roace_matches_fdic_roe(self):
        # avg EQTOT = (3,053,575 + 4,966,364 + 4,857,545 + 4,766,758
        #              + 5,000,153) / 5 = 22,644,395 / 5 = 4,528,879
        # ROACE = 469,735 / 4,528,879 = 10.372% (FDIC ROE 10.37); the
        # Dec-to-Dec average (4,026,864) gave 11.67%.
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame(ONB_FY22), "Annual")
        self.assertEqual(_texts(html, "Return on avg common equity (ROACE)"),
                         ["—", "10.37%"])
        # ROATCE: avg INTAN = 9,448,291 / 5 = 1,889,658.2;
        # 469,735 / (4,528,879 − 1,889,658.2) = 17.80%
        self.assertEqual(_texts(html, "Return on avg tangible common equity (ROATCE)")[1],
                         "17.80%")

    def test_first_column_without_its_quarter_ends_is_na(self):
        # FY2021 has only its own 12/31 point in view: never a period-end
        # balance under an "avg" label.
        html, ws = _render(FS._PERFORMANCE, pd.DataFrame(ONB_FY22), "Annual")
        self.assertEqual(_texts(html, "Return on avg common equity (ROACE)")[0], "—")
        self.assertEqual(_export_row(ws, "Return on avg common equity (ROACE)")[0], "n/a")
        self.assertNotIn("9.54%", html)   # 291,312 / 3,053,575 period-end

    def test_missing_interior_quarter_is_na_not_shorter_average(self):
        rows = [r for r in ONB_FY22 if r["REPDTE"] != "2022-06-30"]
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame(rows), "Annual")
        self.assertEqual(_texts(html, "Return on avg common equity (ROACE)"),
                         ["—", "—"])

    def test_quarterly_keeps_two_point_average(self):
        # Q2'22: NI_q = 110,683 − (−11,233) = 121,916; × 4 = 487,664
        # avg EQTOT = (4,966,364 + 4,857,545) / 2 = 4,911,954.5 → 9.928%
        html, _ = _render(FS._PERFORMANCE, pd.DataFrame(ONB_FY22), "Quarterly")
        self.assertEqual(_texts(html, "Return on avg common equity (ROACE)")[2],
                         "9.93%")


class TestFteNimQuarterlySpan(unittest.TestCase):
    """P0-1 corollary: FTE NIM sits on the same span as the reported NIM it
    grosses up — NIMYQ + single-quarter tax-exempt income in the Quarterly
    view (else FTE NIM < reported NIM on adjacent rows)."""

    BASE = {"ERNAST": 50_000_000}

    def _hist(self):
        rows = [dict(self.BASE, REPDTE=d) for d in
                ("2024-12-31", "2025-03-31", "2025-06-30")]
        rows += [dict(ONB_Q3_25, **self.BASE), dict(ONB_Q4_25, **self.BASE)]
        return pd.DataFrame(rows)

    RI = [{"reporting_period": "2025-09-30", "tax_exempt_loan_income": 30_000,
           "tax_exempt_sec_income": 15_000},
          {"reporting_period": "2025-12-31", "tax_exempt_loan_income": 40_000,
           "tax_exempt_sec_income": 20_000}]

    def test_quarterly(self):
        # single quarter tax-exempt = 60,000 − 45,000 = 15,000
        # FTE adj = 15,000 × 0.21 / 0.79 = 3,987.34; × 4 = 15,949.37
        # ÷ avg ERNAST 50,000,000 × 100 = 0.0319 → 3.69 + 0.0319 = 3.72%
        html, _ = _render(FS._PERFORMANCE, self._hist(), "Quarterly", ri_rows=self.RI)
        self.assertEqual(_texts(html, "Net interest margin (FTE)")[-1], "3.72%")

    def test_annual(self):
        # FY tax-exempt 60,000 → 15,949.37 ÷ 50,000,000 × 100 = 0.0319
        # NIMY 3.6339 + 0.0319 = 3.6658 → 3.67%
        html, _ = _render(FS._PERFORMANCE, self._hist(), "Annual", ri_rows=self.RI)
        self.assertEqual(_texts(html, "Net interest margin (FTE)")[-1], "3.67%")


if __name__ == "__main__":
    unittest.main()
