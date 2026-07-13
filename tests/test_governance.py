"""Unit tests for the Corporate Governance data layer.

The evidence-quote guard is the correctness surface: a provision may only
render when its supporting quote verifies verbatim against the proxy text.
Run: python -m unittest tests.test_governance
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.governance import (  # noqa: E402
    PROVISIONS,
    _norm,
    _parse_governance_json,
    _guard_provisions,
    _slice_governance_sections,
)
from data.state_corp_law import STATE_CORP_LAW, get_state_reference  # noqa: E402

_PROXY = ("Corporate Governance Highlights. Our board is not classified; "
          "all directors stand for election annually. Directors are elected "
          "by a majority of votes cast in uncontested elections. The Company "
          "does not have a shareholder rights plan.")


class TestNorm(unittest.TestCase):
    def test_whitespace_and_curly_quotes(self):
        self.assertEqual(_norm("A  “majority”\n of\tvotes"),
                         'a "majority" of votes')


class TestGuard(unittest.TestCase):
    def _one(self, key, entry):
        raw = {key: entry}
        return _guard_provisions(raw, _PROXY)[key]

    def test_verified_quote_kept(self):
        out = self._one("majority_voting",
                        {"value": True,
                         "quote": "elected by a majority of votes cast"})
        self.assertTrue(out["value"])
        self.assertEqual(out["quote"], "elected by a majority of votes cast")

    def test_quote_with_different_whitespace_still_verifies(self):
        out = self._one("classified_board",
                        {"value": False,
                         "quote": "board is  not\nclassified"})
        self.assertFalse(out["value"])

    def test_paraphrased_quote_nulled(self):
        out = self._one("poison_pill",
                        {"value": False,
                         "quote": "the company has no poison pill in place"})
        self.assertIsNone(out["value"])
        self.assertIsNone(out["quote"])

    def test_bool_without_quote_nulled(self):
        out = self._one("classified_board", {"value": True, "quote": None})
        self.assertIsNone(out["value"])

    def test_non_bool_value_nulled(self):
        out = self._one("classified_board",
                        {"value": "yes", "quote": "board is not classified"})
        self.assertIsNone(out["value"])

    def test_too_short_quote_nulled(self):
        # A 1-2 word "quote" matches almost any text — not evidence.
        out = self._one("classified_board",
                        {"value": True, "quote": "board is"})
        self.assertIsNone(out["value"])

    def test_all_provision_keys_always_present(self):
        out = _guard_provisions({}, _PROXY)
        self.assertEqual(set(out.keys()), {k for k, _ in PROVISIONS})
        self.assertTrue(all(v["value"] is None for v in out.values()))


class TestParse(unittest.TestCase):
    def test_fenced_object(self):
        raw = '```json\n{"classified_board": {"value": false, "quote": "x y z"}}\n```'
        self.assertIn("classified_board", _parse_governance_json(raw))

    def test_garbage_and_list_empty(self):
        self.assertEqual(_parse_governance_json("nope"), {})
        self.assertEqual(_parse_governance_json('["a"]'), {})


class TestSlice(unittest.TestCase):
    def test_anchor_found(self):
        text = ("x" * 60_000) + "CORPORATE GOVERNANCE section here" + ("y" * 60_000)
        out = _slice_governance_sections(text, max_chars=10_000)
        self.assertIn("CORPORATE GOVERNANCE", out)

    def test_short_passthrough(self):
        self.assertEqual(_slice_governance_sections("abc"), "abc")


class TestStateReference(unittest.TestCase):
    def test_known_state(self):
        de = get_state_reference("de")
        self.assertEqual(de["name"], "Delaware")
        self.assertTrue(de["business_combination"]["has"])
        self.assertIn("203", de["business_combination"]["cite"])

    def test_unknown_state_and_none(self):
        self.assertIsNone(get_state_reference("ZZ"))
        self.assertIsNone(get_state_reference(None))

    def test_every_asserted_statute_has_citation(self):
        # Citation-first is the honesty contract of the curated table:
        # an asserted statute without a checkable cite must never ship.
        for code, ref in STATE_CORP_LAW.items():
            for fam in ("business_combination", "control_share", "fair_price"):
                entry = ref.get(fam)
                if isinstance(entry, dict) and entry.get("has"):
                    self.assertTrue(entry.get("cite"),
                                    f"{code}.{fam} asserted without a citation")

    def test_every_entry_has_name_and_cv_default(self):
        for code, ref in STATE_CORP_LAW.items():
            self.assertTrue(ref.get("name"), code)
            self.assertIn("cumulative_voting_default", ref, code)


if __name__ == "__main__":
    unittest.main()
