"""
FDIC quotient ratios with a zero/negative denominator (found 2026-09-25 while
fixing PR #167, fixed 2026-09-30).

The FDIC financials API reports a LITERAL 0 — not null — for a quotient ratio
whose denominator is <= 0. Multi-charter groups were already safe (cert_group
recomputes Σnum/Σden and returns None for den <= 0), but single-charter banks
took FDIC's record as-is and rendered "Prov/NCO 0.00%", "Earn Cov NCO 0.0x",
"Rsv/NPL 0.0%" where the value is undefined.

Live probe, every institution, 12/31/2025 + 3/31/2026 + 6/30/2026: for the 15
ratios in fdic_client._DEN_ZERO_REPORTED_AS_ZERO, den <= 0 gave ratio 0 in
every case (57-2,064 banks per ratio per quarter), never any other value; a
numerator of 0 over a positive denominator gave 0 in every case (the genuine
zero this fix must keep).

Pins (pure, no network) — fixtures are live 6/30/2026 FDIC values ($K):
  * BNY lead charter (cert 639): net recoveries (NTTOT -12,000, NTLNLSA
    -24,000) → ELNANTR and IDERNCVR n/a; no consumer loans / HELOCs → IDNCCONR
    and NCRELOCR n/a; its real ratios (NCLNLSR 0.0429%, LNRESNCR 1000%) and
    genuine zeros (IDNCCIR: 0 noncurrent of 1,854,000 C&I) are untouched.
  * cert 24867: NTTOT 0 → ELNANTR / IDERNCVR n/a; LNRESNCR 15/163 = 9.2025%.
  * cert 23472 (no loans): NCLNLS 0 → LNRESNCR n/a, and every loan ratio n/a;
    LNLSDEPR = 0 / 2,233 deposits is a genuine 0% and stays.
  * cert 10101: normal bank — ELNANTR 384/68 = 564.71%, IDNCCIR 0/28,878 = 0%.
  * the rule needs the denominator KEY: rows stored before the component was
    fetched keep their value (no inference); an FDIC-null denominator with
    the key present is n/a (17 institutions at 6/30/2026, ratio 0 each).
  * applied at every boundary: fetch_financials, fetch_quarter_financials
    (as-of / earnings path, which also skipped the capital scrub), the
    history-store read, and load_fdic_hist's cache read.
  * structural: every rule ratio is an _EXACT_QUOTIENTS entry whose
    denominator is in the fetched field set (else the rule could never fire).
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data.fdic_client import null_undefined_quotients  # noqa: E402

# Live FDIC 6/30/2026 — each ratio with its own numerator/denominator.
_BNY_639 = {
    "CERT": 639, "REPDTE": "20260630",
    "ELNLOS": -36000, "NTTOT": -12000, "ELNANTR": 0,
    "CHFLA": 7468000, "NTLNLSA": -24000, "IDERNCVR": 0,
    "NCLNLS": 20000, "LNLSGRJ": 46659000, "NCLNLSR": 0.042864184830364985,
    "LNATRESJ": 200000, "LNRESNCR": 1000,
    "NCCI": 0, "LNCI": 1854000, "IDNCCIR": 0,
    "NCCON": 0, "LNCON": 0, "IDNCCONR": 0,
    "NCRELOC": 0, "LNRELOC": 0, "NCRELOCR": 0,
    "NCRERES": 20000, "LNRERES": 2270000, "NCRERESR": 0.881057268722467,
}
_CERT_24867 = {
    "CERT": 24867, "REPDTE": "20260630",
    "ELNLOS": 13, "NTTOT": 0, "ELNANTR": 0,
    "CHFLA": 19558, "NTLNLSA": 0, "IDERNCVR": 0,
    "LNATRESJ": 15, "NCLNLS": 163, "LNRESNCR": 9.202453987730062,
}
_CERT_23472 = {
    "CERT": 23472, "REPDTE": "20260630",
    "LNLSNET": 0, "DEP": 2233, "LNLSDEPR": 0,
    "LNATRESJ": 0, "NCLNLS": 0, "LNRESNCR": 0,
    "LNLSGRJ": 0, "NCLNLSR": 0, "LNATRES": 0, "LNLSGR": 0, "LNATRESR": 0,
    "NCRECONS": 0, "LNRECONS": 0, "NCRECONR": 0,
    "ELNLOS": 0, "NTTOT": 0, "ELNANTR": 0,
}
_CERT_10101 = {
    "CERT": 10101, "REPDTE": "20260630",
    "ELNLOS": 384, "NTTOT": 68, "ELNANTR": 564.7058823529412,
    "CHFLA": 10274, "NTLNLSA": 136, "IDERNCVR": 75.54411764705883,
    "NCCI": 0, "LNCI": 28878, "IDNCCIR": 0,
    "NCRECONS": 0, "LNRECONS": 22621, "NCRECONR": 0,
    "LNATRESJ": 4982, "NCLNLS": 4028, "LNRESNCR": 123.6842105263158,
}


class TestUndefinedQuotientsNulled(unittest.TestCase):
    def test_bny_net_recoveries_are_na(self):
        r = null_undefined_quotients(dict(_BNY_639))
        # provision / NCO over net RECOVERIES (-12,000): meaningless → n/a
        self.assertIsNone(r["ELNANTR"])
        # earnings coverage 7,468,000 / -24,000 would be -311x: n/a, not 0.0x
        self.assertIsNone(r["IDERNCVR"])
        # no consumer loans, no HELOCs: 0 / 0 → n/a
        self.assertIsNone(r["IDNCCONR"])
        self.assertIsNone(r["NCRELOCR"])

    def test_bny_real_ratios_and_genuine_zero_kept(self):
        r = null_undefined_quotients(dict(_BNY_639))
        # 20,000 / 46,659,000 × 100 = 0.042864%
        self.assertAlmostEqual(r["NCLNLSR"], 20000 / 46659000 * 100)
        # 200,000 / 20,000 × 100 = 1000%
        self.assertEqual(r["LNRESNCR"], 1000)
        # 20,000 / 2,270,000 × 100 = 0.881057%
        self.assertAlmostEqual(r["NCRERESR"], 20000 / 2270000 * 100)
        # 0 noncurrent of 1,854,000 C&I: a real 0%, must NOT become n/a
        self.assertEqual(r["IDNCCIR"], 0)

    def test_zero_net_chargeoffs_are_na(self):
        r = null_undefined_quotients(dict(_CERT_24867))
        self.assertIsNone(r["ELNANTR"])        # 13 / 0
        self.assertIsNone(r["IDERNCVR"])       # 19,558 / 0
        self.assertAlmostEqual(r["LNRESNCR"], 15 / 163 * 100)   # 9.2025%

    def test_no_loans_bank(self):
        r = null_undefined_quotients(dict(_CERT_23472))
        for k in ("LNRESNCR", "NCLNLSR", "LNATRESR", "NCRECONR", "ELNANTR"):
            self.assertIsNone(r[k], k)
        # 0 net loans / 2,233 deposits is a genuine 0% loans-to-deposits
        self.assertEqual(r["LNLSDEPR"], 0)

    def test_normal_bank_untouched(self):
        r = null_undefined_quotients(dict(_CERT_10101))
        self.assertEqual(r, _CERT_10101)
        self.assertAlmostEqual(r["ELNANTR"], 384 / 68 * 100)      # 564.71%
        self.assertAlmostEqual(r["IDERNCVR"], 10274 / 136)         # 75.54x
        self.assertEqual(r["IDNCCIR"], 0)                          # 0 / 28,878
        self.assertEqual(r["NCRECONR"], 0)                         # 0 / 22,621

    def test_efficiency_negative_or_null_revenue_is_na(self):
        """(2026-09-30) FDIC reports EEFFR 0 whenever IEFF is null or <= 0.
        Live 6/30/2026: cert 12013 EEFF 7,318 / IEFF -2,607 → EEFFR 0;
        JPM lead charter (628) 48,709,000 / 89,575,000 = 54.3779% is kept."""
        r = null_undefined_quotients({"EEFF": 7318, "IEFF": -2607, "EEFFR": 0})
        self.assertIsNone(r["EEFFR"])
        r = null_undefined_quotients({"EEFF": None, "IEFF": None, "EEFFR": 0})
        self.assertIsNone(r["EEFFR"])
        jpm = {"EEFF": 48709000, "IEFF": 89575000, "EEFFR": 54.37789561819704}
        r = null_undefined_quotients(dict(jpm))
        self.assertEqual(r, jpm)
        self.assertAlmostEqual(r["EEFFR"], 48709000 / 89575000 * 100, places=12)

    def test_quarterly_efficiency_non_positive_revenue_is_na(self):
        """(2026-09-30) Over IEFFQ <= 0 FDIC reports the RAW quotient, not 0.
        Live 6/30/2026: cert 12013 3,728 / -1,134 → -328.75; cert 11411
        -668 / -762 → 87.66 (plausible-looking). JPM (628) 24,711,000 /
        45,718,000 = 54.05% is kept."""
        r = null_undefined_quotients({"EEFFQ": 3728, "IEFFQ": -1134, "EEFFQR": -328.75})
        self.assertIsNone(r["EEFFQR"])
        r = null_undefined_quotients({"EEFFQ": -668, "IEFFQ": -762, "EEFFQR": 87.66})
        self.assertIsNone(r["EEFFQR"])
        jpm = {"EEFFQ": 24711000, "IEFFQ": 45718000, "EEFFQR": 54.05}
        r = null_undefined_quotients(dict(jpm))
        self.assertEqual(r, jpm)
        self.assertAlmostEqual(24711000 / 45718000 * 100, r["EEFFQR"], delta=0.005)

    def test_absent_denominator_key_means_no_inference(self):
        # A cache/history row written before NTTOT/NCLNLS were fetched.
        r = null_undefined_quotients({"ELNANTR": 0, "LNRESNCR": 0, "NCRECONR": 0})
        self.assertEqual(r, {"ELNANTR": 0, "LNRESNCR": 0, "NCRECONR": 0})

    def test_fdic_null_denominator_is_na(self):
        r = null_undefined_quotients({"NTTOT": None, "ELNANTR": 0,
                                      "LNCON": float("nan"), "IDNCCONR": 0})
        self.assertIsNone(r["ELNANTR"])
        self.assertIsNone(r["IDNCCONR"])


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class TestBoundaries(unittest.TestCase):
    def test_fetch_financials(self):
        import data.fdic_client as fc
        payload = {"data": [{"data": dict(_BNY_639)}]}
        with patch.object(fc, "_get_with_retry", return_value=_Resp(payload)):
            df = fc.fetch_financials(639, limit=1)
        r = df.to_dict("records")[0]
        self.assertTrue(math.isnan(r["ELNANTR"]) and math.isnan(r["IDERNCVR"]))
        self.assertEqual(r["LNRESNCR"], 1000)
        self.assertEqual(r["IDNCCIR"], 0)

    def test_fetch_quarter_financials(self):
        import data.fdic_client as fc
        rows = [{"data": dict(_CERT_24867)}, {"data": dict(_CERT_10101)},
                {"data": {"CERT": 110, "REPDTE": "20260630", "RWAJ": 0,
                          "RBCRWAJ": 0, "IDT1CER": 0, "RBCT1J": 5}}]
        with patch.object(fc, "_fetch_fin_page", return_value=rows):
            out = fc.fetch_quarter_financials("20260630", certs=[24867, 10101, 110])
        self.assertIsNone(out[24867]["ELNANTR"])
        self.assertAlmostEqual(out[24867]["LNRESNCR"], 15 / 163 * 100)
        self.assertAlmostEqual(out[10101]["ELNANTR"], 384 / 68 * 100)
        # this path also skipped the capital scrub: RWAJ 0 → risk-based n/a
        self.assertIsNone(out[110]["RBCRWAJ"])
        self.assertIsNone(out[110]["IDT1CER"])

    def test_load_fdic_hist_cache_read(self):
        from data import loaders
        cached = [dict(_CERT_24867), dict(_CERT_10101)]
        with patch("data.cache.get", return_value=cached):
            recs = loaders.load_fdic_hist("X", min_quarters=2)
        self.assertIsNone(recs[0]["ELNANTR"])
        self.assertAlmostEqual(recs[1]["ELNANTR"], 384 / 68 * 100)
        self.assertEqual(cached[0]["ELNANTR"], 0)   # shared cache object untouched


class TestStoreReadBoundary(unittest.TestCase):
    """Isolated SQLite store, the same harness tests/test_cet1_fields uses."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.fdic_history_store as store
        self._db, self._store = db, store
        self._saved = (db._engine, store._engine, db.USE_POSTGRES, store._USE_POSTGRES)
        db._engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                   poolclass=StaticPool, future=True)
        db.USE_POSTGRES = False
        store._USE_POSTGRES = False
        store._engine = None
        store.init_history_schema()

    def tearDown(self):
        db, store = self._db, self._store
        db._engine, store._engine, db.USE_POSTGRES, store._USE_POSTGRES = self._saved

    def test_stored_undefined_zero_reads_back_as_na(self):
        self._store.upsert_history(639, [dict(_BNY_639)])
        r = self._store.get_cert_history(639)[0]
        self.assertIsNone(r["ELNANTR"])
        self.assertIsNone(r["IDNCCONR"])
        self.assertEqual(r["IDNCCIR"], 0)
        self.assertEqual(r["LNRESNCR"], 1000)


class TestRuleIsWired(unittest.TestCase):
    def test_every_rule_ratio_has_a_fetched_denominator(self):
        from data.cert_group import _EXACT_QUOTIENTS
        from data.fdic_client import _BASE_FINANCIALS_FIELDS, _DEN_ZERO_REPORTED_AS_ZERO
        from config import get_fdic_fields
        fetched = _BASE_FINANCIALS_FIELDS | get_fdic_fields()
        for ratio in _DEN_ZERO_REPORTED_AS_ZERO:
            self.assertIn(ratio, _EXACT_QUOTIENTS)
            self.assertIn(_EXACT_QUOTIENTS[ratio][1], fetched, ratio)


if __name__ == "__main__":
    unittest.main()
