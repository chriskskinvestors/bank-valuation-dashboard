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

from data.earnings_call import parse_call_info, mid_label  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
