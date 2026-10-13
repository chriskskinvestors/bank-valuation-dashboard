"""A NON-2.02 exhibit must name the results as what it reports (2026-10-13).

The finder accepts an exhibit filed WITHOUT Item 2.02 (a mis-itemized
release — FBP, NPB, FFIN) only when its opening passes the headline gate,
a bag-of-words test: "reports/announces" + "quarter" + "results/earnings/
net income" anywhere in 800 chars. A scan of every non-2.02 candidate the
finder considers across 347 SEC banks found 14 that pass it — 11 of them
NOT releases, passing on stray words, and each one its bank's selected
"latest release" in prod:
  - dividends: FKYS "ANNOUNCES THIRD QUARTER DIVIDEND" (+ "Net income
    increased …"), FMCB "Announces Increase in Quarterly Dividend" (served
    its trailing-twelve-month ROA/ROE as Q2's), FBNC, CAC ("payout results
    in"), CFFI ("expected future earnings"), NBHC, QCRH;
  - decks: CHMG, FFBC;  notices: SSB "to Announce Quarterly Earnings
    Results on Wednesday, October 21", GBFH "… Earnings Call Scheduled".
#399's non-results rule exempts anything passing that headline gate, so it
never reaches these. _RESULTS_TITLE requires the reports/announces verb to
take the results as its object; on the scan it passes exactly FBP, NPB,
FFIN.

Also: NEWT's Q2 release, re-selected by #399, read "within our 2Q26 EPS
guidance range of $0.42-$0.52" as diluted EPS (reported: $0.47).

Fixtures (tests/fixtures/release_notices): the dividend / notice exhibits
and NEWT's release are the real files as EDGAR serves them; the SSB/FMCB
releases are their real first 3,000 chars of text; 8-K rows are the banks'
real submissions rows (2026-10-13).

Run: python -m unittest tests.test_release_finder_results_title
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.ir_provider as ip  # noqa: E402
from tests.test_release_finder_call_notice import (  # noqa: E402
    FIX, _finder, _subs)


def _real(name: str) -> str:
    # _fetch_ex99 decodes exactly this way.
    return (FIX / name).read_bytes().decode("utf-8", "replace")


FMCB_DIVIDEND = _real("FMCB_2026-08-13_ex99_dividend.htm")
FMCB_RELEASE_Q2 = _real("FMCB_2026-07-20_ex99_excerpt.htm")
SSB_NOTICE = _real("SSB_2026-10-02_ex99_1_notice.htm")
SSB_RELEASE_Q2 = _real("SSB_2026-07-23_ex99_1_excerpt.htm")
FKYS_DIVIDEND = _real("FKYS_2026-08-27_ex99_1_dividend.htm")
FKYS_RELEASE_Q2 = _real("FKYS_2026-08-03_release_excerpt.htm")
NEWT_RELEASE_Q2 = _real("NEWT_2026-08-06_ex99_1.htm")


class TestResultsTitle(unittest.TestCase):
    def test_traps_pass_the_headline_gate(self):
        # Why the gate alone let them through — and why #399's non-results
        # rule (which exempts a passing headline) never saw them.
        for doc in (FMCB_DIVIDEND, SSB_NOTICE, FKYS_DIVIDEND):
            self.assertTrue(ip._is_earnings_headline(ip._headline_text(doc)))

    def test_real_release_titles_pass(self):
        # The scan's three real off-item releases, plus two 2.02 releases.
        for t in ("FIRST BANCORP. ANNOUNCES EARNINGS FOR THE QUARTER ENDED "
                  "JUNE 30, 2026 SAN JUAN, Puerto Rico",
                  "NORTHPOINTE BANCSHARES, INC. REPORTS SECOND QUARTER 2026 "
                  "RESULTS GRAND RAPIDS, MICHIGAN",
                  "FIRST FINANCIAL BANKSHARES ANNOUNCES FIRST QUARTER 2026 "
                  "EARNINGS ABILENE, Texas",
                  ip._headline_text(SSB_RELEASE_Q2),
                  ip._headline_text(FMCB_RELEASE_Q2)):
            self.assertTrue(ip._RESULTS_TITLE.search(t), t[:90])

    def test_non_release_titles_fail(self):
        # Real openings (the scan's other non-2.02 traps, verbatim).
        for t in (ip._headline_text(FMCB_DIVIDEND), ip._headline_text(SSB_NOTICE),
                  ip._headline_text(FKYS_DIVIDEND),
                  "Exhibit 99.1 Second Quarter 2026 Investor Presentation "
                  "Chemung Financial Corporation",
                  "GBank Financial Holdings Inc. Announces Second Quarter 2026 "
                  "Quarterly Earnings Call Scheduled for Wednesday, July 29",
                  "COASTAL FINANCIAL CORPORATION TO REPORT SECOND QUARTER 2026 "
                  "RESULTS AND HOST A CONFERENCE CALL ON JULY 30, 2026",
                  "C&F Financial Corporation Announces Quarterly Dividend "
                  "Toano, VA -- The Board of Directors of C&F Financial "
                  "Corporation (NASDAQ:CFFI) (the Corporation) has declared a "
                  "regular cash dividend of 48 cents per share … in light of "
                  "changes in economic conditions, current and future capital "
                  "requirements, and expected future earnings."):
            self.assertFalse(ip._RESULTS_TITLE.search(t), t[:90])


class TestFinderNeedsResultsTitle(unittest.TestCase):
    def test_fmcb_selects_release_not_dividend(self):
        subs = _subs([
            ("8-K", "8.01,9.01", "2026-08-13", "0001140361-26-032560"),
            ("8-K", "7.01,9.01", "2026-07-23", "0001140361-26-029401"),
            ("8-K", "2.02,9.01", "2026-07-20", "0001140361-26-028971"),
        ])
        rel, _ = _finder(subs, {"000114036126032560": FMCB_DIVIDEND,
                                "000114036126028971": FMCB_RELEASE_Q2})
        self.assertEqual(rel["accession"], "000114036126028971")

    def test_ssb_selects_release_not_scheduling_notice(self):
        subs = _subs([
            ("8-K", "7.01,9.01", "2026-10-02", "0001193125-26-411326"),
            ("8-K", "2.02,7.01,8.01,9.01", "2026-07-23", "0001104659-26-086278"),
        ])
        rel, _ = _finder(subs, {"000119312526411326": SSB_NOTICE,
                                "000110465926086278": SSB_RELEASE_Q2})
        self.assertEqual(rel["accession"], "000110465926086278")

    def test_fkys_selects_release_not_dividend(self):
        subs = _subs([
            ("8-K", "1.01,2.03,7.01,9.01", "2026-10-01", "0000737875-26-000035"),
            ("8-K", "8.01", "2026-08-28", "0000737875-26-000033"),
            ("8-K", "8.01,9.01", "2026-08-27", "0000737875-26-000031"),
            ("8-K", "2.02,9.01", "2026-08-03", "0000737875-26-000027"),
        ])
        rel, _ = _finder(subs, {"000073787526000031": FKYS_DIVIDEND,
                                "000073787526000027": FKYS_RELEASE_Q2})
        self.assertEqual(rel["accession"], "000073787526000027")

    def test_mis_itemized_release_still_selected(self):
        # NPB's real Q2 release under 2.01/7.01 (no 2.02) — the reason the
        # gated path exists — still beats the older 2.02. Its real opening.
        npb = ("<p>Contacts: Kevin Comps, President 616-974-8491 | "
               "kevin.comps@northpointe.com Brad Howes, CFO 616-726-2585 | "
               "brad.howes@northpointe.com NORTHPOINTE BANCSHARES, INC. REPORTS "
               "SECOND QUARTER 2026 RESULTS GRAND RAPIDS, MICHIGAN</p>")
        subs = _subs([
            ("8-K", "2.01,7.01", "2026-07-21", "0001336706-26-000059"),
            ("8-K", "2.02,9.01", "2026-04-21", "0001336706-26-000030"),
        ])
        rel, _ = _finder(subs, {"000133670626000059": npb,
                                "000133670626000030": SSB_RELEASE_Q2})
        self.assertEqual(rel["accession"], "000133670626000059")


class TestGuidanceIsNotEps(unittest.TestCase):
    def test_newt_guidance_range_not_diluted_eps(self):
        # Its only statements of $0.47 are unpaired "basic and diluted …
        # $0.48 and $0.47" forms, and its income statement splits "Diluted"
        # across a page — so n/a, never the guidance low end.
        from data.release_metrics import extract_release_metrics
        m = extract_release_metrics(NEWT_RELEASE_Q2, "2026-06-30")
        self.assertIsNone(m["eps_diluted"])
        self.assertEqual(m["tbv_ps"], 12.13)          # the rest still reads


if __name__ == "__main__":
    unittest.main()
