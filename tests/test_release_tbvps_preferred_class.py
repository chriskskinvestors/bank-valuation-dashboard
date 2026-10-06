"""(2026-10-06) Release-reader fixes from the "preferred outstanding, value
unresolved" coverage class — data/sec_earnings_8k. Each fixture reproduces
the real exhibit's row shape; every figure is hand-checked below.

  NEWT 2Q26 (8-K 0001628280-26-054302, finalpr_earningsreleaseq.htm, page
    images + text layer): the reconciliation prints "Tangible book value
    per share" $13.80 (398,847 ÷ 28,899 — EVERY shareholder, $48,181K
    preferred included) ahead of "Tangible book value per common share"
    $12.13 (350,666 ÷ 28,899). Both labels are explicit; the total sits
    inside the ±15% band of the $12.13 reconstruction. The label that says
    COMMON wins.
  BYFC 2Q26 (8-K 0001140361-26-029886, ef20078897_ex99-1.htm): "Tangible
    book value | $110,996 | 9,273,624 | $11.97" — one row carrying equity,
    shares and the per-share figure (110,996 ÷ 9,273.624 = 11.97). Its
    first cell is a $K total; the row ties itself out.
  FRME 2Q26 (8-K 0000712534-26-000056): "Tangible Common Book Value Per
    Share" $29.80 with no book-value row; reconstruction $29.66 (the deck
    nets intangibles of tax). WSBC 2Q26 (8-K 0001193125-26-310361): the
    newest 2.02 8-K (2026-10-02) is "to Host 2026 Third Quarter Earnings
    Conference Call … October 22" — a notice; the July release prints
    "Tangible book value (period end)" $22.98 (2,203,041 ÷ 95,869,209).
  BCBP 2Q26: the 2026-08-07 2.02 8-K is the earnings-call TRANSCRIPT; the
    2026-08-03 release prints book and tangible book per common share
    $14.73 (266,676 ÷ 18,102; goodwill written off, so equal).
  NEWT's 2026-09-14 2.02 8-K declares dividends "payable on October 1,
    2026" — a notice; the 2026-08-06 release is the one.

Run: python -m unittest tests.test_release_tbvps_preferred_class
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

import data.sec_earnings_8k as se8k  # noqa: E402
from data.sec_earnings_8k import (  # noqa: E402
    _self_tied_per_share, _tbvps_candidate, extract_reported_bvps_status,
    extract_reported_tbvps_status)
from tests.test_sec_earnings_8k_misitemized import (  # noqa: E402
    _ARCH, _Site, _index, _subs)


def _tr(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _doc(*tables):
    return ("<html><body>" + "".join(f"<table>{t}</table>" for t in tables)
            + "</body></html>").encode("utf-8")


class TestHandVerifiedInputs(unittest.TestCase):
    def test_figures(self):
        self.assertAlmostEqual(398_847 / 28_899, 13.80, places=2)
        self.assertAlmostEqual(350_666 / 28_899, 12.13, places=2)
        self.assertAlmostEqual(110_996 / 9_273.624, 11.97, places=2)
        self.assertAlmostEqual(2_203_041 / 95_869.209, 22.98, places=2)
        self.assertAlmostEqual(266_676 / 18_102, 14.73, places=2)


class TestNewtCommonLabelWins(unittest.TestCase):
    _TABLE = (_tr("Tangible Book Value Per Share")
              + _tr("Total Shareholders' Equity (GAAP)", "$413,373", "$404,691")
              + _tr("Deduct: Goodwill and Intangibles (GAAP)", "14,526", "14,561")
              + _tr("Numerator: Total Tangible Book Value", "398,847", "390,130")
              + _tr("Denominator: Total Number of Shares Outstanding", "28,899", "28,846")
              + _tr("Tangible Book Value Per Share", "$13.80", "$13.52")
              + _tr("Tangible Book Value Per Common Share")
              + _tr("Total Tangible Book Value", "398,847", "390,130")
              + _tr("Deduct: Preferred Stock (GAAP)", "48,181", "48,181")
              + _tr("Numerator: Tangible Common Book Value", "350,666", "341,949")
              + _tr("Denominator: Total Number of Shares Outstanding", "28,899", "28,846")
              + _tr("Tangible Book Value Per Common Share", "$12.13", "$11.85"))

    def test_common_row_outranks_the_total_row(self):
        html = _doc(self._TABLE)
        self.assertEqual(_tbvps_candidate(html, se8k._book_value_rows(html)),
                         (12.13, True))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=12.134),
                         (12.13, "ok"))
        # Without a book-value anchor the total would have cleared the band.
        self.assertLess(abs(13.80 - 12.134) / 12.134, 0.15)

    def test_single_explicit_label_unchanged(self):
        html = _doc(_tr("Tangible book value per share", "$56.44", "$55.30"))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=56.85),
                         (56.44, "ok"))


class TestByfcSelfTyingRow(unittest.TestCase):
    _TABLE = (_tr("", "Common Equity Capital", "Shares Outstanding", "Per Share Amount")
              + _tr("Tangible book value:")
              + _tr("June 30, 2026")
              + _tr("Common book value", "$ 112,304", "9,273,624", "$ 12.11")
              + _tr("Less: Net unamortized core deposit intangible", "1,308", "", "")
              + _tr("Tangible book value", "$ 110,996", "9,273,624", "$ 11.97"))

    def test_row_ties_itself(self):
        self.assertEqual(_self_tied_per_share([110_996.0, 9_273_624.0, 11.97]), 11.97)
        self.assertIsNone(_self_tied_per_share([110_996.0, 9_273_624.0, 12.50]))
        self.assertIsNone(_self_tied_per_share([110_996.0, None, 11.97]))
        self.assertIsNone(_self_tied_per_share([9.8, 10.2, 11.97]))

    def test_per_share_cell_served_with_and_without_reconstruction(self):
        html = _doc(self._TABLE)
        self.assertEqual(_tbvps_candidate(html, se8k._book_value_rows(html)),
                         (11.97, False))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=11.969),
                         (11.97, "ok"))
        # No reconstruction: the row's own reconciliation is the anchor
        # (bvps 12.11 from the explicit "book value per share" row elsewhere).
        html2 = _doc(_tr("Book value per share", "$ 12.11", "$ 12.10"), self._TABLE)
        self.assertEqual(extract_reported_tbvps_status(html2), (11.97, "ok"))

    def test_plain_dollar_total_row_still_rejected(self):
        html = _doc(_tr("Tangible book value", "$ 110,996", "$ 111,291"))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=11.97),
                         (None, "not_disclosed"))


class TestFrmeAndBohShapes(unittest.TestCase):
    def test_frme_explicit_row_ties_the_reconstruction(self):
        html = _doc(_tr("Tangible Common Book Value Per Share1", "$", "29.80",
                        "$", "27.90"))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=29.658),
                         (29.80, "ok"))
        # Before the 10-Q instance resolved FRME's $25,125K preferred there
        # was no reconstruction and nothing in the release to anchor it.
        self.assertEqual(extract_reported_tbvps_status(html), (None, "not_disclosed"))

    def test_boh_bare_pair_ties_the_reconstruction(self):
        html = _doc(_tr("Per Share of Common Stock")
                    + _tr("Book Value", "$38.81", "$38.10", "$35.16")
                    + _tr("Tangible Book Value", "38.01", "37.31", "34.37"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=38.006, bvps=38.805), (38.01, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=38.805, tbvps=38.01), (38.81, "ok"))


# ── The finder: notices skipped, same-quarter window walked ──────────────────
_WSBC_NOTICE = (
    "<html><body><p>EX-99.1</p><p>WesBanco, Inc. to Host 2026 Third Quarter "
    "Earnings Conference Call and Webcast on Thursday, October 22nd</p>"
    "<p>Wheeling, WVa., October 2, 2026 &#8211; WesBanco, Inc. (Nasdaq: WSBC) "
    "announced today it will host a conference call at 3:00 p.m. ET on "
    "Thursday, October 22, 2026. Results for the quarter are expected to be "
    "released after the market close on Wednesday, October 21, 2026.</p>"
    "</body></html>").encode()
_WSBC_Q2 = (
    "<html><body><p>EX-99.1</p><p>WesBanco Announces Second Quarter 2026 "
    "Financial Results</p><p>Wheeling, W.Va., July 21, 2026 &#8211; WesBanco, "
    "Inc. (Nasdaq: WSBC) today announced net income available to common "
    "shareholders for the second quarter of 2026.</p>"
    "<table><tr><td>Book value (period end)</td><td>40.53</td><td>38.28</td></tr>"
    "<tr><td>Tangible book value (period end) (1)</td><td>22.98</td>"
    "<td>20.48</td></tr></table></body></html>").encode()
_NEWT_DIV = (
    "<html><body><p>EXHIBIT 99.1</p><p>NewtekOne, Inc. Declares Dividends on "
    "Common Stock and Series B Preferred Shares</p><p>Boca Raton, FL "
    "September 14, 2026 - NewtekOne, Inc. announced that its Board declared "
    "a quarterly cash dividend of $0.19 per share. The dividend is payable "
    "on October 1, 2026, to shareholders of record as of September 24, "
    "2026.</p></body></html>").encode()
_BCBP_TRANSCRIPT = (
    "<html><body><p>Exhibit 99.1</p><p>TRANSCRIPT 08 - 03 - 2026 BCB Bancorp "
    "(Q2 Earnings) BCBP Second Quarter Earnings Call TOTAL PAGES: 14</p>"
    "<p>Operator: Thank you for standing by.</p></body></html>").encode()
_BCBP_Q2 = (
    "<html><body><p>Exhibit 99.1</p><p>BCB Bancorp, Inc. Reports Net Loss of "
    "$14.8 million for the Second Quarter of 2026</p><p>BAYONNE, N.J., August "
    "3, 2026 &#8211; BCB Bancorp, Inc. (NASDAQ: BCBP).</p>"
    "<table><tr><td>Book value per common share (1)</td><td>$</td><td>14.73</td>"
    "<td>$</td><td>16.25</td></tr><tr><td>Tangible book value per common "
    "share (2)</td><td>$</td><td>14.73</td><td>$</td><td>15.95</td></tr>"
    "</table></body></html>").encode()
# A real release whose opening names its call date — the headline gate
# keeps it (Park National's shape in the 2026-10-06 universe scan).
_LATER_DATE_RELEASE = (
    "<html><body><p>Exhibit 99.1</p><p>Park National Corporation reports "
    "financial results for second quarter and first half of 2026</p>"
    "<p>NEWARK, Ohio, July 27, 2026 &#8211; Park National Corporation "
    "reported net income of $42.1 million for the second quarter of 2026. "
    "Park will host a conference call on Tuesday, July 28, 2026.</p>"
    "<table><tr><td>Tangible book value per common share</td><td>$</td>"
    "<td>88.10</td></tr></table></body></html>").encode()


def _site(cik, rows, exhibits):
    """rows: newest-first (filed, accession, items); exhibits: {accession:
    EX-99.1 bytes} — each 8-K's index serves one EX-99.1."""
    site = {f"https://data.sec.gov/submissions/CIK{cik:010d}.json":
            _subs([("8-K", f, a, i) for f, a, i in rows])}
    for _, acc_dash, _ in rows:
        acc = acc_dash.replace("-", "")
        site[f"{_ARCH}/{cik}/{acc}/{acc_dash}-index.htm"] = _index(
            cik, acc_dash, [("8-K", "8k.htm", "8-K"),
                            ("EX-99.1", "ex991.htm", "EX-99.1")])
        site[f"{_ARCH}/{cik}/{acc}/ex991.htm"] = exhibits[acc_dash]
    return site


