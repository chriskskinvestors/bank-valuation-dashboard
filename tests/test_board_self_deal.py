"""Self-deal rows by name (prod 2026-10-09: "OTTW Ottawa Bancorp" acquiring
"Ottawa Bancorp, Inc"; the deal is Pontiac Bancorp acquiring Ottawa)."""
from __future__ import annotations

import unittest


class TestSelfDealByName(unittest.TestCase):

    def test_ottawa_self_row_is_dropped_real_rows_stay(self):
        from ui.transactions import _pending_rows
        rows = [
            {"status": "pending", "announce_date": "2026-08-20", "buyer_ticker": "OTTW",
             "buyer_name": "Ottawa Bancorp", "target_name": "Ottawa Bancorp, Inc",
             "target_ticker": None},
            {"status": "pending", "announce_date": "2026-09-30", "buyer_ticker": "PEBO",
             "buyer_name": "Peoples Bancorp", "target_name": "Citizens National Corporation"},
            # generic names carry no brand token: never treated as the same party
            {"status": "pending", "announce_date": "2026-08-28", "buyer_ticker": "THFF",
             "buyer_name": "First Financial", "target_name": "First Illinois Corporation"},
        ]
        self.assertEqual([r["buyer_ticker"] for r in _pending_rows(rows)], ["PEBO", "THFF"])


if __name__ == "__main__":
    unittest.main()
