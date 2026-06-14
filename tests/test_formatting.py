"""Unit tests for utils.formatting compact dollar formatter.

Pins the $K tier added 2026-06-14: a sub-$1M FDIC value ($thousands input)
must compact to $XK (e.g. $843K), matching the rest of a statement column's
B/M/K convention, instead of spelling out the full figure ($843,000).
"""
import unittest

from utils.formatting import usd_compact_from_thousands as usd


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
