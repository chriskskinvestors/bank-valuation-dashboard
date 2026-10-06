"""No intangible tag at the balance sheet: unknown, not silently 0.

CFR (Cullen/Frost) tagged Goodwill $654,952K every quarter through
2023-06-30 and then folded it into other assets — its 2026-06-30 10-Q
(0000039263-26-000046) has no goodwill line, while Frost Bank's FDIC
INTANGW is $652,667K. The resolver's no-tag branch returned 0.0, so the
reconstruction served TBV/share $72.04 (= common equity $4,477,191K ÷
62,148,785 cover shares) and holdco TCE $4,477.2M with $655M of goodwill
never deducted.

Pins (real companyfacts rows; fail before / pass after):
  1. CFR shape → intangible_adjustment None, tce_holdco None, TBV/share
     None on BOTH the display and provenance paths; flag set.
  2. AVBH (Avidbank: no intangible tag ever; FDIC INTANGW 0 / INTAN 0; its
     2026-06-30 10-Q never mentions goodwill) → 0.0, TBV/share
     = $296,185K ÷ 10,982,764 = $26.968 — a genuinely intangible-free
     bank must NOT go n/a — flagged for the caller's FDIC cross-check.
  3. UWHR shape (last Goodwill $987K at 2012-12-31, gone today: FDIC
     INTANGW 0, no goodwill in its 2026 10-Q) → an ancient lapse stays 0.0.
  4. An explicit Goodwill = 0 as the newest fact is a genuine zero.
"""
import unittest
from unittest.mock import patch

from tests import _streamlit_stub

_streamlit_stub.install()

import data.sec_client as sc  # noqa: E402


def _row(end, val, accn, filed, form="10-Q"):
    return {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}


def _usd(*rows):
    return {"units": {"USD": list(rows)}}


CFR_Q2 = ("0000039263-26-000046", "2026-07-30")
CFR_FACTS = {"facts": {"us-gaap": {
    "Goodwill": _usd(
        _row("2022-12-31", 654_952_000, "0000039263-23-000064", "2023-07-27"),
        _row("2023-03-31", 654_952_000, "0000039263-23-000043", "2023-04-27"),
        _row("2023-06-30", 654_952_000, "0000039263-23-000064", "2023-07-27")),
    "FiniteLivedIntangibleAssetsNet": _usd(
        _row("2023-06-30", 208_000, "0000039263-23-000064", "2023-07-27")),
    "StockholdersEquity": _usd(_row("2026-06-30", 4_622_643_000, *CFR_Q2)),
    "Assets": _usd(_row("2026-06-30", 53_881_091_000, *CFR_Q2)),
    "PreferredStockValue": _usd(_row("2026-06-30", 145_452_000, *CFR_Q2)),
}, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
    _row("2026-07-30", 62_148_785, *CFR_Q2)]}}}}}

AVBH_Q2 = ("0001437749-26-027372", "2026-08-12")
AVBH_FACTS = {"facts": {"us-gaap": {
    "StockholdersEquity": _usd(_row("2026-06-30", 296_185_000, *AVBH_Q2)),
    "Assets": _usd(_row("2026-06-30", 2_660_440_000, *AVBH_Q2)),
    "CommonStockSharesOutstanding": {"units": {"shares": [
        _row("2026-06-30", 10_982_764, *AVBH_Q2)]}},
}, "dei": {}}}


def _with_goodwill(facts, *rows):
    ug = dict(facts["facts"]["us-gaap"], Goodwill=_usd(*rows))
    return {"facts": {"us-gaap": ug, "dei": facts["facts"]["dei"]}}


def _display(facts):
    with patch.object(sc, "fetch_company_facts", return_value=facts):
        return sc.get_latest_fundamentals(1)


def _trace(facts):
    with patch.object(sc, "fetch_company_facts", return_value=facts):
        return sc.get_fundamentals_with_provenance(1)


class TestLapsedGoodwillIsUnknown(unittest.TestCase):
    def test_cfr_display_path_renders_na(self):
        r = _display(CFR_FACTS)
        self.assertIsNone(r["intangible_adjustment"])
        self.assertIsNone(r["tce_holdco"])                     # was 4,477.2M
        self.assertIsNone(r["tangible_book_value_per_share"])  # was 72.04
        self.assertTrue(r["intangibles_untagged"])
        self.assertFalse(r["tce_goodwill_prior"])

    def test_cfr_provenance_path_matches(self):
        t = _trace(CFR_FACTS)
        self.assertIsNone(t["intangible_adjustment"]["value"])
        self.assertIsNone(t["tangible_book_value_per_share"]["value"])


class TestGenuineZeroStaysZero(unittest.TestCase):
    def test_avbh_never_tagged_keeps_tbvps(self):
        r = _display(AVBH_FACTS)
        self.assertEqual(r["intangible_adjustment"], 0.0)
        self.assertAlmostEqual(r["tangible_book_value_per_share"],
                               296_185_000 / 10_982_764, places=6)  # 26.968
        self.assertAlmostEqual(r["tangible_book_value_per_share"], 26.968, places=3)
        self.assertEqual(r["tce_holdco"], 296_185_000)
        self.assertTrue(r["intangibles_untagged"])

    def test_uwhr_ancient_lapse_stays_zero(self):
        facts = _with_goodwill(AVBH_FACTS, _row(
            "2012-12-31", 987_000, "0001193125-13-132550", "2013-03-28", "10-K"))
        self.assertFalse(sc._goodwill_tag_lapsed(facts))
        self.assertEqual(_display(facts)["intangible_adjustment"], 0.0)

    def test_explicit_zero_goodwill_is_genuine(self):
        facts = _with_goodwill(
            CFR_FACTS,
            _row("2023-06-30", 654_952_000, "0000039263-23-000064", "2023-07-27"),
            _row("2023-09-30", 0, "0000039263-23-000080", "2023-10-27"))
        self.assertFalse(sc._goodwill_tag_lapsed(facts))
        self.assertEqual(_display(facts)["intangible_adjustment"], 0.0)

    def test_tagged_goodwill_clears_flag(self):
        facts = _with_goodwill(AVBH_FACTS, _row("2026-06-30", 5_000_000, *AVBH_Q2))
        r = _display(facts)
        self.assertEqual(r["intangible_adjustment"], 5_000_000)
        self.assertFalse(r["intangibles_untagged"])


if __name__ == "__main__":
    unittest.main()
