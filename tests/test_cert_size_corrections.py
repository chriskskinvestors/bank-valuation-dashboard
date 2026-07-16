"""
Pins the 2026-07-16 size-sweep wrong-entity cert corrections.

The NAMEHCR ground-truth guard is structurally blind to a same-name holding
company in a DIFFERENT state: cert 4690's high holder is literally
"HOPE BANCSHARES INC", so the name tell AFFIRMED joining Hope Bancorp ($18.7B,
California) to an $86M bank in Kansas. The duplicate-cert tell misses it too —
only one ticker ever claims the wrong cert. The tell that DOES catch it is
SIZE: a single-bank holding company's sub carries ~all of its assets, so a
holdco many times larger than its joined sub is a wrong join.

Each cert below was hand-verified on three independent keys — the SEC
registrant's own state, sub-bank assets within a few percent of holdco assets,
and (where the high holder is legible) a matching NAMEHCR. Evidence is in each
comment as "<correct bank> (<state>) <sub $> vs holdco $ — was <wrong bank>".

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
    # Bank of Hope (CA) $18.65B vs holdco $18.66B — was First National Bank of Hope, KANSAS $86M
    "HOPE": (26610, 4690),
    # Mechanics Bank (CA) $21.40B vs holdco $21.39B — was Mechanics Bank, OHIO $885M
    "MCHB": (1768, 29011),
    # First National Bank Ames (IA) $1.09B, LARGEST of 6 subs — was the SMALLEST, $120M
    "ATLO": (1545, 57391),
    # The Peoples Bank, Biloxi (MS) $0.79B vs holdco $0.79B — was Peoples State Bank of Colfax, ILLINOIS $47M
    "PFBX": (340, 10075),
    # PCB Bank (CA) $3.40B vs holdco $3.40B — was Monterey County Bank $298M
    "PCB": (57463, 22460),
    # Embassy Bank for the Lehigh Valley (PA) $1.84B vs holdco $1.84B — was Embassy National Bank, GEORGIA $233M
    "EMYB": (57228, 58413),
    # Amalgamated Bank (NY) $9.17B vs holdco $9.17B — was Amalgamated Bank of Chicago, ILLINOIS $1.28B
    "AMAL": (622, 903),
    # Union Bank (VT) $1.62B vs holdco $1.63B — was The Bank of Monroe, WEST VIRGINIA $251M
    "UNB": (14158, 6180),
    # Pioneer Bank NA (NY) $2.21B vs holdco $2.22B — was Pioneer Bank, VIRGINIA $363M
    "PBFS": (20741, 6913),
    # Carver Federal Savings Bank (NY) $0.68B vs holdco $0.70B — was Carver State Bank, GEORGIA $115M
    "CARV": (30394, 16584),
    # Opportunity Bank of Montana (MT) $2.09B vs holdco $2.09B — was Bank of Montana $349M
    "EBMT": (30182, 58482),
    # Central Pacific Bank (HI) $7.49B vs holdco $7.50B — was Bank of the Pacific, WASHINGTON $1.29B
    "CPF": (17308, 23041),
    # Quad City Bank & Trust (IA) $3.09B, LARGEST of 4 subs — was Community State Bank $1.74B
    "QCRH": (33867, 18272),
    # Horizon Bank, Michigan City (IN) $6.54B vs holdco $6.56B — was Horizon Bank, NEBRASKA $539M.
    # BANK_MAP override (cert 14327 carries NO high holder, so the name tell had nothing to contradict).
    "HBNC": (4360, 14327),
    # Blue Ridge Bank NA (VA) $2.40B vs holdco $2.41B — was Blue Ridge Bank, SOUTH CAROLINA $195M.
    # BANK_MAP was overriding the CORRECT value already in bank_map_resolved.json.
    "BRBS": (35274, 17773),
    # Commerce Bank, Kansas City (MO) $35.54B — was The Bank of Commerce, White Castle LOUISIANA $88M.
    # Fixed in 5c92310; pinned here so the whole class regresses together.
    "CBSH": (24998, 1374),
}


class TestCertSizeCorrections(unittest.TestCase):
    def test_each_ticker_resolves_to_the_verified_cert(self):
        for tk, (correct, _wrong) in CORRECTIONS.items():
            with self.subTest(ticker=tk):
                self.assertEqual(
                    get_fdic_cert(tk), correct,
                    f"{tk} must join FDIC cert {correct} (hand-verified on "
                    f"state + holdco-asset size + NAMEHCR)")

    def test_no_ticker_carries_its_old_wrong_cert(self):
        # The wrong certs are real banks — a regression would silently show
        # another institution's balance sheet, not crash.
        for tk, (_correct, wrong) in CORRECTIONS.items():
            with self.subTest(ticker=tk):
                self.assertNotEqual(
                    get_fdic_cert(tk), wrong,
                    f"{tk} regressed onto cert {wrong} — a DIFFERENT bank")

    def test_corrected_certs_are_unique(self):
        # Two tickers claiming one cert is the other wrong-entity tell; the
        # corrections must not introduce one.
        certs = [c for c, _ in CORRECTIONS.values()]
        dupes = {c for c in certs if certs.count(c) > 1}
        self.assertEqual(dupes, set(), f"duplicate certs introduced: {dupes}")


if __name__ == "__main__":
    unittest.main()
