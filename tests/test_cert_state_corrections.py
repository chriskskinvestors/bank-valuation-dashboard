"""
Pins the 2026-07-16 STATE-key wrong-entity cert corrections.

The size sweep (tests/test_cert_size_corrections.py) caught every wrong join
where the holdco dwarfed its claimed sub. This is the class it could not see:
same-name twins of SIMILAR size. FDBC's registrant ($2.86B, Dunmore PA) was
joined to Fidelity Bank NA of Wichita KANSAS ($3.35B) — the wrong bank is
BIGGER, so the size ratio is 1.17x and looks perfect. LSBK's was 1.07x.

What separates them is the second identity key: the FDIC record's regulatory
high holder state (STALPHCR) must agree with the SEC registrant's own state.
Each cert below was hand-verified on three independent keys — registrant state
vs holdco state, sub assets vs holdco assets, and a NAMEHCR that names the
registrant. Evidence is in each comment.

Offline: reads the mapping chain only, no network.
"""
import sys
import types
import unittest

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.bank_mapping import get_fdic_cert  # noqa: E402

# ticker -> (correct cert, the wrong cert it used to carry)
CORRECTIONS = {
    # The Fidelity Deposit and Discount Bank (PA) $2.86B = holdco $2.86B, hcr
    # FIDELITY D&D BCORP INC — was Fidelity Bank NA, Wichita KANSAS $3.35B
    # (hcr FIDELITY FINANCIAL CORP). Size ratio 1.17x: size-invisible.
    "FDBC": (11868, 30895),
    # Lake Shore Bank (NY) $0.72B = holdco $0.72B, hcr LAKE SHORE BCORP INC —
    # was Hiawatha National Bank, WISCONSIN $0.77B, whose holdco is
    # coincidentally named LAKE SHORE III CORP. Size ratio 1.07x.
    "LSBK": (30530, 13058),
    # Central Penn Bank & Trust, Mifflinburg (PA) $1.26B vs holdco $1.27B, hcr
    # STEELE BCORP INC — was American State Bank, Arp TEXAS $0.65B, hcr
    # STEELE BANCSHARES INC. The name tell canonicalizes BCORP and BANCSHARES
    # to the same token, so it affirmed the Texas twin.
    "STLE": (10685, 9967),
    # The Commercial and Savings Bank of Millersburg (OH) $1.27B = holdco
    # $1.27B, hcr CSB BCORP INC — was Community State Bank, Galva ILLINOIS
    # $0.40B (hcr CSB FINANCIAL HOLDINGS INC).
    "CSBB": (9139, 23257),
    # New Peoples Bank, Honaker (VA) $0.94B = holdco $0.94B, hcr NEW PEOPLES
    # BANKSHARES INC — was The Peoples Bank, Eatonton GEORGIA $0.27B.
    "NWPP": (34890, 16152),
    # Home Federal Savings and Loan, Grand Island (NE) $0.56B = holdco $0.56B,
    # hcr CENTRAL PLAINS BANCSHARES INC — was Bank of the Plains, KANSAS
    # $0.45B (hcr PLAINS BANCSHARES INC).
    "CPBI": (29476, 18118),
    # First US Bank, Birmingham (AL) $1.17B = holdco $1.17B, hcr FIRST US
    # BANCSHARES INC — was The Farmers State Bank of Oakley, KANSAS $0.39B
    # (hcr SECURITY BANCSHARES INC), matched off the registrant's FORMER name
    # ("United Security Bancshares", also the name of an unrelated CA bank);
    # the entry's stale name is corrected alongside the cert.
    "FUSB": (17077, 1867),
    # Bank of the Pacific, Aberdeen (WA) $1.29B, hcr PACIFIC FINANCIAL CORP —
    # was CENTRAL Pacific Bank, Honolulu $7.49B (CPF's sub). The §12(i) sweep
    # matched holding-company name alone and "PACIFIC FINANCIAL CORP" is a
    # substring of "CENTRAL PACIFIC FINANCIAL CORP". Until 53aab80 moved CPF
    # the two were exactly SWAPPED, so not even the dup-cert tell fired.
    # BANK_MAP entry, cik=None (FDIC-only §12(i) filer).
    "PFLC": (23041, 17308),
}


class TestCertStateCorrections(unittest.TestCase):
    def test_each_ticker_resolves_to_the_verified_cert(self):
        for tk, (correct, _wrong) in CORRECTIONS.items():
            with self.subTest(ticker=tk):
                self.assertEqual(
                    get_fdic_cert(tk), correct,
                    f"{tk} must join FDIC cert {correct} (hand-verified on "
                    f"registrant state + holdco state + assets + NAMEHCR)")

    def test_no_ticker_carries_its_old_wrong_cert(self):
        # Every wrong cert here is a real, operating bank — a regression shows
        # another institution's balance sheet rather than failing loudly.
        for tk, (_correct, wrong) in CORRECTIONS.items():
            with self.subTest(ticker=tk):
                self.assertNotEqual(
                    get_fdic_cert(tk), wrong,
                    f"{tk} regressed onto cert {wrong} — a DIFFERENT bank")

    def test_cpf_keeps_its_own_cert(self):
        # PFLC's correction must not be made by taking CPF's: 17308 is Central
        # Pacific Bank and belongs to CPF alone.
        self.assertEqual(get_fdic_cert("CPF"), 17308)

    def test_corrected_certs_are_unique(self):
        certs = [c for c, _ in CORRECTIONS.values()]
        dupes = {c for c in certs if certs.count(c) > 1}
        self.assertEqual(dupes, set(), f"duplicate certs introduced: {dupes}")


if __name__ == "__main__":
    unittest.main()
