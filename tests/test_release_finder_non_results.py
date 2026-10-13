"""The release finder steps past a NON-RESULTS Item 2.02 exhibit (2026-10-13).

NewtekOne (NEWT, CIK 1587987) furnishes its quarterly dividend declarations
under Items 2.02/9.01 ("NewtekOne, Inc. Declares Dividends on Common Stock
and Series B Preferred Shares", 0001628280-26-061720, 2026-09-14). As the
newest 2.02 it was selected over the Q2 release (2026-08-06), and
release_metrics stored a div_ps-only record that shadowed it. Its 2026-04-06
loan-origination update did the same to the Q4 release.

Guards, each from the 2026-10-13 scan of 1,213 real 2.02 exhibits:
  - FKYS's real release ("Announces Second Quarter 2026 Earnings") yields
    no figure and states no results in our terms — its headline keeps it;
  - image-only exhibits (NWPP) are unread, not disproven — kept;
  - BAFN's 2.02+4.02 restatement notice (2026-07-15) disowns its Q1-2026
    release — never stepped past (None, not the disowned Q1 numbers).

Fixtures (tests/fixtures/release_notices): each exhibit's real first 3,000
chars of text, checked to give the same verdict as the full document; 8-K
rows are the banks' real submissions rows.

Run: python -m unittest tests.test_release_finder_non_results
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.ir_provider as ip  # noqa: E402
from tests.test_release_finder_call_notice import (  # noqa: E402
    FIX, _finder, _subs)


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


NEWT_DIVIDEND = _fx("NEWT_2026-09-14_dividend_excerpt.htm")
NEWT_ORIGINATIONS = _fx("NEWT_2026-04-06_originations_excerpt.htm")
NEWT_RELEASE = _fx("NEWT_2026-08-06_release_excerpt.htm")
FKYS_RELEASE = _fx("FKYS_2026-08-03_release_excerpt.htm")
BAFN_RESTATEMENT = _fx("BAFN_2026-07-15_restatement_excerpt.htm")
NWPP_IMAGE = "<p>EX-99.1 2 ex99_1.htm Exhibit 99.1</p>"   # real text layer

NEWT_SUBS = _subs([
    ("8-K", "2.02,9.01", "2026-09-14", "0001628280-26-061720"),
    ("8-K", "5.02", "2026-08-14", "0001628280-26-057098"),
    ("8-K", "2.02,9.01", "2026-08-06", "0001628280-26-054302"),
])


def _ex(html, filed):
    return {"html": html, "filed_date": filed}


class TestIsNonResultsExhibit(unittest.TestCase):
    def test_non_reports(self):
        from data.release_metrics import extract_release_metrics
        # The dividend's only figure is div_ps — not a results figure.
        self.assertEqual(extract_release_metrics(NEWT_DIVIDEND)["div_ps"], 0.19)
        for doc, filed in ((NEWT_DIVIDEND, "2026-09-14"),
                           (NEWT_ORIGINATIONS, "2026-04-06"),
                           (BAFN_RESTATEMENT, "2026-07-15")):
            self.assertTrue(ip._is_non_results_exhibit(_ex(doc, filed)),
                            doc[:120])

    def test_releases_kept(self):
        self.assertFalse(ip._is_non_results_exhibit(
            _ex(NEWT_RELEASE, "2026-08-06")))
        # FKYS: no figure, no results wording we know — the headline keeps it.
        self.assertFalse(ip._has_results_figure(_ex(FKYS_RELEASE, "2026-08-03")))
        self.assertFalse(ip._is_non_results_exhibit(
            _ex(FKYS_RELEASE, "2026-08-03")))

    def test_image_only_exhibit_is_unread_not_disproven(self):
        self.assertFalse(ip._is_non_results_exhibit(
            _ex(NWPP_IMAGE, "2026-07-31")))


class TestFinderSkipsNonResults(unittest.TestCase):
    def test_newt_selects_q2_release_not_dividend(self):
        rel, _ = _finder(NEWT_SUBS, {"000162828026061720": NEWT_DIVIDEND,
                                     "000162828026054302": NEWT_RELEASE})
        self.assertEqual(rel["accession"], "000162828026054302")
        self.assertEqual(ip._quarter_end_before(rel["filed_date"]),
                         "2026-06-30")

    def test_newt_origination_update_skipped(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-04-06", "0001628280-26-023892"),
            ("8-K", "2.02,9.01", "2026-03-13", "0001628280-26-017574"),
            ("8-K", "2.02,9.01", "2026-02-26", "0001628280-26-000001"),
        ])
        rel, _ = _finder(subs, {"000162828026023892": NEWT_ORIGINATIONS,
                                "000162828026017574": NEWT_DIVIDEND,
                                "000162828026000001": NEWT_RELEASE})
        self.assertEqual(rel["accession"], "000162828026000001")

    def test_fkys_release_without_figures_still_selected(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-08-03", "0000737875-26-000027"),
            ("8-K", "2.02,9.01", "2026-05-01", "0000737875-26-000015"),
        ])
        rel, fetched = _finder(subs, {"000073787526000027": FKYS_RELEASE,
                                      "000073787526000015": FKYS_RELEASE})
        self.assertEqual(rel["accession"], "000073787526000027")
        self.assertEqual(fetched, ["000073787526000027"])

    def test_image_only_release_ends_the_search(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-07-31", "0001-26-000002"),
            ("8-K", "2.02,9.01", "2026-04-27", "0001-26-000001"),
        ])
        rel, _ = _finder(subs, {"000126000002": NWPP_IMAGE,
                                "000126000001": NEWT_RELEASE})
        self.assertEqual(rel["accession"], "000126000002")


class TestNonRelianceIsNeverSteppedPast(unittest.TestCase):
    def test_bafn_restatement_does_not_fall_back_to_disowned_q1(self):
        subs = _subs([
            ("8-K", "2.02,4.02,9.01", "2026-07-15", "0001649739-26-000041"),
            ("8-K", "1.01,2.02,3.02,5.02,5.03,7.01,8.01,9.01", "2026-04-30",
             "0001649739-26-000020"),
        ])
        rel, fetched = _finder(subs, {"000164973926000041": BAFN_RESTATEMENT,
                                      "000164973926000020": NEWT_RELEASE})
        self.assertIsNone(rel)
        self.assertEqual(fetched, ["000164973926000041"])

    def test_newer_release_after_restatement_is_selected(self):
        # BAFN today: its Q2 release (08-13) is newer than the 4.02 notice.
        subs = _subs([
            ("8-K", "2.02,7.01,9.01", "2026-08-13", "0001649739-26-000058"),
            ("8-K", "2.02,4.02,9.01", "2026-07-15", "0001649739-26-000041"),
        ])
        rel, _ = _finder(subs, {"000164973926000058": NEWT_RELEASE,
                                "000164973926000041": BAFN_RESTATEMENT})
        self.assertEqual(rel["accession"], "000164973926000058")

    def test_candidates_flag_nonreliance(self):
        subs = _subs([("8-K", "2.02,4.02,9.01", "2026-07-15", "a-1"),
                      ("8-K", "2.02,9.01", "2026-04-30", "a-2")])
        cands = ip._earnings_8k_candidates(subs, max_202=2)
        self.assertEqual([c["nonreliance"] for c in cands], [True, False])


if __name__ == "__main__":
    unittest.main()
