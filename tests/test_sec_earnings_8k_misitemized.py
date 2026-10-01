"""Earnings releases furnished under the WRONG 8-K item — data/sec_earnings_8k
(_latest_earnings_8k / _is_misitemized_release), found 2026-10-01.

The finder took the newest Item-2.02 8-K. Two banks furnished their Q2-2026
release under other items, so it landed on their Q1 (April) 8-Ks:

  FBP (CIK 1057706): 8-K 0001057706-26-000020 filed 2026-07-22, items
    "2.01,9.01", EX-99.1 exhibit991.htm "FIRST BANCORP. ANNOUNCES EARNINGS
    FOR THE QUARTER ENDED JUNE 30, 2026". Prints BVPS $12.95 / TBVPS $12.68.
    Hand-check: TCE 1,976,833 − 38,611 goodwill − 3,022 CDI = 1,935,200 ($K)
    / 152,674K shares = 12.675; 1,976,833 / 152,674 = 12.948.
  NPB (CIK 1336706): 8-K 0001336706-26-000059 filed 2026-07-21, items
    "2.01,7.01", EX-99.1 npb-202606x8kxexx991.htm "NORTHPOINTE BANCSHARES,
    INC. REPORTS SECOND QUARTER 2026 RESULTS". Non-GAAP reconciliation:
    (611,648 − 24,979 preferred − 919 intangibles) = 585,750 ($K) /
    34,581,842 shares = 16.938 → "Tangible book value per share" $16.94
    (Q1 column $16.35 = 563,985 / 34,494,116 — what the 2.02 finder served).
    NPB's NEWER 8-K 0001336706-26-000068 (2026-08-28, items "5.02,9.01")
    also carries an EX-99.1 — a COO appointment — and must be rejected.

A non-2.02 8-K is taken only when its EX-99.1 opens with an earnings
headline AND the first period that text names is the quarter its filing
date implies. The item code alone never decides.

Run: python -m unittest tests.test_sec_earnings_8k_misitemized
"""
import json
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
import data.sec_earnings_8k as se8k  # noqa: E402

_ARCH = "https://www.sec.gov/Archives/edgar/data"


def _subs(rows):
    """Submissions JSON from newest-first (form, filed, accession, items)."""
    return json.dumps({"filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "items": [r[3] for r in rows],
        "reportDate": [r[1] for r in rows],
    }}}).encode()


def _index(cik, acc_dash, docs):
    """A filing -index.htm in EDGAR's real 'Document Format Files' table
    shape: [(description, document, type)]."""
    acc = acc_dash.replace("-", "")
    trs = "".join(
        f'<tr><td scope="row">{i}</td><td scope="row">{d}</td>'
        f'<td scope="row"><a href="/Archives/edgar/data/{cik}/{acc}/{doc}">'
        f'{doc}</a></td><td scope="row">{t}</td><td scope="row">1</td></tr>'
        for i, (d, doc, t) in enumerate(docs, start=1))
    return ('<html><body><p>Document Format Files</p>'
            '<table class="tableFile" summary="Document Format Files"><tr>'
            '<th scope="col"><acronym title="Sequence Number">Seq</acronym></th>'
            '<th scope="col">Description</th><th scope="col">Document</th>'
            '<th scope="col">Type</th><th scope="col">Size</th></tr>'
            f'{trs}</table></body></html>').encode()


# Opening text of the real exhibits (excerpted).
_FBP_Q2_991 = (
    "<html><body><p>Exhibit 99.1</p><p><b>FIRST BANCORP. ANNOUNCES EARNINGS "
    "FOR THE QUARTER ENDED JUNE 30, 2026</b></p><p>SAN JUAN, Puerto Rico "
    "&#8211; July 22, 2026 &#8211; First BanCorp. (NYSE: FBP), the bank "
    "holding company for FirstBank Puerto Rico, today reported a net income "
    "of $96.1 million, or $0.62 per diluted share, for the second quarter of "
    "2026, compared to $88.8 million, or $0.57 per diluted share, for the "
    "first quarter of 2026.</p></body></html>").encode()

_NPB_HEAD = (
    "<p>Contacts: Kevin Comps, President 616-974-8491 | "
    "kevin.comps@northpointe.com Brad Howes, CFO 616-726-2585 | "
    "brad.howes@northpointe.com</p>")


