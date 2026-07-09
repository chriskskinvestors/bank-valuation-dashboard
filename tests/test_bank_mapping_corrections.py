"""
Pins the 2026-07-09 wrong-entity sweep (FDIC NAMEHCR ground-truth check of
every universe ticker's cert): 18 tickers were joined to ANOTHER bank's FDIC
cert, so their pages served that bank's financials. Duplicate-cert claims were
the tell — e.g. CFG, FCBM and FCNCA all claimed cert 11063 (First-Citizens'
bank); BNY claimed Bank of America's 3510; FNB claimed a $310M South Carolina
namesake instead of the $50B First National Bank of Pennsylvania.

Every (ticker -> cert) below was verified against the FDIC institution's own
NAMEHCR (regulatory high holder) matching the SEC registrant. BANK_MAP is
priority 1 in get_fdic_cert AND overwrites fuzzy matches in the nightly
universe rebuild, so pinning BANK_MAP pins both paths.

Offline — reads only the static mapping tables.
"""
import sys
import types
import unittest

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.bank_mapping import BANK_MAP  # noqa: E402


# ticker -> (verified cert, FDIC NAMEHCR that proved it)
VERIFIED = {
    "FNB":   (7888,  "F N B CORP"),                    # was 2107 (FNB of South Carolina)
    "FFIN":  (3066,  "FIRST FINANCIAL BANKSHARES INC"),  # was 19440 (Bessemer AL ESOP)
    "FMNB":  (6540,  "FARMERS NATIONAL BANC CORP"),    # was 3732 (Prophetstown IL)
    "CFG":   (57957, "CITIZENS FINANCIAL GROUP INC"),  # was 11063 (First-Citizens)
    "BNY":   (639,   "BANK OF NY MELLON CORP THE"),    # was 3510 (Bank of America NA)
    "MBIN":  (8056,  "MERCHANTS BCORP"),               # was 4365 (First Merchants)
    "FMCB":  (1331,  "FARMERS&MERCHANTS BCORP"),       # was 6540 (Canfield OH)
    "FMFG":  (1895,  "FARMERS&MERCHANTS BANCSHARES"),  # was 6540 (Canfield OH)
    "CTBI":  (2720,  "COMMUNITY TRUST BCORP INC"),     # was 6989 (Community Bank NA)
    "FCBM":  (35530, "FIRST CAROLINA FINL SERVICES"),  # was 11063 (First-Citizens)
    "HBCP":  (28094, "HOME BCORP INC"),                # was 11241 (Centennial)
    "HFBL":  (27654, "HOME FEDERAL BCORP INC OF LA"),  # was 11241 (Centennial)
    "UBCP":  (9463,  "UNITED BCORP INC"),              # was 22858 (United Bank/UBSI)
    "UBOH":  (12969, "UNITED BANCSHARES INC"),         # was 22858 (United Bank/UBSI)
    "BCAL":  (57044, "CALIFORNIA BCORP"),              # was 24045 (Banc of California)
    "FCBC":  (13012, "FIRST COMMUNITY BANKSHARES INC"),  # was 3850 (Xenia IL)
    "INBC":  (11492, "INBANKSHARES CORP"),             # was 9712 (Rockland/INDB)
    "FMBN":  (13737, "FARMERS&MERCHANTS BANCSHARES"),  # Burlington IA; was 1895 = the
                                                       # MARYLAND same-name co (= FMFG)
}


class TestVerifiedCerts(unittest.TestCase):
    def test_corrected_certs_pinned(self):
        for t, (cert, hcr) in VERIFIED.items():
            with self.subTest(ticker=t, expected_hcr=hcr):
                self.assertEqual(BANK_MAP[t]["fdic_cert"], cert)

    def test_inbc_is_inbankshares_not_rockland(self):
        # Ticker INBC = InBankshares, Corp. (InBank, NM). Rockland Trust's
        # parent trades as INDB; the old entry duplicated it under INBC.
        self.assertIn("InBankshares", BANK_MAP["INBC"]["name"])
        self.assertIsNone(BANK_MAP["INBC"]["cik"])  # OTCQX, not SEC-listed

    def test_fbsi_defunct_excluded(self):
        # FBSI's bank died 10/2017; the fuzzy join grabbed Centier's cert.
        # All-None BANK_MAP (gate skip) + _SKIP_TICKERS (rebuild-proof).
        self.assertIsNone(BANK_MAP["FBSI"]["fdic_cert"])
        self.assertIsNone(BANK_MAP["FBSI"]["cik"])
        from data.bank_universe import _SKIP_TICKERS
        self.assertIn("FBSI", _SKIP_TICKERS)

    def test_no_duplicate_certs_in_bank_map(self):
        # One cert = one holdco. A duplicate claim inside the curated map is
        # exactly how INBC shadowed INDB — fail fast on any future collision.
        seen = {}
        for t, info in BANK_MAP.items():
            cert = info.get("fdic_cert")
            if not cert:
                continue
            self.assertNotIn(cert, seen,
                             f"cert {cert} claimed by both {seen.get(cert)} and {t}")
            seen[cert] = t


if __name__ == "__main__":
    unittest.main()
