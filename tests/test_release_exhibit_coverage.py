"""Results-board exhibit coverage (owner report 2026-10-13: "too much missing
info" on UNTY's exhibit). Every case is the bank's REAL EDGAR exhibit text
(tests/fixtures/release_exhibit_3q26 — verbatim tables / sentences, styling
stripped; URLs in each file's header comment), and every expected value was
read off that text by hand.

Classified on 2026-10-13 (stated-but-missed → fixed here; genuine
non-disclosure → stays None, pinned too):
  UNTY  EPS / adj. EPS / capital / NCOs / NPAs / dividend history / loan
        yield / NII + nonII; NOT stated: ROTCE, TBV, TCE, total-deposit cost.
  FBK   Tier 1 / leverage / TCE / cost of deposits (+ supplement loan yield).
  WFC   ROA / NIM / efficiency / NCOs / ACL / CET1 under "% Change from".
  GS    nine-month efficiency refused; BV + EPS history; Standardized CET1.
  JPM   adj. EPS / dividend / overhead ratio prose; supplement history.
  C     revenue / efficiency / RoTCE beside caption-named segment tables.
Regression guards from the 217-bank old/new diff: FUNC, EWBC, TRMK, PDLB,
KEY, FHN, BMRC, BANR.
"""

import os
import unittest

from data import release_metrics as rm

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "release_exhibit_3q26")


