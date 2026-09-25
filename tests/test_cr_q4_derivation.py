"""Company Reported quarterly income: Q4 = FY − 9M on one reporting basis
(docs/REVIEW-2026-09-24-numbers.md P0-2; also fixes P1-7).

Three defects, each pinned with the real filings' numbers ($ thousands unless
noted), hand-checked against the 10-Ks' own tagged Q4 facts:

  * HBAN — the 10-Q lays fee lines out as dimensional member blocks that
    repeat one label ("Noninterest income") and an NCI block that repeats
    "Income after income taxes". Every repeat shared a stitch key, so the last
    block overwrote the statement's own line: 9M "Income after income taxes"
    read 14 (the NCI block) instead of 1,706 and Q4'25 rendered 2,229 − 14 =
    2,215 ($M) — the full year. Fixed by folding member blocks
    (_fold_member_blocks) and numbering repeated keys (_keyed_rows).
    Q4'25 = 2,229 − 1,706 = 523.
  * BBT Q4'23 — FY came from the NEWEST 10-K (FY2025, Brookline-recast
    FY2023 NII 339,711) while the 9M came from legacy Berkshire's 10-Q
    (280,626): 59,085 rendered, the quarter is 88,421 (Berkshire FY2023 10-K
    369,047 − 280,626). Fixed by _nearest_vintage (the 10-K filed first after
    the 9M source).
  * BBT Q4'25 — the FY2025 10-K restated Q3-25 net income (−50,240 → −4,221,
    ASU 2025-08); FY − original 9M is not a quarter. _interim_restated
    compares the two filings' own facts and blanks the Q4 column.
"""
import unittest

import data.sec_statements as S


def _row(label, values, header=False, eid="", etype="xbrli:monetaryItemType"):
    return {"label": label, "header": header, "element_id": eid,
            "etype": "" if header else etype, "values": [] if header else values}


def _filing(rows, colmeta, date, acc):
    return {"rows": rows, "_colmeta": colmeta, "units_scale": 1e3,
            "_meta": {"date": date, "accession": acc}}


class TestFoldMemberBlocks(unittest.TestCase):
    """HBAN Q3-2025 R4 shape ($M): main statement, then one block per member."""

    ROWS = [
        _row("Noninterest income:", [], header=True, eid="us-gaap_NoninterestIncomeAbstract"),
        _row("Noninterest income", [628, 523, 1593, 1481], eid="us-gaap_NoninterestIncome"),
        _row("Income after income taxes", [633, 522, 1706, 1426], eid="us-gaap_ProfitLoss"),
        _row("Non-controlling Interest", [], header=True,
             eid="us-gaap_StatementEquityComponentsAxis"),
        _row("Noninterest expense:", [], header=True, eid="us-gaap_NoninterestExpenseAbstract"),
        _row("Income after income taxes", [None, 5, 14, 16], eid="us-gaap_ProfitLoss"),
        _row("Payments and cash management revenue", [], header=True,
             eid="srt_ProductOrServiceAxis"),
        _row("Noninterest income:", [], header=True, eid="us-gaap_NoninterestIncomeAbstract"),
        _row("Noninterest income", [174, 158, 494, 458], eid="us-gaap_NoninterestIncome"),
        _row("Other noninterest income", [], header=True, eid="srt_ProductOrServiceAxis"),
        _row("Noninterest income:", [], header=True, eid="us-gaap_NoninterestIncomeAbstract"),
        _row("Noninterest income", [68, 33, 114, 85], eid="us-gaap_NoninterestIncome"),
    ]

    def test_single_row_blocks_take_the_member_name(self):
        out = S._fold_member_blocks(self.ROWS)
        labels = [(r["label"], r["header"]) for r in out]
        self.assertEqual(labels, [
            ("Noninterest income:", True),
            ("Noninterest income", False),
            ("Income after income taxes", False),
            ("Non-controlling Interest", False),
            ("Payments and cash management revenue", False),
            ("Other noninterest income", False),
        ])
        byl = {r["label"]: r["values"] for r in out if not r["header"]}
        self.assertEqual(byl["Noninterest income"], [628, 523, 1593, 1481])   # the total
        self.assertEqual(byl["Income after income taxes"], [633, 522, 1706, 1426])
        self.assertEqual(byl["Payments and cash management revenue"], [174, 158, 494, 458])
        self.assertEqual(byl["Non-controlling Interest"], [None, 5, 14, 16])

    def test_multi_row_block_keeps_member_header(self):
        rows = [_row("Consolidated VIEs", [], header=True, eid="us-gaap_ConsolidatedEntitiesAxis"),
                _row("Assets:", [], header=True, eid="us-gaap_AssetsAbstract"),
                _row("Trading assets", [10]), _row("Loans", [20])]
        out = S._fold_member_blocks(rows)
        self.assertEqual([(r["label"], r["header"]) for r in out],
                         [("Consolidated VIEs", True), ("Trading assets", False),
                          ("Loans", False)])

    def test_no_axis_rows_untouched(self):
        rows = [_row("Net income", [1.0]), _row("Other", [2.0])]
        self.assertIs(S._fold_member_blocks(rows), rows)