WSBC, NEWT, BCBP = 203596, 1587987, 1228454


class TestNoticeSkipped(_Site):
    def test_wsbc_scheduling_notice_under_202_skipped(self):
        self.serve(_site(WSBC, [
            ("2026-10-02", "0001193125-26-412356", "2.02,9.01"),
            ("2026-07-21", "0001193125-26-310361", "2.02,9.01")],
            {"0001193125-26-412356": _WSBC_NOTICE,
             "0001193125-26-310361": _WSBC_Q2}))
        f8k = se8k._latest_earnings_8k(WSBC)
        self.assertEqual(f8k["accession_dash"], "0001193125-26-310361")
        self.assertEqual(se8k.reported_tbvps_status(WSBC, reconstructed=22.368),
                         (22.98, "ok"))
        self.assertTrue(se8k._is_notice_8k(
            {"accession_dash": "0001193125-26-412356",
             "accession": "000119312526412356", "date": "2026-10-02",
             "cik": WSBC}))

    def test_newt_dividend_declaration_skipped(self):
        self.serve(_site(NEWT, [
            ("2026-09-14", "0001628280-26-061720", "2.02,9.01"),
            ("2026-08-06", "0001628280-26-054302", "2.02,9.01")],
            {"0001628280-26-061720": _NEWT_DIV,
             "0001628280-26-054302": _LATER_DATE_RELEASE}))
        self.assertEqual(se8k._latest_earnings_8k(NEWT)["accession_dash"],
                         "0001628280-26-054302")

    def test_release_naming_its_call_date_is_kept(self):
        # Later date + earnings headline = a release, never skipped.
        self.serve(_site(805676, [
            ("2026-07-27", "0000805676-26-000040", "2.02,9.01"),
            ("2026-04-27", "0000805676-26-000020", "2.02,9.01")],
            {"0000805676-26-000040": _LATER_DATE_RELEASE,
             "0000805676-26-000020": _WSBC_Q2}))
        self.assertEqual(se8k._latest_earnings_8k(805676)["accession_dash"],
                         "0000805676-26-000040")
        self.assertEqual(len(self.calls), 3)      # submissions + one check

    def test_notice_verdict_cached_by_accession(self):
        self.serve(_site(WSBC, [
            ("2026-10-02", "0001193125-26-412356", "2.02,9.01"),
            ("2026-07-21", "0001193125-26-310361", "2.02,9.01")],
            {"0001193125-26-412356": _WSBC_NOTICE,
             "0001193125-26-310361": _WSBC_Q2}))
        se8k._latest_earnings_8k(WSBC)
        n = len(self.calls)
        import data.cache as cache
        cache.invalidate(f"earnings_8k_latest:v3:{WSBC}")
        se8k._latest_earnings_8k(WSBC)
        self.assertEqual(len(self.calls), n + 1)          # submissions only


