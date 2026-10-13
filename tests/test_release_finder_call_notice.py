"""The release finder steps past an Item 2.02 call NOTICE (2026-10-13).

WesBanco (WSBC, CIK 203596) furnished "WesBanco, Inc. to Host 2026 Third
Quarter Earnings Conference Call and Webcast on Thursday, October 22" under
Items 2.02/9.01 (0001193125-26-412356, 2026-10-02). As the newest 2.02 8-K,
data/ir_provider.latest_earnings_release returned it, and release_metrics
recorded a figure-less qend-2026-09-30 extraction that shadowed the real Q2
release (0001193125-26-310361, 2026-07-21) for every consumer.

The notice exhibit is the real file (tests/fixtures/release_notices); the
other exhibits are their real opening text; the 8-K rows are WSBC's real
submissions `recent` block (2026-10-13).

Run: python -m unittest tests.test_release_finder_call_notice
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.ir_provider as ip  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "release_notices"
NOTICE_Q3 = (FIX / "WSBC_2026-10-02_ex99_1.htm").read_text(encoding="utf-8")
NOTICE_Q2 = ("<p>WesBanco, Inc. to Host 2026 Second Quarter Earnings Conference "
             "Call and Webcast on Wednesday, July 22nd</p><p>Wheeling, WVa., "
             "July 2, 2026 – WesBanco, Inc. (Nasdaq:WSBC), a diversified, "
             "multi-state bank holding company, announced today it will host "
             "a conference call at 9:00 a.m. ET on Wednesday, July 22, 2026.</p>")
DECK = ("<p>Q3 2026 Investor Presentation John iannone SVP &amp; Director of "
        "Investor Relations (304) 905-7021 (WSBC financials as of the three "
        "months ending June 30, 2026)</p>")
RELEASE_Q2 = ("<p>WesBanco Announces Second Quarter 2026 Financial Results</p>"
              "<p>Marked by strong annualized loan growth, top-tier efficiency "
              "ratio, and accelerating growth in targeted expansion markets</p>"
              "<p>Wheeling, WVa. (July 21, 2026) – WesBanco, Inc. (“WesBanco” or "
              "“Company”) (Nasdaq: WSBC), a diversified, multi-state bank "
              "holding company, today announced net income and related "
              "per share amounts for the three and six months ended June 30, "
              "2026.</p>")

ACC_NOTICE_Q3 = "000119312526412356"
ACC_DECK = "000119312526371610"
ACC_RELEASE_Q2 = "000119312526310361"
ACC_NOTICE_Q2 = "000119312526294653"


def _subs(rows):
    forms, items, dates, accs = zip(*rows)
    return {"filings": {"recent": {
        "form": list(forms), "items": list(items), "filingDate": list(dates),
        "accessionNumber": list(accs),
        "primaryDocument": ["d.htm"] * len(rows)}}}


WSBC_SUBS = _subs([
    ("8-K", "2.02,9.01", "2026-10-02", "0001193125-26-412356"),
    ("8-K", "7.01,9.01", "2026-08-27", "0001193125-26-371610"),
    ("8-K", "2.02,9.01", "2026-07-21", "0001193125-26-310361"),
    ("8-K", "2.02,9.01", "2026-07-02", "0001193125-26-294653"),
    ("8-K", "5.02,9.01", "2026-06-04", "0001193125-26-257536"),
])


def _finder(subs, docs):
    """latest_earnings_release over `subs`, each accession's EX-99.1 served
    from `docs` (absent ⇒ the fetch fails). Returns (release, fetched)."""
    fetched = []

    def fetch(cik, hit):
        fetched.append(hit["accession"])
        html = docs.get(hit["accession"])
        return None if html is None else {
            "url": hit["accession"], "html": html,
            "filed_date": hit["filed_date"], "accession": hit["accession"],
            "form": hit["form"]}

    with mock.patch.object(ip, "_get",
                           return_value=json.dumps(subs).encode()), \
            mock.patch.object(ip, "_fetch_ex99", side_effect=fetch):
        return ip.latest_earnings_release(203596), fetched


class TestFinderSkipsCallNotice(unittest.TestCase):
    def test_wsbc_selects_q2_release_not_q3_notice(self):
        rel, _ = _finder(WSBC_SUBS, {
            ACC_NOTICE_Q3: NOTICE_Q3, ACC_DECK: DECK,
            ACC_RELEASE_Q2: RELEASE_Q2, ACC_NOTICE_Q2: NOTICE_Q2})
        self.assertEqual(rel["accession"], ACC_RELEASE_Q2)
        self.assertEqual(ip._quarter_end_before(rel["filed_date"]), "2026-06-30")

    def test_q2_notice_skipped_before_release_existed(self):
        # 2026-07-02..07-20: the Q2 notice was the newest 2.02 and the Q1
        # release sat behind it — the same shadowing, a quarter earlier.
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-07-02", "0001193125-26-294653"),
            ("8-K", "2.02,9.01", "2026-04-21", "0001193125-26-000001"),
        ])
        rel, _ = _finder(subs, {
            ACC_NOTICE_Q2: NOTICE_Q2,
            "000119312526000001": RELEASE_Q2.replace("Second", "First")})
        self.assertEqual(rel["accession"], "000119312526000001")

    def test_genuine_newest_release_costs_no_extra_fetch(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-07-21", "0001193125-26-310361"),
            ("8-K", "2.02,9.01", "2026-07-02", "0001193125-26-294653"),
        ])
        rel, fetched = _finder(subs, {ACC_RELEASE_Q2: RELEASE_Q2,
                                      ACC_NOTICE_Q2: NOTICE_Q2})
        self.assertEqual(rel["accession"], ACC_RELEASE_Q2)
        self.assertEqual(fetched, [ACC_RELEASE_Q2])

    def test_unreadable_202_still_ends_the_search(self):
        # An older release must never stand in for a 2.02 we could not read.
        rel, _ = _finder(WSBC_SUBS, {ACC_RELEASE_Q2: RELEASE_Q2})
        self.assertIsNone(rel)

    def test_only_notices_is_none(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-10-02", "0001193125-26-412356"),
            ("8-K", "2.02,9.01", "2026-07-02", "0001193125-26-294653"),
        ])
        rel, _ = _finder(subs, {ACC_NOTICE_Q3: NOTICE_Q3,
                                ACC_NOTICE_Q2: NOTICE_Q2})
        self.assertIsNone(rel)

    def test_walk_back_is_bounded(self):
        rows = [("8-K", "2.02", f"2026-0{m}-01", f"n-{m}") for m in (9, 8, 7, 6)]
        rel, fetched = _finder(_subs(rows), {f"n{m}": NOTICE_Q3
                                             for m in (9, 8, 7, 6)})
        self.assertIsNone(rel)
        self.assertEqual(fetched, ["n9", "n8", "n7"])


# Horizon (HBNC, CIK 706129) files the same notice under Items 2.02/9.01
# every quarter (04-01, 07-01, 10-01) — and its headline PASSES the
# earnings-headline gate (announces + quarter + results), so only
# is_call_notice catches it. Real opening text, 0000706129-26-000077.
HBNC_NOTICE_Q3 = (
    "<p>Contact: John R. Stewart Chief Financial Officer Phone: 219-814-5833 "
    "Date: October 1, 2026</p><p>Horizon Bancorp, Inc. Announces Conference "
    "Call to Review Third Quarter Results on October 22</p><p>MICHIGAN CITY, "
    "Ind., October 1, 2026 (GLOBE NEWSWIRE) -- (NASDAQ GS: HBNC) – Horizon "
    "Bancorp, Inc. (“Horizon” or the “Company”) will host a conference call "
    "at 7:30 a.m. CT on Thursday, October 22, 2026 to review its third "
    "quarter 2026 financial results.</p>")
HBNC_RELEASE_Q2 = (
    "<p>Horizon Bancorp, Inc. Reports Second Quarter 2026 Results</p><p>"
    "Contact: John R. Stewart, CFA EVP, Chief Financial Officer Phone: (219) "
    "814–5833 Fax: (219) 874–9280 Date: July 22, 2026 FOR IMMEDIATE RELEASE "
    "Horizon Bancorp, Inc. Reports Strong Second Quarter 2026 Results</p>")


class TestHbncNoticePassesHeadlineGate(unittest.TestCase):
    def test_hbnc_notice_skipped(self):
        self.assertTrue(ip._is_earnings_headline(
            ip._headline_text(HBNC_NOTICE_Q3)))   # the trap
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-10-01", "0000706129-26-000077"),
            ("8-K", "8.01", "2026-09-16", "0000706129-26-000073"),
            ("8-K", "5.02,9.01", "2026-08-17", "0000706129-26-000066"),
            ("8-K", "2.02,7.01,9.01", "2026-07-22", "0000706129-26-000060"),
        ])
        rel, _ = _finder(subs, {"000070612926000077": HBNC_NOTICE_Q3,
                                "000070612926000060": HBNC_RELEASE_Q2})
        self.assertEqual(rel["accession"], "000070612926000060")


# MVB Financial (MVBF, CIK 1277902): a REAL release whose opening reads like
# a notice — "Announces Second Quarter 2026 Results / Company to Host a
# Conference Call and Webcast", its "reported net income" clause past
# is_call_notice's window. Its figures keep it. Fixture: the first 10,000
# chars of the real exhibit's text (0001277902-26-000078, 2026-07-28).
MVBF_RELEASE_Q2 = (FIX / "MVBF_2026-07-28_ex99_1_excerpt.htm").read_text(
    encoding="utf-8")


class TestReleaseOpeningLikeNoticeIsKept(unittest.TestCase):
    def test_mvbf_release_selected(self):
        from data.release_metrics import is_call_notice
        self.assertTrue(is_call_notice(MVBF_RELEASE_Q2))   # the trap
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-07-28", "0001277902-26-000078"),
            ("8-K", "2.02,9.01", "2026-04-29", "0001277902-26-000040"),
        ])
        rel, fetched = _finder(subs, {"000127790226000078": MVBF_RELEASE_Q2,
                                      "000127790226000040": RELEASE_Q2})
        self.assertEqual(rel["accession"], "000127790226000078")
        self.assertEqual(fetched, ["000127790226000078"])

    def test_dividend_alone_does_not_make_a_notice_a_release(self):
        notice = NOTICE_Q2.replace(
            "</p>", " The Board declared a quarterly cash dividend of $0.37 "
                    "per share.</p>", 1)
        from data.release_metrics import extract_release_metrics
        self.assertEqual(extract_release_metrics(notice)["div_ps"], 0.37)
        self.assertTrue(ip._is_notice_exhibit(
            {"html": notice, "filed_date": "2026-07-02"}))


class TestCandidatesWalkPastNotice(unittest.TestCase):
    def test_default_still_stops_at_first_202(self):
        cands = ip._earnings_8k_candidates(WSBC_SUBS)
        self.assertEqual([c["accession"] for c in cands], [ACC_NOTICE_Q3])

    def test_max_202_reaches_the_previous_release(self):
        cands = ip._earnings_8k_candidates(WSBC_SUBS, max_202=2)
        self.assertEqual([(c["accession"], c["gated"]) for c in cands],
                         [(ACC_NOTICE_Q3, False), (ACC_DECK, True),
                          (ACC_RELEASE_Q2, False)])


if __name__ == "__main__":
    unittest.main()
