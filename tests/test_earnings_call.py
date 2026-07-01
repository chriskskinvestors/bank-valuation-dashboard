"""
Unit tests for data.earnings_call.parse_call_info — the best-effort extractor
that pulls conference-call time / webcast URL / dial-in out of an earnings-
announcement press-release body. Fixtures mirror real bank-PR phrasings.

The contract: every field is None when not confidently present; nothing is
fabricated; {} when the release carries no call info at all.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date  # noqa: E402

from data.earnings_call import (  # noqa: E402
    parse_call_info,
    mid_label,
    build_calls_agenda,
    _announced_release_date,
    _parse_release_timing,
)


class TestAnnouncedReleaseDate(unittest.TestCase):
    """The universe-wide release-date signal parsed from PR headlines."""

    T = "2026-06-26"

    def test_weekday_dates_and_body_release(self):
        from data.earnings_call import (_parse_on_date, _parse_release_date,
                                         _is_earnings_announcement)
        # A weekday before the month must parse (the common PR-body format that
        # was silently breaking call/release date extraction).
        self.assertEqual(_parse_on_date("on Thursday, July 23, 2026"), "2026-07-23")
        self.assertEqual(_parse_on_date("on July 14, 2026"), "2026-07-14")
        # Announcement headlines with NO inline date are still recognized (the
        # date lives in the body).
        self.assertTrue(_is_earnings_announcement(
            "SB Financial Group Announces Schedule for Second Quarter 2026 Results"))
        self.assertTrue(_is_earnings_announcement(
            "Heritage Financial Announces Earnings Release Date and Conference Call"))
        self.assertFalse(_is_earnings_announcement(
            "Acme Bancorp Announces Quarterly Cash Dividend"))
        # Release date pulled from the body near a release/report cue.
        body = ("will release its second quarter 2026 results on Thursday, "
                "July 23, 2026, after the close. Conference call on July 24.")
        self.assertEqual(_parse_release_date(body, self.T), "2026-07-23")

    def test_announcement_headlines(self):
        f = _announced_release_date
        self.assertEqual(f("Equity Bancshares, Inc. Will Announce Second Quarter "
                           "2026 Results on July 14, 2026", self.T), "2026-07-14")
        self.assertEqual(f("Acme to Report Q3 2026 Results on October 20, 2026",
                           self.T), "2026-10-20")
        self.assertEqual(f("XYZ to Release Q2 Results on or about August 1, 2026",
                           self.T), "2026-08-01")
        self.assertEqual(f("DEF Schedules Earnings Release for July 22, 2026",
                           self.T), "2026-07-22")

    def test_not_a_release_date(self):
        f = _announced_release_date
        # Call/webcast-only headline → not the release date.
        self.assertIsNone(f("ABC to Host Q2 2026 Conference Call on July 15, 2026",
                            self.T))
        # A results recap (no announcement cue) → nothing.
        self.assertIsNone(f("GHI Reports Record Second Quarter 2026 Results", self.T))
        # A past date is ignored (not an upcoming report).
        self.assertIsNone(f("JKL Will Announce Q1 Results on April 14, 2026", self.T))
        self.assertIsNone(f("", self.T))

    def test_call_date_from_body(self):
        from data.earnings_call import _parse_call_date as C
        self.assertEqual(C("The company will host a conference call on July 15, "
                           "2026 at 9:00 a.m. ET.", self.T), "2026-07-15")
        # No call cue → nothing (a bare future date isn't a call date).
        self.assertIsNone(C("Results will be released on July 14, 2026.", self.T))
        self.assertIsNone(C("", self.T))


class TestParseCallInfo(unittest.TestCase):

    def test_full_release_time_webcast_dialin(self):
        text = (
            "Banner Financial Inc. (NASDAQ: BANR) will report its second quarter "
            "2026 results after market close on Wednesday, July 24, 2026. The "
            "company will host a conference call at 11:00 a.m. PT to discuss the "
            "results. A live webcast will be available at "
            "https://investor.bannerbank.com/events. To access the call, please "
            "dial 1-833-470-1428 and enter conference ID 123456."
        )
        info = parse_call_info(text)
        self.assertEqual(info["call_time"], "11:00a PT")
        self.assertEqual(info["webcast_url"], "https://investor.bannerbank.com/events")
        self.assertEqual(info["dial_in"], "1-833-470-1428 (ID 123456)")

    def test_real_citizens_release_parenthesized_zone(self):
        # VERBATIM-shaped Citizens (CFG) Q2-2026 call-details release. Pins the
        # "9:00 am (ET)" parenthesized-zone format + dial-in boilerplate gap that
        # the first parser version missed.
        text = (
            "As previously announced, Citizens Financial Group, Inc. (NYSE: CFG) "
            "will report its second quarter 2026 earnings on July 16, 2026. The "
            "news release and supplemental materials will be available at "
            "http://investor.citizensbank.com. CFG management will host a live "
            "conference call that morning with details as follows: Time: 9:00 am "
            "(ET) Dial-in: To ask a question on the call, individuals may call in "
            "by dialing 800-369-1703, conference ID 1679767."
        )
        info = parse_call_info(text)
        self.assertEqual(info["call_time"], "9:00a ET")
        self.assertEqual(info["webcast_url"], "http://investor.citizensbank.com")
        self.assertEqual(info["dial_in"], "800-369-1703 (ID 1679767)")

    def test_eastern_time_words_normalize(self):
        text = ("Acme Bancorp will release Q2 results on July 22 followed by a "
                "conference call at 9:00 a.m. Eastern Time.")
        info = parse_call_info(text)
        self.assertEqual(info["call_time"], "9:00a ET")
        self.assertIsNone(info["webcast_url"])
        self.assertIsNone(info["dial_in"])

    def test_by_phone_cue_no_minutes(self):
        text = ("The call begins at 8 a.m. CT. Listen via the live audio webcast "
                "at https://ir.examplebank.com/q2 or by phone at 1-800-555-1234.")
        info = parse_call_info(text)
        self.assertEqual(info["call_time"], "8a CT")
        self.assertEqual(info["webcast_url"], "https://ir.examplebank.com/q2")
        self.assertEqual(info["dial_in"], "1-800-555-1234")

    def test_no_call_info_returns_empty(self):
        text = ("XYZ Bancorp declares a quarterly cash dividend of $0.25 per "
                "share, payable August 1, 2026 to holders of record on July 15.")
        self.assertEqual(parse_call_info(text), {})

    def test_empty_input(self):
        self.assertEqual(parse_call_info(""), {})
        self.assertEqual(parse_call_info(None), {})

    def test_webcast_url_requires_context(self):
        # A bare company URL with no webcast/listen cue must NOT be taken as the
        # webcast link (avoid surfacing an unrelated URL).
        text = ("First Bancorp will report Q3 results on October 20, 2026. Visit "
                "https://www.firstbancorp.com for more information.")
        self.assertIsNone(parse_call_info(text).get("webcast_url"))

    def test_mid_label(self):
        self.assertEqual(mid_label({}), "")
        self.assertEqual(mid_label(None), "")
        self.assertEqual(
            mid_label({"call_time": "10:00a ET", "webcast_url": "http://x"}),
            "10:00a ET · webcast ↗")
        self.assertEqual(mid_label({"call_time": "10:00a ET"}), "10:00a ET")
        self.assertEqual(mid_label({"dial_in": "1-800-555-1234"}), "call")


class TestReleaseTiming(unittest.TestCase):
    """Report timing read from the bank's own announcement — fills the When column
    for banks FMP's before/after-open flag doesn't cover."""

    def test_after_close(self):
        self.assertEqual(_parse_release_timing(
            "will report results after the market closes on July 23, 2026."),
            "After close")
        self.assertEqual(_parse_release_timing(
            "results will be issued after the close of trading"), "After close")

    def test_before_open(self):
        self.assertEqual(_parse_release_timing(
            "will release results before the market opens"), "Before open")
        self.assertEqual(_parse_release_timing(
            "to report before the opening of U.S. markets"), "Before open")

    def test_none_when_no_timing_phrase(self):
        self.assertIsNone(_parse_release_timing(
            "will host a conference call at 9:00 a.m. ET"))
        self.assertIsNone(_parse_release_timing(""))

    def test_flows_through_parse_call_info(self):
        info = parse_call_info(
            "will report after the market closes; webcast at https://x.com/ir")
        self.assertEqual(info.get("when"), "After close")


class TestEarningsTimingMap(unittest.TestCase):
    """FMP earnings-calendar timing → {ticker: {when, confirmed}} — the reliable
    universe-wide 'Before open' / 'After close' layer."""

    def test_maps_bmo_amc_skips_unknown(self):
        import data.fmp_client as fmp
        from data import earnings_call as ec
        saved = fmp.get_earnings_calendar
        try:
            fmp.get_earnings_calendar = lambda f, t: [
                {"symbol": "JPM", "date": "2026-07-14", "time": "bmo", "confirmed": True},
                {"symbol": "GS", "date": "2026-07-14", "time": "amc", "confirmed": False},
                {"symbol": "XX", "date": "2026-07-15", "time": "--", "confirmed": True},
            ]
            m = ec.earnings_timing_map()
        finally:
            fmp.get_earnings_calendar = saved
        self.assertEqual(m["JPM"], {"when": "Before open", "confirmed": True})
        self.assertEqual(m["GS"]["when"], "After close")
        self.assertNotIn("XX", m)            # unrecognized time code → skipped


class TestBuildCallsAgenda(unittest.TestCase):
    """build_calls_agenda — merge the yfinance date spine with FMP timing/
    revenue/confirmed overlays, universe-filter, dedupe to soonest per ticker,
    join call info, and bucket by report day for the calendar."""

    # JPM, BKSC, WFC report; FMPONLY only appears in FMP; FAR is past horizon.
    UNIVERSE = {"JPM", "BKSC", "WFC", "FMPONLY", "FAR", "OLD"}
    CALLS = {"JPM": {"call_time": "8:30a ET",
                     "webcast_url": "https://investor.jpm.com/q2",
                     "dial_in": "1-800-555-0100"}}

    def _yf(self):
        # yfinance snapshot shape (data.estimates.fetch_earnings_calendar rows).
        return [
            {"ticker": "JPM", "next_earnings_date": "2026-07-14", "eps_estimate": 5.41},
            {"ticker": "BKSC", "next_earnings_date": "2026-07-16", "eps_estimate": 0.07},
            {"ticker": "WFC", "next_earnings_date": "2026-07-21", "eps_estimate": 1.2},
            {"ticker": "OLD", "next_earnings_date": "2026-06-01", "eps_estimate": 9.0},  # past
            {"ticker": "NOTBANK", "next_earnings_date": "2026-07-15"},                   # not a bank
        ]

    def _fmp(self):
        # FMP calendar shape — overlays timing/confirmed/revenue, extends coverage.
        return [
            {"symbol": "JPM", "date": "2026-08-06", "time": "bmo", "confirmed": True,
             "epsEstimated": 5.5, "revenueEstimated": 4.2e10, "periodEnding": "2026-06-30"},
            {"symbol": "FMPONLY", "date": "2026-07-20", "time": "amc", "confirmed": False,
             "epsEstimated": 2.0, "revenueEstimated": 5.0e8, "periodEnding": "2026-06-30"},
            {"symbol": "FAR", "date": "2026-12-01", "time": "bmo", "confirmed": True},  # past horizon
        ]

    def test_merges_sources_and_buckets(self):
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, self.CALLS, date(2026, 7, 13))
        # One bucket per report DAY, soonest-first; near days get relative labels.
        self.assertEqual([b["date"] for b in agenda],
                         ["2026-07-14", "2026-07-16", "2026-07-20", "2026-07-21"])
        self.assertEqual([b["label"] for b in agenda],
                         ["Tomorrow", "Thu, Jul 16", "Mon, Jul 20", "Tue, Jul 21"])
        # Each day holds only its own reports.
        self.assertEqual([r["ticker"] for r in agenda[0]["rows"]], ["JPM"])
        self.assertEqual([r["ticker"] for r in agenda[1]["rows"]], ["BKSC"])
        self.assertEqual([r["ticker"] for r in agenda[2]["rows"]], ["FMPONLY"])
        self.assertEqual([r["ticker"] for r in agenda[3]["rows"]], ["WFC"])

        rows = {r["ticker"]: r for b in agenda for r in b["rows"]}
        jpm = rows["JPM"]                                 # in BOTH sources
        self.assertEqual(jpm["date"], "2026-07-14")       # yfinance date wins over FMP's 08-06
        self.assertEqual(jpm["days_until"], 1)
        self.assertEqual(jpm["when"], "Before open")      # overlaid from FMP
        self.assertTrue(jpm["confirmed"])                 # FMP confirmed
        self.assertEqual(jpm["eps_est"], 5.41)            # yfinance EPS preferred
        self.assertEqual(jpm["rev_est"], 4.2e10)          # FMP revenue
        self.assertEqual(jpm["webcast_url"], "https://investor.jpm.com/q2")
        self.assertEqual(jpm["dial_in"], "1-800-555-0100")
        self.assertNotIn("_date", jpm)                    # internal key not leaked

        bksc = rows["BKSC"]                               # yfinance-only
        self.assertIsNone(bksc["when"])                   # no FMP overlay
        self.assertFalse(bksc["confirmed"])               # yfinance carries no confirmed flag
        self.assertEqual(bksc["eps_est"], 0.07)
        self.assertIsNone(bksc["rev_est"])
        self.assertIsNone(bksc["call_time"])              # no PR info → None, not faked

        fmponly = rows["FMPONLY"]                         # FMP-only extends coverage
        self.assertEqual(fmponly["date"], "2026-07-20")
        self.assertEqual(fmponly["when"], "After close")
        self.assertEqual(fmponly["rev_est"], 5.0e8)

    def test_excludes_universe_horizon_and_past_dates(self):
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, self.CALLS, date(2026, 7, 13))
        tickers = {r["ticker"] for b in agenda for r in b["rows"]}
        self.assertNotIn("NOTBANK", tickers)              # not in universe (yfinance side)
        self.assertNotIn("OLD", tickers)                  # date in the past
        self.assertNotIn("FAR", tickers)                  # beyond the 75-day horizon

    def test_call_date_carried_separately_and_confirms_consistent_report(self):
        # The call is often a different day than the release; the row date stays
        # the REPORT date and call_date rides along (so the UI shows both). A call
        # within a few days of the report = the cycle is announced ⇒ confirmed.
        calls = dict(self.CALLS)
        calls["BKSC"] = {"call_time": "9:00a ET",
                         "webcast_url": "https://events.q4inc.com/x",
                         "call_date": "2026-07-17"}   # call a day after the 07-16 release
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, calls, date(2026, 7, 13))
        rows = {r["ticker"]: r for b in agenda for r in b["rows"]}
        bksc = rows["BKSC"]
        self.assertEqual(bksc["date"], "2026-07-16")        # report date unchanged
        self.assertTrue(bksc["confirmed"])                  # announced call ⇒ confirmed
        self.assertEqual(bksc["call_date"], "2026-07-17")   # carried for display
        self.assertEqual(bksc["call_time"], "9:00a ET")

    def test_call_far_from_report_does_not_confirm(self):
        # A call date wildly inconsistent with the report estimate is NOT used to
        # confirm it (guards against a stale/mismatched call event).
        calls = dict(self.CALLS)
        calls["BKSC"] = {"call_date": "2026-08-10"}   # 25d after the 07-16 report
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, calls, date(2026, 7, 13))
        rows = {r["ticker"]: r for b in agenda for r in b["rows"]}
        self.assertFalse(rows["BKSC"]["confirmed"])         # gap too large → stays (proj.)

    def test_announced_release_date_overrides_estimate_and_confirms(self):
        # An announced release date (from the PR headline) is authoritative — it
        # becomes the row date and confirms it, over the yfinance estimate.
        calls = dict(self.CALLS)
        calls["BKSC"] = {"release_date": "2026-07-15"}   # yfinance said 07-16
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, calls, date(2026, 7, 13))
        rows = {r["ticker"]: r for b in agenda for r in b["rows"]}
        bksc = rows["BKSC"]
        self.assertEqual(bksc["date"], "2026-07-15")        # announced date wins
        self.assertTrue(bksc["confirmed"])                  # announced ⇒ confirmed

    def test_announcement_timing_fills_when_fmp_still_wins(self):
        # BKSC has no FMP row (no before/after-open flag) but its PR stated the
        # timing → the When column is filled from the announcement. JPM has an FMP
        # flag, which still takes precedence over any announcement timing.
        calls = dict(self.CALLS)
        calls["BKSC"] = {"when": "After close"}
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, calls, date(2026, 7, 13))
        rows = {r["ticker"]: r for b in agenda for r in b["rows"]}
        self.assertEqual(rows["BKSC"]["when"], "After close")   # from the PR
        self.assertEqual(rows["JPM"]["when"], "Before open")    # FMP flag wins

    def test_horizon_days_bounds_window(self):
        # FMPONLY reports 2026-07-20 — inside 75 days, outside a tight 5-day window.
        agenda = build_calls_agenda(
            self._yf(), self._fmp(), self.UNIVERSE, self.CALLS, date(2026, 7, 13),
            horizon_days=5)
        tickers = {r["ticker"] for b in agenda for r in b["rows"]}
        self.assertIn("JPM", tickers)                     # 07-14, within 5 days
        self.assertNotIn("FMPONLY", tickers)              # 07-20, beyond 5 days

    def test_empty_inputs_return_empty(self):
        self.assertEqual(
            build_calls_agenda(None, None, self.UNIVERSE, {}, date(2026, 7, 13)), [])
        self.assertEqual(
            build_calls_agenda([], [], set(), {}, date(2026, 7, 13)), [])
        self.assertEqual(
            build_calls_agenda(self._yf(), self._fmp(), set(), self.CALLS,
                               date(2026, 7, 13)), [])


if __name__ == "__main__":
    unittest.main()
