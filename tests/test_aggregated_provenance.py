"""
(2026-08-17) Click-through provenance said "FDIC Call Report, cert <lead>" for
multi-charter aggregates.

data/cert_group.aggregate_records sums levels across a holdco's charters and
carries the LEAD charter's identity fields, but the provenance surfaces —
build_fdic_provenance, the _fdic_doc labels, and the source-trace calc
payloads — still presented the lead charter's single call report as THE source
of the group-summed number. That is exactly the plausible-wrong claim the
platform forbids.

Pins:
  1. an aggregated record (_charter_count > 1) discloses the consolidation —
     the exact "Consolidated across N bank charters" sentence, with the lead
     cert named — in the doc label, the calc payload/tooltip, and Source.notes;
  2. a single-charter record renders byte-identical output to before: same
     doc dict, same payload keys, no "Consolidated"/"note" anywhere;
  3. _charter_count of 1, missing, None, or NaN (DataFrame round-trip) is
     treated as single-charter.

Pure-function tests; no FDIC calls.

Run: python -m unittest tests.test_aggregated_provenance
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.fdic_client import build_fdic_provenance  # noqa: E402
from ui.financial_highlights import _agg_charter_count, _fdic_doc  # noqa: E402
from ui.source_trace import _calc_tooltip, fdic_calc  # noqa: E402

LEAD = 19629   # IBOC's lead charter (largest of its 5)

AGG = {"CERT": LEAD, "REPDTE": "2026-06-30", "ASSET": 17_289_901.0,
       "ROA": None, "_aggregated": True, "_charter_count": 5}

SINGLE = {"CERT": 3510, "REPDTE": "2026-06-30", "ASSET": 500_000.0,
          "ROA": 1.10}

NOTE = ("Consolidated across 5 bank charters (sum of call reports); "
        "link shows the lead charter (cert 19629)")

# The payload shape make_calc produced before the note field existed — the
# single-charter path must keep exactly this, no extra keys.
CALC_KEYS = {"metric", "value", "entity", "source", "asof", "unit", "ref",
             "definition", "terms", "op", "reported", "link"}


class TestAggregatedDisclosed(unittest.TestCase):
    def test_doc_label_discloses_consolidation(self):
        doc = _fdic_doc(LEAD, AGG["REPDTE"], AGG)
        self.assertEqual(doc["label"], f"6/30/2026 Call Report — {NOTE}")
        # The link may honestly keep pointing at the lead charter's facsimile.
        self.assertIn(f"id={LEAD}", doc["url"])

    def test_calc_payload_and_tooltip_disclose(self):
        calc = fdic_calc("Total assets", "ASSET", AGG, LEAD,
                         unit="$ in thousands", definition="Total assets.",
                         entity="IBOC", value="$17.3B")
        self.assertEqual(calc["note"], NOTE)
        self.assertIn(NOTE, _calc_tooltip(calc))

    def test_build_fdic_provenance_notes(self):
        src = build_fdic_provenance(LEAD, "ASSET", AGG["REPDTE"],
                                    charter_count=5)
        self.assertEqual(src.notes,
                         "Consolidated across 5 bank charters (sum of call "
                         "reports); cert 19629 is the lead charter")
        self.assertEqual(src.identifier, str(LEAD))


class TestSingleCharterUnchanged(unittest.TestCase):
    def test_doc_identical_with_and_without_record(self):
        before = _fdic_doc(3510, SINGLE["REPDTE"])          # pre-change call
        after = _fdic_doc(3510, SINGLE["REPDTE"], SINGLE)   # new call shape
        self.assertEqual(before, after)
        self.assertEqual(before["label"], "6/30/2026 Call Report")
        self.assertNotIn("Consolidated", before["label"])

    def test_calc_payload_shape_and_tooltip_unchanged(self):
        calc = fdic_calc("Total assets", "ASSET", SINGLE, 3510,
                         unit="$ in thousands", definition="Total assets.",
                         entity="Test Bank", value="$500M")
        self.assertEqual(set(calc.keys()), CALC_KEYS)   # no "note" key at all
        self.assertNotIn("Consolidated", _calc_tooltip(calc))

    def test_build_fdic_provenance_default_is_empty_notes(self):
        src = build_fdic_provenance(3510, "ASSET", SINGLE["REPDTE"])
        self.assertEqual(src.notes, "")


class TestCharterCountEdgeCases(unittest.TestCase):
    def test_count_one_missing_none_or_nan_is_single(self):
        for rec in ({"_charter_count": 1}, {}, {"_charter_count": None},
                    {"_charter_count": float("nan")}, None):
            self.assertEqual(_agg_charter_count(rec), 1, rec)
            doc = _fdic_doc(3510, "2026-06-30", rec)
            self.assertNotIn("Consolidated", doc["label"])

    def test_string_count_from_json_roundtrip(self):
        self.assertEqual(_agg_charter_count({"_charter_count": "5"}), 5)


if __name__ == "__main__":
    unittest.main()


class TestEarningsDirectCallPassesRecord(unittest.TestCase):
    """(2026-08-17 follow-up) ui/earnings' Key Reported Metrics built its
    call-report doc link with the two-arg _fdic_doc, so a multi-charter
    bank's link stayed unlabeled while every fdic_calc tooltip next to it
    disclosed. Structural: the call must pass the record."""

    def test_earnings_fdic_doc_call_passes_rec(self):
        src = (REPO / "ui/earnings.py").read_text(encoding="utf-8")
        self.assertIn('_fdic_doc(cert, rec.get("REPDTE"), rec)', src)
        self.assertNotIn('_fdic_doc(cert, rec.get("REPDTE"))', src)
