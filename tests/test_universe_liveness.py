"""
Curated-entry liveness gate (data/bank_universe.curated_entry_is_dead).

The FFWM incident (owner-reported 2026-08-25): First Foundation merged into
FirstSun on 2026-04-01 — FDIC cert 58647 inactive, ticker gone from SEC's
file — yet the curated BANK_MAP entry re-admitted FFWM into the universe on
every nightly rebuild, and get_universe_tickers' _resolves() short-circuits
on a curated CIK without ever checking the cert. The dead bank then rendered
FMP's March 31 last print as a live price for five months. FFIC (merged into
OCFC, June) and NFBK (into CLBK, July) rode the same holes.

The gate drops a curated entry only when BOTH primary sources agree the bank
is gone. Pins:
  • FFWM/FFIC facts: inactive cert + SEC-absent -> dead
  • inactive cert but still SEC-listed -> ALIVE (intra-holdco charter
    consolidation, the WTFC multi-charter class — and GBNY, which is why it
    needs its _SKIP_TICKERS entry rather than this gate)
  • active cert but SEC-absent -> ALIVE (deregistered-but-trading OTC banks,
    the HCBC class — 60 admitted the same day this gate was written; also
    NFBK during the FDIC lag window, whose price the price-store retirement
    handles instead)
  • cert-less entry -> never killed here (cannot be liveness-checked)
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from data.bank_universe import curated_entry_is_dead  # noqa: E402

ACTIVE = {100, 29783, 28710}          # active certs incl. HCBC + NFBK's
SEC = {"LIVE", "WTFC", "GBNY"}        # tickers SEC's file still lists


class TestCuratedLivenessGate(unittest.TestCase):

    def test_both_sources_dead_drops(self):
        """The FFWM/FFIC shape: cert inactive AND SEC-absent."""
        self.assertTrue(curated_entry_is_dead("FFWM", 58647, ACTIVE, SEC))
        self.assertTrue(curated_entry_is_dead("FFIC", 58564, ACTIVE, SEC))

    def test_inactive_cert_but_sec_listed_stays(self):
        """Charter consolidation / lingering SEC listing — one source only."""
        self.assertFalse(curated_entry_is_dead("WTFC", 999, ACTIVE, SEC))
        self.assertFalse(curated_entry_is_dead("GBNY", 998, ACTIVE, SEC))

    def test_active_cert_but_sec_absent_stays(self):
        """The HCBC class: deregistered from SEC, alive at FDIC."""
        self.assertFalse(curated_entry_is_dead("HCBC", 29783, ACTIVE, SEC))
        self.assertFalse(curated_entry_is_dead("NFBK", 28710, ACTIVE, SEC))

    def test_certless_entry_never_killed(self):
        self.assertFalse(curated_entry_is_dead("XXXX", None, ACTIVE, SEC))
        self.assertFalse(curated_entry_is_dead("XXXX", 0, ACTIVE, SEC))

    def test_case_insensitive_ticker(self):
        self.assertTrue(curated_entry_is_dead("ffwm", 58647, ACTIVE, SEC))
        self.assertFalse(curated_entry_is_dead("live", 997, ACTIVE, SEC))


if __name__ == "__main__":
    unittest.main()