class TestSameQuarterWindow(_Site):
    def _bcbp(self):
        self.serve(_site(BCBP, [
            ("2026-09-25", "0001193125-26-402710", "8.01,9.01"),
            ("2026-08-07", "0001193125-26-339642", "2.02,9.01"),
            ("2026-08-03", "0001193125-26-329536", "2.02,8.01,9.01"),
            ("2026-05-01", "0001193125-26-200000", "2.02,9.01")],
            {"0001193125-26-402710": _NEWT_DIV,
             "0001193125-26-339642": _BCBP_TRANSCRIPT,
             "0001193125-26-329536": _BCBP_Q2,
             "0001193125-26-200000": _WSBC_Q2}))

    def test_window_is_the_release_quarter_only(self):
        self._bcbp()
        self.assertEqual(se8k._latest_earnings_8k(BCBP)["accession_dash"],
                         "0001193125-26-339642")
        self.assertEqual([f["accession_dash"] for f in
                          se8k._earnings_8ks_same_quarter(BCBP)],
                         ["0001193125-26-339642", "0001193125-26-329536"])

    def test_transcript_discloses_nothing_release_decides(self):
        self._bcbp()
        self.assertEqual(se8k.reported_tbvps_status(
            BCBP, reconstructed=14.732, bvps=14.732), (14.73, "ok"))
        self.assertEqual(se8k.reported_bvps_status(
            BCBP, reconstructed=14.732, tbvps=14.73), (14.73, "ok"))
        # The May (Q1) 8-K is never read: its figure would be a quarter old.
        self.assertFalse(any("200000" in u for u in self.calls))

    def test_newest_answer_is_never_second_guessed(self):
        self._bcbp()
        import data.cache as cache
        cache.put("reported_tbvps:v14:000119312526339642:14.7320:na",
                  {"value": None, "status": "gate_rejected"})
        self.assertEqual(se8k.reported_tbvps_status(BCBP, reconstructed=14.732),
                         (None, "gate_rejected"))

    def test_fetch_failure_ends_the_walk(self):
        self._bcbp()
        import data.cache as cache
        from unittest.mock import patch

        def fail(url):
            if "339642/ex991" in url:
                raise RuntimeError("429")
            return self._site_get(url)
        self._site_get = se8k._get
        with patch.object(se8k, "_get", side_effect=fail):
            self.assertEqual(se8k.reported_tbvps_status(BCBP, reconstructed=14.732),
                             (None, "not_disclosed"))
        self.assertIsNone(cache.get(
            "reported_tbvps:v14:000119312526339642:14.7320:na", max_age_s=None))


if __name__ == "__main__":
    unittest.main()