def _npb_q2_991(table: str = "") -> bytes:
    return (
        "<html><body>" + _NPB_HEAD +
        "<p><b>NORTHPOINTE BANCSHARES, INC. REPORTS SECOND QUARTER 2026 "
        "RESULTS</b></p><p>GRAND RAPIDS, MICHIGAN, July 21, 2026 &#8211; "
        "Northpointe Bancshares, Inc. (NYSE: NPB), the holding company for "
        "Northpointe Bank, today reported net income to common stockholders "
        "of $21.3 million, or $0.60 per diluted share, for the second quarter "
        "of 2026. This compares to $21.7 million, or $0.62 per diluted share, "
        "for the first quarter of 2026.</p>" + table +
        "</body></html>").encode()


_NPB_AUG_991 = (
    "<html><body>" + _NPB_HEAD +
    "<p><b>Northpointe Bancshares, Inc. Strengthens Leadership Team with "
    "Appointment of Joseph (JB) Long as Chief Operating Officer and Chief "
    "Credit Officer</b></p><p>GRAND RAPIDS, MICHIGAN (August 28, 2026) "
    "&#8211; Northpointe Bancshares, Inc. (NYSE: NPB) today announced that "
    "Joseph (JB) Long will join as Executive Vice President.</p>"
    "</body></html>").encode()


