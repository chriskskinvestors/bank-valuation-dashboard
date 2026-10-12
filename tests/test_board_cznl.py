"""
2026-10-12: Citizens National (CZNL, generic name -> no ticker on its row)
showed no target price, spread or chart; the spread chart charted a
"PBCO <- PBCO" self row the board already hides.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

CZNL = {"status": "pending", "announce_date": "2026-04-21", "buyer_ticker": "PEBO",
        "buyer_name": "Peoples Bancorp", "target_name": "Citizens National Corporation",
        "target_ticker": None, "target_cert": 2718,
        "terms": {"consideration": "mixed", "exchange_ratio": 2.1, "cash_per_share": 8.0,
                  "expected_close_date": "2026-12-31"}}
SELF = {"status": "pending", "announce_date": "2026-07-22", "buyer_ticker": "PBCO",
        "buyer_name": "PBCO Financial", "target_name": "PBCO Financial Corporation",
        "target_ticker": "PBCO", "terms": {"consideration": "stock", "exchange_ratio": 1.16,
                                           "expected_close_date": "2027-03-31"}}


def _hist(t):
    return {"PEBO": [{"date": "2026-10-09", "close": 36.00}],
            "CZNL": [{"date": "2026-10-09", "close": 85.00}],
            "PBCO": [{"date": "2026-10-09", "close": 27.94}]}.get(t)


class TestSpreadBuilder(unittest.TestCase):

    def test_cert_gives_cznl_its_ticker_and_a_series(self):
        from data.deal_spreads import build_spread_histories
        with patch("data.deal_spreads._cert_tickers", return_value={2718: "CZNL"}):
            built = build_spread_histories([CZNL], history=_hist)
        (entry,) = built["deals"].values()
        p = entry["series"][0]
        # 2.1 x 36.00 + 8.00 = 83.60 offer vs 85.00 -> 83.60 / 85.00 - 1 = -1.647%
        self.assertAlmostEqual(p["offer"], 83.60, places=2)
        self.assertAlmostEqual(p["gross"], 83.60 / 85.00 - 1, places=5)

    def test_self_row_is_never_charted(self):
        from data.deal_spreads import build_spread_histories
        with patch("data.deal_spreads._cert_tickers", return_value={}):
            built = build_spread_histories([SELF], history=_hist)
        self.assertEqual(built["deals"], {})


class TestBoardPriceRequest(unittest.TestCase):

    def test_board_requests_the_cert_ticker(self):
        import ui.transactions as tx
        src = open(tx.__file__, encoding="utf-8").read()
        self.assertIn('d.get("target_ticker") or cert_map.get(d.get("target_cert") or 0))', src)


if __name__ == "__main__":
    unittest.main()
