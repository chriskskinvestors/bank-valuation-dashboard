"""
Spread tracker data (data/deal_spreads) and figures (ui/spread_tracker).
Hand-computed values:

  John Marshall / Eagle (all-stock, 2.0 JMSB per EFSI, stated close
  2027-03-31):
    2026-09-08  JMSB 23.36, EFSI 45.00 -> offer 46.72, gross 46.72/45.00-1
                = 0.0382222; days 204; annualized 0.0382222*365/204 = 0.0683878
    2026-10-06  JMSB 22.90, EFSI 45.55 -> offer 45.80, gross 45.80/45.55-1
                = 0.0054885; days 176; annualized 0.0054885*365/176 = 0.0113824
  TowneBank / blueharbor (mixed, 1.0534 TOWN + $12.70):
    TOWN 36.15 -> offer 1.0534*36.15 + 12.70 = 50.78041; BLHK 48.55 ->
    gross 50.78041/48.55 - 1 = 0.0459405
  All-cash $19.58: target 19.00 -> gross 0.0305263 regardless of acquirer.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _hist(rows):
    return [{"date": d, "close": c} for d, c in rows]


JMSB = {"status": "pending", "buyer_ticker": "JMSB", "buyer_name": "John Marshall Bancorp",
        "target_ticker": "EFSI", "target_name": "Eagle Financial Services",
        "announce_date": "2026-09-07",
        "terms": {"consideration": "stock", "exchange_ratio": 2.0,
                  "expected_close_date": "2027-03-31"},
        "milestones": {"votes": [], "regulatory_approval": {"date": "2026-10-01", "url": "u"}}}


class TestSpreadSeries(unittest.TestCase):

    def test_stock_deal_hand_math(self):
        from data.deal_spreads import spread_series
        acq = _hist([("2026-09-05", 23.00), ("2026-09-08", 23.36), ("2026-10-06", 22.90)])
        tgt = _hist([("2026-09-05", 40.00), ("2026-09-08", 45.00), ("2026-10-06", 45.55)])
        s, dropped = spread_series(JMSB["terms"], "2026-09-07", acq, tgt, acq_ticker="JMSB")
        self.assertEqual(dropped, 0)
        self.assertEqual([p["date"] for p in s], ["2026-09-08", "2026-10-06"])  # pre-announce excluded
        # the announcement day itself is excluded (after-close announcements)
        s2, _ = spread_series(JMSB["terms"], "2026-09-08", acq, tgt)
        self.assertEqual([p["date"] for p in s2], ["2026-10-06"])
        self.assertAlmostEqual(s[0]["offer"], 46.72, places=4)
        self.assertAlmostEqual(s[0]["gross"], 0.0382222, places=6)
        self.assertEqual(s[0]["days"], 204)
        self.assertAlmostEqual(s[0]["annualized"], 0.0382222 * 365 / 204, places=5)
        self.assertAlmostEqual(s[1]["gross"], 0.0054885, places=5)
        self.assertEqual(s[1]["days"], 176)
        self.assertAlmostEqual(s[1]["annualized"], 0.0113824, places=5)

    def test_mixed_and_cash_and_unaligned_days(self):
        from data.deal_spreads import spread_series
        town = {"consideration": "mixed", "exchange_ratio": 1.0534, "cash_per_share": 12.70,
                "expected_close_date": "2027-03-31"}
        s, _ = spread_series(town, "2026-10-05", _hist([("2026-10-06", 36.15), ("2026-10-07", 36.0)]),
                             _hist([("2026-10-06", 48.55)]))
        self.assertEqual(len(s), 1)                       # 10-07 has no target close
        self.assertAlmostEqual(s[0]["offer"], 50.7804, places=3)
        self.assertAlmostEqual(s[0]["gross"], 0.0459405, places=5)
        cash = {"consideration": "cash", "cash_per_share": 19.58}
        s, _ = spread_series(cash, "2026-04-08", _hist([("2026-05-01", 30.0)]),
                             _hist([("2026-05-01", 19.00)]))
        self.assertAlmostEqual(s[0]["gross"], 19.58 / 19.00 - 1, places=6)
        self.assertIsNone(s[0]["annualized"])             # no stated close

    def test_bad_print_is_dropped_and_counted(self):
        from data.deal_spreads import spread_series
        s, dropped = spread_series(JMSB["terms"], "2026-09-07",
                                   _hist([("2026-09-08", 23.36), ("2026-09-09", 23.40)]),
                                   _hist([("2026-09-08", 45.00), ("2026-09-09", 4.50)]))
        self.assertEqual(([p["date"] for p in s], dropped), (["2026-09-08"], 1))


class TestSpikeFilter(unittest.TestCase):

    def test_one_day_reversing_spike_is_dropped(self):
        # EFSI prints 35.00 for one day between 45.00 days: 46.72/35-1 = 33.5%
        # vs ~3.8% either side -> a bad print, dropped and counted. A level
        # shift that persists (45 -> 40 -> 40) is real and kept.
        from data.deal_spreads import spread_series
        acq = _hist([("2026-09-08", 23.36), ("2026-09-09", 23.36), ("2026-09-10", 23.36),
                     ("2026-09-11", 23.36), ("2026-09-14", 23.36)])
        tgt = _hist([("2026-09-08", 45.00), ("2026-09-09", 35.00), ("2026-09-10", 45.00),
                     ("2026-09-11", 40.00), ("2026-09-14", 40.00)])
        s, dropped = spread_series(JMSB["terms"], "2026-09-07", acq, tgt)
        self.assertEqual(dropped, 1)
        self.assertEqual([p["date"] for p in s],
                         ["2026-09-08", "2026-09-10", "2026-09-11", "2026-09-14"])


class TestBuild(unittest.TestCase):

    def test_build_charts_computable_deals_and_lists_the_rest(self):
        from data.deal_spreads import build_spread_histories, deal_key
        hist = {"JMSB": _hist([("2026-09-08", 23.36), ("2026-10-06", 22.90)]),
                "EFSI": _hist([("2026-09-08", 45.00), ("2026-10-06", 45.55)])}
        deals = [JMSB,
                 {"status": "pending", "buyer_ticker": "HBT", "target_ticker": "TYFG",
                  "target_name": "Tri-County Financial Group", "announce_date": "2026-08-10",
                  "terms": {"consideration": "election", "exchange_ratio": 2.4589,
                            "cash_per_share": 71.01}},
                 {"status": "pending", "buyer_ticker": "BY", "target_ticker": None,
                  "target_name": "Illinois State Bancorp", "announce_date": "2026-10-06",
                  "terms": {"consideration": "mixed", "exchange_ratio": 4.5208}},
                 {"status": "pending", "buyer_ticker": "X", "target_ticker": "Y",
                  "target_name": "Agg Cash", "announce_date": "2026-10-06",
                  "terms": {"consideration": "mixed", "exchange_ratio": 1.0}},
                 {"status": "completed", "buyer_ticker": "Z", "target_ticker": "W"}]
        built = build_spread_histories(deals, history=lambda t: hist.get(t))
        k = deal_key(JMSB)
        self.assertEqual(list(built["deals"]), [k])
        self.assertEqual(built["deals"][k]["label"], "EFSI ← JMSB")
        self.assertEqual(built["deals"][k]["milestones"],
                         [{"date": "2026-10-01", "label": "Regulatory approval"}])
        reasons = {s["target_name"]: s["reason"] for s in built["skipped"]}
        self.assertIn("no stated proration", reasons["Tri-County Financial Group"])
        self.assertIn("not listed", reasons["Illinois State Bancorp"])
        self.assertIn("aggregate", reasons["Agg Cash"])
        self.assertEqual(len(built["skipped"]), 3)        # completed rows ignored

    def test_one_ticker_failing_does_not_fail_the_build(self):
        from data.deal_spreads import build_spread_histories
        def boom(t):
            raise RuntimeError("fmp down")
        built = build_spread_histories([JMSB], history=boom)
        self.assertEqual(built["deals"], {})
        self.assertIn("no overlapping daily closes", built["skipped"][0]["reason"])

    def test_refresh_writes_and_read_path_reads(self):
        from data import deal_spreads as ds
        with patch("data.cache.put") as put:
            built = ds.refresh_spread_histories({"deals": []}, history=lambda t: None)
        put.assert_called_once()
        self.assertEqual(put.call_args.args[0], ds.SPREADS_KEY)
        self.assertEqual(built["deals"], {})
        self.assertIsNone(ds.refresh_spread_histories(None))
        with patch("data.cache.get", return_value={"deals": {}, "built_at": "x"}):
            self.assertEqual(ds.get_spread_histories()["built_at"], "x")
        with patch("data.cache.get", return_value=None):
            self.assertIsNone(ds.get_spread_histories())


class TestTrackerFigures(unittest.TestCase):

    def _deal(self):
        from data.deal_spreads import build_spread_histories
        hist = {"JMSB": _hist([("2026-09-08", 23.36), ("2026-09-30", 23.00), ("2026-10-06", 22.90)]),
                "EFSI": _hist([("2026-09-08", 45.00), ("2026-09-30", 45.20), ("2026-10-06", 45.55)])}
        built = build_spread_histories([JMSB], history=lambda t: hist[t])
        return built["deals"]

    def test_summary_hand_math(self):
        from datetime import date
        from ui.spread_tracker import summary
        d = next(iter(self._deal().values()))
        sm = summary(d, today=date(2026, 10, 6))
        # 1W back = 09-29 -> last close on/before it is 09-08 (0.0382222):
        # change = 0.0054885 - 0.0382222 = -0.0327337. 1M back = 09-06 is
        # before the announcement -> no 1M change (honest None).
        self.assertAlmostEqual(sm["gross"], 0.0054885, places=5)
        self.assertAlmostEqual(sm["chg_1w"], -0.0327337, places=5)
        self.assertIsNone(sm["chg_1m"])
        self.assertEqual((sm["widest"]["date"], sm["tightest"]["date"]),
                         ("2026-09-08", "2026-10-06"))

    def test_figures_build(self):
        from ui.spread_tracker import detail_figure, overlay_figure
        deals = self._deal()
        keys = list(deals)
        fig = overlay_figure(deals, keys, "gross")
        self.assertEqual(fig.data[0].name, "EFSI ← JMSB")
        self.assertEqual(len(fig.data), 3)                # line + milestones + latest dot
        self.assertEqual(len(overlay_figure(deals, keys, "annualized").data), 3)
        d = detail_figure(deals[keys[0]])
        self.assertEqual(len(d.data), 6)                  # 3 prices + 2 fills + spread
        self.assertAlmostEqual(d.data[0].y[0], 100.0)     # rebased


if __name__ == "__main__":
    unittest.main()
