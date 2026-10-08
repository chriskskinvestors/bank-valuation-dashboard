"""
Owner 2026-10-08: "company-stated always wins". A multiple the acquirer's
deck states replaces our computation everywhere the snapshot row is read;
ours stays on the row as <key>_computed. Values from the live board
(Third Coast / Great Plains deck, 425 filed 2026-10-07).
"""
from __future__ import annotations

import unittest


class TestCompanyStatedWins(unittest.TestCase):

    def test_great_plains_deck_replaces_bank_sub_computation(self):
        from data.deal_comps import _snapshot_row
        mult = {"p_tbv": 1.36, "core_dep_premium": 0.043, "p_e": None,
                "tbv_basis": "bank-sub", "flagged": None}
        terms = {"deck_p_tbv": 1.56, "deck_p_e_ltm": 9.3, "deck_core_dep_premium": 0.077}
        d = {"status": "pending", "announce_date": "2026-10-07", "terms": terms,
             "counterparty": {"name": "Great Plains Bancshares, Inc", "cert": 34207}}
        row = _snapshot_row({"ticker": "TCBX", "name": "Third Coast"}, 58716, d, mult)
        self.assertEqual((row["p_tbv"], row["p_tbv_basis"], row["p_tbv_computed"]),
                         (1.56, "stated", 1.36))
        self.assertEqual((row["p_e"], row["p_e_basis"], row["p_e_computed"]),
                         (9.3, "stated", None))
        self.assertEqual((row["core_dep_premium"], row["core_dep_premium_computed"]),
                         (0.077, 0.043))
        self.assertEqual(row["tbv_basis"], "bank-sub")       # ours, kept for the hover

    def test_no_deck_keeps_our_computation(self):
        from data.deal_comps import _snapshot_row
        mult = {"p_tbv": 1.94, "p_e": 15.1, "core_dep_premium": None}
        d = {"status": "pending", "terms": {"deck_p_tbv": None}, "counterparty": {}}
        row = _snapshot_row({"ticker": "FHB"}, 1, d, mult)
        self.assertEqual((row["p_tbv"], row["p_e"]), (1.94, 15.1))
        self.assertNotIn("p_tbv_basis", row)

    def test_board_marks_stated_cells(self):
        import ui.transactions as tx
        src = open(tx.__file__, encoding="utf-8").read()
        self.assertIn('if p and d.get("p_tbv_basis") == "stated":', src)
        self.assertIn('elif pe and d.get("p_e_basis") == "stated":', src)
        self.assertIn('d.get("core_dep_premium_basis") == "stated"', src)


if __name__ == "__main__":
    unittest.main()