class TestRepeatedLabelsDoNotClobber(unittest.TestCase):

    def test_second_occurrence_is_its_own_key(self):
        f = {"rows": [_row("Other", [5.0]), _row("Total income", [50.0]),
                      _row("Other", [7.0]), _row("Total expense", [70.0])]}
        vals = S._column_values(f, 0)
        other = ("other", False, "monetary")
        self.assertEqual(vals[other], 5.0)                  # was 7.0 (last wins)
        self.assertEqual(vals[other + (1,)], 7.0)
        order = [k for k, *_ in S._merge_row_order([f])]
        self.assertEqual(order.count(other), 1)
        self.assertIn(other + (1,), order)                  # both lines displayed


class TestNearestVintage(unittest.TestCase):

    K25 = {"_meta": {"date": "2026-03-02"}}
    K24 = {"_meta": {"date": "2025-03-03"}}
    K23 = {"_meta": {"date": "2024-02-28"}}
    CANDS = [(K25, 2), (K24, 1), (K23, 0)]                  # newest first, FY2023 column

    def test_first_10k_after_the_9m_filing(self):
        # 9M-2023 from the Q3-2024 10-Q (filed 2024-11-12) → FY2024 10-K.
        self.assertIs(S._nearest_vintage(self.CANDS, "2024-11-12")[0], self.K24)

    def test_latest_before_when_none_after(self):
        self.assertIs(S._nearest_vintage(self.CANDS, "2026-11-10")[0], self.K25)

    def test_no_dates_keeps_newest(self):
        cands = [({"_meta": {}}, 2), ({"_meta": {}}, 1)]
        self.assertIs(S._nearest_vintage(cands, "2024-11-12"), cands[0])
        self.assertIs(S._nearest_vintage(self.CANDS, ""), self.CANDS[0])


