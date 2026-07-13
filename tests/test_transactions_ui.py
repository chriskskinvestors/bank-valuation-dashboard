"""
Transactions section render guard (docs/SNL-BUILD-PLAN.md §14).

Runs app.py headlessly via AppTest, switches to the Transactions section,
and asserts the owner-decided structure renders: the lazy_tabs pill bar
with the built sub-tabs (Transactions Summary, Detailed M&A History,
Detailed Offerings, and the kept Insider Activity), and the shared bank picker in its no-selection
state (index=None — no network fan-out happens until a bank is picked, so
this test never touches FDIC/EDGAR).

Run: python -m unittest tests.test_transactions_ui
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTransactionsSection(unittest.TestCase):

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
        tab_bar = next((r for r in at.radio
                        if list(map(str, r.options)) ==
                        ["Transactions Summary", "Detailed M&A History",
                         "Detailed Offerings", "Insider Activity"]), None)
        self.assertIsNotNone(
            tab_bar,
            "Transactions lazy_tabs bar missing. Radios: "
            f"{[list(r.options) for r in at.radio]}")
        picker = next((sb for sb in at.selectbox if sb.key == "txn_bank"), None)
        self.assertIsNotNone(picker, "shared bank picker missing")
        self.assertIsNone(picker.value, "picker must default to no selection "
                          "(no network fan-out on first render)")
        # No-selection state renders the pick-a-bank prompt, not a table.
        self.assertTrue(any("Pick a bank" in str(i.value) for i in at.info),
                        [str(i.value) for i in at.info])
        # Default pane is Transactions Summary (first pill).
        self.assertEqual(str(tab_bar.value), "Transactions Summary")


if __name__ == "__main__":
    unittest.main()
