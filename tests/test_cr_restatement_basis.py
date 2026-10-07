"""Company Reported statements: latest filing wins + reverse-merger basis
(docs/REVIEW-2026-09-24-numbers.md P1-6; owner decision 2026-10).

Beacon Financial (BBT, CIK 1108134) is Berkshire Hills Bancorp's registrant
renamed after the 2025-09-01 merger in which Brookline was the ACCOUNTING
acquirer. Every value below is copied from the filings' own R-files / iXBRL
($ thousands ×1000 = dollars), hand-checked 2026-10-06:

  * 10-Q Q2-2026 (0001628280-26-054720, filed 2026-08-07) R4: Q2-2025
    prior-period column = Brookline recast — net income 22,026, EPS 0.25.
  * 10-Q Q2-2025 (0001108134-25-000011, filed 2025-08-11) R4: legacy
    Berkshire's own Q2-2025 — net income 30,366, EPS 0.66.
  * 10-Q Q3-2025 (0001108134-25-000020, filed 2025-11-10) R4: Q3-2025 as
    originally filed — net income -50,240, EPS -0.57, provision 87,496.
  * 10-K FY2025 (0001628280-26-013247, filed 2026-03-02) iXBRL, Q3-2025
    (2025-07-01..2025-09-30, undimensioned): net income -4,221, EPS -0.05,
    NII 128,850 — restated under ASU 2025-08.
  * 10-K FY2024 (0001108134-25-000003) / FY2023 (0001108134-24-000003):
    legacy Berkshire's own annual statements.

Owner rule: the LATER filing's value is displayed and the original goes into
the cell's click-through; a column only the legacy registrant reports is
labeled with that entity, never shown as the same company silently.
"""
import unittest
from unittest import mock

import data.sec_statements as S
from data.sec_filing_scraper import Fact

CIK = 1108134
LEGACY = "Berkshire Hills Bancorp (legacy)"
MON, EPS, SH = "xbrli:monetaryItemType", "dtr-types:perShareItemType", "xbrli:sharesItemType"


def _row(label, values, eid, etype=MON):
    return {"label": label, "header": False, "element_id": eid, "etype": etype,
            "values": [None if v is None else (v * 1000 if etype == MON else v)
                       for v in values]}


def _filing(rows, colmeta, *, acc, doc, date, form):
    return {"rows": rows, "_colmeta": colmeta, "units_scale": 1e3,
            "periods": [p for _, p in colmeta],
            "_meta": {"cik": CIK, "accession": acc, "doc": doc, "date": date,
                      "form": form}}


def _flow_cm(m, y0, y1, ytd):
    d = {6: 30, 9: 30, 12: 31, 3: 31}[m]
    mon = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}[m]
    return [(3, f"{mon}. {d}, {y0}"), (3, f"{mon}. {d}, {y1}"),
            (ytd, f"{mon}. {d}, {y0}"), (ytd, f"{mon}. {d}, {y1}")]


