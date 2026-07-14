"""Pin the guarded-AI release extraction (data/release_ai.py, 2026-07-14).

The guards are the product: every accepted value must have a verbatim quote
in the document, the number printed in the quote, an in-band value, no
variant/segment language, and (for history) a period cue. These tests pin
each guard's accept AND reject side, the cache contract (API failure never
cached), and the fill's merge precedence (deterministic always wins).

Run: python -m unittest tests.test_release_ai
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.release_ai as rai
from data.release_ai import guard_items

DOC = ("For the second quarter of 2026, net interest margin was 4.56%, "
       "compared with 4.54% in the first quarter of 2026 and 4.48% a year "
       "ago. Return on average assets was 2.01%. The efficiency ratio, as "
       "adjusted, was 40.1%. Card Services net charge-off rate of 3.47%. "
       "Tangible book value per share (non-GAAP) was $113.35. Book value "
       "per share was $122.40. Net loss per diluted share of $(0.29). "
       "Cost of total deposits was 1.86% for the quarter.")


def g(items, doc=DOC):
    return guard_items(items, doc)


def item(key, period, value, quote):
    return {"key": key, "period": period, "value": value, "quote": quote}


class TestGuards(unittest.TestCase):
    def test_valid_current_and_history_accepted(self):
        out = g([
            item("nim", "cur", 4.56, "net interest margin was 4.56%,"),
            item("nim", "prior", 4.54,
                 "compared with 4.54% in the first quarter of 2026"),
            item("nim", "yoy", 4.48, "and 4.48% a year ago"),
            item("roa", "cur", 2.01, "Return on average assets was 2.01%."),
            item("cost_of_deposits", "cur", 1.86,
                 "Cost of total deposits was 1.86% for the quarter."),
        ])
        self.assertEqual(out["cur"], {"nim": 4.56, "roa": 2.01,
                                      "cost_of_deposits": 1.86})
        self.assertEqual(out["prior"], {"nim": 4.54})
        self.assertEqual(out["yoy"], {"nim": 4.48})

    def test_quote_must_exist_verbatim(self):
        out = g([item("nim", "cur", 4.56,
                      "net interest margin expanded to 4.56%")])  # not in doc
        self.assertEqual(out["cur"], {})

    def test_number_must_be_printed_in_quote(self):
        out = g([item("roa", "cur", 2.11, "Return on average assets was 2.01%.")])
        self.assertEqual(out["cur"], {})

    def test_rounded_rendering_never_evidences_a_precise_claim(self):
        # "5" in a quote is not evidence for 4.56.
        out = guard_items([item("nim", "cur", 4.56, "margin of 5 percent was")],
                          "margin of 5 percent was reported")
        self.assertEqual(out["cur"], {})

    def test_out_of_band_rejected(self):
        out = guard_items([item("nim", "cur", 34.2, "margin was 34.2% overall")],
                          "margin was 34.2% overall this year")
        self.assertEqual(out["cur"], {})

    def test_adjusted_variant_rejected(self):
        out = g([item("efficiency", "cur", 40.1,
                      "The efficiency ratio, as adjusted, was 40.1%.")])
        self.assertEqual(out["cur"], {})

    def test_segment_figure_rejected(self):
        out = g([item("nco_ratio", "cur", 3.47,
                      "Card Services net charge-off rate of 3.47%.")])
        self.assertEqual(out["cur"], {})

    def test_nongaap_tag_allowed_only_on_nongaap_keys(self):
        ok = g([item("tbv_ps", "cur", 113.35,
                     "Tangible book value per share (non-GAAP) was $113.35.")])
        self.assertEqual(ok["cur"], {"tbv_ps": 113.35})
        # Same tag on a GAAP key → rejected.
        bad = guard_items([item("roa", "cur", 1.5, "ROA (non-GAAP) was 1.5%")],
                          "ROA (non-GAAP) was 1.5% for the quarter")
        self.assertEqual(bad["cur"], {})

    def test_history_without_period_cue_rejected(self):
        out = guard_items([item("nim", "prior", 4.54, "margin was 4.54% flat")],
                          "margin was 4.54% flat overall")
        self.assertEqual(out["prior"], {})

    def test_negative_paren_value_accepted(self):
        out = g([item("eps_diluted", "cur", -0.29,
                      "Net loss per diluted share of $(0.29).")])
        self.assertEqual(out["cur"], {"eps_diluted": -0.29})

    def test_conflicting_duplicate_claims_drop_the_key(self):
        doc = "NIM was 4.56% early. Later the NIM was 4.16% again."
        out = guard_items([
            item("nim", "cur", 4.56, "NIM was 4.56% early."),
            item("nim", "cur", 4.16, "the NIM was 4.16% again."),
        ], doc)
        self.assertEqual(out["cur"], {})

    def test_unknown_key_or_period_ignored(self):
        out = g([item("nii", "cur", 5.0, "net interest margin was 4.56%,"),
                 item("nim", "ytd", 4.56, "net interest margin was 4.56%,")])
        self.assertEqual(out["cur"], {})


class TestTableHistoryEvidence(unittest.TestCase):
    """Jul-14 catch: megabank history lives in table columns; the row quote
    carries no period words — the period_quote (column header) is the proof."""
    DOC = ("Consolidated ratios 2Q26 1Q26 2Q25 "
           "Net interest margin 4.56 4.54 4.48 "
           "Return on average assets 2.01 1.98 1.90 all periods shown")

    def _it(self, period, value, pq=None):
        d = item("nim", period, value, "Net interest margin 4.56 4.54 4.48")
        if pq:
            d["period_quote"] = pq
        return d

    def test_header_period_quote_accepts_table_history(self):
        out = guard_items([self._it("prior", 4.54, pq="1Q26"),
                           self._it("yoy", 4.48, pq="2Q25")],
                          self.DOC, prior_qend="2026-03-31", yoy_qend="2025-06-30")
        self.assertEqual(out["prior"], {"nim": 4.54})
        self.assertEqual(out["yoy"], {"nim": 4.48})

    def test_wrong_quarter_header_rejected(self):
        out = guard_items([self._it("prior", 4.54, pq="2Q25")],  # that's yoy
                          self.DOC, prior_qend="2026-03-31", yoy_qend="2025-06-30")
        self.assertEqual(out["prior"], {})

    def test_header_not_in_document_rejected(self):
        out = guard_items([self._it("prior", 4.54, pq="4Q25")],
                          self.DOC, prior_qend="2025-12-31", yoy_qend="2025-06-30")
        self.assertEqual(out["prior"], {})

    def test_table_history_without_period_quote_still_rejected(self):
        out = guard_items([self._it("prior", 4.54)],
                          self.DOC, prior_qend="2026-03-31", yoy_qend="2025-06-30")
        self.assertEqual(out["prior"], {})

    def test_current_from_table_needs_no_period_proof(self):
        out = guard_items([self._it("cur", 4.56)],
                          self.DOC, prior_qend="2026-03-31", yoy_qend="2025-06-30")
        self.assertEqual(out["cur"], {"nim": 4.56})


class TestTruncationSalvage(unittest.TestCase):
    def test_truncated_array_salvages_complete_items(self):
        raw = ('[{"key": "nim", "period": "cur", "value": 4.56, '
               '"quote": "net interest margin was 4.56%,"}, '
               '{"key": "roa", "period": "cur", "value": 2.01, "quo')
        items = rai._parse_items(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["key"], "nim")

    def test_intact_array_parses_fully(self):
        raw = '[{"key": "nim", "period": "cur", "value": 4.5, "quote": "q"}]'
        self.assertEqual(len(rai._parse_items(raw)), 1)


class TestAiRetryNotLocked(unittest.TestCase):
    """The accession short-circuit must not lock in a failed AI fill (the
    read-only trace's central finding): pending state retries bounded; ok is
    immutable; legacy cached values without ai_state are treated pending."""

    def setUp(self):
        import data.cache as dc
        import data.ir_provider as ip
        import data.release_metrics as rm
        self.rm, self.store = rm, {}
        self._orig = (dc.get, dc.put, rm._current_accession,
                      ip.latest_earnings_release)
        dc.get = lambda k: self.store.get(k)
        dc.put = lambda k, v: self.store.__setitem__(k, v)
        rm._current_accession = lambda cik: "ACC1"
        ip.latest_earnings_release = lambda cik: {
            "html": "<p>x</p>", "filed_date": "2026-07-14", "accession": "ACC1",
            "url": "u"}
        self.fill_calls = []

    def tearDown(self):
        import data.cache as dc
        import data.ir_provider as ip
        (dc.get, dc.put, self.rm._current_accession,
         ip.latest_earnings_release) = self._orig

    def _seed(self, ai_state=None, attempts=0):
        value = {"accession": "ACC1", "qend": "2026-06-30",
                 "metrics": {"nim": 4.56}, "prior_metrics": {},
                 "yoy_metrics": {}, "capital": {}, "url": "u",
                 "filed_date": "2026-07-14"}
        if ai_state is not None:
            value["ai_state"] = ai_state
            value["ai_attempts"] = attempts
        self.store[f"release_metrics:v8:{1}"] = {
            "cached_at": "2020-01-01T00:00:00", "value": value}  # stale ⇒ re-check

    def _patch_fill(self, state):
        def fake(val, cik, rel):
            self.fill_calls.append(1)
            if state == "ok":
                val.setdefault("prior_metrics", {})["nim"] = 4.54
            return state
        self.rm._ai_fill = fake

    def test_pending_retries_and_heals(self):
        self._seed(ai_state="pending", attempts=1)
        self._orig_fill = self.rm._ai_fill
        try:
            self._patch_fill("ok")
            out = self.rm.release_metrics(1)
            self.assertEqual(out["ai_state"], "ok")
            self.assertEqual(out["prior_metrics"], {"nim": 4.54})
            self.assertEqual(len(self.fill_calls), 1)
        finally:
            self.rm._ai_fill = self._orig_fill

    def test_ok_state_never_recalls_ai(self):
        self._seed(ai_state="ok", attempts=1)
        self._orig_fill = self.rm._ai_fill
        try:
            self._patch_fill("ok")
            self.rm.release_metrics(1)
            self.assertEqual(self.fill_calls, [])
        finally:
            self.rm._ai_fill = self._orig_fill

    def test_legacy_value_without_state_is_retried(self):
        self._seed(ai_state=None)         # pre-fix cached extraction
        self._orig_fill = self.rm._ai_fill
        try:
            self._patch_fill("ok")
            out = self.rm.release_metrics(1)
            self.assertEqual(len(self.fill_calls), 1)
            self.assertEqual(out["ai_state"], "ok")
        finally:
            self.rm._ai_fill = self._orig_fill

    def test_attempt_cap_stops_retrying(self):
        # Cap is 24 (sized to the 900s re-check TTL — ~6h retry horizon).
        self._seed(ai_state="pending", attempts=24)
        self._orig_fill = self.rm._ai_fill
        try:
            self._patch_fill("ok")
            self.rm.release_metrics(1)
            self.assertEqual(self.fill_calls, [])
        finally:
            self.rm._ai_fill = self._orig_fill

    def test_frontier_moved_same_release_keeps_extraction(self):
        # A furnished non-release 8-K (investor deck) moves the FRONTIER but
        # latest_earnings_release still selects the same release: the stored
        # extraction (and its AI state) must survive, with the frontier
        # advanced so the next pass short-circuits — never a refetch loop.
        self._seed(ai_state="ok", attempts=1)
        self.rm._current_accession = lambda cik: "DECK2"
        self._orig_fill = self.rm._ai_fill
        try:
            self._patch_fill("ok")
            out = self.rm.release_metrics(1)
            self.assertEqual(out["accession"], "ACC1")   # release unchanged
            self.assertEqual(out["frontier"], "DECK2")   # frontier advanced
            self.assertEqual(out["metrics"], {"nim": 4.56})
            self.assertEqual(out["ai_state"], "ok")
            self.assertEqual(self.fill_calls, [])        # no re-extraction
        finally:
            self.rm._ai_fill = self._orig_fill

    def test_new_release_reextracts_and_stamps_frontier(self):
        import data.ir_provider as ip
        self._seed(ai_state="ok", attempts=1)
        self.rm._current_accession = lambda cik: "ACC2"
        ip.latest_earnings_release = lambda cik: {
            "html": "<p>net interest margin of 4.60%.</p>",
            "filed_date": "2026-07-14", "accession": "ACC2", "url": "u2"}
        out = self.rm.release_metrics(1)
        self.assertEqual(out["accession"], "ACC2")
        self.assertEqual(out["frontier"], "ACC2")
        self.assertEqual(out["metrics"].get("nim"), 4.60)


class TestCacheContract(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.calls = []
        self._orig = (rai._call_model,)
        import data.cloud_storage as cs
        self._cs = (cs.load_json, cs.save_json)
        cs.load_json = lambda p, f: self.store.get((p, f))
        cs.save_json = lambda p, f, d: self.store.__setitem__((p, f), d) or True

    def tearDown(self):
        (rai._call_model,) = self._orig
        import data.cloud_storage as cs
        cs.load_json, cs.save_json = self._cs

    def test_success_cached_and_served(self):
        rai._call_model = lambda *a, **k: (self.calls.append(1),
                                           [item("nim", "cur", 4.56,
                                                 "net interest margin was 4.56%,")])[1]
        a = rai.release_ai_metrics(1, "0001-26-000001", DOC)
        b = rai.release_ai_metrics(1, "0001-26-000001", DOC)
        self.assertEqual(a["cur"], {"nim": 4.56})
        self.assertEqual(a, b)
        self.assertEqual(len(self.calls), 1, "second call must serve the cache")

    def test_api_failure_not_cached(self):
        rai._call_model = lambda *a, **k: (self.calls.append(1), None)[1]
        self.assertIsNone(rai.release_ai_metrics(1, "acc", DOC))
        rai._call_model = lambda *a, **k: [item("nim", "cur", 4.56,
                                                "net interest margin was 4.56%,")]
        self.assertIsNotNone(rai.release_ai_metrics(1, "acc", DOC))

    def test_nothing_verified_not_cached(self):
        rai._call_model = lambda *a, **k: [item("nim", "cur", 4.56, "made up")]
        self.assertIsNone(rai.release_ai_metrics(1, "acc2", DOC))
        self.assertEqual(self.store, {})


class TestMergePrecedence(unittest.TestCase):
    def test_deterministic_wins_ai_fills_gaps(self):
        import data.release_ai as mod
        import data.release_metrics as rm
        orig = (mod.release_ai_metrics, mod.has_api_key)
        mod.release_ai_metrics = lambda *a, **k: {
            "cur": {"nim": 9.99, "roa": 2.01, "cet1_ratio": 12.5},
            "prior": {"nim": 4.54}, "yoy": {}}
        mod.has_api_key = lambda: True
        try:
            val = {"accession": "a", "qend": "2026-06-30",
                   "metrics": {"nim": 4.56, "roa": None},
                   "prior_metrics": {}, "yoy_metrics": {},
                   "capital": {"cet1_ratio": None}}
            rm._ai_fill(val, 1, {"html": "<p>x</p>"})
            self.assertEqual(val["metrics"]["nim"], 4.56)     # deterministic wins
            self.assertEqual(val["metrics"]["roa"], 2.01)     # AI fills the gap
            self.assertEqual(val["prior_metrics"]["nim"], 4.54)
            self.assertEqual(val["capital"]["cet1_ratio"], 12.5)
        finally:
            mod.release_ai_metrics, mod.has_api_key = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
