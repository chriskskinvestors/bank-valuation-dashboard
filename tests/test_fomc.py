"""
Tests for data/fomc.py — FOMC policy snapshot + SEP projections + gated dots.

All pure logic is pinned with no network: `data.fomc.fetch_series` is
monkeypatched to return synthetic (date, value) frames, and the dot-extraction
seam (`_extract_dots`) is monkeypatched so the gate logic is exercised
directly. No live FRED / PDF access in these tests.
"""
import unittest
from datetime import date

import pandas as pd

import data.fomc as fomc


def _frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """(date_str, value) rows → a (date[datetime64], value[float]) frame."""
    dates = pd.to_datetime([r[0] for r in rows])
    values = [r[1] for r in rows]
    return pd.DataFrame({"date": dates, "value": values})


class TestLastMove(unittest.TestCase):
    def test_detects_cut(self):
        # Upper bound held at 4.00, then stepped down to 3.75 on 2026-06-17.
        df = _frame([
            ("2026-03-01", 4.00),
            ("2026-04-01", 4.00),
            ("2026-05-01", 4.00),
            ("2026-06-17", 3.75),
            ("2026-06-18", 3.75),
        ])
        move = fomc._detect_last_move(df)
        self.assertEqual(move["direction"], "cut")
        # bps = round((new-old)*100) is signed per spec → negative for a cut.
        self.assertEqual(move["bps"], -25)
        self.assertEqual(move["date"], date(2026, 6, 17))

    def test_detects_hike(self):
        df = _frame([
            ("2026-01-01", 5.25),
            ("2026-02-01", 5.50),
        ])
        move = fomc._detect_last_move(df)
        self.assertEqual(move["direction"], "hike")
        self.assertEqual(move["bps"], 25)
        self.assertEqual(move["date"], date(2026, 2, 1))

    def test_picks_most_recent_change(self):
        # Two cuts; must report the later one (50 bps on 2026-05-01).
        df = _frame([
            ("2026-01-01", 4.50),
            ("2026-03-01", 4.25),
            ("2026-05-01", 3.75),
            ("2026-06-01", 3.75),
        ])
        move = fomc._detect_last_move(df)
        self.assertEqual(move["direction"], "cut")
        self.assertEqual(move["bps"], -50)
        self.assertEqual(move["date"], date(2026, 5, 1))

    def test_no_change_is_hold(self):
        df = _frame([("2026-01-01", 4.0), ("2026-02-01", 4.0)])
        move = fomc._detect_last_move(df)
        self.assertEqual(move, {"direction": "hold", "bps": 0, "date": None})

    def test_empty(self):
        move = fomc._detect_last_move(pd.DataFrame(columns=["date", "value"]))
        self.assertEqual(move, {"direction": "hold", "bps": 0, "date": None})


class TestNextMeeting(unittest.TestCase):
    def test_reference_dates(self):
        # Strictly after → 2026-06-17 returns the NEXT meeting, not itself.
        self.assertEqual(fomc.next_meeting(date(2026, 1, 1)), date(2026, 1, 28))
        self.assertEqual(fomc.next_meeting(date(2026, 6, 17)), date(2026, 7, 29))
        self.assertEqual(fomc.next_meeting(date(2026, 6, 16)), date(2026, 6, 17))
        self.assertEqual(fomc.next_meeting(date(2026, 11, 1)), date(2026, 12, 9))

    def test_none_remaining(self):
        self.assertIsNone(fomc.next_meeting(date(2026, 12, 31)))


