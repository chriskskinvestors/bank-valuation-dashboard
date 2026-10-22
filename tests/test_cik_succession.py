"""(2026-10-22) Ticker → CIK succession — data/bank_universe
.detect_successions / run_cik_succession_guard and data/bank_mapping.get_cik.

CLBK (Columbia Financial): its second-step conversion created a successor
registrant, Columbia Financial, Inc./MD/ (CIK 2115119; 8-K12B 2026-07-21,
Q2 release 2026-07-30, Q2 10-Q 2026-08-07). data/bank_map_resolved.json
pinned the predecessor 1723596, whose filings stop at the Q1 10-Q (filed
2026-05-11), so the card served March figures on 104M shares against a
post-conversion price and 269.5M shares (P/TBV read 1.07x). An audit of all
658 prod tickers against SEC's ticker file found CLBK the only mismatch.

Rule: a ticker whose SEC CIK differs from ours AND whose SEC CIK has a
10-Q/10-K for a later period is a succession; the nightly guard persists
it and get_cik serves the successor ahead of the curated map. A differing
CIK with no newer periodic filing is observe-only.

Run: python -m unittest tests.test_cik_succession
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from sqlalchemy import create_engine, text  # noqa: E402

import data.cache as cache  # noqa: E402
from data import bank_mapping as bm  # noqa: E402
from data import bank_universe as bu  # noqa: E402

OLD, NEW = 1723596, 2115119
PERIODIC = {
    OLD: {"form": "10-Q", "date": "2026-05-11", "report_date": "2026-03-31"},
    NEW: {"form": "10-Q", "date": "2026-08-07", "report_date": "2026-06-30"},
    93751: {"form": "10-Q", "date": "2026-07-30", "report_date": "2026-06-30"},
    2105965: None,                    # WCCB: Form 10 only, no 10-Q yet
}


class TestDetectSuccessions(unittest.TestCase):
    def test_clbk_successor_detected(self):
        out = bu.detect_successions({"CLBK": OLD, "STT": 93751},
                                    {"CLBK": NEW, "STT": 93751},
                                    PERIODIC.get)
        self.assertEqual(out, {"CLBK": {"cik": NEW, "old": OLD, "form": "10-Q",
                                        "report_date": "2026-06-30"}})

    def test_differing_cik_without_newer_filing_is_not_a_succession(self):
        self.assertEqual(bu.detect_successions({"X": OLD}, {"X": 2105965},
                                               PERIODIC.get), {})
        # Same period on both → nothing to prefer.
        same = {**PERIODIC, NEW: PERIODIC[OLD]}
        self.assertEqual(bu.detect_successions({"CLBK": OLD}, {"CLBK": NEW},
                                               same.get), {})

    def test_unmapped_or_unlisted_tickers_ignored(self):
        self.assertEqual(bu.detect_successions({"A": None, "B": 5},
                                               {"A": 9, "C": 7}, PERIODIC.get), {})


class TestGetCikServesTheSuccessor(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE cache (key VARCHAR(255) PRIMARY KEY, "
                "value TEXT NOT NULL, timestamp DOUBLE PRECISION NOT NULL)"))
        p = patch.object(cache, "_engine", eng)
        p.start()
        self.addCleanup(p.stop)
        bm._SUCCESSOR_MEMO["map"], bm._SUCCESSOR_MEMO["at"] = None, 0.0
        self.addCleanup(lambda: bm._SUCCESSOR_MEMO.update(map=None, at=0.0))

    def test_persisted_map_outranks_every_tier(self):
        with patch.dict(bm.BANK_MAP, {"CLBK": {"cik": OLD, "fdic_cert": 28834}}):
            self.assertEqual(bm.get_cik("CLBK"), OLD)
            cache.put("cik_successor:v1", {"date": "2026-10-22",
                                           "successors": {"CLBK": {"cik": NEW, "old": OLD}}})
            bm._SUCCESSOR_MEMO["map"] = None
            self.assertEqual(bm.get_cik("clbk"), NEW)
            self.assertEqual(bm.get_data_cik("CLBK") in (NEW, None), True)

    def test_no_map_is_the_curated_value(self):
        with patch.dict(bm.BANK_MAP, {"CLBK": {"cik": OLD, "fdic_cert": 28834}}):
            self.assertEqual(bm.get_cik("CLBK"), OLD)


class TestCuratedMapRepointed(unittest.TestCase):
    def test_clbk_curated_cik_is_the_successor(self):
        d = json.loads((REPO / "data/bank_map_resolved.json").read_text(encoding="utf-8"))
        self.assertEqual(d["CLBK"]["cik"], NEW)


if __name__ == "__main__":
    unittest.main()
