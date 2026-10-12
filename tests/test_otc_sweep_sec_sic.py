"""SEC-listed tickers with no SIC are the sweep's to admit (tools/sweep_otc_banks).

SEC's ticker file also lists banks that report to their bank regulator, not
the SEC — Preferred Bank (PFBC) and Endeavor Bancorp (EDVR) have a CIK only
from others' 13G / Form D filings, and no SIC. The universe's SEC path drops
them at its SIC gate, and the sweep skipped every SEC-listed ticker as "SEC
path territory", so both banks fell between the two paths (2026-10-12).
Pins: blank SIC -> auto (still behind the price + state gates); a SIC on file
or an unreadable SEC record -> review, never auto. All seams are stubbed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from tools import sweep_otc_banks as sw  # noqa: E402


def _bank(cert, name, namehcr, state):
    return {"CERT": cert, "NAME": name, "NAMEHCR": namehcr, "STALP": state,
            "ASSET": 800_000, "BKCLASS": "SM", "RSSDHCR": str(cert)}


FDIC = [_bank(59099, "Endeavor Bank", "ENDEAVOR BCORP", "CA"),
        _bank(1001, "Acme Bank", "ACME BCORP", "TX"),
        _bank(1002, "Murky Bank", "MURKY BCORP", "OH")]
SYMBOLS = [{"symbol": "EDVR", "companyName": "Endeavor Bancorp"},
           {"symbol": "ACME", "companyName": "Acme Bancorp, Inc."},
           {"symbol": "MRKY", "companyName": "Murky Bancorp"}]
SEC = {"EDVR": 1835000, "ACME": 2000, "MRKY": 3000}
SIC = {1835000: "", 2000: "6022", 3000: None}   # None = unreadable record
STATE = {"EDVR": "CA", "ACME": "TX", "MRKY": "OH"}


class TestSecListedWithoutSic(unittest.TestCase):

    def test_blank_sic_admitted_sic_or_unreadable_reviewed(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(sw, "_OUT_DIR", Path(d)), \
             patch.object(sw, "_fdic_active", lambda: FDIC), \
             patch.object(sw, "_covered_certs_and_tickers", lambda: (set(), set())), \
             patch.object(sw, "_sec_tickers", lambda: SEC), \
             patch.object(sw, "_sec_sic", lambda cik: SIC[cik]), \
             patch.object(sw, "_fmp_symbols", lambda: SYMBOLS), \
             patch.object(sw, "_fmp_profile_state", lambda t: STATE[t]), \
             patch("data.fmp_client.get_eod_close_batch",
                   lambda ts: {t: {"price": 19.75} for t in ts}):
            out = sw.sweep(apply=False)
        self.assertEqual([(r["ticker"], r["cert"]) for r in out["auto"]],
                         [("EDVR", 59099)])
        why = {r["symbols"]: r["why"] for r in out["review"]}
        self.assertEqual(why, {
            "ACME": "SEC-listed ticker (SEC path territory)",
            "MRKY": "SEC-listed ticker; SEC record unreadable"})


if __name__ == "__main__":
    unittest.main()