class TestSepProjections(unittest.TestCase):
    def setUp(self):
        # Synthetic FRED responses keyed by series id. By-horizon series carry
        # observations at year-starts incl. a prior (2025) horizon that must be
        # filtered out (release is dated 2026 via FEDTARMDLR).
        self.frames = {
            # longer-run funds median: one obs per meeting → latest is the release
            "FEDTARMDLR": _frame([("2026-03-18", 3.0), ("2026-06-17", 3.1)]),
            "FEDTARCTHLR": _frame([("2026-06-17", 3.4)]),
            "FEDTARCTLLR": _frame([("2026-06-17", 2.8)]),
            "FEDTARRHLR": _frame([("2026-06-17", 3.9)]),
            "FEDTARRLLR": _frame([("2026-06-17", 2.4)]),
            # funds by-horizon (2025 stale obs must be dropped)
            "FEDTARMD": _frame([
                ("2025-01-01", 9.9),
                ("2026-01-01", 3.8), ("2027-01-01", 3.6), ("2028-01-01", 3.4),
            ]),
            "FEDTARCTL": _frame([
                ("2026-01-01", 3.6), ("2027-01-01", 3.1), ("2028-01-01", 2.9),
            ]),
            "FEDTARCTH": _frame([
                ("2026-01-01", 3.9), ("2027-01-01", 3.9), ("2028-01-01", 3.6),
            ]),
            "FEDTARRL": _frame([
                ("2026-01-01", 3.4), ("2027-01-01", 2.9), ("2028-01-01", 2.6),
            ]),
            "FEDTARRH": _frame([
                ("2026-01-01", 4.1), ("2027-01-01", 4.1), ("2028-01-01", 4.1),
            ]),
            # macro medians by horizon
            "GDPC1CTM": _frame([("2026-01-01", 2.15), ("2027-01-01", 1.9)]),
            "UNRATEMD": _frame([("2026-01-01", 4.3), ("2027-01-01", 4.4)]),
            "PCECTPICTM": _frame([("2026-01-01", 3.6)]),
            "JCXFECTM": _frame([("2026-01-01", 3.35)]),
        }
        self._orig = fomc.fetch_series
        fomc.fetch_series = lambda sid, years=5: self.frames.get(sid, pd.DataFrame(columns=["date", "value"]))

    def tearDown(self):
        fomc.fetch_series = self._orig

    def test_as_of_is_release_date(self):
        out = fomc.sep_projections()
        self.assertEqual(out["as_of"], date(2026, 6, 17))

    def test_funds_first_horizon(self):
        out = fomc.sep_projections()
        f0 = out["funds"][0]
        self.assertEqual(f0["horizon"], "2026")
        self.assertAlmostEqual(f0["median"], 3.8)
        self.assertAlmostEqual(f0["ct_low"], 3.6)
        self.assertAlmostEqual(f0["ct_high"], 3.9)
        self.assertAlmostEqual(f0["range_low"], 3.4)
        self.assertAlmostEqual(f0["range_high"], 4.1)

    def test_funds_horizons_filtered_and_ordered(self):
        out = fomc.sep_projections()
        horizons = [e["horizon"] for e in out["funds"]]
        # 2025 stale obs dropped; longer run appended last.
        self.assertEqual(horizons, ["2026", "2027", "2028", "Longer run"])

    def test_longer_run_entry(self):
        out = fomc.sep_projections()
        lr = out["funds"][-1]
        self.assertEqual(lr["horizon"], "Longer run")
        self.assertAlmostEqual(lr["median"], 3.1)
        self.assertAlmostEqual(lr["ct_low"], 2.8)
        self.assertAlmostEqual(lr["ct_high"], 3.4)
        self.assertAlmostEqual(lr["range_low"], 2.4)
        self.assertAlmostEqual(lr["range_high"], 3.9)

    def test_macro_medians(self):
        out = fomc.sep_projections()
        self.assertEqual(out["macro"]["gdp"][0], {"horizon": "2026", "median": 2.15})
        self.assertEqual(out["macro"]["unemployment"][0], {"horizon": "2026", "median": 4.3})
        self.assertEqual(out["macro"]["pce"], [{"horizon": "2026", "median": 3.6}])
        self.assertEqual(out["macro"]["core_pce"], [{"horizon": "2026", "median": 3.35}])

    def test_empty_series_is_none(self):
        # Drop the macro spine series → that field is None, no crash.
        self.frames["GDPC1CTM"] = pd.DataFrame(columns=["date", "value"])
        out = fomc.sep_projections()
        self.assertIsNone(out["macro"]["gdp"])


