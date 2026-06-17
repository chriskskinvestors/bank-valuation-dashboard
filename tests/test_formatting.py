"""Unit tests for utils.formatting compact dollar formatter.

Pins the $K tier added 2026-06-14: a sub-$1M FDIC value ($thousands input)
must compact to $XK (e.g. $843K), matching the rest of a statement column's
B/M/K convention, instead of spelling out the full figure ($843,000).
"""
import unittest

from utils.formatting import usd_compact_from_thousands as usd
from utils.formatting import format_bank_name as fbn


class TestFormatBankName(unittest.TestCase):
    """Names reach the UI from sources with inconsistent casing (ALL-CAPS SEC
    EDGAR vs curated Title Case) and EDGAR /XX/ suffixes; format_bank_name
    normalizes them all to one Title-Case, suffix-dropped style."""

    def test_allcaps_sources_titlecased_suffix_dropped(self):
        self.assertEqual(fbn("PATRIOT NATIONAL BANCORP INC"),
                         "Patriot National Bancorp")
        self.assertEqual(fbn("CARVER BANCORP INC"), "Carver Bancorp")
        self.assertEqual(fbn("GREENE COUNTY BANCORP INC"), "Greene County Bancorp")
        self.assertEqual(fbn("HAWTHORN BANCSHARES, INC."), "Hawthorn Bancshares")

    def test_state_suffix_stripped(self):
        self.assertEqual(fbn("CONSUMERS BANCORP INC /OH/"), "Consumers Bancorp")
        self.assertEqual(fbn("Redwood Financial Inc /MN/"), "Redwood Financial")
        self.assertEqual(fbn("Citizens Holding Co /Ms/"), "Citizens Holding")

    def test_minor_words_lowercased(self):
        self.assertEqual(fbn("BANK OF SOUTH CAROLINA CORP"),
                         "Bank of South Carolina")

    def test_acronyms_and_ampersands_preserved(self):
        self.assertEqual(fbn("F&M BANK CORP"), "F&M Bank")
        self.assertEqual(fbn("C&F FINANCIAL CORP"), "C&F Financial")
        self.assertEqual(fbn("Generations Bancorp NY, Inc."),
                         "Generations Bancorp NY")

    def test_mixed_case_camelcase_preserved(self):
        # Already-good names keep their intentional casing; only suffix dropped.
        self.assertEqual(fbn("HomeTrust Bancshares"), "HomeTrust Bancshares")
        self.assertEqual(fbn("BancFirst Corporation"), "BancFirst")
        self.assertEqual(fbn("InsCorp, Inc."), "InsCorp")
        self.assertEqual(fbn("HBT Financial, Inc."), "HBT Financial")

    def test_and_co_and_national_association(self):
        self.assertEqual(fbn("JPMorgan Chase & Co."), "JPMorgan Chase")
        self.assertEqual(fbn("Wells Fargo & Co."), "Wells Fargo")
        self.assertEqual(fbn("Zions Bancorporation, National Association"),
                         "Zions Bancorporation")

    def test_ticker_acronym_kept_uppercase(self):
        # A name word that IS the ticker is the company's acronym → uppercase;
        # a real word whose ticker differs still title-cases. (ACNB keeps its
        # "Corp" via the collapse-guard — see the dedicated test below.)
        self.assertEqual(fbn("ACNB CORP", "ACNB"), "ACNB Corp")
        self.assertEqual(fbn("ESSA BANCORP INC", "ESSA"), "ESSA Bancorp")
        self.assertEqual(fbn("AMES NATIONAL CORP", "ATLO"), "Ames National")
        self.assertEqual(fbn("ARROW FINANCIAL CORP", "AROW"), "Arrow Financial")

    def test_edgar_registration_suffix_variants(self):
        # Fully-enclosed "/NEW/", trailing 2-letter "/MN", dangling slash —
        # but an INTERNAL slash (Cullen/Frost) must be preserved.
        self.assertEqual(fbn("KEYCORP /NEW/", "KEY"), "Keycorp")
        self.assertEqual(fbn("WELLS FARGO & COMPANY/MN", "WFC"), "Wells Fargo")
        self.assertEqual(fbn("TRICO BANCSHARES /", "TCBK"), "Trico Bancshares")
        self.assertEqual(fbn("CULLEN/FROST BANKERS, INC.", "CFR"),
                         "Cullen/Frost Bankers")
        # Hyphenated "Banc-Corp" is part of the name, not a droppable suffix.
        self.assertEqual(fbn("ASSOCIATED BANC-CORP", "ASB"),
                         "Associated Banc-Corp")

    def test_suffix_drop_never_collapses_to_bare_ticker(self):
        # When the name IS the ticker acronym + a corporate form, keep the form
        # so the picker shows a real name instead of a bare ticker.
        self.assertEqual(fbn("ACNB CORP", "ACNB"), "ACNB Corp")
        self.assertEqual(fbn("FNB CORP/PA/", "FNB"), "FNB Corp")
        self.assertEqual(fbn("LCNB CORP", "LCNB"), "LCNB Corp")
        self.assertEqual(fbn("FNB Corp.", "FNB"), "FNB Corp")
        # But a descriptive name still drops the suffix normally.
        self.assertEqual(fbn("DELHI BANK CORP", "DWNX"), "Delhi Bank")

    def test_idempotent(self):
        for raw in ("PATRIOT NATIONAL BANCORP INC", "JPMorgan Chase & Co.",
                    "CONSUMERS BANCORP INC /OH/", "HomeTrust Bancshares"):
            self.assertEqual(fbn(fbn(raw)), fbn(raw))

    def test_empty_and_none(self):
        self.assertEqual(fbn(""), "")
        self.assertEqual(fbn(None), "")


class TestUsdCompactFromThousands(unittest.TestCase):
    def test_billions(self):
        self.assertEqual(usd(16_338_071), "$16.34B")   # FDIC $000 -> $16.34B

    def test_millions(self):
        self.assertEqual(usd(160_276), "$160.3M")

    def test_thousands_tier(self):
        # 843 ($000) = $843,000 -> $843K (the bug: used to render $843,000).
        self.assertEqual(usd(843), "$843K")
        self.assertEqual(usd(12_000), "$12.0M")        # 12,000 ($000) = $12M
        self.assertEqual(usd(999), "$999K")

    def test_sub_thousand_dollars_spelled_out(self):
        # < $1K: exact amount (no K). 0.5 ($000) = $500.
        self.assertEqual(usd(0.5), "$500")
        self.assertEqual(usd(0), "$0")

    def test_boundaries(self):
        self.assertEqual(usd(1_000), "$1.0M")          # exactly $1M -> M tier
        self.assertEqual(usd(1_000_000), "$1.00B")     # exactly $1B -> B tier
        self.assertEqual(usd(1), "$1K")                # 1 ($000) = $1,000 -> $1K

    def test_negative(self):
        self.assertEqual(usd(-843), "$-843K")

    def test_none_and_unparseable(self):
        self.assertEqual(usd(None), "—")
        self.assertEqual(usd("not a number"), "—")


if __name__ == "__main__":
    unittest.main()
