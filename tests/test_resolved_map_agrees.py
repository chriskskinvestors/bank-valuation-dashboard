"""
bank_map_resolved.json must never contradict BANK_MAP.

BANK_MAP wins at lookup (data/bank_mapping.get_fdic_cert priority 1), so a
disagreement serves nothing today — which is exactly what made it dangerous.
On 2026-07-20 this file still carried FIFTY stale certs, including every
wrong-entity join fixed in the 2026-07-09 sweep: CFG on 11063 (First-Citizens'
cert, the original incident), HBCP on 11241, HBNC on 4977, FNB on 3305, MBIN
on 4365, UBCP on 22858, WFC on a $520M Missouri bank, USB on FUSB's bank.
Several were even marked fdic_score 1.0. Every fix had been made in BANK_MAP
alone, so the moment a BANK_MAP entry were removed — or any code read this
file directly — the wrong bank would come back.

The two files are kept in sync instead. This test is the ratchet.

Offline: reads the mapping chain only, no network.
"""
import json
import sys
import types
import unittest
from pathlib import Path

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.bank_mapping import BANK_MAP  # noqa: E402

_PATH = Path(__file__).parent.parent / "data" / "bank_map_resolved.json"
RESOLVED = json.loads(_PATH.read_text(encoding="utf-8"))


class TestResolvedMapAgreesWithBankMap(unittest.TestCase):
    def test_certs_agree(self):
        for ticker, curated in sorted(BANK_MAP.items()):
            entry = RESOLVED.get(ticker)
            if entry is None:
                continue  # not every curated ticker is in the generated file
            with self.subTest(ticker=ticker):
                self.assertEqual(
                    entry.get("fdic_cert"), curated.get("fdic_cert"),
                    f"{ticker}: bank_map_resolved.json has cert "
                    f"{entry.get('fdic_cert')} but BANK_MAP (hand-verified, and "
                    f"the winner at lookup) has {curated.get('fdic_cert')}. "
                    "Update the JSON to match — a stale cert here is a latent "
                    "wrong-entity join waiting for the override to be removed.")

    def test_ciks_agree(self):
        for ticker, curated in sorted(BANK_MAP.items()):
            entry = RESOLVED.get(ticker)
            if entry is None:
                continue
            with self.subTest(ticker=ticker):
                self.assertEqual(entry.get("cik"), curated.get("cik"),
                                 f"{ticker}: CIK disagrees with BANK_MAP")

    def test_no_two_tickers_claim_one_cert(self):
        # A cert belongs to exactly one holding company. Share-class siblings
        # (same registrant name) legitimately repeat; anything else is the
        # duplicate-claim tell that caught the megabank errors.
        by_cert: dict[int, list[str]] = {}
        for ticker, entry in RESOLVED.items():
            cert = entry.get("fdic_cert")
            if cert:
                by_cert.setdefault(int(cert), []).append(ticker)
        for cert, tickers in sorted(by_cert.items()):
            if len(tickers) < 2:
                continue
            names = {(RESOLVED[t].get("name") or "").upper() for t in tickers}
            with self.subTest(cert=cert):
                self.assertEqual(
                    len(names), 1,
                    f"cert {cert} claimed by {sorted(tickers)} — distinct "
                    "registrants cannot share one bank")

    def test_scores_are_evidenced(self):
        # fdic_score < 1.0 means "matched on a name-similarity heuristic and
        # never verified". Those were the entries the 0.3-Jaccard resolver
        # wrote; all have since been re-derived through the state-gated
        # resolver or copied from BANK_MAP. A new one means an unverified
        # mapping slipped in.
        weak = {t: e.get("fdic_score") for t, e in RESOLVED.items()
                if e.get("fdic_cert") and (e.get("fdic_score") or 0) < 1.0}
        self.assertEqual(
            weak, {},
            "unverified cert mappings present — re-resolve them through the "
            "state-gated resolver (tools/resolve_all_mappings.best_fdic_match "
            "with a profile) and verify before committing")


if __name__ == "__main__":
    unittest.main()