def _doc(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def _cols(html: str, key: str, qends=("2026-09-30", "2026-06-30", "2025-09-30")):
    """The key's (current, prior, year-ago) table reads."""
    return tuple(rm.extract_table_metrics(html, q).get(key) for q in qends)


Q2 = ("2026-06-30", "2026-03-31", "2025-06-30")


class TestUNTY(unittest.TestCase):
    """Unity Bancorp 3Q26 (the owner's screenshot)."""

    @classmethod
    def setUpClass(cls):
        cls.html = _doc("UNTY_2026-10-13_ex99_1.htm")
        cls.cur = rm.extract_release_metrics(cls.html, "2026-09-30")

    def test_eps_ytd_section_no_longer_poisons_the_quarter(self):
        # "Net income per common share - Diluted 1.70 | 1.42 | 1.41"; the
        # same table's "FINANCIAL DATA - YEAR TO DATE" rows (4.52 | 4.15 |
        # 8.9) used to read 4.52 as the quarter → disagreement → n/a.
        self.assertEqual(_cols(self.html, "eps_diluted"), (1.70, 1.42, 1.41))
        self.assertEqual(self.cur["eps_diluted"], 1.70)

    def test_adjusted_eps_with_na_column(self):
        # "Adjusted net income per common share - Diluted (non-GAAP) | 1.51 |
        # N/A | 1.33" — the N/A holds its column; 2Q26 stays n/a.
        self.assertEqual(_cols(self.html, "eps_adj"), (1.51, None, 1.33))

    def test_capital_ratios_all_columns(self):
        self.assertEqual(_cols(self.html, "cet1_ratio"), (14.46, 14.19, 14.45))
        self.assertEqual(_cols(self.html, "t1_ratio"), (14.84, 14.57, 14.88))
        self.assertEqual(_cols(self.html, "total_ratio"), (16.09, 15.82, 16.13))
        self.assertEqual(_cols(self.html, "lev_ratio"), (13.22, 13.17, 12.71))

    def test_capital_merge_fills_the_capital_dict(self):
        from data.ir_provider import extract_capital_ratios
        metrics = dict(self.cur)
        cap = rm.merge_table_capital(metrics, extract_capital_ratios(self.html))
        self.assertEqual((cap["cet1_ratio"], cap["t1_ratio"], cap["total_ratio"],
                          cap["lev_ratio"]), (14.46, 14.84, 16.09, 13.22))
        self.assertNotIn("cet1_ratio", metrics)   # never masked by a None

    def test_inverted_sign_nco_label(self):
        # "QTD annualized net recoveries (chargeoffs) to QTD average loans |
        # 0.02 % | 0.03 % | (0.03)%" — recoveries print positive.
        self.assertEqual(_cols(self.html, "nco_ratio"), (-0.02, -0.03, 0.03))

    def test_nonaccrual_assets_is_the_npa_line(self):
        self.assertEqual(_cols(self.html, "npa_assets"), (0.74, 1.08, 0.71))

    def test_dividends_paid_under_share_section(self):
        self.assertEqual(_cols(self.html, "div_ps"), (0.17, 0.16, 0.15))

    def test_loan_yield_from_average_balance_groups(self):
        # "Total loans (B) | 2,718,209 | 47,816 | 6.88 | 2,423,630 | 42,015 |
        # 6.78" under "Average Balance | Interest | Rate/Yield" × 2.
        self.assertEqual(_cols(self.html, "loan_yield"), (6.88, 6.73, 6.78))

    def test_revenue_is_the_labeled_sum(self):
        # No total line: NII 34,291 + noninterest income 2,359 ($K).
        self.assertEqual(_cols(self.html, "rev_sum"),
                         (36_650_000.0, 33_748_000.0, 32_823_000.0))
        self.assertIsNone(self.cur["total_revenue"])

    def test_genuine_non_disclosure_stays_none(self):
        for key in ("rotce", "tbv_ps", "tce_ratio", "cost_of_deposits"):
            self.assertEqual(_cols(self.html, key), (None, None, None), key)


class TestFBK(unittest.TestCase):
    def test_capital_tce_and_deposit_cost(self):
        html = _doc("FBK_2026-10-13_ex99_1.htm")
        self.assertEqual(_cols(html, "t1_ratio"), (11.0, 11.0, 11.7))
        self.assertEqual(_cols(html, "lev_ratio"), (10.0, 10.1, 10.6))
        self.assertEqual(_cols(html, "tce_ratio"), (9.32, 9.49, 10.1))
        self.assertEqual(_cols(html, "cost_of_deposits"), (2.32, 2.26, 2.53))

    def test_supplement_loan_yield(self):
        # "Loans HFI (a)(b) | … | 6.42 % | … | 6.48 %" (Sep 26 | Jun 26).
        html = _doc("FBK_2026-10-13_ex99_2.htm")
        self.assertEqual(_cols(html, "loan_yield")[:2], (6.42, 6.48))


class TestWFC(unittest.TestCase):
    """'% Change from' columns re-name the comparison periods."""

    @classmethod
    def setUpClass(cls):
        cls.html = _doc("WFC_2026-10-13_ex99_1.htm")

    def test_change_period_header_folds(self):
        rows = rm._table_rows(rm.re.findall(r"(?is)<table[^>]*>(.*?)</table>",
                                            self.html)[0])
        qends = rm._period_header(rows, detail=True)[0]
        self.assertEqual(qends, ["2026-09-30", "2026-06-30", "2025-09-30"])

    def test_ratios(self):
        self.assertEqual(_cols(self.html, "roa"), (1.05, 1.15, 1.10))
        self.assertEqual(_cols(self.html, "nim"), (2.43, 2.43, 2.61))
        self.assertEqual(_cols(self.html, "efficiency"), (63.0, 60.0, 65.0))
        self.assertEqual(_cols(self.html, "cet1_ratio"), (10.0, 10.3, 11.0))

    def test_negative_change_cell_keeps_width(self):
        # "Diluted earnings per common share | 1.87 | 2.00 | 1.66 | (6) | 13"
        # — "(6)" is a change value, not a footnote.
        self.assertEqual(_cols(self.html, "eps_diluted"), (1.87, 2.00, 1.66))

    def test_nco_and_acl_subrow(self):
        self.assertEqual(_cols(self.html, "nco_ratio"), (0.38, 0.34, 0.40))
        # "Allowance for credit losses for loans | $14,458" then "As a % of
        # total loans | 1.38%"; nonperforming assets are stated vs LOANS only.
        self.assertEqual(_cols(self.html, "acl_loans"), (1.38, 1.40, 1.52))
        self.assertEqual(_cols(self.html, "npa_assets"), (None, None, None))


class TestGS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _doc("GS_2026-10-13_ex99_1.htm")
        cls.cur = rm.extract_release_metrics(cls.html, "2026-09-30")

    def test_nine_month_efficiency_is_not_the_quarter(self):
        # "efficiency ratio 3 was 59.4% for the first nine months of 2026" —
        # shipped as the 3Q26 efficiency before; the quarter's isn't stated.
        self.assertIsNone(self.cur["efficiency"])
        self.assertEqual(self.cur["roe"], 18.7)        # quarter, kept

    def test_split_header_with_spaced_comma(self):
        # "SEPTEMBER 30 , | JUNE 30 , | …" over "% CHANGE FROM".
        self.assertEqual(_cols(self.html, "bv_ps"), (372.64, 367.67, 353.79))
        self.assertEqual(_cols(self.html, "eps_diluted"), (17.01, 20.98, 12.25))

    def test_standardized_beats_advanced(self):
        self.assertEqual(_cols(self.html, "cet1_ratio")[:2], (12.4, 12.9))

    def test_total_net_revenues_firmwide(self):
        # "Total net revenues | 18,679 | 20,338 | 15,184" ($ in millions);
        # the "GLOBAL BANKING & MARKETS" segment block's rows never compete.
        self.assertEqual(_cols(self.html, "total_revenue"),
                         (18_679e6, 20_338e6, 15_184e6))


class TestJPM(unittest.TestCase):
    def test_prose_forms(self):
        cur = rm.extract_release_metrics(_doc("JPM_2026-10-13_ex99_1.htm"),
                                         "2026-09-30")
        self.assertEqual(cur["eps_adj"], 5.75)    # "($5.75 PER SHARE) EXCLUDING …"
        self.assertEqual(cur["div_ps"], 1.65)     # "$4.4 billion or $1.65 per share"
        self.assertEqual(cur["efficiency"], 54.0)  # reported overhead ratio

    def test_supplement_fill(self):
        val = {"qend": "2026-09-30", "prior_qend": "2026-06-30",
               "yoy_qend": "2025-09-30",
               "metrics": {"tbv_ps": 112.64, "div_ps": 1.65, "efficiency": 54.0},
               "prior_metrics": {}, "yoy_metrics": {}, "capital": {}}
        rm._supplement_fill(val, _doc("JPM_2026-10-13_ex99_2.htm"), "<p></p>")
        m, p, y, cap = (val["metrics"], val["prior_metrics"],
                        val["yoy_metrics"], val["capital"])
        # NET YIELD ON INTEREST-EARNING ASSETS / AVERAGE RATES "Loans"
        self.assertEqual((m["nim"], p["nim"], y["nim"]), (2.48, 2.40, 2.45))
        self.assertEqual((m["loan_yield"], p["loan_yield"], y["loan_yield"]),
                         (6.59, 6.48, 6.74))
        # Standardized capital, 5 quarters + YTD + change columns
        self.assertEqual((cap["cet1_ratio"], p["cet1_ratio"], y["cet1_ratio"]),
                         (13.9, 14.2, 14.8))
        self.assertEqual((cap["lev_ratio"], p["lev_ratio"], y["lev_ratio"]),
                         (6.5, 6.6, 6.9))
        # release-stated keys: history only where the supplement AGREES
        self.assertEqual((p["tbv_ps"], y["tbv_ps"]), (113.35, 105.70))
        self.assertEqual((p["div_ps"], y["div_ps"]), (1.50, 1.50))
        # "Overhead ratio - reported 48" beats "- managed 47"
        self.assertEqual((p["efficiency"], y["efficiency"]), (48.0, 52.0))
        # firmwide NCO rate from the post-segment CREDIT-RELATED pages
        self.assertEqual((m["nco_ratio"], p["nco_ratio"], y["nco_ratio"]),
                         (0.59, 0.66, 0.76))
        # the CONSUMER & COMMUNITY BANKING page (segment) never fills ROE
        self.assertEqual((m.get("roe"), p.get("roe")), (17.0, 24.0))

    def test_disagreeing_release_value_blocks_history(self):
        val = {"qend": "2026-09-30", "prior_qend": "2026-06-30",
               "yoy_qend": "2025-09-30", "metrics": {"tbv_ps": 111.11},
               "prior_metrics": {}, "yoy_metrics": {}, "capital": {}}
        rm._supplement_fill(val, _doc("JPM_2026-10-13_ex99_2.htm"), "<p></p>")
        self.assertIsNone(val["prior_metrics"].get("tbv_ps"))
        self.assertEqual(val["metrics"]["tbv_ps"], 111.11)


class TestCiti(unittest.TestCase):
    """Citi 3Q26: segment tables name the segment in the header caption
    ("Services ($ in millions …)") and repeat the firmwide labels."""

    @classmethod
    def setUpClass(cls):
        cls.html = _doc("C_2026-10-13_ex99_1.htm")

    def test_firmwide_revenue_and_rotce(self):
        # "Total revenues, net of interest expense | 23,845 | 24,766 |
        # 22,090 | (4)% | 8%" — the "(4)%" change cell keeps the width.
        self.assertEqual(_cols(self.html, "total_revenue"),
                         (23_845e6, 24_766e6, 22_090e6))
        self.assertEqual(_cols(self.html, "rotce"), (11.5, 13.0, 8.0))

    def test_operating_expenses_in_definition_is_not_a_variant(self):
        # "Efficiency Ratio (total operating expenses/total revenues, net)"
        self.assertEqual(_cols(self.html, "efficiency"), (58.5, 57.4, 64.7))


class TestCorpusRegressions(unittest.TestCase):
    """Cases the 217-bank old/new diff surfaced (2Q26 releases)."""

    def test_func_ytd_ratio_block_is_not_quarterly(self):
        # "Performance ratios: (Year to Date Period End, annualized)" heads the
        # 6-column block, and the 3M/6M table's 2-value ratio rows are the
        # six-month pair — the quarter's ratios are not stated.
        html = _doc("FUNC_2026-07-20_ex99_1.htm")
        for key in ("roa", "efficiency"):
            self.assertEqual(_cols(html, key, Q2), (None, None, None), key)

    def test_ewbc_six_month_block_inside_ratio_table(self):
        # 1Q26 ROA is 1.79% / ROE 16.04%; the old read took the six-month
        # block's 2025 value (1.59 / 15.19) as 1Q26.
        html = _doc("EWBC_2026-07-21_ex99_1.htm")
        self.assertEqual(_cols(html, "roa", Q2), (1.75, 1.79, 1.62))
        self.assertEqual(_cols(html, "roe", Q2), (16.01, 16.04, 15.42))
        self.assertEqual(_cols(html, "cost_of_deposits", Q2), (2.10, 2.13, 2.52))

    def test_trmk_operating_section_is_a_variant(self):
        html = _doc("TRMK_2026-07-28_ex99_1.htm")
        self.assertEqual(_cols(html, "roe", Q2), (11.88, 10.62, 10.97))

    def test_pdlb_year_to_date_eps_refused(self):
        # "Our diluted earnings per share of $0.71 year to date" was served as
        # the quarter; the 3-month table's Diluted row is $0.35.
        cur = rm.extract_release_metrics(_doc("PDLB_2026-07-27_ex99_1.htm"),
                                         "2026-06-30")
        self.assertEqual(cur["eps_diluted"], 0.35)

    def test_bmrc_interleaved_change_columns(self):
        # "$0.58 | $0.53 | 9.4 % | $(0.53) | NM | …" under "June 30, 2026 |
        # March 31, 2026 | % Change | June 30, 2025 | % Change | …": the
        # 9.4% change was served as the year-ago EPS ($9.40). The real 2Q25
        # figure is a loss (outside the EPS band → n/a, never 9.40).
        html = _doc("BMRC_2026-07-27_ex99_1.htm")
        self.assertEqual(_cols(html, "eps_diluted", Q2), (0.58, 0.53, None))

    def test_key_line_of_business_table_skipped(self):
        html = _doc("KEY_2026-07-21_ex99_1.htm")
        self.assertEqual(_cols(html, "nco_ratio", Q2), (0.42, 0.38, 0.39))

    def test_fhn_segment_page_skipped(self):
        html = _doc("FHN_2026-07-15_ex99_1.htm")
        self.assertEqual(_cols(html, "efficiency", Q2)[1], 58.54)


    def test_banr_rollforward_sign_is_presentation(self):
        # "Net charge-offs (101) …" then "Net charge-offs / average loans
        # receivable | (0.001)% | (0.010)% | (0.009)%" — charge-offs printed
        # negative in the allowance roll-forward (the prose: "net loan
        # charge-offs … $101,000"), never net recoveries.
        html = _doc("BANR_2026-07-22_ex99_1.htm")
        self.assertEqual(_cols(html, "nco_ratio", Q2), (None, None, None))


class TestLabelSemantics(unittest.TestCase):
    """Row labels from the 217-bank corpus that must (not) map."""

    CASES = [
        # Tier 1 capital over ASSETS is leverage (PDLB, TFIN-style), never T1.
        ("t1_ratio", "Tier 1 Capital / Average Assets", False),
        ("t1_ratio", "Tier 1 capital to total assets (Ponce Financial Group)", False),
        ("lev_ratio", "Tier 1 capital to total assets (Ponce Financial Group)", True),
        ("lev_ratio", "Tier 1 capital to average assets (3)", True),
        ("t1_ratio", "Tier 1 risk-based capital to total risk-weighted assets", True),
        ("lev_ratio", "Supplementary leverage ratio (“SLR”)", False),
        # tangible EQUITY includes preferred — not TCE (TRMK, GBCI)
        ("tce_ratio", "Tangible equity / tangible assets", False),
        ("tce_ratio", "Tangible common equity to tangible assets (non-GAAP)", True),
        # a funding cost and a spot rate are not the quarter's deposit cost
        ("cost_of_deposits", "Average cost of total deposits and other borrowings", False),
        ("cost_of_deposits", "Cost of total deposits, Qtr-End", False),
        ("cost_of_deposits", "Cost of deposits, including demand deposits", True),
        ("cost_of_deposits", "Average cost of deposits", True),
        ("efficiency", "Overhead ratio - reported", True),
    ]

    def test_cases(self):
        import re
        for key, label, want in self.CASES:
            got = bool(re.match(r"\s*" + rm._TABLE_SPECS[key][0], label, re.I))
            self.assertEqual(got, want, (key, label))


class TestCapitalOrdering(unittest.TestCase):
    def test_filled_values_breaking_order_are_dropped(self):
        metrics = {"cet1_ratio": 15.0, "t1_ratio": 12.0}
        cap = rm.merge_table_capital(metrics, {"total_ratio": 16.0})
        self.assertIsNone(cap["cet1_ratio"])
        self.assertIsNone(cap["t1_ratio"])
        self.assertEqual(cap["total_ratio"], 16.0)        # corroborated stays

    def test_bucket_order(self):
        b = {"cet1_ratio": 15.0, "t1_ratio": 12.0, "total_ratio": 16.0}
        rm._order_capital(b)
        self.assertEqual(b, {"cet1_ratio": None, "t1_ratio": None,
                             "total_ratio": None})


class TestReleaseMetricsEndToEnd(unittest.TestCase):
    """release_metrics() on UNTY's real exhibit: capital lands in `capital`,
    history in the period buckets, v31 key."""

    def setUp(self):
        import data.cache as dc
        import data.ir_provider as ip
        self.store = {}
        self._orig = (dc.get, dc.put, rm._current_accession,
                      ip.latest_earnings_release, ip.earnings_supplement,
                      rm._ai_fill)
        dc.get = lambda k, max_age_s=None: self.store.get(k)
        dc.put = lambda k, v: self.store.__setitem__(k, v)
        rm._current_accession = lambda cik: "ACC9"
        ip.latest_earnings_release = lambda cik: {
            "html": _doc("UNTY_2026-10-13_ex99_1.htm"),
            "filed_date": "2026-10-13", "accession": "ACC9", "url": "u"}
        ip.earnings_supplement = lambda cik, acc: None
        rm._ai_fill = lambda val, cik, rel: "ok"

    def tearDown(self):
        import data.cache as dc
        import data.ir_provider as ip
        (dc.get, dc.put, rm._current_accession, ip.latest_earnings_release,
         ip.earnings_supplement, rm._ai_fill) = self._orig

    def test_unty_record(self):
        out = rm.release_metrics(920427)
        self.assertIn("release_metrics:v34:920427", self.store)
        self.assertEqual(out["capital"]["cet1_ratio"], 14.46)
        self.assertEqual(out["capital"]["lev_ratio"], 13.22)
        self.assertEqual(out["prior_metrics"]["cet1_ratio"], 14.19)
        self.assertEqual(out["metrics"]["eps_diluted"], 1.70)
        self.assertEqual(out["metrics"]["rev_sum"], 36_650_000.0)
        self.assertEqual(out["yoy_metrics"]["loan_yield"], 6.78)


class TestBoardAndExhibit(unittest.TestCase):
    def test_rev_sum_fills_board_actual_labeled(self):
        from data import earnings_results as er
        row = {"ticker": "UNTY", "date": "2026-10-13", "eps_act": None,
               "rev_act": None, "eps_est": 1.49, "rev_est": 35_630_330.0,
               "pending": True}
        rm_val = {"filed_date": "2026-10-13", "qend": "2026-09-30",
                  "metrics": {"eps_diluted": 1.70, "rev_sum": 36_650_000.0},
                  "capital": {}}
        import data.bank_mapping as bm
        import data.release_metrics as mod
        orig = (bm.get_cik, mod.release_metrics)
        bm.get_cik = lambda tk: 920427
        mod.release_metrics = lambda cik: rm_val
        try:
            er._fill_release_metrics([row], max_workers=1)
        finally:
            bm.get_cik, mod.release_metrics = orig
        self.assertEqual(row["rev_act"], 36_650_000.0)
        self.assertEqual(row["rev_act_src"], "release, NII + nonint. inc.")
        self.assertIsNone(row["rev_surprise"])            # never scored

    def test_exhibit_revenue_sum_and_capital(self):
        from ui.earnings import _rel_exhibit_rows
        r = {"ticker": "UNTY", "rel": {
            "qend": "2026-09-30", "prior_qend": "2026-06-30",
            "yoy_qend": "2025-09-30",
            "metrics": {"rev_sum": 36_650_000.0, "total_revenue": None},
            "capital": {"cet1_ratio": None, "lev_ratio": 13.22},
            "prior_metrics": {"rev_sum": 33_748_000.0},
            "yoy_metrics": {"rev_sum": 32_823_000.0}}}
        rows = {x["key"]: x for x in _rel_exhibit_rows(r)}
        rev = rows["total_revenue"]
        self.assertEqual((rev["cur"], rev["prior"], rev["yoy"]),
                         (36_650_000.0, 33_748_000.0, 32_823_000.0))
        self.assertTrue(rev["label"].endswith("‡"))
        self.assertEqual(rows["lev_ratio"]["cur"], 13.22)


if __name__ == "__main__":
    unittest.main()