class TestSepDotsGate(unittest.TestCase):
    """Exercise the gate in sep_dots() directly via monkeypatched seams."""

    def setUp(self):
        self._orig_proj = fomc.sep_projections
        self._orig_extract = fomc._extract_dots
        self._orig_lib = fomc._pdf_lib
        fomc._pdf_lib = lambda: "pypdf"  # pretend a lib is present
        fomc.sep_projections = lambda: {
            "as_of": date(2026, 6, 17),
            "funds": [
                {"horizon": "2026", "median": 3.8},
                {"horizon": "2027", "median": 3.6},
                {"horizon": "2028", "median": 3.4},
                {"horizon": "Longer run", "median": 3.1},
            ],
            "macro": {},
        }

    def tearDown(self):
        fomc.sep_projections = self._orig_proj
        fomc._extract_dots = self._orig_extract
        fomc._pdf_lib = self._orig_lib

    def test_gate_rejects_median_mismatch(self):
        # Extracted dots whose 2026 median (3.6) is > 0.05 off the FRED 3.8.
        fomc._extract_dots = lambda as_of: {
            "2026": [3.5, 3.6, 3.7],   # median 3.6 vs FRED 3.8 → reject
            "2027": [3.6, 3.6, 3.6],
            "2028": [3.4, 3.4, 3.4],
            "Longer run": [3.1, 3.1, 3.1],
        }
        self.assertIsNone(fomc.sep_dots())

    def test_gate_passes_when_medians_match(self):
        fomc._extract_dots = lambda as_of: {
            "2026": [3.7, 3.8, 3.9],   # median 3.8
            "2027": [3.5, 3.6, 3.7],   # median 3.6
            "2028": [3.3, 3.4, 3.5],   # median 3.4
            "Longer run": [3.0, 3.1, 3.2],  # median 3.1
        }
        out = fomc.sep_dots()
        self.assertIsNotNone(out)
        self.assertEqual(out["as_of"], date(2026, 6, 17))
        self.assertEqual(out["dots"]["2026"], [3.7, 3.8, 3.9])

    def test_no_lib_returns_none(self):
        fomc._pdf_lib = lambda: None
        self.assertIsNone(fomc.sep_dots())

    def test_extraction_none_returns_none(self):
        fomc._extract_dots = lambda as_of: None
        self.assertIsNone(fomc.sep_dots())


class TestMedianHelper(unittest.TestCase):
    def test_odd_and_even(self):
        self.assertEqual(fomc._median([3.0, 1.0, 2.0]), 2.0)
        self.assertEqual(fomc._median([1.0, 2.0, 3.0, 4.0]), 2.5)


class TestStatementDate(unittest.TestCase):
    def test_picks_latest_on_or_before_today(self):
        # Day after a meeting → that meeting's date.
        self.assertEqual(fomc._statement_date(date(2026, 6, 20)), date(2026, 6, 17))
        # On a meeting day → that day (<= is inclusive).
        self.assertEqual(fomc._statement_date(date(2026, 6, 17)), date(2026, 6, 17))
        # Between meetings → the most recent prior one.
        self.assertEqual(fomc._statement_date(date(2026, 4, 28)), date(2026, 3, 18))
        # Late in the year → the December meeting.
        self.assertEqual(fomc._statement_date(date(2026, 12, 31)), date(2026, 12, 9))

    def test_before_first_meeting_is_none(self):
        self.assertIsNone(fomc._statement_date(date(2026, 1, 1)))
        self.assertIsNone(fomc._statement_date(date(2026, 1, 27)))


_CANNED_STATEMENT_HTML = """
<html><body>
  <div class="col-md-8 col-xs-12">
    <p>Recent indicators suggest that economic activity has continued to expand.</p>
    <p>The Committee decided to lower the target range for the federal funds
       rate to 3-1/2 to 3-3/4 percent.</p>
    <p>In assessing the appropriate stance of monetary policy, the Committee
       will continue to monitor incoming information.</p>
    <p>Voting for the monetary policy action were: Jerome H. Powell, Chair;
       John C. Williams, Vice Chair; and Michael S. Barr.</p>
    <p>Voting against the action was: Christopher J. Waller.</p>
    <p>Implementation Note issued June 17, 2026</p>
    <p>Last Update: June 17, 2026</p>
    <p>Share</p>
  </div>
</body></html>
"""


