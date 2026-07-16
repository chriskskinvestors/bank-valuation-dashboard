"""
Tests for data/ma_pending.py — pending-deal detection WITH open-status
verification (§14 rebuild, 2026-07-16; the first cut was reverted for
showing CLOSED deals as pending). All lookups mocked — the cash leg
(find_open_announcements) is patched so no test touches the network. Pins:

  • 425 episode detection: trailing cluster (gaps ≤180d), stale episodes
    (>540d) dropped — the completed/terminated legs own their outcomes
  • Subject Company legend parse: subject != self -> acquisition of the
    subject; subject == self -> sale; dropped when unparseable
  • comma-tolerant ratio forms incl. the live-verified FHB phrasing
  • computed value hand-math via the audited price/shares helpers
  • universe match must be UNIQUE or fields stay None
  • fetch failure -> ok=False (caller must not cache)

  OPEN-STATUS GATE (the revert's rebuild bar — every case ground-truthed
  against primary sources 2026-07-15/16):
  • a later 8-K Item 2.01 naming the counterparty DROPS the row (CLST/
    Lakeside, PB/Stellar, FULT/Blue Foundry class)
  • an Item 8.01 naming it in COMPLETED tense drops it too — item
    discipline varies by filer (HOPE filed the Territorial completion
    under 8.01 only, no 2.01 anywhere); prospective "upon completion"
    boilerplate must NOT drop an open deal
  • the resolving filing may be dated shortly BEFORE our announce anchor
    (a post-close 8-K latched as the announcement — UMBF/Heartland class)
  • an all-generic counterparty name verifies via the full phrase; a name
    with no usable needle at all is DROPPED (unprovable open status is
    never shown)
  • a resolver fetch failure drops the row AND makes ok=False
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

COMPLETION_801 = ("First Hawaiian, Inc. today announced the completion of "
                  "the merger of TriCo Bancshares with and into First "
                  "Hawaiian, effective today.")

PROSPECTIVE_801 = ("First Hawaiian, Inc. provided an update on its proposed "
                   "transaction with TriCo Bancshares. Upon completion of "
                   "the transaction, the combined company will have "
                   "approximately $22 billion in assets.")


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


class _Harness(unittest.TestCase):
    """Shared offline harness: filings list feeds BOTH the 425 episode scan
    and the open-status resolver; texts maps doc filename -> text (a plain
    (text, ok) tuple applies to every fetch)."""

    def _run(self, filings, texts=(LEGEND_425, True), today="2026-07-14",
             shares=(31_910_590, "2026-05-01", True),
             price=(30.13, "2026-07-10", True), universe=None,
             cash_rows=([], True), cik=FHB_CIK,
             subject="First Hawaiian Bank"):
        from data import ma_pending
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date.fromisoformat(today)

        if isinstance(texts, tuple):
            fetch = lambda cik_, acc, doc, _t=texts: _t
        else:
            fetch = lambda cik_, acc, doc: texts[doc]

        with patch("data.ma_pending.iter_submission_filings",
                   return_value=(filings, True)), \
             patch("data.ma_pending._accession_text", side_effect=fetch), \
             patch("data.ma_pending._shares_outstanding_asof",
                   return_value=shares), \
             patch("data.ma_pending._close_before", return_value=price), \
             patch("data.ma_pending.find_open_announcements",
                   return_value=cash_rows), \
             patch("data.bank_universe.get_universe",
                   return_value=universe or UNIVERSE), \
             patch("data.ma_pending.time.sleep", lambda *_: None), \
             patch("data.ma_pending.date", _FakeDate):
            return ma_pending.find_pending_deals(cik, subject)


class TestFindPendingDeals(_Harness):

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
        rows, ok = self._run(_filings([
            ("425", "2026-07-13", "0001-26-2", "t.htm", "")]),
            texts=(legend, True), cik=356171, subject="Tri Counties Bank")
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
            texts=("some 425 without the subject line", True))
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_fetch_failure_not_ok(self):
        rows, ok = self._run(_filings([
            ("425", "2026-07-13", "0001-26-1", "d.htm", "")]),
            texts=(None, False))
        self.assertEqual(rows, [])
        self.assertFalse(ok)

    def test_no_425s_no_pending(self):
        rows, ok = self._run(_filings([("8-K", "2026-07-13", "a", "b.htm", "")]))
        self.assertEqual(rows, [])
        self.assertTrue(ok)


class TestOpenStatusGate(_Harness):
    """The rebuild bar: presence of announcement/425 filings is NOT proof a
    deal is open — each candidate needs a clean bill against the filer's
    later resolving 8-Ks."""

    _BASE = [("425", "2026-05-01", "0001-26-1", "d4_425.htm", "")]

    def test_later_201_naming_counterparty_drops_row(self):
        # CLST/Lakeside class: deal announced, then completed via 2.01.
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "close.htm", "2.01,9.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "close.htm": (COMPLETION_801, True)})
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_later_801_completed_tense_drops_row(self):
        # HOPE/Territorial class: completion filed under 8.01 ONLY.
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "close.htm", "8.01,9.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "close.htm": (COMPLETION_801, True)})
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_prospective_801_keeps_open_deal(self):
        # "Upon completion of the transaction" boilerplate in a later 8.01
        # must NOT kill a live deal.
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "update.htm", "8.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "update.htm": (PROSPECTIVE_801, True)})
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["counterparty_name"], "TriCo Bancshares")

    def test_201_shortly_before_anchor_drops_row(self):
        # UMBF/Heartland class: our announce anchor was a post-close 8-K —
        # the completion 2.01 sits shortly BEFORE it (slack window).
        rows, ok = self._run(_filings([
            ("425", "2026-05-01", "0001-26-1", "d4_425.htm", ""),
            ("8-K", "2026-04-20", "0001-26-8", "close.htm", "2.01,9.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "close.htm": (COMPLETION_801, True)})
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_201_not_naming_counterparty_keeps_row(self):
        # A 2.01 completing a DIFFERENT deal must not kill this one.
        other = ("First Hawaiian, Inc. completed its previously announced "
                 "acquisition of Someother Bancorp, Inc.")
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "other.htm", "2.01,9.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "other.htm": (other, True)})
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)

    def test_resolver_fetch_failure_drops_row_not_ok(self):
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "close.htm", "2.01")]),
            texts={"d4_425.htm": (LEGEND_425, True),
                   "close.htm": (None, False)})
        self.assertEqual(rows, [])
        self.assertFalse(ok)

    def test_generic_name_resolves_via_phrase(self):
        # PB/American class: every token generic — the FULL PHRASE in a
        # later 2.01 still resolves it.
        legend = ("Filed by: First Hawaiian, Inc. Pursuant to Rule 425 "
                  "Subject Company: American Bank Holding Company "
                  "Commission File No.: 001-00000 transaction between "
                  "First Hawaiian, Inc. (FHI) and American Bank Holding "
                  "Company pursuant to the agreement.")
        close = ("First Hawaiian, Inc. completed its previously announced "
                 "acquisition of American Bank Holding Company today.")
        rows, ok = self._run(_filings(self._BASE + [
            ("8-K", "2026-06-20", "0001-26-9", "close.htm", "2.01,9.01")]),
            texts={"d4_425.htm": (legend, True), "close.htm": (close, True)})
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_no_usable_needle_drops_row(self):
        # Unprovable open status is never shown: a counterparty name with
        # no brand token AND no multi-word phrase cannot be verified.
        legend = ("Filed by: First Hawaiian, Inc. Pursuant to Rule 425 "
                  "Subject Company: Bancorp Commission File No.: 001-00000 "
                  "transaction between First Hawaiian, Inc. (FHI) and "
                  "Bancorp pursuant to the agreement.")
        rows, ok = self._run(_filings(list(self._BASE)),
                             texts=(legend, True))
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_cash_leg_rows_gated_too(self):
        # A cash-leg candidate (Lakeside class) is dropped by a later 2.01.
        cash = ([{"announce_date": "2026-05-01", "direction": "acquisition",
                  "counterparty_name": "Lakeside Bancshares, Inc",
                  "counterparty_cik": None, "value_usd": 41_100_000,
                  "value_basis": "stated", "value_note": None,
                  "target_cik": None, "announce_url": "u",
                  "accession": "a"}], True)
        close = ("First Hawaiian, Inc. completed its previously announced "
                 "acquisition of Lakeside Bancshares, Inc. today.")
        rows, ok = self._run(_filings([
            ("8-K", "2026-06-20", "0001-26-9", "close.htm", "2.01,9.01")]),
            texts={"close.htm": (close, True)}, cash_rows=cash)
        self.assertEqual(rows, [])
        self.assertTrue(ok)

    def test_cash_leg_open_row_kept(self):
        cash = ([{"announce_date": "2026-05-01", "direction": "acquisition",
                  "counterparty_name": "Lakeside Bancshares, Inc",
                  "counterparty_cik": None, "value_usd": 41_100_000,
                  "value_basis": "stated", "value_note": None,
                  "target_cik": None, "announce_url": "u",
                  "accession": "a"}], True)
        rows, ok = self._run([], cash_rows=cash)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["counterparty_name"],
                         "Lakeside Bancshares, Inc")
        self.assertEqual(rows[0]["value_usd"], 41_100_000)

    def test_425_wins_dedup_over_cash_leg(self):
        cash = ([{"announce_date": "2026-05-02", "direction": "acquisition",
                  "counterparty_name": "TriCo Bancshares",
                  "counterparty_cik": None, "value_usd": None,
                  "value_basis": None, "value_note": None,
                  "target_cik": None, "announce_url": "u",
                  "accession": "a"}], True)
        rows, ok = self._run(_filings(list(self._BASE)), cash_rows=cash)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)                    # merged, not two
        self.assertEqual(rows[0]["value_basis"], "computed")  # 425 row won


class TestResolvingNeedle(unittest.TestCase):

    def test_needle_forms(self):
        from data.ma_pending import _resolving_needle
        self.assertEqual(_resolving_needle("TriCo Bancshares"), "trico")
        self.assertEqual(_resolving_needle("American Bank Holding Company"),
                         "american bank holding company")
        self.assertIsNone(_resolving_needle("Bancorp"))
        self.assertIsNone(_resolving_needle(""))
        self.assertIsNone(_resolving_needle(None))


if __name__ == "__main__":
    unittest.main()
