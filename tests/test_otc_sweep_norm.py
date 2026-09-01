"""
Name canonicalization for the OTC bank sweep (tools/sweep_otc_banks).

The sweep admits non-SEC-filer OTC banks by joining FDIC institutions against
FMP's symbol list on EXACT normalized legal name — exact because the hazard it
guards against is the WRONG-TICKER JOIN (a bank priced with another company's
quote). FDIC abbreviates holding-company names in NAMEHCR while vendors spell
them out, so that compare could never match ~24% of them:

    FDIC NAMEHCR : "HIGH COUNTRY BCORP INC"
    FMP name     : "High Country Bancorp, Inc."

HCBC (High Country Bancorp, FDIC cert 29783) was the reported miss. Measured
over the 3,556 active FDIC institutions carrying a NAMEHCR (2026-08-25):
BCORP 870 vs BANCORP 11, FINL 83, BK 35, NATL 32, TR 30, CMTY 27.

Pins:
  • each abbreviation canonicalizes to the word it stands for, so the FDIC and
    vendor spellings compare equal
  • BANC is NOT folded into BANK — "Banc of California" is a real name, and
    equating it with "Bank of California" is exactly the wrong-ticker join
  • expansion does not collapse genuinely different institutions
  • suffix stripping and leading-THE removal still apply
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

from tools.sweep_otc_banks import _norm  # noqa: E402


class TestAbbreviationExpansion(unittest.TestCase):

    def test_fdic_and_vendor_spellings_compare_equal(self):
        for fdic, vendor in [
            ("HIGH COUNTRY BCORP INC", "High Country Bancorp, Inc."),
            ("ABC FINL CORP", "ABC Financial Corporation"),
            ("XYZ BK & TR CO", "XYZ Bank & Trust Co."),
            ("FIRST NATL BSHRS", "First National Bancshares Inc"),
            ("CMTY BCORP", "Community Bancorp"),
        ]:
            with self.subTest(fdic=fdic):
                self.assertEqual(_norm(fdic), _norm(vendor))

    def test_the_reported_miss(self):
        """HCBC / FDIC cert 29783 — the bank that surfaced this."""
        self.assertEqual(_norm("HIGH COUNTRY BCORP INC"), "HIGH COUNTRY BANCORP")

    def test_triage_additions_2026_08_31(self):
        """The four gaps the 36-bank triage exposed."""
        cases = [
            # ampersand tokenization (FDIC writes it unspaced)
            ("MERCHANTS&MARINE BCORP INC", "Merchants & Marine Bancorp, Inc."),
            ("SVB&T CORP", "SVB & T Corporation"),
            # BANCORPORATION spelled out
            ("DENALI BCORP INC", "Denali Bancorporation, Inc."),
            ("BARABOO BCORP INC THE", "The Baraboo Bancorporation, Inc."),
            # OF as connective
            ("1ST SUMMIT BCORP JOHNSTOWN INC",
             "1ST SUMMIT BANCORP of Johnstown, Inc."),
            # trailing MHC structural suffix
            ("AUBURN BCORP MHC", "Auburn Bancorp, Inc."),
            ("MUTUAL FEDERAL BCORP MHC", "Mutual Federal Bancorp, Inc."),
        ]
        for fdic, vendor in cases:
            with self.subTest(fdic=fdic):
                self.assertEqual(_norm(fdic), _norm(vendor))

    def test_triage_guards_2026_08_31(self):
        """What the new canonicalizations must NOT equate."""
        # & is kept as an identity-bearing token
        self.assertNotEqual(_norm("M&T BANK"), _norm("MT BANK"))
        # OF-drop preserves word order
        self.assertNotEqual(_norm("BANK OF AMERICA"), _norm("AMERICA BANK"))
        # MHC strips only trailing — a mid-name MHC-ish token survives
        self.assertNotEqual(_norm("TEB MHC"), _norm("TEB BANCORP"))
        # Signature Bank (failed NY) vs Signature Bancorp (Ohio) stay distinct
        self.assertNotEqual(_norm("SIGNATURE BANK"), _norm("SIGNATURE BCORP INC"))


class TestSafetyGuards(unittest.TestCase):

    def test_banc_is_not_folded_into_bank(self):
        """The one expansion deliberately omitted: 'Banc' is a real spelling,
        so Banc of California must NOT match Bank of California."""
        self.assertNotEqual(_norm("BANC OF CALIFORNIA"),
                            _norm("BANK OF CALIFORNIA"))

    def test_different_institutions_still_differ(self):
        for a, b in [
            ("FIRST NATIONAL BCORP", "SECOND NATIONAL BCORP"),
            ("PEOPLES BCORP", "PEOPLES BANCSHARES"),
            ("VALLEY BK", "VALLEY TRUST"),
        ]:
            with self.subTest(a=a):
                self.assertNotEqual(_norm(a), _norm(b))

    def test_distinctive_words_are_never_dropped(self):
        """Only corporate-form suffixes come off; Bancorp/Financial/Holdings
        stay, or different institutions would collapse onto each other."""
        self.assertEqual(_norm("ACME BANCORP INC"), "ACME BANCORP")
        self.assertNotEqual(_norm("ACME BANCORP"), _norm("ACME"))

    def test_suffix_and_leading_the_still_stripped(self):
        self.assertEqual(_norm("The Acme Bank Company"), "ACME BANK")


class TestSameNameHoldcoAmbiguity(unittest.TestCase):
    """Expanding abbreviations makes MORE names match, which surfaced a
    pre-existing hazard: several UNRELATED banks can share one common name.
    "Citizens Bancorp" is six distinct holding companies (LA/OR/MO/TX/WI/IL),
    all normalizing to one string. Keeping the largest-asset cert — the old
    behaviour — would price CZBC off a coin-flip bank and be wrong for the
    other five. RSSDHCR (the holding company's RSSD id) is the discriminator:
    same id = one holdco with several charters (keep largest); different ids =
    ambiguous, review only.
    """

    @staticmethod
    def _dedup(rows):
        """The sweep's de-dup stage, isolated (it is inline in sweep())."""
        groups = {}
        for row in rows:
            groups.setdefault(row["ticker"], []).append(row)
        auto, review = {}, []
        for t, rows_t in groups.items():
            ids = {(r.get("rssdhcr") or f"?cert{r['cert']}") for r in rows_t}
            if len(ids) > 1:
                review.extend(rows_t)
                continue
            keep = max(rows_t, key=lambda r: (r.get("assets_k") or 0))
            review.extend(r for r in rows_t if r is not keep)
            auto[t] = keep
        return auto, review

    def _row(self, cert, rssd, assets, state="XX"):
        return {"ticker": "CZBC", "cert": cert, "rssdhcr": rssd,
                "assets_k": assets, "state": state}

    def test_unrelated_holdcos_are_never_auto_admitted(self):
        rows = [self._row(1376, "1084472", 471372, "LA"),
                self._row(17809, "2519038", 810448, "OR"),
                self._row(33753, "2550143", 538565, "IL")]
        auto, review = self._dedup(rows)
        self.assertEqual(auto, {})              # NONE admitted
        self.assertEqual(len(review), 3)

    def test_one_holdco_many_charters_keeps_largest(self):
        rows = [self._row(100, "999", 50_000),
                self._row(101, "999", 900_000),
                self._row(102, "999", 10_000)]
        auto, review = self._dedup(rows)
        self.assertEqual(auto["CZBC"]["cert"], 101)   # largest asset
        self.assertEqual(len(review), 2)

    def test_missing_rssdhcr_is_not_treated_as_same_holdco(self):
        """Unknown must never be assumed equal — that would silently admit a
        wrong join whenever FDIC omits the field."""
        rows = [self._row(200, None, 10_000), self._row(201, None, 20_000)]
        auto, _ = self._dedup(rows)
        self.assertEqual(auto, {})

    def test_single_match_still_admits(self):
        auto, review = self._dedup([self._row(300, "555", 12_345)])
        self.assertEqual(auto["CZBC"]["cert"], 300)
        self.assertEqual(review, [])


if __name__ == "__main__":
    unittest.main()