def _td(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


# NPB 2Q26 "Non-GAAP Measures Reconciliation", real row/cell structure
# ('$' decoration cells; columns Jun-26, Mar-26, Jun-25, 6M-26, 6M-25).
_NPB_RECON = (
    "<table>"
    + _td("Non-GAAP Measures Reconciliation")
    + _td("", "As of or for the Three Months Ended",
          "As of or for the Six Months Ended")
    + _td("(Dollars in thousands)", "June 30,2026", "Mar 31,2026",
          "June 30,2025", "June 30,2026", "June 30,2025")
    + _td("Stockholders' equity (GAAP)", "$", "611,648", "$", "589,993", "$",
          "604,277", "$", "611,648", "$", "604,277")
    + _td("Less: Preferred stock", "", "24,979", "", "24,979", "", "98,734",
          "", "24,979", "", "98,734")
    + _td("Less: Intangible assets, net of DTL", "", "919", "", "1,029", "",
          "1,379", "", "919", "", "1,379")
    + _td("Tangible common equity", "", "585,750", "", "563,985", "",
          "504,164", "", "585,750", "", "504,164")
    + _td("Common shares at end of period", "", "34,581,842", "",
          "34,494,116", "", "34,364,659", "", "34,581,842", "", "34,364,659")
    + _td("Tangible book value per share", "$", "16.94", "$", "16.35", "$",
          "14.67", "$", "16.94", "$", "14.67")
    + _td("Book value per share (GAAP)", "$", "17.69", "$", "17.10", "$",
          "17.58", "$", "17.69", "$", "17.58")
    + "</table>")

FBP, NPB = 1057706, 1336706

_FBP_SUBS = _subs([
    ("10-Q", "2026-08-07", "0001057706-26-000023", ""),
    ("8-K", "2026-07-22", "0001057706-26-000020", "2.01,9.01"),
    ("8-K/A", "2026-07-01", "0001140361-26-027163", "5.02,9.01"),
    ("8-K", "2026-05-12", "0001140361-26-020795", "5.02,5.07,9.01"),
    ("10-Q", "2026-05-08", "0001057706-26-000012", ""),
    ("8-K", "2026-04-22", "0001057706-26-000010", "2.02,9.01"),
])
_NPB_SUBS = _subs([
    ("8-K", "2026-08-28", "0001336706-26-000068", "5.02,9.01"),
    ("10-Q", "2026-08-13", "0001336706-26-000064", ""),
    ("8-K", "2026-07-21", "0001336706-26-000059", "2.01,7.01"),
    ("10-Q", "2026-05-14", "0001336706-26-000044", ""),
    ("8-K", "2026-05-13", "0001336706-26-000039", "5.07"),
    ("8-K", "2026-04-21", "0001336706-26-000035", "2.02,7.01"),
])


def _fbp_site(ex991=_FBP_Q2_991):
    return {
        "https://data.sec.gov/submissions/CIK0001057706.json": _FBP_SUBS,
        f"{_ARCH}/1057706/000105770626000020/0001057706-26-000020-index.htm":
            _index(FBP, "0001057706-26-000020", [
                ("8-K", "fbpPRQ22026.htm", "8-K"),
                ("EXHIBIT 99.1", "exhibit991.htm", "EX-99.1"),
                ("EXHIBIT 99.2", "exhibit992.htm", "EX-99.2")]),
        f"{_ARCH}/1057706/000105770626000020/exhibit991.htm": ex991,
    }


def _npb_site(q2_991=None):
    return {
        "https://data.sec.gov/submissions/CIK0001336706.json": _NPB_SUBS,
        f"{_ARCH}/1336706/000133670626000068/0001336706-26-000068-index.htm":
            _index(NPB, "0001336706-26-000068", [
                ("8-K", "npb-20260828.htm", "8-K"),
                ("EX-99.1", "pressreleasedatedaugust282.htm", "EX-99.1")]),
        f"{_ARCH}/1336706/000133670626000068/pressreleasedatedaugust282.htm":
            _NPB_AUG_991,
        f"{_ARCH}/1336706/000133670626000059/0001336706-26-000059-index.htm":
            _index(NPB, "0001336706-26-000059", [
                ("8-K", "npb-20260721.htm", "8-K"),
                ("EX-99.1", "npb-202606x8kxexx991.htm", "EX-99.1"),
                ("EX-99.2", "q22026earningscallslides.htm", "EX-99.2")]),
        f"{_ARCH}/1336706/000133670626000059/npb-202606x8kxexx991.htm":
            q2_991 if q2_991 is not None else _npb_q2_991(),
        # EX-99.2: the earnings-call deck — slide images only.
        f"{_ARCH}/1336706/000133670626000059/q22026earningscallslides.htm":
            b"<html><body><img src='q22026earningscallslides001.jpg'>"
            b"</body></html>",
    }


class _Site(unittest.TestCase):
    """Isolated in-memory cache + a fake EDGAR that logs every fetch."""

    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def serve(self, site):
        def fake_get(url):
            self.calls.append(url)
            if url not in site:
                raise AssertionError(f"unexpected fetch: {url}")
            return site[url]
        p = patch.object(se8k, "_get", side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)


class TestMisitemizedReleaseSelected(_Site):
    def test_fbp_q2_release_under_item_201_is_selected(self):
        self.serve(_fbp_site())
        f8k = se8k._latest_earnings_8k(FBP)
        self.assertEqual(f8k, {"accession_dash": "0001057706-26-000020",
                               "accession": "000105770626000020",
                               "date": "2026-07-22", "cik": FBP})
        # The 8-K/A and the May 5.02 8-K (still Q1, ≤ the 2.02's quarter)
        # are never fetched.
        self.assertEqual(len(self.calls), 3)

    def test_npb_skips_newer_appointment_release_takes_q2_release(self):
        self.serve(_npb_site())
        f8k = se8k._latest_earnings_8k(NPB)
        self.assertEqual(f8k["accession_dash"], "0001336706-26-000059")
        self.assertEqual(f8k["date"], "2026-07-21")

    def test_periodic_record_unchanged(self):
        self.serve(_fbp_site())
        self.assertEqual(se8k.latest_periodic_filing(FBP),
                         {"form": "10-Q", "date": "2026-08-07",
                          "report_date": "2026-08-07"})


class TestUnprovenExhibitFallsBackTo202(_Site):
    """Anything short of proof keeps the Item-2.02 8-K (the Q1 release; the
    valuation layer's _earnings_8k_predates guard handles its staleness)."""

    def _pick(self, ex991):
        self.serve(_fbp_site(ex991))
        return se8k._latest_earnings_8k(FBP)["accession_dash"]

    def test_non_earnings_headline_rejected(self):
        doc = (b"<html><body><p>FIRST BANCORP. ANNOUNCES QUARTERLY CASH "
               b"DIVIDEND ON COMMON STOCK</p><p>SAN JUAN, Puerto Rico - July "
               b"22, 2026 - payable September 12, 2026.</p></body></html>")
        self.assertEqual(self._pick(doc), "0001057706-26-000010")

    def test_prior_quarter_headline_rejected(self):
        # Earnings headline, but it opens on Q1 — a re-furnished old release
        # is not the quarter a July filing reports.
        doc = _FBP_Q2_991.replace(b"JUNE 30, 2026", b"MARCH 31, 2026")
        self.assertEqual(self._pick(doc), "0001057706-26-000010")

    def test_headline_naming_no_period_rejected(self):
        doc = (b"<html><body><p>FIRST BANCORP. ANNOUNCES QUARTERLY EARNINGS"
               b"</p></body></html>")
        self.assertEqual(self._pick(doc), "0001057706-26-000010")

    def test_notice_of_earnings_date_rejected(self):
        doc = (b"<html><body><p>First BanCorp. Announces Date for Second "
               b"Quarter 2026 Earnings Release</p></body></html>")
        self.assertEqual(self._pick(doc), "0001057706-26-000010")

    def test_item_code_alone_never_selects(self):
        # 2.01 8-K with no EX-99.1 at all: only the index is fetched.
        site = _fbp_site()
        site[f"{_ARCH}/1057706/000105770626000020/"
             "0001057706-26-000020-index.htm"] = _index(
            FBP, "0001057706-26-000020", [("8-K", "fbpPRQ22026.htm", "8-K")])
        self.serve(site)
        self.assertEqual(se8k._latest_earnings_8k(FBP)["accession_dash"],
                         "0001057706-26-000010")
        self.assertEqual(len(self.calls), 2)

    def test_same_quarter_as_202_never_fetched(self):
        # A 7.01 deck filed AFTER the Q2 2.02 release reports no newer
        # quarter — no exhibit fetch at all.
        self.serve({"https://data.sec.gov/submissions/CIK0000000077.json":
                    _subs([("8-K", "2026-08-01", "0000000077-26-000009", "7.01"),
                           ("8-K", "2026-07-21", "0000000077-26-000005",
                            "2.02,9.01")])})
        self.assertEqual(se8k._latest_earnings_8k(77)["accession_dash"],
                         "0000000077-26-000005")
        self.assertEqual(len(self.calls), 1)


class TestCachingAndFailure(_Site):
    def test_verdict_cached_by_accession(self):
        self.serve(_npb_site())
        se8k._latest_earnings_8k(NPB)
        n = len(self.calls)
        cache.invalidate("earnings_8k_latest:v2:1336706")   # past the 2h TTL
        self.assertEqual(se8k._latest_earnings_8k(NPB)["accession_dash"],
                         "0001336706-26-000059")
        self.assertEqual(len(self.calls), n + 1)        # submissions only

    def test_fetch_failure_serves_no_8k_and_caches_nothing(self):
        site = _fbp_site()
        del site[f"{_ARCH}/1057706/000105770626000020/exhibit991.htm"]

        def fail(url):
            self.calls.append(url)
            if url.endswith("exhibit991.htm"):
                raise RuntimeError("SEC/EDGAR fetch exhausted by 429s")
            return site[url]
        with patch.object(se8k, "_get", side_effect=fail):
            # Never the older Q1 2.02 8-K in place of an unverified newer one.
            self.assertIsNone(se8k._latest_earnings_8k(FBP))
            self.assertEqual(se8k.latest_periodic_filing(FBP)["form"], "10-Q")
        self.assertIsNone(cache.get("earnings_8k_latest:v2:1057706",
                                    max_age_s=None))
        self.assertIsNone(cache.get(
            "earnings_8k_misitemized:v1:000105770626000020", max_age_s=None))

    def test_checks_bounded_without_any_202(self):
        rows = [("8-K", f"2026-09-{d:02d}", f"0000000088-26-0000{d:02d}", "8.01")
                for d in range(20, 10, -1)]
        site = {"https://data.sec.gov/submissions/CIK0000000088.json":
                _subs(rows)}
        for _, _, acc, _ in rows:
            site[f"{_ARCH}/88/{acc.replace('-', '')}/{acc}-index.htm"] = \
                _index(88, acc, [("8-K", "d.htm", "8-K")])
        self.serve(site)
        self.assertIsNone(se8k._latest_earnings_8k(88))
        self.assertEqual(len(self.calls),
                         1 + se8k._MISITEMIZED_MAX_CHECKS)


class TestNpbBookValueFromQ2Release(_Site):
    """End to end: NPB has no reconstruction (unresolvable preferred), so the
    release is its only TBVPS. The finder now reads Q2, not Q1's $16.35."""

    def test_tbvps_is_q2_and_bvps_stays_na(self):
        self.serve(_npb_site(_npb_q2_991(_NPB_RECON)))
        self.assertEqual(se8k.reported_tbvps_status(NPB), (16.94, "ok"))
        # "Book value per share (GAAP)" $17.69 includes the $24,979K
        # preferred (611,648 / 34,581,842); per-common is 16.96 — the label
        # doesn't say COMMON and the release shows preferred → n/a.
        self.assertEqual(se8k.reported_bvps_status(NPB),
                         (None, "not_disclosed"))


if __name__ == "__main__":
    unittest.main()
