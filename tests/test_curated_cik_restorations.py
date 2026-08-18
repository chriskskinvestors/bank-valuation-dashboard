"""(2026-08-18) 18 SEC registrants were frozen at cik=null in the curated map.

Surfaced by "the screening table shows nothing for small banks": the whole
< $1B tier dashed every SEC-derived column. The resolver had once failed on
these tickers and its null was CACHED into data/bank_map_resolved.json —
which OVERRIDES the universe snapshot, so the miss could never self-heal.
Bank OZK ($37B, files SEC directly with no holding company) ran with no SEC
half at all; a docstring even listed it as a non-filer.

Each mapping below was hand-verified 2026-08-18 against SEC's own
company_tickers.json (ticker→CIK) AND the CIK's submissions JSON (name
congruent with the curated bank name, bank SIC or the known blank-SIC quirk
for OZK/WRIV, and a filing within the last ~15 months). companyfacts was
then checked per CIK: CCNB/FOTB/OSBK/UBOH/VWFB serve full XBRL (their
EPS/TBV populate from SEC); FGFH/OAKV/REDW/OZK/WRIV have EDGAR identity but
NO companyfacts (paper-style or 12(i) filers whose financials go to the
FDIC) — for them the cik lights up the filings tab and the with-CIK-but-
empty-XBRL release fallback, and their SEC-derived dashes remain correct.
Four tickers (CCNB, REDW, UBOH, OZK) also needed their BANK_MAP entries
set — that static tier OUTRANKS this json, so a json-only fix was inert.

DELIBERATELY LEFT NULL — banks that went dark (no filing since 2024/2025:
CULL, CIZN, UNIB, BCOW, BCTF, BMBN, CPKF, FNFI): mapping them would surface
2024-era EPS/TTM beside live prices — a plausible-wrong number. cik=null
routes them through the OTC earnings-release path, which serves their
CURRENT disclosures. If one resumes filing, verify and add it here.

These pins also guard against tools/resolve_all_mappings.py regenerating
the json and re-nulling the fixes.

Run: python -m unittest tests.test_curated_cik_restorations
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESTORED = {
    "CCNB": 1437213, "FGFH": 786298, "FOTB": 1099668, "OAKV": 1865429,
    "OSBK": 1076691, "REDW": 942895, "UBOH": 1087456, "VWFB": 1913838,
    "OZK": 1569650, "WRIV": 1328409,
}

DELIBERATELY_DARK = ("CULL", "CIZN", "UNIB", "BCOW", "BCTF", "BMBN",
                     "CPKF", "FNFI")


class TestRestoredCiks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolved = json.loads(
            (REPO / "data/bank_map_resolved.json").read_text(encoding="utf-8"))

    def test_restored_mappings_present_and_exact(self):
        for t, cik in RESTORED.items():
            with self.subTest(ticker=t):
                self.assertIn(t, self.resolved)
                self.assertEqual(
                    self.resolved[t].get("cik"), cik,
                    f"{t} cik regressed — resolve_all_mappings.py re-nulled "
                    f"a hand-verified mapping; restore it, don't delete this pin")

    def test_get_cik_serves_the_restorations(self):
        """Through the FULL lookup chain — this is what caught the BANK_MAP
        shadow (the static tier returned its own cik=None before the json
        was ever consulted)."""
        from tests import _streamlit_stub
        _streamlit_stub.install()
        from data.bank_mapping import get_cik
        for t, cik in RESTORED.items():
            with self.subTest(ticker=t):
                self.assertEqual(get_cik(t), cik)

    def test_dark_banks_stay_null_by_design(self):
        for t in DELIBERATELY_DARK:
            with self.subTest(ticker=t):
                if t in self.resolved:
                    self.assertIsNone(
                        self.resolved[t].get("cik"),
                        f"{t} gained a cik — it was dark (no SEC filing since "
                        f"2024/2025); re-verify a RESUMED filing cadence before "
                        f"mapping, else stale EPS renders beside live prices")


if __name__ == "__main__":
    unittest.main()
