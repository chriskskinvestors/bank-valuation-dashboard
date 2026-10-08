"""
Transactions section render guard (docs/SNL-BUILD-PLAN.md §14).

Runs app.py headlessly via AppTest, switches to the Transactions section,
and asserts the owner-decided structure renders: the lazy_tabs pill bar
with the built sub-tabs (Recent Deals first — universe-wide, reads only
the deal-comps snapshot; By Bank; Detailed M&A History; Detailed Offerings;
and the kept Insider Activity). The default pane is Recent Deals, which
renders its empty-snapshot notice on the isolated store (no network) and,
seeded with a snapshot, lists ONLY the pending deals (owner 2026-10-06); the
By Bank pane is then selected to assert the shared bank picker in its
no-selection state (index=None — no network fan-out happens until a bank
is picked, so this test never touches FDIC/EDGAR).

Run: python -m unittest tests.test_transactions_ui
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))  # _apptest_store, not via tests/__init__

from _apptest_store import isolate_store  # noqa: E402


# Bounded universe for the AppTest run — same rationale as tests/test_nav_renders:
# in a cold environment the first at.run() lands on Home, whose universe +
# metrics builds fan out live for the whole universe; AppTest's timeout joins
# the script thread, so the test hung then ERRORed. Injecting the module-level
# universe cache (data, not a function replacement) bounds every consumer to
# 2 real banks; the Transactions structure under test still renders for real.
_STUB_UNIVERSE = {
    "AMAL": {"cik": 1823608, "fdic_cert": 622, "share_class": "common",
             "bank_name": "Amalgamated Financial Corp."},
    "JPM": {"cik": 19617, "fdic_cert": 628, "share_class": "common",
            "bank_name": "JPMorgan Chase & Co."},
}


class TestTransactionsSection(unittest.TestCase):

    def setUp(self):
        isolate_store(self)   # never the dev's real cache.db
        import data.bank_universe as bu
        self._bu = bu
        self._saved = (bu._UNIVERSE_CACHE, bu._NONCOMMON_CACHE,
                       bu._NONCOMMON_PRIMARY_CACHE)
        bu._UNIVERSE_CACHE = dict(_STUB_UNIVERSE)
        bu._NONCOMMON_CACHE = None
        bu._NONCOMMON_PRIMARY_CACHE = None

    def tearDown(self):
        (self._bu._UNIVERSE_CACHE, self._bu._NONCOMMON_CACHE,
         self._bu._NONCOMMON_PRIMARY_CACHE) = self._saved

    def _open_transactions(self):
        from streamlit.testing.v1 import AppTest
        app_path = str(Path(__file__).parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=180)
        at.run()
        nav = next(r for r in at.radio if "Transactions" in list(map(str, r.options)))
        nav.set_value("Transactions")
        at.run()
        return at

    def test_subtabs_and_bank_picker_render(self):
        try:
            at = self._open_transactions()
        except ModuleNotFoundError as e:  # very old streamlit — skip, not fail
            self.skipTest(f"AppTest unavailable: {e}")
        tabs = ["Recent Deals", "By Bank", "Detailed M&A History",
                "Detailed Offerings", "Private Equity Transactions",
                "Comparable Deal Analysis", "Insider Activity"]
        tab_bar = next((r for r in at.radio
                        if list(map(str, r.options)) == tabs), None)
        self.assertIsNotNone(
            tab_bar,
            "Transactions lazy_tabs bar missing. Radios: "
            f"{[list(r.options) for r in at.radio]}")
        # Default pane is Recent Deals (first pill); on the isolated store
        # there is no compiled snapshot, so it renders the honest notice.
        self.assertEqual(str(tab_bar.value), "Recent Deals")
        self.assertTrue(any("snapshot has not been compiled" in str(i.value)
                            for i in at.info),
                        [str(i.value) for i in at.info])
        tab_bar.set_value("By Bank")
        at.run()
        picker = next((sb for sb in at.selectbox if sb.key == "txn_bank"), None)
        self.assertIsNotNone(picker, "shared bank picker missing")
        self.assertIsNone(picker.value, "picker must default to no selection "
                          "(no network fan-out on first render)")
        # No-selection state renders the pick-a-bank prompt, not a table.
        self.assertTrue(any("Pick a bank" in str(i.value) for i in at.info),
                        [str(i.value) for i in at.info])

    def test_recent_deals_lists_only_pending(self):
        # Owner 2026-10-06: closed deals are off the arb board. Seed the
        # isolated store with one pending and one completed deal.
        from data import cache
        from data.deal_comps import SNAPSHOT_KEY
        cache.put(SNAPSHOT_KEY, {"built_at": "2026-10-06T12:00:00", "deals": [
            {"status": "pending", "announce_date": "2026-10-06",
             "target_name": "blueharbor bank", "target_ticker": "BLHK",
             "buyer_name": "TowneBank", "buyer_ticker": "TOWN",
             "terms": {"exchange_ratio": 1.0534, "cash_per_share": 12.70}},
            {"status": "completed", "announce_date": "2026-05-01",
             "completion_date": "2026-09-30",
             "target_name": "Closed Target Bancorp", "target_ticker": "CLSD",
             "buyer_name": "Buyer Bancorp", "buyer_ticker": "BUYR"},
        ]})
        try:
            at = self._open_transactions()
        except ModuleNotFoundError as e:
            self.skipTest(f"AppTest unavailable: {e}")
        html = "\n".join(str(m.value) for m in at.markdown)
        self.assertIn("blueharbor bank", html)
        self.assertIn("1.0534", html)
        self.assertNotIn("Closed Target Bancorp", html)
        self.assertNotIn("CLSD", html)
        self.assertFalse(any("snapshot has not been compiled" in str(i.value)
                             for i in at.info))

    def test_merger_arb_group_comes_first_and_stated_wins(self):
        # Owner 2026-10-08: "make the merger arb section first" and
        # "company-stated always wins". Header and row cells must stay aligned.
        import re
        from data import cache
        from data.deal_comps import SNAPSHOT_KEY
        cache.put(SNAPSHOT_KEY, {"built_at": "2026-10-08T12:00:00", "deals": [
            {"status": "pending", "announce_date": "2026-10-07",
             "target_name": "Great Plains Bancshares, Inc", "target_ticker": None,
             "buyer_name": "Third Coast", "buyer_ticker": "TCBX",
             "p_tbv": 1.56, "p_tbv_basis": "stated", "p_tbv_computed": 1.36,
             "terms": {"consideration": "stock", "deck_p_tbv": 1.56,
                       "expected_close_date": "2027-03-31"}}]})
        try:
            at = self._open_transactions()
        except ModuleNotFoundError as e:
            self.skipTest(f"AppTest unavailable: {e}")
        html = next(str(m.value) for m in at.markdown if "Merger arb (live)" in str(m.value))
        groups = re.findall(r'<th colspan="\d+"[^>]*>([^<]+)</th>', html)
        self.assertEqual(groups, ["Deal", "Merger arb (live)", "Terms",
                                  "Valuation at announce", "Agreement"])
        cols = re.findall(r"<th(?: [^>]*)?>([^<]+)</th>", html.split("</tr>", 1)[1])
        self.assertEqual(cols[3:6], ["Consid.", "Target px", "Implied offer"])
        row = html.split("<tbody>", 1)[1].split("</tr>", 1)[0]
        cells = re.findall(r"<td[ >]", row)
        self.assertEqual(len(cells), len(cols))                # aligned
        self.assertIn("1.56xᵈ", row)
        self.assertIn("our computation: 1.36x", row)

    def test_spread_tracker_renders_under_the_board(self):
        # Owner 2026-10-07: interactive spread chart under the board. Seed the
        # snapshot and the tracker history; both charts and the picker render.
        from data import cache
        from data.deal_comps import SNAPSHOT_KEY
        from data.deal_spreads import SPREADS_KEY
        cache.put(SNAPSHOT_KEY, {"built_at": "2026-10-07T12:00:00", "deals": [
            {"status": "pending", "announce_date": "2026-09-08",
             "target_name": "Eagle Financial Services", "target_ticker": "EFSI",
             "buyer_name": "John Marshall Bancorp", "buyer_ticker": "JMSB",
             "terms": {"exchange_ratio": 2.0, "consideration": "stock"}}]})
        series = [{"date": "2026-09-08", "acq": 23.36, "tgt": 45.00, "offer": 46.72,
                   "gross": 0.038222, "annualized": 0.068388, "days": 204},
                  {"date": "2026-10-06", "acq": 22.90, "tgt": 45.55, "offer": 45.80,
                   "gross": 0.005488, "annualized": 0.011382, "days": 176}]
        cache.put(SPREADS_KEY, {"built_at": "2026-10-07T12:05:00", "skipped": [
            {"key": "BY:x", "label": "Illinois State Bancorp ← BY",
             "target_name": "Illinois State Bancorp", "reason": "target not listed"}],
            "deals": {"JMSB:EFSI:2026-09-08": {
                "label": "EFSI ← JMSB", "target_ticker": "EFSI",
                "target_name": "Eagle Financial Services", "buyer_ticker": "JMSB",
                "buyer_name": "John Marshall Bancorp", "announce_date": "2026-09-08",
                "expected_close_date": "2027-03-31", "consideration": "stock",
                "exchange_ratio": 2.0, "cash_per_share": None, "milestones": [],
                "series": series, "dropped": 0}}})
        try:
            at = self._open_transactions()
        except ModuleNotFoundError as e:
            self.skipTest(f"AppTest unavailable: {e}")
        html = "\n".join(str(m.value) for m in at.markdown)
        self.assertIn("Spread tracker", html)
        self.assertEqual(len(at.get("plotly_chart")), 2)   # overlay + drill-down
        picker = next(m for m in at.multiselect if m.key == "spread_deals")
        self.assertEqual(list(picker.value), ["JMSB:EFSI:2026-09-08"])


if __name__ == "__main__":
    unittest.main()
