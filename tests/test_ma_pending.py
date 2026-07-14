"""
Tests for data/ma_pending.py — Rule 425 pending-deal detection (§14; the
FHB/TriCo gap). All lookups mocked. Pins:

  • 425 episode detection: trailing cluster (gaps ≤180d), stale episodes
    (>540d) dropped — the completed/terminated legs own their outcomes
  • Subject Company legend parse: subject != self -> acquisition of the
    subject; subject == self -> sale, counterparty from the "transaction
    between A and B" legend, dropped when unparseable (never guessed)
  • comma-tolerant ratio forms incl. the live-verified FHB phrasing
    "2.095 First Hawaiian shares for each TriCo share"
  • computed value hand-math via the audited price/shares helpers
  • universe match must be UNIQUE or fields stay None
  • fetch failure -> ok=False (caller must not cache)
"""
import sys
import types
import unittest
from unittest.mock import patch

# Full house stub (see tests/test_audit_regressions.py): a minimal stub that
# wins the sys.modules setdefault race would break later suites needing
# st.fragment / streamlit.components.v1 at module load (the stub-rot trap,
# memory 2026-07-02).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)

FHB_CIK = 36377

# Live-verified FHB/TriCo 425 legend + PR phrasing (2026-07-13).
LEGEND_425 = ("Filed by: First Hawaiian, Inc. Pursuant to Rule 425 "
              "Subject Company: TriCo Bancshares Commission File No.: "
              "000-10661 This filing relates to the proposed transaction "
              "between First Hawaiian, Inc. (FHI) and TriCo Bancshares "
              "(TriCo) pursuant to the Agreement and Plan of Reorganization "
              "and Merger, dated as of July 12, 2026. TriCo's shareholders "
              "will receive 2.095 First Hawaiian shares for each TriCo "
              "share, representing $63.12 per share.")


def _filings(rows):
    return [{"form": f, "date": d, "accession": a, "doc": doc, "items": it}
            for f, d, a, doc, it in rows]


def _universe(entries):
    """entries: {ticker: (name, cert, cik)}"""
    return {t: {"name": n, "fdic_cert": c, "cik": k}
            for t, (n, c, k) in entries.items()}


UNIVERSE = _universe({
    "FHB": ("First Hawaiian", 17985, 36377),
    "TCBK": ("TriCo Bancshares", 21943, 356171),
})


class TestFindPendingDeals(unittest.TestCase):

    def _run(self, filings, texts_ok=(LEGEND_425, True), today="2026-07-14",
             shares=(31_910_590, "2026-05-01", True),
             price=(30.13, "2026-07-10", True), universe=None):
        from data import ma_pending
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date.fromisoformat(today)

        with patch("data.ma_pending.iter_submission_filings",
                   return_value=(filings, True)), \
             patch("data.ma_pending._accession_text",
                   return_value=texts_ok), \
             patch("data.ma_pending._shares_outstanding_asof",
                   return_value=shares), \
             patch("data.ma_pending._close_before", return_value=price), \
             patch("data.bank_universe.get_universe",
                   return_value=universe or UNIVERSE), \
             patch("data.ma_pending.time.sleep", lambda *_: None), \
             patch("data.ma_pending.date", _FakeDate):
            return ma_pending.find_pending_deals(FHB_CIK,
                                                 "First Hawaiian Bank")

    def test_fhb_trico_hand_math(self):
        rows, ok = self._run(_filings([
            ("425", "2026-07-13", "0001-26-1", "d4_425.htm", ""),
            ("8-K", "2026-07-13", "0001-26-0", "8k.htm", "2.02,7.01,8.01"),
        ]))
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["announce_date"], "2026-07-13")
        self.assertEqual(r["direction"], "acquisition")
        self.assertEqual(r["counterparty_name"], "TriCo Bancshares")
        self.assertEqual(r["counterparty_ticker"], "TCBK")
        self.assertEqual(r["counterparty_cert"], 21943)
        # 2.095 × $30.13 × 31,910,590 = $2,014,271,431 (hand; the PR's own
        # $63.12/share convention: 2.095 × 30.13 = 63.12).
        self.assertEqual(r["value_usd"], 2_014_271_431)
        self.assertEqual(r["value_basis"], "computed")
        self.assertIn("2.095", r["value_note"])
        self.assertEqual(r["target_cik"], 356171)

    def test_stale_episode_dropped(self):
        rows, ok = self._run(_filings([
            ("425", "2024-01-05", "0001-24-1", "x.htm", "")]))
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_episode_clustering_gap_breaks(self):
        # Old deal's 425s years ago + a fresh one: only the fresh episode.
        rows, ok = self._run(_filings([
            ("425", "2021-03-01", "0001-21-1", "old.htm", ""),
            ("425", "2026-07-13", "0001-26-1", "new.htm", "")]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["announce_date"], "2026-07-13")

    def test_subject_is_self_sale_direction(self):
        legend = ("Filed by: TriCo Bancshares Pursuant to Rule 425 "
                  "Subject Company: TriCo Bancshares Commission File No.: "
                  "000-10661 This filing relates to the proposed transaction "
                  "between First Hawaiian, Inc. (FHI) and TriCo Bancshares, "
                  "pursuant to the agreement.")
        from data import ma_pending
        with patch("data.ma_pending.iter_submission_filings",
                   return_value=(_filings([
                       ("425", "2026-07-13", "0001-26-2", "t.htm", "")]), True)), \
             patch("data.ma_pending._accession_text",
                   return_value=(legend, True)), \
             patch("data.bank_universe.get_universe", return_value=UNIVERSE), \
             patch("data.ma_pending.time.sleep", lambda *_: None):
            rows, ok = ma_pending.find_pending_deals(356171, "Tri Counties Bank")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sale")
        self.assertIn("First Hawaiian", rows[0]["counterparty_name"])

    def test_ambiguous_universe_match_stays_none(self):
        dup = _universe({
            "FHB": ("First Hawaiian", 17985, 36377),
            "TCBK": ("TriCo Bancshares", 21943, 356171),
            "TCB2": ("TriCo Community Bancorp", 999, 888),
        })
        rows, _ = self._run(_filings([
            ("425", "2026-07-13", "0001-26-1", "d.htm", "")]), universe=dup)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["counterparty_ticker"])
        self.assertIsNone(rows[0]["counterparty_cert"])

    def test_no_legend_never_guesses(self):
        rows, ok = self._run(_filings([
            ("425", "2026-07-13", "0001-26-1", "d.htm", "")]),
            texts_ok=("some 425 without the subject line", True))
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_fetch_failure_not_ok(self):
        rows, ok = self._run(_filings([
            ("425", "2026-07-13", "0001-26-1", "d.htm", "")]),
            texts_ok=(None, False))
        self.assertEqual(rows, [])
        self.assertFalse(ok)

    def test_no_425s_no_network(self):
        rows, ok = self._run(_filings([("8-K", "2026-07-13", "a", "b.htm", "")]))
        self.assertEqual(rows, [])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
