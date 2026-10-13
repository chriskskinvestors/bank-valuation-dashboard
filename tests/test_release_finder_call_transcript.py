"""The release finder steps past an Item 2.02 call TRANSCRIPT (2026-10-13).

PEBO (2026-07-27, "Conference Call Script 2nd Quarter 2026 Results") and BCBP
(2026-08-07, "TRANSCRIPT 08-03-2026 … Second Quarter Earnings Call") furnished
their call transcripts under Item 2.02 days after the release. As the newest
2.02, the transcript was selected: prod's release_metrics served PEBO NCO only
and BCBP nothing, shadowing the real releases. SBFG's Q4-25 transcript
(2026-02-02) read "a 27% lift over our 2024 EPS of $1.72" as the quarter's
diluted EPS (the release: $0.63) — a plausible-wrong number.

Fixtures (tests/fixtures/release_notices): the PEBO and SBFG transcripts are
the real exhibits' first 6,000 / 4,000 chars of text — enough to reproduce
the figures they yield (so the call-NOTICE rule, which needs a figure-less
exhibit, does not already skip them); everything else is the real first 600
chars (openings_2026-10-13.json, from a scan of 1,213 2.02 exhibit openings
across 347 SEC banks). 8-K rows are the banks' real submissions rows.

Run: python -m unittest tests.test_release_finder_call_transcript
"""
import html
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.ir_provider as ip  # noqa: E402
from tests.test_release_finder_call_notice import (  # noqa: E402
    FIX, MVBF_RELEASE_Q2, NOTICE_Q3, _finder, _subs)

_OPEN = json.loads((FIX / "openings_2026-10-13.json").read_text(encoding="utf-8"))


def _doc(key_prefix: str) -> str:
    (key,) = [k for k in _OPEN if k.startswith(key_prefix)]
    return f"<p>{html.escape(_OPEN[key], quote=False)}</p>"


PEBO_TRANSCRIPT = (FIX / "PEBO_2026-07-27_transcript_excerpt.htm").read_text(
    encoding="utf-8")
PEBO_RELEASE = _doc("PEBO_2026-07-21")
BCBP_TRANSCRIPT = _doc("BCBP_2026-08-07")
BCBP_RELEASE = _doc("BCBP_2026-08-03")
SBFG_TRANSCRIPT = (FIX / "SBFG_2026-02-02_transcript_excerpt.htm").read_text(
    encoding="utf-8")
SBFG_RELEASE = _doc("SBFG_2026-01-30")


class TestIsCallTranscript(unittest.TestCase):
    def test_transcripts_yield_figures(self):
        # Why the notice rule can't catch them — and SBFG's is plain wrong:
        # 2024's full-year $1.72 read as the quarter's diluted EPS ($0.63).
        from data.release_metrics import extract_release_metrics
        # (Since release_metrics v35 the sentence-scoped period check — "For
        # the full year, our GAAP EPS of $2.19 … over our 2024 EPS of $1.72"
        # — refuses it; PEBO's transcript still yields a figure.)
        sbfg = extract_release_metrics(SBFG_TRANSCRIPT, "2025-12-31")
        self.assertIsNone(sbfg["eps_diluted"])
        pebo = extract_release_metrics(PEBO_TRANSCRIPT, "2026-06-30")
        self.assertEqual(pebo["nco_ratio"], 0.31)
        for doc, d in ((SBFG_TRANSCRIPT, "2026-02-02"),
                       (PEBO_TRANSCRIPT, "2026-07-27")):
            self.assertFalse(ip._is_notice_exhibit(
                {"html": doc, "filed_date": d}))

    def test_transcripts(self):
        for doc in (PEBO_TRANSCRIPT, BCBP_TRANSCRIPT, SBFG_TRANSCRIPT):
            self.assertTrue(ip._is_call_transcript(doc), doc[:120])

    def test_releases_and_notices_are_not_transcripts(self):
        # STT's "description" and INBK's "Company to hold earnings call
        # today" are the near-misses a wider marker would trip.
        for doc in (PEBO_RELEASE, BCBP_RELEASE, SBFG_RELEASE,
                    _doc("STT_2026-07-16"), _doc("INBK_2026-07-30"),
                    MVBF_RELEASE_Q2, NOTICE_Q3):
            self.assertFalse(ip._is_call_transcript(doc), doc[:120])

    def test_marker_must_open_the_document(self):
        # A release that offers a transcript further down is still a release.
        tail = PEBO_RELEASE.replace(
            "</p>", " " + "x " * 200 + "A transcript of the call will be "
                    "available on our website.</p>")
        self.assertFalse(ip._is_call_transcript(tail))


class TestFinderSkipsTranscript(unittest.TestCase):
    def test_pebo_release_not_transcript(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-07-27", "0000318300-26-000187"),
            ("8-K", "2.02,9.01", "2026-07-21", "0000318300-26-000183"),
        ])
        rel, _ = _finder(subs, {"000031830026000187": PEBO_TRANSCRIPT,
                                "000031830026000183": PEBO_RELEASE})
        self.assertEqual(rel["accession"], "000031830026000183")

    def test_bcbp_release_not_transcript(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-08-07", "0001193125-26-339642"),
            ("8-K", "2.02,9.01", "2026-08-03", "0001193125-26-329536"),
        ])
        rel, _ = _finder(subs, {"000119312526339642": BCBP_TRANSCRIPT,
                                "000119312526329536": BCBP_RELEASE})
        self.assertEqual(rel["accession"], "000119312526329536")

    def test_sbfg_q4_release_not_transcript(self):
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-02-02", "0001213900-26-010673"),
            ("8-K", "2.02,9.01", "2026-01-30", "0001213900-26-010201"),
        ])
        rel, _ = _finder(subs, {"000121390026010673": SBFG_TRANSCRIPT,
                                "000121390026010201": SBFG_RELEASE})
        self.assertEqual(rel["accession"], "000121390026010201")

    def test_notice_then_transcript_then_release(self):
        # The worst case _MAX_202 = 3 covers: next quarter's notice, then
        # this quarter's transcript, ahead of the release (HBNC + PEBO shape).
        subs = _subs([
            ("8-K", "2.02,9.01", "2026-10-02", "0000318300-26-000200"),
            ("8-K", "2.02,9.01", "2026-07-27", "0000318300-26-000187"),
            ("8-K", "2.02,9.01", "2026-07-21", "0000318300-26-000183"),
        ])
        rel, _ = _finder(subs, {"000031830026000200": NOTICE_Q3,
                                "000031830026000187": PEBO_TRANSCRIPT,
                                "000031830026000183": PEBO_RELEASE})
        self.assertEqual(rel["accession"], "000031830026000183")


if __name__ == "__main__":
    unittest.main()