def _facts(rows):
    return {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": rows}}}}}


def _fact(start, end, val, accn, form="10-Q"):
    return {"start": start, "end": end, "val": val, "accn": accn, "form": form,
            "filed": "2026-01-01"}


class TestInterimRestated(unittest.TestCase):
    Q, K = "0001108134-25-000020", "0001628280-26-013247"   # Q3-25 10-Q, FY2025 10-K

    def test_bbt_q3_restated_in_10k(self):
        f = _facts([_fact("2025-07-01", "2025-09-30", -50_240_000, self.Q),
                    _fact("2025-07-01", "2025-09-30", -4_221_000, self.K, "10-K")])
        self.assertTrue(S._interim_restated(f, (2025, 12), self.Q.replace("-", ""), self.K))

    def test_same_values_not_flagged(self):
        f = _facts([_fact("2024-07-01", "2024-09-30", 30_000_000, self.Q),
                    _fact("2024-07-01", "2024-09-30", 30_000_000, self.K, "10-K")])
        self.assertFalse(S._interim_restated(f, (2024, 12), self.Q, self.K))

    def test_rounding_not_flagged(self):
        f = _facts([_fact("2025-07-01", "2025-09-30", 30_000_000, self.Q),
                    _fact("2025-07-01", "2025-09-30", 30_000_400, self.K, "10-K")])
        self.assertFalse(S._interim_restated(f, (2025, 12), self.Q, self.K))

    def test_10k_without_interim_facts_gives_no_evidence(self):
        f = _facts([_fact("2025-07-01", "2025-09-30", -50_240_000, self.Q),
                    _fact("2025-01-01", "2025-12-31", 159_000_000, self.K, "10-K")])
        self.assertFalse(S._interim_restated(f, (2025, 12), self.Q, self.K))

    def test_other_fiscal_year_ignored(self):
        f = _facts([_fact("2024-07-01", "2024-09-30", 1_000_000, self.Q),
                    _fact("2024-07-01", "2024-09-30", 9_000_000, self.K, "10-K")])
        self.assertFalse(S._interim_restated(f, (2025, 12), self.Q, self.K))

    def test_missing_inputs_false(self):
        self.assertFalse(S._interim_restated({}, (2025, 12), self.Q, self.K))
        self.assertFalse(S._interim_restated(_facts([]), (2025, 12), "", self.K))


class TestFlowStitchQ4(unittest.TestCase):
    """End-to-end through _stitch_flow_quarters with the filings' real values."""

    @staticmethod
    def _k(values, date, acc, years):
        cm = [(12, f"Dec. 31, {y}") for y in years]
        return _filing([_row("Net interest income", values,
                             eid="us-gaap_InterestIncomeExpenseNet")], cm, date, acc)

    @staticmethod
    def _q3(values, date, acc, years):
        y0, y1 = years
        cm = [(3, f"Sep. 30, {y0}"), (3, f"Sep. 30, {y1}"),
              (9, f"Sep. 30, {y0}"), (9, f"Sep. 30, {y1}")]
        return _filing([_row("Net interest income", values,
                             eid="us-gaap_InterestIncomeExpenseNet")], cm, date, acc)

    def _nii(self, st, label="Q4'23"):
        i = st["periods"].index(label)
        return next(r for r in st["rows"] if r["label"] == "Net interest income")["values"][i]

    def test_bbt_q4_23_uses_same_vintage_fy(self):
        k25 = self._k([503_106, 400_000, 339_711], "2026-03-02", "K25", (2025, 2024, 2023))
        k24 = self._k([360_000, 369_047, 400_000], "2025-03-03", "K24", (2024, 2023, 2022))
        q3_24 = self._q3([88_000, 90_334, 270_000, 280_626], "2024-11-12", "Q324", (2024, 2023))
        st = S._stitch_flow_quarters([q3_24], [k25, k24], [(2023, 12), (2023, 9)])
        self.assertEqual(self._nii(st), 88_421)             # 369,047 − 280,626; was 59,085
        # (Q3'23 itself comes only from Q3-2023's own 10-Q — never a later
        # 10-Q's comparative column — so it is blank in this fixture.)
        self.assertIsNone(self._nii(st, "Q3'23"))

    def test_restated_interim_blanks_q4(self):
        k25 = self._k([461_714, 0, 0], "2026-03-02", "0001628280-26-013247", (2025, 2024, 2023))
        q3_25 = self._q3([45_078, 0, 206_607, 0], "2025-11-10", "000110813425000020",
                         (2025, 2024))
        facts = _facts([_fact("2025-07-01", "2025-09-30", -50_240_000, "0001108134-25-000020"),
                        _fact("2025-07-01", "2025-09-30", -4_221_000,
                              "0001628280-26-013247", "10-K")])
        st = S._stitch_flow_quarters([q3_25], [k25], [(2025, 12), (2025, 9)], facts=facts)
        self.assertIsNone(self._nii(st, "Q4'25"))           # was 255,107
        self.assertEqual(self._nii(st, "Q3'25"), 45_078)    # the 10-Q quarter is as filed
        # without facts (fixtures, transient failure) the prior difference stands
        st2 = S._stitch_flow_quarters([q3_25], [k25], [(2025, 12), (2025, 9)])
        self.assertEqual(self._nii(st2, "Q4'25"), 461_714 - 206_607)

    def test_hban_duplicate_block_row_no_longer_clobbers_9m(self):
        k = _filing([_row("Income after income taxes", [2_229, 1_960, 1_971],
                          eid="us-gaap_ProfitLoss")],
                    [(12, "Dec. 31, 2025"), (12, "Dec. 31, 2024"), (12, "Dec. 31, 2023")],
                    "2026-02-13", "K")
        q_rows = S._fold_member_blocks([
            _row("Income after income taxes", [633, 522, 1_706, 1_426], eid="us-gaap_ProfitLoss"),
            _row("Non-controlling Interest", [], header=True,
                 eid="us-gaap_StatementEquityComponentsAxis"),
            _row("Income after income taxes", [None, 5, 14, 16], eid="us-gaap_ProfitLoss"),
        ])
        q = _filing(q_rows, [(3, "Sep. 30, 2025"), (3, "Sep. 30, 2024"),
                             (9, "Sep. 30, 2025"), (9, "Sep. 30, 2024")], "2025-10-28", "Q")
        st = S._stitch_flow_quarters([q], [k], [(2025, 12), (2025, 9)])
        row = next(r for r in st["rows"] if r["label"] == "Income after income taxes")
        self.assertEqual(row["values"], [523, 633])          # Q4'25 was 2,215; Q3'25 was blank

    def test_hban_duplicate_without_fold_still_safe(self):
        # Even if a repeated label escapes the member fold, _keyed_rows keeps the
        # statement's first line as the Q4 input.
        k = _filing([_row("Income after income taxes", [2_229], eid="us-gaap_ProfitLoss")],
                    [(12, "Dec. 31, 2025")], "2026-02-13", "K")
        q = _filing([_row("Income after income taxes", [633, 1_706], eid="us-gaap_ProfitLoss"),
                     _row("Income after income taxes", [4, 14], eid="us-gaap_ProfitLoss")],
                    [(3, "Sep. 30, 2025"), (9, "Sep. 30, 2025")], "2025-10-28", "Q")
        st = S._stitch_flow_quarters([q], [k], [(2025, 12), (2025, 9)])
        row = next(r for r in st["rows"] if r["label"] == "Income after income taxes")
        self.assertEqual(row["values"], [523, 633])


if __name__ == "__main__":
    unittest.main()
