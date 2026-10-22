"""data.export.tables_transcripts — incremental FMP transcript export.

Offline: data.fmp_transcripts' two fetchers and the non-common filter are
stubbed, pacing is disabled. Pins `have` skip, newest-first budget spend across
banks, empty-content handling, no-coverage counting, and the column contract.
"""
import unittest
from datetime import date

import data.export.tables_transcripts as tt

# Two banks, 4 calls each, interleaved in time (newest first per bank, as
# get_transcript_dates returns them).
_CALLS = {
    "AAA": [
        {"quarter": 2, "year": 2026, "date": "2026-07-15"},
        {"quarter": 1, "year": 2026, "date": "2026-04-15"},
        {"quarter": 4, "year": 2025, "date": "2026-01-15"},
        {"quarter": 3, "year": 2025, "date": "2025-10-15"},
    ],
    "BBB": [
        {"quarter": 2, "year": 2026, "date": "2026-07-20"},
        {"quarter": 1, "year": 2026, "date": "2026-04-10"},
        {"quarter": 4, "year": 2025, "date": "2026-01-10"},
        {"quarter": 3, "year": 2025, "date": "2025-10-10"},
    ],
}


class _Stubbed(unittest.TestCase):
    def setUp(self):
        self._orig = (tt.fmp_transcripts.get_transcript_dates,
                      tt.fmp_transcripts.get_transcript,
                      tt._noncommon, tt._MIN_INTERVAL_S)
        self.calls = dict(_CALLS)
        self.empty: set = set()          # (t, y, q) whose content is empty
        self.fetch_log: list = []
        tt._MIN_INTERVAL_S = 0.0
        tt._noncommon = lambda: {"AAAP"}
        tt.fmp_transcripts.get_transcript_dates = \
            lambda t: list(self.calls.get(t.upper(), []))
        tt.fmp_transcripts.get_transcript = self._fake_tx

    def tearDown(self):
        (tt.fmp_transcripts.get_transcript_dates,
         tt.fmp_transcripts.get_transcript,
         tt._noncommon, tt._MIN_INTERVAL_S) = self._orig

    def _fake_tx(self, t, y, q):
        self.fetch_log.append((t, y, q))
        if (t, y, q) in self.empty:
            return None
        d = next(c["date"] for c in self.calls[t]
                 if (c["year"], c["quarter"]) == (y, q))
        return {"quarter": q, "year": y, "date": d,
                "content": f"{t} {y} Q{q} call text here"}


class TestColumns(_Stubbed):
    def test_frame_columns_equal_dict(self):
        df = tt.build_transcripts(["AAA"], have=set())
        self.assertEqual(list(df.columns), list(tt.COLUMNS_TRANSCRIPTS))
        self.assertEqual(list(tt.COLUMNS_TRANSCRIPTS)[0], "ticker")

    def test_empty_frame_keeps_columns(self):
        df = tt.build_transcripts([], have=set())
        self.assertEqual(list(df.columns), list(tt.COLUMNS_TRANSCRIPTS))
        self.assertEqual(len(df), 0)

    def test_row_values(self):
        df = tt.build_transcripts(["AAA"], have=set(), max_quarters=1)
        r = df.iloc[0]
        self.assertEqual((r.ticker, r.fiscal_year, r.quarter),
                         ("AAA", 2026, 2))
        self.assertEqual(r.call_date, date(2026, 7, 15))
        self.assertEqual(r.word_count, 6)   # "AAA 2026 Q2 call text here"
        self.assertEqual(r.source, "FMP")
        self.assertIsNotNone(r.fetched_at.tzinfo)


class TestIncremental(_Stubbed):
    def test_have_skipped_and_not_fetched(self):
        have = {("AAA", 2026, 2), ("aaa", 2026, 1)}   # case-insensitive
        df = tt.build_transcripts(["AAA"], have=have)
        self.assertEqual(df.attrs["skipped_have"], 2)
        self.assertNotIn(("AAA", 2026, 2), self.fetch_log)
        self.assertNotIn(("AAA", 2026, 1), self.fetch_log)
        self.assertEqual(set(zip(df.fiscal_year, df.quarter)),
                         {(2025, 4), (2025, 3)})

    def test_max_quarters_caps_lookback(self):
        df = tt.build_transcripts(["AAA"], have=set(), max_quarters=2)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.attrs["budget_exhausted"], False)

    def test_budget_spent_newest_first_across_banks(self):
        df = tt.build_transcripts(["AAA", "BBB"], have=set(), max_fetch=3)
        # The 3 newest calls overall, newer before older.
        self.assertEqual(self.fetch_log, [("BBB", 2026, 2), ("AAA", 2026, 2),
                                          ("AAA", 2026, 1)])
        self.assertEqual(list(df.call_date), [date(2026, 7, 20),
                                              date(2026, 7, 15),
                                              date(2026, 4, 15)])
        self.assertTrue(df.attrs["budget_exhausted"])
        self.assertEqual(df.attrs["fetched"], 3)
        self.assertEqual(df.attrs["not_fetched"], 5)

    def test_empty_content_not_emitted_but_counted(self):
        self.empty = {("AAA", 2026, 1)}
        df = tt.build_transcripts(["AAA"], have=set())
        self.assertEqual(df.attrs["missing_content"], 1)
        self.assertEqual(df.attrs["fetched"], 4)
        self.assertEqual(len(df), 3)
        self.assertNotIn((2026, 1), set(zip(df.fiscal_year, df.quarter)))

    def test_repeated_label_fetched_once(self):
        self.calls = {"AAA": _CALLS["AAA"][:1] * 2 + _CALLS["AAA"][1:]}
        df = tt.build_transcripts(["AAA"], have=set())
        self.assertEqual(len(df), 4)
        self.assertEqual(self.fetch_log.count(("AAA", 2026, 2)), 1)

    def test_no_coverage_bank_counted(self):
        df = tt.build_transcripts(["AAA", "ZZZ"], have=set())
        self.assertEqual(df.attrs["banks_no_coverage"], 1)
        self.assertEqual(df.attrs["banks_no_coverage_list"], ["ZZZ"])
        self.assertEqual(set(df.ticker), {"AAA"})

    def test_noncommon_excluded_and_dupes_collapsed(self):
        self.calls["AAAP"] = _CALLS["AAA"]
        df = tt.build_transcripts(["AAA", "aaa", "AAAP"], have=set())
        self.assertEqual(df.attrs["excluded_noncommon"], 1)
        self.assertEqual(len(df), 4)
        self.assertEqual(set(df.ticker), {"AAA"})


