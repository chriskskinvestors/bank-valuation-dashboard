"""
Pin the Results-board evidence rules (owner report 2026-10-12: 18 of the
board's 27 rows were not earnings reports).

Every headline below is the REAL production headline (prod events store /
results_board dump, 2026-10-12). Failure classes pinned:
  1. call/webcast/date notices minted PENDING rows (14 rows),
  2. a revision of earlier quarters counted as this season's report (SLRK),
  3. old archive releases (Q3 2025, Q1/Q2 2026) dated by scrape time — the
     IR-site HTML scrape stamps undated links "now" (`date_inferred`),
  4. UTC dating put a Friday-evening 8-K on Saturday (WSBC),
  5. an Item 2.02 8-K whose exhibit is a CALL NOTICE (WSBC) counted as results,
  6. weekend-dated rows took the live 1D change (Friday's move) as "live",
  7. a nine-month EPS ($5.29, WBHC) shipped as the quarter's EPS actual.

Run: python -m unittest tests.test_results_board_evidence
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from data import earnings_results as er  # noqa: E402

CALL_NOTICES = [
    "Amalgamated Financial Corp. Announces Third Quarter 2026 Earnings Conference Call",
    "Cullen/Frost Bankers, Inc. Hosts Third Quarter 2026 Earnings Conference Call",
    "Central Pacific Financial Corp. Announces Conference Call to Discuss Third "
    "Quarter 2026 Financial Results",
    "UMB Announces Conference Call to Discuss Third Quarter 2026 Results",
    "Bank7 Corp. Announces Third Quarter 2026 Earnings Conference Call",
    "First Business Bank Announces Third Quarter 2026 Earnings Conference Call",
    "Bank of Marin Bancorp to Webcast Q3 Earnings on Monday, October 26, 2026, "
    "at 8:30 a.m. PT",
    "Horizon Bancorp, Inc. Announces Conference Call to Review Third Quarter "
    "Results on October 21",
    "Central Bancompany, Inc. Announces Conference Call to Discuss Third Quarter "
    "2026 Results",
    "First Bank Announces Third Quarter 2026 Earnings Conference Call",
    "PROSPERITY BANCSHARES, INC.® INVITES YOU TO JOIN ITS THIRD QUARTER 2026 "
    "EARNINGS CONFERENCE CALL",
    "Northern Trust Corporation to Webcast Third Quarter 2026 Earnings Conference Call",
    "BOK Financial Corporation Announces Third Quarter 2026 Earnings Conference Call",
    "Hanmi Financial Corporation Announces Second Quarter 2026 Earnings and "
    "Conference Call Date",
]
REVISION = ("Solera National Bancorp Announces Revised First & Second Quarter "
            "2026 Financial Results")
REAL_RESULTS = [
    "PB Financial Corporation Reports Record Third Quarter 2026 Earnings",
    "Bank of South Carolina Corporation Announces Third Quarter Earnings",
    "Security Federal Corporation Announces Increase in Quarterly and "
    "Year-To-Date Earnings",
    "Acme Bancorp Reports Third Quarter 2026 Results; Conference Call Thursday",
    "8-K · Earnings / Results",
    "Bank OZK Announces Second Quarter 2026 Earnings",
    "Morgan Stanley Reports Second Quarter",
]


class TestHeadlineRules(unittest.TestCase):
    def test_call_notices_and_revisions_are_never_results(self):
        for h in CALL_NOTICES + [REVISION]:
            self.assertFalse(er.is_results_headline(h), h)

    def test_real_results_shapes_still_qualify(self):
        for h in REAL_RESULTS:
            self.assertTrue(er.is_results_headline(h), h)

    def test_stale_named_quarter(self):
        on = date(2026, 10, 10)
        stale = ["Finward Bancorp Announces Third Quarter 2025 Results",
                 "Hanmi Reports 2026 First Quarter Results",
                 "First Carolina Financial Services Reports Net Income of $5.1 "
                 "Million, or $0.20 Per Diluted Share, for the Second Quarter of 2026",
                 "Acme Reports Fourth Quarter and Full Year 2025 Results"]
        fine = ["PB Financial Corporation Reports Record Third Quarter 2026 Earnings",
                "Acme Reports Q3 2026 Results", "Acme Reports 3Q26 Results",
                # fiscal naming can't be mapped to a calendar quarter → unjudged
                "Northeast Bank Reports Fiscal 2027 First Quarter Results",
                # no named quarter → unjudged
                "Bank of South Carolina Corporation Announces Third Quarter Earnings"]
        for h in stale:
            self.assertTrue(er.names_stale_quarter(h, on), h)
        for h in fine:
            self.assertFalse(er.names_stale_quarter(h, on), h)
        # A late Q2 filer inside 75 days of quarter end is still this report.
        q2 = "Acme Reports Second Quarter 2026 Results"
        self.assertFalse(er.names_stale_quarter(q2, date(2026, 8, 14)))
        self.assertFalse(er.names_stale_quarter(q2, date(2026, 9, 10)))


def _ev(headline, published_at, source="prnewswire", url="u", **kw):
    return dict(headline=headline, published_at=published_at, source=source,
                url=url, **kw)


class TestRowBuilder(unittest.TestCase):
    TODAY = date(2026, 10, 12)

    def test_notice_wave_mints_no_rows_real_reports_do(self):
        pub = "2026-10-08T12:00:00+00:00"
        events = {f"N{i}": [_ev(h, pub)] for i, h in enumerate(CALL_NOTICES)}
        events["SLRK"] = [_ev(REVISION, pub)]
        events["PBNC"] = [_ev(REAL_RESULTS[0], pub)]
        rows = {r["ticker"]: r for r in er.build_results_rows(
            [], set(events), events, self.TODAY)}
        self.assertEqual(sorted(rows), ["PBNC"])
        self.assertTrue(rows["PBNC"]["pending"])
        self.assertFalse(rows["PBNC"]["from_fmp"])
        self.assertEqual(rows["PBNC"]["pr_source"], "prnewswire")

    def test_old_archive_releases_never_become_this_report(self):
        # The exact Oct-10 rows: same-second scrape timestamps, ir_site source.
        events = {
            "HAFC": [_ev("Hanmi Reports 2026 First Quarter Results",
                         "2026-10-10 11:05:52.411857+00:00", source="ir_site")],
            "FNWD": [_ev("Finward Bancorp Announces Third Quarter 2025 Results",
                         "2026-10-10 11:05:50.103591+00:00", source="ir_site")],
            "FCBM": [_ev("First Carolina Financial Services Reports Net Income "
                         "of $5.1 Million, or $0.20 Per Diluted Share, for the "
                         "Second Quarter of 2026",
                         "2026-10-10 11:05:48.874460+00:00", source="ir_site")],
        }
        rows = er.build_results_rows([], set(events), events, self.TODAY)
        self.assertEqual(rows, [])

    def test_inferred_dates_are_not_evidence(self):
        events = {"AAA": [_ev("Acme Reports Q3 2026 Results",
                              "2026-10-10T11:05:00+00:00", source="ir_site",
                              date_inferred=True)],
                  "BBB": [_ev("Acme Reports Q3 2026 Results",
                              "2026-10-10T11:05:00+00:00", source="ir_site",
                              date_inferred=False)]}
        kept = er.first_party_events(events)
        self.assertEqual(kept["AAA"], [])
        self.assertEqual(len(kept["BBB"]), 1)

    def test_rows_are_dated_in_eastern_time(self):
        # WSBC's 8-K: accepted 20:24 ET Friday Oct 2 = 00:24 UTC Saturday.
        events = {"ACME": [_ev("Acme Reports Q3 2026 Results",
                               "2026-10-03T00:24:12+00:00")]}
        rows = er.build_results_rows([], {"ACME"}, events, self.TODAY)
        self.assertEqual(rows[0]["date"], "2026-10-02")
        # datetime objects (Postgres) date the same way
        self.assertEqual(er._et_date(datetime(2026, 10, 3, 0, 24,
                                              tzinfo=timezone.utc)),
                         date(2026, 10, 2))
        # a bare date is already a calendar date — never shifted a day back
        self.assertEqual(er._et_date("2026-07-18"), date(2026, 7, 18))

    def test_fmp_row_with_a_call_notice_stays_awaiting(self):
        fmp = [{"symbol": "AMAL", "date": "2026-10-11", "epsActual": None,
                "revenueActual": None}]
        events = {"AMAL": [_ev(CALL_NOTICES[0], "2026-10-11T13:00:00+00:00")]}
        rows = er.build_results_rows(fmp, {"AMAL"}, events, self.TODAY)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["awaiting"])
        self.assertFalse(rows[0]["pending"])
        self.assertTrue(rows[0]["from_fmp"])


def _row(**kw):
    base = {"ticker": "X", "date": "2026-10-09", "pending": True,
            "awaiting": False, "eps_act": None, "rev_act": None,
            "pr_source": "sec_8k", "from_fmp": False, "rel": None}
    base.update(kw)
    return base


class TestUnproven8kRows(unittest.TestCase):
    TODAY = date(2026, 10, 12)

    def test_notice_and_dividend_exhibits_dropped_letter_kept(self):
        rows = [
            _row(ticker="WSBC", date="2026-10-02",
                 rel={"metrics": {}, "capital": {}, "call_notice": True,
                      "results_stated": False}),
            # NEWT 2026-09-14: a dividend announcement furnished under 2.02 —
            # not a call notice, but it states no results either.
            _row(ticker="NEWT", date="2026-09-14",
                 rel={"metrics": {}, "capital": {}, "call_notice": False,
                      "results_stated": False}),
            _row(ticker="WBHC",
                 rel={"metrics": {"eps_diluted": None}, "capital": {},
                      "call_notice": False, "results_stated": True}),
            _row(ticker="FIG", rel={"metrics": {"nim": 3.4}, "capital": {},
                                    "results_stated": False}),
            # a pre-v28 record (no results_stated) is not proof
            _row(ticker="OLDREC", rel={"metrics": {}, "capital": {},
                                       "call_notice": False}),
            _row(ticker="NOREL", rel=None),
            _row(ticker="ACT", rel=None, eps_act=1.0, pending=False),
            _row(ticker="WIRE", pr_source="prnewswire", rel=None),
        ]
        kept = [r["ticker"] for r in er.drop_unproven_8k_rows(rows, self.TODAY)]
        self.assertEqual(kept, ["WBHC", "FIG", "ACT", "WIRE"])

    def test_fmp_row_in_window_reverts_to_awaiting(self):
        r = _row(ticker="SCHED", date="2026-10-11", from_fmp=True,
                 pr_headline="8-K · Earnings / Results", pr_url="https://sec",
                 rel={"metrics": {}, "capital": {}, "call_notice": True},
                 px_react=1.2, px_react_live=True)
        out = er.drop_unproven_8k_rows([r], self.TODAY)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["awaiting"])
        self.assertFalse(out[0]["pending"])
        self.assertIsNone(out[0]["pr_url"])
        self.assertIsNone(out[0]["rel"])
        self.assertIsNone(out[0]["px_react"])
        # outside the window the same row is dropped
        old = _row(ticker="OLD", date="2026-10-05", from_fmp=True,
                   rel={"metrics": {}, "capital": {}, "call_notice": True})
        self.assertEqual(er.drop_unproven_8k_rows([old], self.TODAY), [])


class TestLivePriceReaction(unittest.TestCase):
    def test_live_session_rule(self):
        sat, sun, mon = date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)
        fri = date(2026, 10, 9)
        self.assertFalse(er.live_session_is_current(sat, sat))   # Friday's move
        self.assertFalse(er.live_session_is_current(sat, sun))
        self.assertTrue(er.live_session_is_current(sat, mon))    # Monday reacts
        self.assertTrue(er.live_session_is_current(fri, sat))    # Fri close lagging
        self.assertFalse(er.live_session_is_current(fri, mon))   # stale by Monday
        self.assertFalse(er.live_session_is_current(None, mon))

    def test_weekend_row_never_takes_fridays_move(self):
        import pandas as pd
        hist = pd.DataFrame({"date": pd.to_datetime(["2026-10-08", "2026-10-09"]),
                             "close": [10.0, 10.2]})
        row = {"ticker": "HAFC", "reaction_session": "2026-10-10",
               "awaiting": False}
        with mock.patch("data.fmp_client.get_history", return_value=hist), \
                mock.patch("data.fmp_client.get_price_change",
                           return_value={"1D": -1.58}) as pc:
            er._fill_price_reactions([row], date(2026, 10, 10))
        self.assertIsNone(row["px_react"])
        self.assertFalse(row["px_react_live"])
        pc.assert_not_called()


class TestCachedHistoryDates(unittest.TestCase):
    """2026-10-12 prod: get_history's CACHE HIT rebuilt the frame from JSON
    with string dates; the board's `d.date()` raised, its handler emptied the
    history, and every row took the live 1D change as its "reaction"."""

    def test_cache_hit_returns_datetime_dates_and_board_reacts(self):
        from data import fmp_client
        records = [{"date": "2026-10-08 00:00:00", "close": 10.0},
                   {"date": "2026-10-09 00:00:00", "close": 10.42}]
        with mock.patch.object(fmp_client, "_has_key", return_value=True), \
                mock.patch.object(fmp_client, "_cache_get", return_value=records):
            df = fmp_client.get_history("AMBK", "1M")
            self.assertTrue(hasattr(df["date"].iloc[0], "date"))
            row = {"ticker": "AMBK", "reaction_session": "2026-10-09",
                   "awaiting": False}
            with mock.patch.object(fmp_client, "get_price_change") as pc:
                er._fill_price_reactions([row], date(2026, 10, 12))
        self.assertAlmostEqual(row["px_react"], 4.2, places=6)
        self.assertFalse(row["px_react_live"])
        pc.assert_not_called()


class TestStoreDateInferredFlag(unittest.TestCase):
    def setUp(self):
        import data.db as db
        import data.events.store as store
        from sqlalchemy import create_engine
        self._saved = (db._engine, db.USE_POSTGRES, store._USE_POSTGRES,
                       store._engine)
        db._engine = create_engine("sqlite://")
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_schema()
        self.store = store

    def tearDown(self):
        import data.db as db
        (db._engine, db.USE_POSTGRES, self.store._USE_POSTGRES,
         self.store._engine) = self._saved

    def test_flag_round_trips_without_the_payload(self):
        from data.events.base import Event
        now = datetime(2026, 10, 10, 11, 5, tzinfo=timezone.utc)
        self.store.insert_events([
            Event(ticker="HAFC", source="ir_site", event_type="earnings",
                  headline="Hanmi Reports 2026 First Quarter Results",
                  published_at=now, url="https://x/1", external_id="https://x/1",
                  raw={"ir_home": "h", "date_inferred": True}),
            Event(ticker="RF", source="prnewswire", event_type="earnings",
                  headline="Regions Reports Q3 2026 Results",
                  published_at=now, url="https://x/2", external_id="x2", raw={}),
        ])
        got = {e["ticker"]: e for e in self.store.get_events_by_type("earnings")}
        self.assertTrue(got["HAFC"]["date_inferred"])
        self.assertFalse(got["RF"]["date_inferred"])
        self.assertNotIn("raw_json", got["HAFC"])


class TestNineMonthEps(unittest.TestCase):
    """data/release_metrics prose guard (v26)."""

    def test_multi_quarter_context_is_refused(self):
        from data import release_metrics as rm
        f = lambda s: rm._dollar_metric(s, rm._EPS_PATS, rm._EPS_BAND)  # noqa: E731
        # WBHC 2026-10-09 EX-99.1, verbatim
        self.assertIsNone(f(
            "Earnings Per Diluted Share for the nine-month period ended "
            "September 30, 2026, were $5.29, compared to $4.43 for the "
            "nine-month period ended September 30, 2025, an increase of 19.4%."))
        self.assertIsNone(f("For the nine months ended September 30, 2026, "
                            "diluted EPS was $5.29."))
        self.assertIsNone(f("Year-to-date diluted earnings per share were $5.29."))
        # Quarterly statements still parse; "year-ago" is not a year period.
        self.assertEqual(f("Diluted earnings per share for the third quarter "
                           "were $1.20, compared to $1.10 for the year-ago "
                           "quarter."), 1.20)
        self.assertEqual(f("Compared with the year-ago quarter, diluted EPS "
                           "was $1.20."), 1.20)

    def test_call_notice_detection(self):
        from data import release_metrics as rm
        notice = ("<p>WesBanco, Inc. to Host 2026 Third Quarter Earnings "
                  "Conference Call and Webcast on Thursday, October 22nd</p>"
                  "<p>Wheeling, WVa., October 2, 2026 – WesBanco, Inc. "
                  "announced today it will host a conference call …</p>")
        letter = ("<p>October 9, 2026 Dear Shareholders, As we enter the final "
                  "quarter of 2026, Wilson Bank &amp; Trust continues to build "
                  "… Net Income for the nine months ended September 30, 2026, "
                  "was $65.3 million …</p>")
        results = ("<p>Acme Bancorp Reports Third Quarter 2026 Net Income of "
                   "$12.1 Million; Conference Call Thursday</p>")
        self.assertTrue(rm.is_call_notice(notice))
        self.assertFalse(rm.is_call_notice(letter))
        self.assertFalse(rm.is_call_notice(results))
        # NEWT 2026-09-14 EX-99.1 (Item 2.02), opening verbatim
        dividend = ("<p>NewtekOne, Inc. Declares Dividends on Common Stock and "
                    "Series B Preferred Shares</p><p>Boca Raton, FL September "
                    "14, 2026 - NewtekOne, Inc. (the Company) (NASDAQ: NEWT) "
                    "announced that its Board of Directors declared a "
                    "quarterly cash dividend of $0.19 per share on the "
                    "Company's outstanding common stock.</p>")
        self.assertFalse(rm.states_results(notice))     # a notice, even if it
        self.assertFalse(rm.states_results(dividend))   # mentions "results"
        self.assertTrue(rm.states_results(letter))
        self.assertTrue(rm.states_results(results))


if __name__ == "__main__":
    unittest.main()