# ── income R4 rows ($K), columns: 3M current, 3M prior, YTD current, YTD prior ──
Q2_26 = _filing([
    _row("Total interest and dividend income", [293634, 154072, 586018, 307800],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [100427, 65387, 202037, 133285],
         "us-gaap_InterestExpenseOperating"),
    _row("Net interest income", [193207, 88685, 383981, 174515],
         "us-gaap_InterestIncomeExpenseNet"),
    _row("Provision for credit losses on loans", [5007, 6997, 12906, 12971],
         "bbt_ProvisionCreditForCreditLossesOnLoans"),
    _row("Total non-interest income", [25988, 5970, 49935, 11630], "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [127256, 58061, 268078, 118083],
         "us-gaap_NoninterestExpense"),
    _row("Net income", [64426, 22026, 110643, 41126], "us-gaap_NetIncomeLoss"),
    _row("Diluted (in dollars per share)", [0.77, 0.25, 1.32, 0.46],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _flow_cm(6, 2026, 2025, 6), acc="000162828026054720", doc="bbt-20260630.htm",
    date="2026-08-07", form="10-Q")

Q3_25 = _filing([
    _row("Total interest and dividend income", [216161, 159560, 523961, 470374],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [83555, 76552, 216840, 225777],
         "us-gaap_InterestExpenseOperating"),
    _row("Net interest income", [132606, 83008, 307121, 244597],
         "us-gaap_InterestIncomeExpenseNet"),
    _row("Provision for credit losses on loans", [87496, 4832, 100467, 17862],
         "bbt_ProvisionCreditForCreditLosses"),
    _row("Total non-interest income", [12345, 6348, 23975, 19028], "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [129296, 57948, 247379, 178146],
         "us-gaap_NoninterestExpense"),
    _row("Net (loss) income", [-50240, 20142, -9114, 51179], "us-gaap_NetIncomeLoss"),
    _row("Diluted (in dollars per share)", [-0.57, 0.23, -0.1, 0.57],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _flow_cm(9, 2025, 2024, 9), acc="000110813425000020", doc="bbt-20250930.htm",
    date="2025-11-10", form="10-Q")

Q2_25 = _filing([       # legacy Berkshire Hills' own 10-Q
    _row("Total interest and dividend income", [151469, 154109, 299799, 306115],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [59548, 65577, 118107, 129443],
         "us-gaap_InterestExpenseOperating"),
    _row("Net interest income", [91921, 88532, 181692, 176672],
         "us-gaap_InterestIncomeExpenseNet"),
    _row("Total non-interest income (loss)", [21752, 20133, 42424, -12466],
         "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [68144, 70931, 138510, 146951],
         "us-gaap_NoninterestExpense"),
    _row("Net income", [30366, 24025, 56085, 3837],
         "us-gaap_IncomeLossFromContinuingOperations"),
    _row("Diluted earnings per common share (in dollars per share)", [0.66, 0.57, 1.22, 0.09],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _flow_cm(6, 2025, 2024, 6), acc="000110813425000011", doc="bhlb-20250630.htm",
    date="2025-08-11", form="10-Q")

Q2_24 = _filing([       # legacy Berkshire Hills' own 10-Q
    _row("Total interest and dividend income", [154109, 145425, 306115, 277741],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [65577, 52666, 129443, 87449], "us-gaap_InterestExpense"),
    _row("Net interest income", [88532, 92759, 176672, 190292],
         "us-gaap_InterestIncomeExpenseNet"),
    _row("Total non-interest income/(loss)", [20133, 17094, -12466, 33700],
         "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [70931, 74048, 146951, 146003],
         "us-gaap_NoninterestExpense"),
    _row("Net income", [24025, 23861, 3837, 51498],
         "us-gaap_IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"),
    _row("Diluted earnings per common share (in dollars per share)", [0.57, 0.55, 0.09, 1.18],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _flow_cm(6, 2024, 2023, 6), acc="000110813424000015", doc="bhlb-20240630.htm",
    date="2024-08-09", form="10-Q")

_K_CM = lambda ys: [(12, f"Dec. 31, {y}") for y in ys]   # noqa: E731
K25_INC = _filing([      # Beacon FY2025 10-K R5 (Brookline-basis prior years)
    _row("Total interest and dividend income", [832788, 628521, 577287],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [329682, 298936, 237576], "us-gaap_InterestExpenseOperating"),
    _row("Net interest income", [503106, 329585, 339711], "us-gaap_InterestIncomeExpenseNet"),
    _row("Total non-interest income", [49893, 25615, 31934], "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [389745, 241865, 239524], "us-gaap_NoninterestExpense"),
    _row("Net income", [90271, 68715, 74999], "us-gaap_NetIncomeLoss"),
    _row("Diluted (in dollars per share)", [1.03, 0.77, 0.85],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _K_CM((2025, 2024, 2023)), acc="000162828026013247", doc="brkl-20251231.htm",
    date="2026-03-02", form="10-K")
K24_INC = _filing([      # legacy Berkshire FY2024 10-K R5
    _row("Total interest and dividend income", [613938, 576299, 387257],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [262352, 207252, 42660], "us-gaap_InterestExpenseOperating"),
    _row("Net interest income", [351586, 369047, 344597], "us-gaap_InterestIncomeExpenseNet"),
    _row("Total non-interest income", [48414, 42782, 68937], "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [296486, 301508, 288716], "us-gaap_NoninterestExpense"),
    _row("Net income", [61003, 69598, 92533], "us-gaap_IncomeLossFromContinuingOperations"),
    _row("Diluted earnings per share (in dollars per share)", [1.43, 1.6, 2.02],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _K_CM((2024, 2023, 2022)), acc="000110813425000003", doc="bhlb-20241231.htm",
    date="2025-03-03", form="10-K")
K23_INC = _filing([      # legacy Berkshire FY2023 10-K R5
    _row("Total interest and dividend income", [576299, 387257, 329065],
         "us-gaap_InterestAndDividendIncomeOperating"),
    _row("Total interest expense", [207252, 42660, 37899], "us-gaap_InterestExpense"),
    _row("Net interest income", [369047, 344597, 291166], "us-gaap_InterestIncomeExpenseNet"),
    _row("Total non-interest income", [42782, 68937, 143248], "us-gaap_NoninterestIncome"),
    _row("Total non-interest expense", [301508, 288716, 285893], "us-gaap_NoninterestExpense"),
    _row("Net income", [69598, 92533, 118664], "us-gaap_IncomeLossFromContinuingOperations"),
    _row("Diluted earnings per share (in dollars per share)", [1.6, 2.02, 2.39],
         "us-gaap_EarningsPerShareDiluted", EPS),
], _K_CM((2023, 2022, 2021)), acc="000110813424000003", doc="bhlb-20231231.htm",
    date="2024-02-28", form="10-K")

# companyfacts NetIncomeLoss for Q3-2025 from the two filings (the gate).
FACTS = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
    {"start": "2025-07-01", "end": "2025-09-30", "val": -50_240_000,
     "accn": "0001108134-25-000020", "form": "10-Q", "filed": "2025-11-10"},
    {"start": "2025-07-01", "end": "2025-09-30", "val": -4_221_000,
     "accn": "0001628280-26-013247", "form": "10-K", "filed": "2026-03-02"},
]}}}}}

# FY2025 10-K iXBRL facts for the third quarter (undimensioned), $ / $/share.
_Q3 = ("2025-09-30", "2025-07-01")
K25_Q3_FACTS = [
    Fact("us-gaap:InterestAndDividendIncomeOperating", 212_405_000, *_Q3, {}, "usd"),
    Fact("us-gaap:InterestExpense", 83_555_000, *_Q3, {}, "usd"),
    Fact("us-gaap:InterestIncomeExpenseNet", 128_850_000, *_Q3, {}, "usd"),
    Fact("us-gaap:ProvisionForLoanLeaseAndOtherLosses", 20_300_000, *_Q3, {}, "usd"),
    Fact("us-gaap:NetIncomeLoss", -4_221_000, *_Q3, {}, "usd"),
    Fact("us-gaap:EarningsPerShareDiluted", -0.05, *_Q3, {}, "usd/shares"),
    Fact("us-gaap:NetIncomeLoss", 7, *_Q3, {"seg": "x"}, "usd"),     # dimensioned: ignored
]


# Cover-page dei:EntityRegistrantName of each filing (R1, verified 2026-10-06).
_BERKSHIRE = {"000110813425000011", "000110813424000015", "000110813425000003",
              "000110813424000003", "000110813424000020"}


def _names(meta):
    return ("BERKSHIRE HILLS BANCORP, INC." if meta.get("accession") in _BERKSHIRE
            else "BEACON FINANCIAL CORPORATION")


def _cell(st, label, period):
    i = st["periods"].index(period)
    rows = [r for r in st["rows"] if not r["header"] and r["label"] == label]
    assert len(rows) == 1, f"{label!r}: {len(rows)} rows"
    r = rows[0]
    return r["values"][i], (r.get("restated") or [None] * len(st["periods"]))[i]


def _quarterly_income():
    with mock.patch("data.sec_filing_scraper.instance_facts", return_value=K25_Q3_FACTS), \
            mock.patch.object(S, "_registrant_name", side_effect=_names):
        return S._stitch_flow_quarters(
            [Q2_26, Q3_25, Q2_25, Q2_24], [K25_INC, K24_INC],
            [(2026, 6), (2025, 9), (2025, 6), (2024, 6)], facts=FACTS)


class TestQ3RestatedIn10K(unittest.TestCase):
    """Q3'25 shows the 10-K's restated figures; the 10-Q's are the notes."""

    @classmethod
    def setUpClass(cls):
        cls.st = _quarterly_income()

    def test_net_income_restated_with_original_in_click_through(self):
        v, note = _cell(self.st, "Net income", "Q3'25")
        self.assertEqual(v, -4_221_000)                      # was -50,240,000
        self.assertEqual(note["original"], -50_240_000)
        self.assertEqual(note["original_source"], "10-Q filed 2025-11-10")
        self.assertEqual(note["restated_in"], "10-K filed 2026-03-02")
        self.assertFalse(note["not_re_reported"])
        self.assertTrue(note["restated_url"].endswith(
            "/1108134/000162828026013247/brkl-20251231.htm"))

    def test_eps_and_nii_restated(self):
        v, note = _cell(self.st, "Diluted (in dollars per share)", "Q3'25")
        self.assertEqual((v, note["original"]), (-0.05, -0.57))
        v, note = _cell(self.st, "Net interest income", "Q3'25")
        self.assertEqual((v, note["original"]), (128_850_000, 132_606_000))

    def test_line_the_10k_did_not_re_report_is_na_not_superseded(self):
        # The 10-Q's provision (87,496) is tagged with a custom element the
        # 10-K's quarterly table does not re-report: shown n/a with the
        # as-filed figure in the note — never beside the restated NII.
        v, note = _cell(self.st, "Provision for credit losses on loans", "Q3'25")
        self.assertIsNone(v)
        self.assertEqual(note["original"], 87_496_000)
        self.assertTrue(note["not_re_reported"])
        # The 10-Q tags interest expense InterestExpenseOperating, the 10-K's
        # quarterly table InterestExpense: not the same element, so not a
        # re-report of this line — n/a too, though the amounts happen to match.
        v, note = _cell(self.st, "Total interest expense", "Q3'25")
        self.assertIsNone(v)
        self.assertTrue(note["not_re_reported"])
        self.assertEqual(note["original"], 83_555_000)

    def test_column_caveat(self):
        i = self.st["periods"].index("Q3'25")
        self.assertIn("Restated in 10-K filed 2026-03-02", self.st["period_notes"][i])


class TestReverseMergerQuarterBasis(unittest.TestCase):
    """Q2'25 comes from the Q2-2026 10-Q's recast column; Q2'24 (only in the
    legacy registrant's filings) is labeled with that entity."""

    @classmethod
    def setUpClass(cls):
        cls.st = _quarterly_income()

    def test_q2_25_is_the_recast_column(self):
        v, note = _cell(self.st, "Net income", "Q2'25")
        self.assertEqual(v, 22_026_000)                      # was 30,366,000
        self.assertEqual(note["original"], 30_366_000)
        self.assertEqual(note["original_entity"], LEGACY)
        self.assertEqual(note["original_source"], "10-Q filed 2025-08-11")
        self.assertEqual(note["restated_in"], "10-Q filed 2026-08-07")
        v, note = _cell(self.st, "Diluted (in dollars per share)", "Q2'25")
        self.assertEqual((v, note["original"]), (0.25, 0.66))

    def test_legacy_only_column_labeled(self):
        ent = dict(zip(self.st["periods"], self.st["period_entity"]))
        self.assertEqual(ent, {"Q2'26": None, "Q3'25": None, "Q2'25": None,
                               "Q2'24": LEGACY})
        v, note = _cell(self.st, "Net income", "Q2'24")
        self.assertEqual((v, note), (24_025_000, None))       # same company, no note
        i = self.st["periods"].index("Q2'25")
        self.assertIn("Recast in 10-Q filed 2026-08-07", self.st["period_notes"][i])
        self.assertIn(LEGACY, self.st["period_notes"][i])

    def test_recast_break_is_the_first_post_merger_filing(self):
        fs = [Q2_26, Q3_25, Q2_25, Q2_24, K25_INC, K24_INC]
        # Q3-25's own comparatives match no loaded legacy filing here (only the
        # FY2025 10-K and the Q2-2026 10-Q recast one), so the Q3-2025 10-Q is
        # placed by the name it was filed under: Beacon -> post-merger.
        with mock.patch.object(S, "_registrant_name", side_effect=_names):
            self.assertEqual(S._recast_break(fs), "2025-11-10")
        # One registrant name on both sides: a sweeping re-presentation by the
        # same company (BNY's FY2025 10-K re-presenting FY2023) is a
        # restatement, never another entity — no break. (A reverse merger in
        # which the legal acquirer kept its name is the evidence's limit.)
        with mock.patch.object(S, "_registrant_name", return_value="SAME CORP"):
            self.assertEqual(S._recast_break(fs), "")
        # With the legacy Q3-2024 10-Q loaded, the Q3-2025 10-Q's own
        # comparative column recasts it directly.
        q3_24 = _filing([
            _row("Total interest and dividend income", [159000, 1, 470000, 2],
                 "us-gaap_InterestAndDividendIncomeOperating"),
            _row("Total interest expense", [70000, 1, 200000, 2], "us-gaap_InterestExpenseOperating"),
            _row("Net interest income", [88059, 1, 270000, 2], "us-gaap_InterestIncomeExpenseNet"),
            _row("Total non-interest income", [30000, 1, 40000, 2], "us-gaap_NoninterestIncome"),
            _row("Total non-interest expense", [70000, 1, 210000, 2], "us-gaap_NoninterestExpense"),
            _row("Net (loss) income", [37509, 1, 41346, 2], "us-gaap_NetIncomeLoss"),
        ], _flow_cm(9, 2024, 2023, 9), acc="000110813424000020", doc="d", date="2024-11-12",
            form="10-Q")
        with mock.patch.object(S, "_registrant_name", side_effect=_names):
            self.assertEqual(S._recast_break([Q2_26, Q3_25, Q2_25, q3_24, K25_INC]),
                             "2025-11-10")

    def test_q3_25_column_not_labeled_legacy(self):
        # The Q3-2025 10-Q (filed before the FY2025 10-K) agrees with the
        # Q2-2026 / FY2025 basis wherever they overlap, so it is post-merger.
        i = self.st["periods"].index("Q3'25")
        self.assertIsNone(self.st["period_entity"][i])


class TestAnnualBasis(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with mock.patch.object(S, "_registrant_name", side_effect=_names):
            cls.st = S._stitch_statement([K25_INC, K24_INC, K23_INC], 5)

    def test_values_are_the_newest_10k(self):
        ni = next(r for r in self.st["rows"] if r["label"] == "Net income")
        self.assertEqual(ni["values"], [90_271_000, 68_715_000, 74_999_000,
                                        92_533_000, 118_664_000])

    def test_recast_years_carry_the_original_and_legacy_years_are_labeled(self):
        ni = next(r for r in self.st["rows"] if r["label"] == "Net income")
        fy24 = ni["restated"][1]
        self.assertEqual(fy24["original"], 61_003_000)
        self.assertEqual(fy24["original_entity"], LEGACY)
        self.assertEqual(fy24["original_source"], "10-K filed 2025-03-03")
        self.assertEqual(ni["restated"][2]["original"], 69_598_000)   # FY2023: oldest 10-K
        self.assertEqual(self.st["period_entity"], [None, None, None, LEGACY, LEGACY])

    def test_no_backfill_across_the_merger(self):
        # A hole in the recast FY2024 column is NOT filled from Berkshire's
        # own FY2024 10-K (that would splice two companies into one column).
        k25 = {**K25_INC, "rows": [dict(r) for r in K25_INC["rows"]]}
        k25["rows"][-2] = {**k25["rows"][-2], "values": [90_271_000, None, 74_999_000]}
        with mock.patch.object(S, "_registrant_name", side_effect=_names):
            st = S._stitch_statement([k25, K24_INC, K23_INC], 5)
        ni = next(r for r in st["rows"] if r["label"] == "Net income")
        self.assertIsNone(ni["values"][1])                   # never 61,003,000


LINES = ["Loans", "Securities", "Deposits", "Borrowings", "Card fees", "Service charges"]


class TestOrdinaryBankUnchanged(unittest.TestCase):
    """No merger, no restatement: identical values, no notes, no labels."""

    def test_same_comparative_values_add_nothing(self):
        def q(vals, y, acc, date):
            return _filing([_row(LINES[i], v, f"us-gaap_L{i}") for i, v in enumerate(vals)],
                           [(3, f"Jun. 30, {y}"), (3, f"Jun. 30, {y - 1}")],
                           acc=acc, doc="d", date=date, form="10-Q")
        a = [[10, 9], [20, 19], [30, 29], [40, 39], [50, 49], [60, 59]]
        b = [[9, 8], [19, 18], [29, 28], [39, 38], [49, 48], [59, 58]]
        st = S._stitch_flow_quarters([q(a, 2026, "A", "2026-08-01"),
                                      q(b, 2025, "B", "2025-08-01")], [],
                                     [(2026, 6), (2025, 6)])
        self.assertNotIn("period_entity", st)
        self.assertNotIn("period_notes", st)
        self.assertTrue(all("restated" not in r for r in st["rows"]))
        self.assertEqual(st["rows"][0]["values"], [10_000, 9_000])

    def test_partial_restatement_is_not_an_entity_swap(self):
        # A later 10-Q revises 2 of 6 lines (a reclassification): those two
        # cells show the later value with notes; no legacy label.
        def q(vals, y, acc, date):
            return _filing([_row(LINES[i], v, f"us-gaap_L{i}") for i, v in enumerate(vals)],
                           [(3, f"Jun. 30, {y}"), (3, f"Jun. 30, {y - 1}")],
                           acc=acc, doc="d", date=date, form="10-Q")
        new = q([[10, 9], [20, 19], [30, 29], [40, 39], [50, 47], [60, 61]], 2026, "A",
                "2026-08-01")
        old = q([[9, 8], [19, 18], [29, 28], [39, 38], [49, 48], [59, 58]], 2025, "B",
                "2025-08-01")
        self.assertEqual(S._recast_break([new, old]), "")
        st = S._stitch_flow_quarters([new, old], [], [(2026, 6), (2025, 6)])
        self.assertNotIn("period_entity", st)
        line4 = next(r for r in st["rows"] if r["label"] == "Card fees")
        self.assertEqual(line4["values"], [50_000, 47_000])
        self.assertEqual(line4["restated"][1]["original"], 49_000)
        self.assertEqual(line4["restated"][1]["original_entity"], "")
        self.assertIsNone(line4["restated"][0])
        self.assertIn("Re-presented in 10-Q filed 2026-08-01", st["period_notes"][1])

    def test_reclassified_column_replaces_whole_and_flags_folded_line(self):
        # The later 10-Q folds 'Service charges' into 'Card fees' for the
        # prior-year quarter (49 -> 108; 59 -> gone). Patching only Card fees
        # would show 108 + 59 (double count); the whole later column is shown
        # and the folded line is n/a with its as-filed 59 in the note.
        def q(rows, y, acc, date):
            return _filing([_row(lab, v, f"us-gaap_{lab.replace(' ', '')}") for lab, v in rows],
                           [(3, f"Jun. 30, {y}"), (3, f"Jun. 30, {y - 1}")],
                           acc=acc, doc="d", date=date, form="10-Q")
        base = [(LINES[i], [10 * i + 9, 10 * i + 8]) for i in range(6)]
        later = [(LINES[i], [10 * i + 10, 10 * i + 9]) for i in range(4)] + [
            ("Card fees", [110, 108])]
        st = S._stitch_flow_quarters([q(later, 2026, "A", "2026-08-01"),
                                      q(base, 2025, "B", "2025-08-01")], [],
                                     [(2026, 6), (2025, 6)])
        l4 = next(r for r in st["rows"] if r["label"] == "Card fees")
        l5 = next(r for r in st["rows"] if r["label"] == "Service charges")
        self.assertEqual(l4["values"], [110_000, 108_000])
        self.assertEqual(l4["restated"][1]["original"], 49_000)
        self.assertEqual(l5["values"], [None, None])
        self.assertEqual(l5["restated"][1]["original"], 59_000)
        self.assertTrue(l5["restated"][1]["not_re_reported"])


class TestBalanceQuarterBasis(unittest.TestCase):

    def test_legacy_quarter_ends_labeled_and_recast_year_end_noted(self):
        def bal(rows, dates, acc, doc, date, form):
            return _filing([_row(lab, v, eid) for lab, v, eid in rows],
                           [(12, d) for d in dates], acc=acc, doc=doc, date=date, form=form)
        L = ("Total cash and cash equivalents", "Total assets", "Total deposits",
             "Total liabilities", "Total liabilities and equity")
        E = ("us-gaap_Cash", "us-gaap_Assets", "us-gaap_Deposits", "us-gaap_Liabilities",
             "us-gaap_LiabilitiesAndStockholdersEquity")
        q2_26 = bal(zip(L, [[1216103, 2041745], [22250964, 23220372], [18485864, 19514657],
                            [19711168, 20724311], [22250964, 23220372]], E),
                    ["Jun. 30, 2026", "Dec. 31, 2025"], "000162828026054720",
                    "bbt-20260630.htm", "2026-08-07", "10-Q")
        k25 = bal(zip(L, [[2041745, 543670], [23220372, 11905326], [19514657, 8901644],
                          [20724311, 10683387], [23220372, 11905326]], E),
                  ["Dec. 31, 2025", "Dec. 31, 2024"], "000162828026013247",
                  "brkl-20251231.htm", "2026-03-02", "10-K")
        q2_25 = bal(zip(L, [[802731, 1128409], [12034748, 12273408], [9979031, 10375204],
                            [10812437, 11105984], [12034748, 12273408]], E),
                    ["Jun. 30, 2025", "Dec. 31, 2024"], "000110813425000011",
                    "bhlb-20250630.htm", "2025-08-11", "10-Q")
        k24 = bal(zip(L, [[1128409, 1203244], [12273408, 12430821], [10375204, 10633384],
                          [11105984, 11418600], [12273408, 12430821]], E),
                  ["Dec. 31, 2024", "Dec. 31, 2023"], "000110813425000003",
                  "bhlb-20241231.htm", "2025-03-03", "10-K")
        with mock.patch.object(S, "_registrant_name", side_effect=_names):
            st = S._stitch_balance_quarters([q2_26, q2_25], [k25, k24],
                                            [(2026, 6), (2025, 12), (2025, 6),
                                             (2024, 12), (2023, 12)])
        ent = dict(zip(st["periods"], st["period_entity"]))
        self.assertEqual(ent, {"Q2'26": None, "Q4'25": None, "Q2'25": LEGACY,
                               "Q4'24": None, "Q4'23": LEGACY})
        v, note = _cell(st, "Total assets", "Q4'24")
        self.assertEqual(v, 11_905_326_000)                  # Brookline recast year-end
        self.assertEqual(note["original"], 12_273_408_000)   # Berkshire's own FY2024 10-K
        self.assertEqual(note["original_entity"], LEGACY)
        self.assertEqual(_cell(st, "Total assets", "Q2'25")[0], 12_034_748_000)


class TestReplacementSafety(unittest.TestCase):
    """Defects the 170-bank old/new diff caught in the first cut, pinned."""

    def test_repeated_label_rekeyed_by_element(self):
        # INBK ($K): its 10-Qs label BOTH the asset line (InterestBearing-
        # DepositsInBanks) and the deposit liability 'Interest-bearing
        # deposits'; its 10-K words the asset line differently, so the
        # liability is the first 'interest-bearing deposits' there. The 10-K
        # restating the year-end (another line) must not drop its liability
        # (4,796,962) into the asset row (456,826).
        A, L = "us-gaap_InterestBearingDepositsInBanks", "us-gaap_InterestBearingDepositLiabilities"
        q = _filing([_row("Interest-bearing deposits", [776738, 456826], A),
                     _row("Loans", [3_000_000, 2_900_000], "us-gaap_Loans"),
                     _row("Interest-bearing deposits", [4671895, 4796962], L)],
                    [(12, "Sep. 30, 2025"), (12, "Dec. 31, 2024")],
                    acc="Q", doc="d", date="2025-11-10", form="10-Q")
        k = _filing([_row("Interest-bearing deposits in banks", [450632, 456826], A),
                     _row("Loans", [3_100_000, 2_901_000], "us-gaap_Loans"),
                     _row("Interest-bearing deposits", [4692934, 4796962], L)],
                    [(12, "Dec. 31, 2025"), (12, "Dec. 31, 2024")],
                    acc="K", doc="d", date="2026-03-11", form="10-K")
        st = S._stitch_balance_quarters([q], [k], [(2025, 9), (2024, 12)])
        by_eid = {r["element_id"]: r for r in st["rows"] if not r["header"]}
        i = st["periods"].index("Q4'24")
        self.assertEqual(by_eid[A]["values"][i], 456_826_000)
        self.assertEqual(by_eid[L]["values"][i], 4_796_962_000)
        loans = next(r for r in st["rows"] if r["label"] == "Loans")
        self.assertEqual(loans["values"][i], 2_901_000_000)            # the 10-K's restated
        self.assertEqual(loans["restated"][i]["original"], 2_900_000_000)

    def test_coreported_elements_never_fold(self):
        # EBC: 'Net (loss) income from discontinued operations' is a label
        # token-superset of 'Net income (loss)'. Once re-presented quarters left
        # the two rows never populated in the same column, the variant tier
        # folded them and Q4'23 net income rendered the discontinued 286,994
        # instead of 318,503. Elements one filing reports on separate lines
        # are distinct lines.
        NI, DO = "us-gaap_NetIncomeLoss", "us-gaap_IncomeLossFromDiscontinuedOperations"
        rows = [
            {"label": "Net income (loss)", "header": False, "kind": "monetary",
             "element_id": NI, "values": [-6_188_000, None]},
            {"label": "Net (loss) income from discontinued operations", "header": False,
             "kind": "monetary", "element_id": DO, "values": [None, 286_994_000]},
        ]
        out = S._consolidate_variants({"periods": ["Q3'24", "Q4'23"], "rows": rows,
                                       "_cooccur": {NI: {0, 2}, DO: {2}}})
        ni = next(r for r in out["rows"] if r["label"] == "Net income (loss)")
        self.assertEqual(ni["values"], [-6_188_000, None])             # never 286,994,000
        self.assertEqual(len(out["rows"]), 2)
        self.assertNotIn("_cooccur", out)
        # Never reported side by side (a relabel that also switched element,
        # e.g. Berkshire's IncomeLossFromContinuingOperations -> NetIncomeLoss):
        # the variant tier still folds the twins as before.
        out2 = S._consolidate_variants({"periods": ["Q3'24", "Q4'23"], "rows": rows,
                                        "_cooccur": {NI: {0}, DO: {1}}})
        self.assertEqual(len(out2["rows"]), 1)

    def test_blocked_row_folds_into_no_other_element(self):
        # FRST 10-Ks: basic EPS (2025-26 10-Ks); continuing-ops EPS beside
        # basic EPS (2025-04 10-K); continuing + discontinued EPS (2024-10
        # 10-K: 2021 = 1.26 + 0.01). No filing states 2021 basic EPS, so that
        # cell is n/a — never the 0.01 discontinued figure.
        B, C, D = ("us-gaap_EarningsPerShareBasic",
                   "us-gaap_IncomeLossFromContinuingOperationsPerBasicShare",
                   "us-gaap_IncomeLossFromDiscontinuedOperationsNetOfTaxPerBasicShare")
        rows = [
            {"label": "Earnings (loss) per share, basic (in dollars per share)", "header": False,
             "kind": "pershare", "element_id": B, "values": [2.49, None]},
            {"label": "Earnings (loss) per share from continuing operations, basic", "header": False,
             "kind": "pershare", "element_id": C, "values": [None, 1.26]},
            {"label": "Earnings per share from discontinued operation, basic", "header": False,
             "kind": "pershare", "element_id": D, "values": [None, 0.01]},
        ]
        out = S._consolidate_variants({"periods": ["2025", "2021"], "rows": rows,
                                       "_cooccur": {B: {0, 1}, C: {1, 2}, D: {2}}})
        basic = next(r for r in out["rows"] if r["element_id"] == B)
        self.assertEqual(basic["values"], [2.49, None])
        self.assertEqual(len(out["rows"]), 3)
        # either row order (the fold walks rows top-down)
        out = S._consolidate_variants({"periods": ["2025", "2021"], "rows": rows[::-1],
                                       "_cooccur": {B: {0, 1}, C: {1, 2}, D: {2}}})
        self.assertEqual({r["element_id"]: r["values"] for r in out["rows"]},
                         {B: [2.49, None], C: [None, 1.26], D: [None, 0.01]})

    def test_rounding_switch_and_ampersand_rename_are_not_a_merger(self):
        # FMFG's FY2023 10-K: whole dollars, 'Farmers & Merchants Bancshares,
        # Inc.'; FY2024 10-K: rounded thousands under the same 'USD ($)'
        # title, 'Farmers and Merchants Bancshares, Inc.'. Every line "differs"
        # by < $1,000 — the same company, never a legacy label.
        def k(vals, years, acc, date):
            f = _filing([_row(LINES[i], [v / 1000 for v in vs], f"us-gaap_L{i}")
                         for i, vs in enumerate(vals)],
                        [(12, f"Dec. 31, {y}") for y in years],
                        acc=acc, doc="d", date=date, form="10-K")
            f["units_scale"] = 1.0
            return f
        k24 = k([[38363000, 31323000], [20836000, 21416000], [1, 21986000],
                 [2, 6418000], [3, 4000], [4, 5000]], (2024, 2023), "K24", "2025-03-13")
        k23 = k([[31323138, 26269653], [21416013, 24123495], [21986013, 23648495],
                 [6418337, 8090127], [4321, 1], [5432, 2]], (2023, 2022), "K23", "2024-03-11")
        names = {"K24": "Farmers and Merchants Bancshares, Inc.",
                 "K23": "Farmers & Merchants Bancshares, Inc."}
        with mock.patch.object(S, "_registrant_name",
                               side_effect=lambda m: names[m["accession"]]):
            self.assertEqual(S._recast_break([k24, k23]), "")
            st = S._stitch_statement([k24, k23], 5)
        self.assertNotIn("period_entity", st)
        self.assertTrue(all("restated" not in r for r in st["rows"]))

    def test_pnc_net_income_relabel_still_one_row(self):
        # Regression anchor (200-bank audit): PNC 'Net income (loss)' (FY2021,
        # IncomeLossFromContinuingOperations...) and 'Net income' (FY2022+,
        # ProfitLoss) are ONE row ($M, PNC 10-Ks). Both sit beside 'Net income
        # attributable to common shareholders', which resembles both labels.
        PL, X, AC = ("us-gaap_ProfitLoss",
                     "us-gaap_IncomeLossFromContinuingOperationsIncludingPortionAttributable"
                     "ToNoncontrollingInterest",
                     "us-gaap_NetIncomeLossAvailableToCommonStockholdersBasic")
        rows = [
            {"label": "Net income", "header": False, "kind": "monetary", "element_id": PL,
             "values": [6113e6, None]},
            {"label": "Net income (loss)", "header": False, "kind": "monetary",
             "element_id": X, "values": [None, 5725e6]},
            {"label": "Net income attributable to common shareholders", "header": False,
             "kind": "monetary", "element_id": AC, "values": [5735e6, 5436e6]},
        ]
        out = S._consolidate_variants({"periods": ["2022", "2021"], "rows": rows,
                                       "_cooccur": {PL: {0, 1}, X: {2}, AC: {0, 1, 2}}})
        ni = [r for r in out["rows"] if r["label"].startswith("Net income")
              and "common" not in r["label"]]
        self.assertEqual(len(ni), 1)
        self.assertEqual(ni[0]["values"], [6113e6, 5725e6])

    def test_same_year_periods_order_by_month(self):
        # HNVR changed fiscal year-end (Sep -> Dec) in 2023: the column order
        # was left to set iteration (hash seed) — two runs, two orders.
        f = {"periods": ["Dec. 31, 2024", "Dec. 31, 2023", "Sep. 30, 2023"], "units_scale": 1e3,
             "rows": [{"label": "Net income", "header": False, "element_id": "e",
                       "etype": MON, "values": [3.0, 2.0, 1.0]}]}
        st = S._stitch_statement([f], 5)
        self.assertEqual(st["periods"], ["Dec. 31, 2024", "Dec. 31, 2023", "Sep. 30, 2023"])
        self.assertEqual(st["rows"][0]["values"], [3.0, 2.0, 1.0])


if __name__ == "__main__":
    unittest.main()