# FMP's real HFWA list (2026-10-22): "FY2024 Q4" is dated 2024-01-23 although
# the transcript is the Jan-2025 Q4-2024 call; "FY2023 Q1" is dated 2018-03-09.
_HFWA = [
    {"quarter": 1, "year": 2025, "date": "2025-04-24"},
    {"quarter": 4, "year": 2024, "date": "2024-01-23"},
    {"quarter": 3, "year": 2024, "date": "2024-10-24"},
    {"quarter": 2, "year": 2024, "date": "2024-07-25"},
    {"quarter": 1, "year": 2024, "date": "2024-04-25"},
    {"quarter": 4, "year": 2023, "date": "2024-01-25"},
    {"quarter": 3, "year": 2023, "date": "2023-10-19"},
    {"quarter": 2, "year": 2023, "date": "2023-07-20"},
    {"quarter": 1, "year": 2023, "date": "2018-03-09"},
]


class TestCallDateCadence(_Stubbed):
    def test_misdated_calls_render_na_and_are_flagged(self):
        self.calls["HFWA"] = _HFWA
        df = tt.build_transcripts(["HFWA"], have=set())
        by = {(r.fiscal_year, r.quarter): r.call_date
              for r in df.itertuples()}
        self.assertIsNone(by[(2024, 4)])            # never the wrong year
        self.assertIsNone(by[(2023, 1)])
        self.assertEqual(by[(2023, 4)], date(2024, 1, 25))   # genuine Q4 kept
        self.assertEqual(by[(2025, 1)], date(2025, 4, 24))
        self.assertEqual(df.attrs["call_date_na"], 2)
        self.assertIn(("HFWA", 2024, 4, "2024-01-23"),
                      df.attrs["call_date_na_list"])

    def test_misdated_call_queues_by_true_recency(self):
        # HFWA 2024Q4 really happened ~Jan 2025: it outranks a Oct-2024 call
        # even though FMP's raw date says Jan 2024.
        self.calls = {"HFWA": _HFWA,
                      "CCC": [{"quarter": 3, "year": 2024,
                               "date": "2024-10-01"}]}
        have = {("HFWA", c["year"], c["quarter"]) for c in _HFWA
                if (c["year"], c["quarter"]) != (2024, 4)}
        tt.build_transcripts(["CCC", "HFWA"], have=have, max_fetch=1)
        self.assertEqual(self.fetch_log, [("HFWA", 2024, 4)])

    def test_near_miss_misdate_caught_late_q4_bank_not_penalized(self):
        # Q1-Q3 calls ~day 20, Q4 calls ~day 50 (a late-Q4 reporter, real
        # cadence). FY2023 Q1 dated 03-15 (JPM-style, 36 days early) is n/a.
        rows = []
        for y in (2025, 2024, 2023):        # newest first, as listed
            rows += [{"quarter": 4, "year": y, "date": f"{y + 1}-02-19"},
                     {"quarter": 3, "year": y, "date": f"{y}-10-20"},
                     {"quarter": 2, "year": y, "date": f"{y}-07-21"},
                     {"quarter": 1, "year": y, "date": f"{y}-04-20"}]
        rows[-1]["date"] = "2023-03-15"     # 2023 Q1 mis-dated
        self.calls = {"EEE": rows}
        df = tt.build_transcripts(["EEE"], have=set())
        self.assertEqual(df.attrs["call_date_na_list"],
                         [("EEE", 2023, 1, "2023-03-15")])
        q4 = df[df.quarter == 4]
        self.assertTrue(q4.call_date.notna().all())   # late Q4s kept

    def test_too_few_calls_pass_dates_through(self):
        self.calls = {"DDD": _HFWA[:2]}    # 2 dated calls: nothing to check
        df = tt.build_transcripts(["DDD"], have=set())
        self.assertEqual(df.attrs["call_date_na"], 0)
        self.assertEqual(set(df.call_date), {date(2025, 4, 24),
                                             date(2024, 1, 23)})


if __name__ == "__main__":
    unittest.main()
