"""Bank-picker URL-state regression tests (2026-06-14 "can't change banks").

The bug: app.py forced the picker to the URL's ?bank= value on EVERY rerun, so
a fresh selection was reverted before it could take effect — the dropdown froze
on the deep-linked bank. The fix routes that decision through
ui.company_nav.resolve_url_bank, which lets the URL win only on external
navigation (URL names a different bank than the one we last applied).

These tests model one app.py rerun as a pure function using the REAL helper
plus the same bookkeeping app.py performs (record the applied bank in the early
seed AND after the widget syncs the URL). A sequence of reruns then proves the
picker is no longer clobbered. app.py itself executes Streamlit at import time
and can't be imported in unit tests, so the rerun loop is reproduced here; the
load-bearing decision (resolve_url_bank) is the actual shipped code.
"""
import unittest

from ui.company_nav import resolve_url_bank


def _rerun(url_bank, applied, widget_pick):
    """Model one app.py Company-section rerun.

    Inputs mirror the live state at the TOP of a rerun:
      url_bank    – ?bank= currently in the address bar
      applied     – st.session_state['_applied_url_bank'] from the prior rerun
      widget_pick – st.session_state['company_pick'] the selectbox carries in
                    (the user's latest choice on a widget-driven rerun, else
                    whatever was last shown)

    Returns the post-rerun (shown_pick, url_bank, applied), matching app.py:
    the early-seed guard, then the URL<-widget sync that records the applied
    bank once a ticker is selected.
    """
    # ── early seed (app.py lines ~154-163) ──
    force = resolve_url_bank(url_bank, applied)
    pick = force if force else widget_pick
    if force:
        applied = force
    # ── picker renders with `pick`; selection read back ──
    company_ticker = (pick or "").strip().upper() or None
    # ── URL <- widget sync + bookkeeping (app.py ~837-845) ──
    if company_ticker:
        url_bank = company_ticker
        applied = company_ticker
    return company_ticker, url_bank, applied


class TestBankPickerNotClobbered(unittest.TestCase):
    def test_fresh_deeplink_seeds_picker(self):
        # Land via ?bank=ABCB with no prior state — picker should adopt ABCB.
        pick, url, applied = _rerun("ABCB", None, None)
        self.assertEqual(pick, "ABCB")
        self.assertEqual(url, "ABCB")
        self.assertEqual(applied, "ABCB")

    def test_user_switch_sticks(self):
        # Steady state on ABCB, then the user picks BANR. The new pick must
        # survive the rerun (this is the exact bug: it used to revert to ABCB).
        _, url, applied = _rerun("ABCB", None, None)          # settle on ABCB
        # Widget-driven rerun: company_pick is BANR, URL still says ABCB.
        pick, url, applied = _rerun(url, applied, "BANR")
        self.assertEqual(pick, "BANR")
        self.assertEqual(url, "BANR")

    def test_three_way_switch_chain(self):
        # ABCB -> BANR -> JPM, each via the dropdown, none reverting.
        _, url, applied = _rerun("ABCB", None, None)
        pick, url, applied = _rerun(url, applied, "BANR")
        self.assertEqual(pick, "BANR")
        pick, url, applied = _rerun(url, applied, "JPM")
        self.assertEqual(pick, "JPM")
        self.assertEqual(url, "JPM")
        # A subsequent no-change rerun keeps JPM (does not snap back).
        pick, url, applied = _rerun(url, applied, "JPM")
        self.assertEqual(pick, "JPM")

    def test_external_deeplink_overrides_current(self):
        # On BANR, then the user clicks a deep-link to ?bank=JPM (external nav:
        # URL changes to a bank != applied). The URL must win here.
        _, url, applied = _rerun("ABCB", None, None)
        pick, url, applied = _rerun(url, applied, "BANR")     # now on BANR
        self.assertEqual(pick, "BANR")
        # Deep-link: address bar becomes JPM while applied is still BANR.
        pick, url, applied = _rerun("JPM", applied, "BANR")
        self.assertEqual(pick, "JPM")
        self.assertEqual(url, "JPM")

    def test_no_bank_in_url_leaves_picker_alone(self):
        # No ?bank= and nothing picked yet → no selection forced.
        pick, url, applied = _rerun(None, None, None)
        self.assertIsNone(pick)

    def test_resolve_helper_units(self):
        self.assertEqual(resolve_url_bank("ABCB", None), "ABCB")   # fresh
        self.assertEqual(resolve_url_bank("JPM", "BANR"), "JPM")   # external nav
        self.assertIsNone(resolve_url_bank("ABCB", "ABCB"))        # stale rerun
        self.assertIsNone(resolve_url_bank(None, "ABCB"))          # no URL bank


if __name__ == "__main__":
    unittest.main()