class TestParseStatementHtml(unittest.TestCase):
    def setUp(self):
        self.dt = date(2026, 6, 17)
        self.url = fomc._statement_url(self.dt)
        self.out = fomc._parse_statement_html(_CANNED_STATEMENT_HTML, self.url, self.dt)

    def test_returns_dict_shape(self):
        self.assertIsNotNone(self.out)
        self.assertEqual(self.out["date"], self.dt)
        self.assertEqual(self.out["url"], self.url)

    def test_substantive_paragraphs_kept_in_order(self):
        paras = self.out["paragraphs"]
        self.assertEqual(len(paras), 3)
        self.assertTrue(paras[0].startswith("Recent indicators"))
        self.assertIn("3-1/2 to 3-3/4 percent", paras[1])
        self.assertTrue(paras[2].startswith("In assessing"))

    def test_boilerplate_excluded(self):
        joined = " ".join(self.out["paragraphs"])
        self.assertNotIn("Last Update", joined)
        self.assertNotIn("Implementation Note", joined)
        self.assertNotEqual(self.out["paragraphs"][-1], "Share")

    def test_vote_captured(self):
        self.assertIsNotNone(self.out["vote"])
        self.assertTrue(self.out["vote"].startswith("Voting for the monetary policy action were"))
        # The "Voting against" line is appended to the same string.
        self.assertIn("Voting against", self.out["vote"])
        self.assertIn("Christopher J. Waller", self.out["vote"])
        # Vote lines must not leak into the prose list.
        self.assertFalse(any("Voting for" in p for p in self.out["paragraphs"]))

    def test_article_id_fallback_div(self):
        html = '<div id="article"><p>The Committee held the target range steady.</p></div>'
        out = fomc._parse_statement_html(html, self.url, self.dt)
        self.assertIsNotNone(out)
        self.assertEqual(out["paragraphs"], ["The Committee held the target range steady."])
        self.assertIsNone(out["vote"])

    def test_empty_html_is_none(self):
        self.assertIsNone(fomc._parse_statement_html("", self.url, self.dt))

    def test_garbage_html_is_none(self):
        # No article container at all → None.
        self.assertIsNone(
            fomc._parse_statement_html("<html><body><p>hi</p></body></html>", self.url, self.dt)
        )

    def test_article_with_no_substantive_paragraphs_is_none(self):
        html = '<div id="article"><p>Share</p><p>Last Update: x</p></div>'
        self.assertIsNone(fomc._parse_statement_html(html, self.url, self.dt))

    def test_date_header_excluded(self):
        # A bare "Month D, YYYY" header (and "... Share" variant) is dropped.
        joined = " ".join(self.out["paragraphs"])
        self.assertNotIn("June 17, 2026", joined)


class TestVoteTallyFallback(unittest.TestCase):
    """The 'approved ... by an N-0 vote' page format (no roster line)."""

    def setUp(self):
        self.dt = date(2026, 6, 17)
        self.url = fomc._statement_url(self.dt)
        # Mirrors the live June 2026 page: date header, release line, a vote
        # tally sentence, and substantive prose — no "Voting for ... were" line.
        html = """
        <div id="article">
          <p>June 17, 2026</p>
          <p>For release at 2:00 p.m. EDT Share</p>
          <p>The Federal Open Market Committee approved the following statement
             for release by a 12-0 vote:</p>
          <p>The Committee decided to maintain the target range for the federal
             funds rate at 3-1/2 to 3-3/4 percent.</p>
          <p>Implementation Note issued June 17, 2026</p>
        </div>
        """
        self.out = fomc._parse_statement_html(html, self.url, self.dt)

    def test_vote_tally_captured_as_vote(self):
        self.assertIsNotNone(self.out["vote"])
        self.assertIn("12-0 vote", self.out["vote"])

    def test_vote_tally_stays_in_prose(self):
        # The tally sentence is genuine prose → also present in paragraphs.
        self.assertTrue(any("12-0 vote" in p for p in self.out["paragraphs"]))

    def test_header_and_release_and_impl_note_excluded(self):
        joined = " ".join(self.out["paragraphs"])
        self.assertNotIn("June 17, 2026", joined)
        self.assertNotIn("For release at", joined)
        self.assertNotIn("Implementation Note", joined)

    def test_mojibake_dash_still_matches(self):
        # En-dash that arrived mojibake'd ("12 \xe2 0") must still be detected.
        html = (
            '<div id="article"><p>The FOMC approved the following statement for '
            'release by a 12 â 0 vote:</p><p>Body prose paragraph here.</p></div>'
        )
        out = fomc._parse_statement_html(html, self.url, self.dt)
        self.assertIsNotNone(out["vote"])
        self.assertIn("vote", out["vote"].lower())


if __name__ == "__main__":
    unittest.main()
