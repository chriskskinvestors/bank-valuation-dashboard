"""Prose "basic and diluted EPS … $A and $B" pairs (2026-10-13).

NEWT's 2Q26 release (0001628280-26-054302) is page images with a text
overlay — no statement table — and states EPS only as unpaired-by-
"respectively" pairs: "Reports 2Q26 Basic and Diluted EPS of $0.48 and
$0.47". The pair reader demanded "respectively", so diluted EPS was n/a
(before #402: the guidance low end, $0.42). The stated order maps it.

The same reader, WITH "respectively", mapped PERIOD pairs as basic/diluted:
STLE 1Q26 (0001437749-26-012777) "Basic and diluted earnings per share for
the quarters ended March 31, 2026 and 2025 were $1.43 and $0.97,
respectively" shipped 0.97 — last year's EPS — as the quarter's diluted EPS
(also STLE 4Q25 0.23 / 3Q25 0.74, ENBP 4Q25 0.66). Two periods named in the
label make it a period pair → n/a.

Population (1,239 cached 2.02 exhibits, 347 SEC banks): the change moves
exactly NEWT 2Q26 None → 0.47 and those four wrong values → n/a; every
"respectively" basic/diluted pair (FXNC, HFBL, ISTR, MGYR, TCBX) is
unchanged.

Fixtures: NEWT's real file; STLE/ENBP are their real first 3,000 chars of
text (same verdict as the full document).

Run: python -m unittest tests.test_release_eps_pairs
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.release_metrics import extract_release_metrics  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "release_notices"


def _real(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8", "replace")


def _eps(html: str, qend: str):
    return extract_release_metrics(html, qend)["eps_diluted"]


class TestUnqualifiedPair(unittest.TestCase):
    def test_newt_reads_reported_diluted(self):
        self.assertEqual(_eps(_real("NEWT_2026-08-06_ex99_1.htm"),
                              "2026-06-30"), 0.47)

    def test_stated_order_both_ways(self):
        self.assertEqual(_eps("<p>Diluted and basic EPS of $0.47 and $0.48 "
                              "for the quarter.</p>", None), 0.47)

    def test_period_list_after_pair_refused(self):
        # "$0.48 and $0.43 for 2Q26 and 1Q26" is two quarters, not a pair.
        self.assertIsNone(_eps("<p>Basic and diluted EPS of $0.48 and $0.43 "
                               "for 2Q26 and 1Q26.</p>", None))

    def test_diluted_above_basic_refused(self):
        # Dilution never raises EPS — the order can't be basic/diluted.
        self.assertIsNone(_eps("<p>Basic and diluted EPS of $1.00 and $1.02 "
                               "for the quarter.</p>", None))


class TestPeriodPairNeverBasicDiluted(unittest.TestCase):
    def test_stle_prior_year_not_diluted(self):
        self.assertIsNone(_eps(_real("STLE_2026-04-20_ex99_excerpt.htm"),
                               "2026-03-31"))      # was 0.97 (1Q25's EPS)

    def test_enbp_prior_year_not_diluted(self):
        self.assertIsNone(_eps(_real("ENBP_2026-01-22_ex99_excerpt.htm"),
                               "2025-12-31"))      # was 0.66 (4Q24's EPS)

    def test_respectively_pair_still_maps(self):
        # TCBX 2Q26 — the case the pair reader was built for.
        self.assertEqual(_eps(
            "<p>Basic and diluted earnings per common share were $1.25 per "
            "share and $1.08 per share, respectively, in the second quarter "
            "of 2026, compared to $1.03 per share and $0.88 per share, "
            "respectively.</p>", None), 1.08)


if __name__ == "__main__":
    unittest.main()
