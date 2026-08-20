"""analysis.merger_hhi — pro-forma merger screening on SOD deposits.

Runs against an isolated in-memory SQLite branches table (same SQL the live
store runs). All HHI values are hand-computed in the comments; deposits are
SOD $thousands.

Synthetic markets (cert 1 = acquirer A, cert 2 = target B):

  county 11111 / msa 10001 — total 1,000
      A=400 (two branches: 250+150), B=300, C=200, D=100
      shares 40/30/20/10 → pre-HHI = 1600+900+400+100      = 3000
      post (A+B=70%):      4900+400+100                    = 5400
      Δ = 2400 → screened, "post > 2,500 with Δ > 200" severity

  county 22222 / msa 10002 — total 1,000
      A=200, B=100, C=150, D=150, E=200, F=200
      shares 20/10/15/15/20/20 → pre = 400+100+225+225+400+400 = 1750
      post (A+B=30%): 900+225+225+400+400                      = 2150
      Δ = 400 → screened via "post > 1,800 with Δ > 100" only

  county 33333 / msa 10003 — total 1,000
      A=100, B=50, C=450, D=400
      shares 10/5/45/40 → pre = 100+25+2025+1600 = 3750
      post (A+B=15%): 225+2025+1600              = 3850
      Δ = 100 exactly → NOT screened (thresholds are strict >)

  county 44444 — A=500, C=500 (B absent) → not an overlap market
  county 66666 — A=300, B=0, C=700 → skipped: zero recorded deposits for B
"""
import unittest
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs
from analysis import merger_hhi as mh


#            cert  ticker  name     stcntybr  msa      deposits
_FIXTURES = [
    (1, "AAA", "Alpha", "11111", "10001", 250),
    (1, "AAA", "Alpha", "11111", "10001", 150),
    (2, "BBB", "Beta",  "11111", "10001", 300),
    (3, None,  "Gamma", "11111", "10001", 200),
    (4, None,  "Delta", "11111", "10001", 100),
    (1, "AAA", "Alpha", "22222", "10002", 200),
    (2, "BBB", "Beta",  "22222", "10002", 100),
    (3, None,  "Gamma", "22222", "10002", 150),
    (4, None,  "Delta", "22222", "10002", 150),
    (5, None,  "Eps",   "22222", "10002", 200),
    (6, None,  "Zeta",  "22222", "10002", 200),
    (1, "AAA", "Alpha", "33333", "10003", 100),
    (2, "BBB", "Beta",  "33333", "10003", 50),
    (3, None,  "Gamma", "33333", "10003", 450),
    (4, None,  "Delta", "33333", "10003", 400),
    (1, "AAA", "Alpha", "44444", "10004", 500),
    (3, None,  "Gamma", "44444", "10004", 500),
    (1, "AAA", "Alpha", "66666", "10006", 300),
    (2, "BBB", "Beta",  "66666", "10006", 0),
    (3, None,  "Gamma", "66666", "10006", 700),
]


class TestHhiHelper(unittest.TestCase):
    def test_hand_computed_hhi(self):
        # 40²+30²+20²+10² = 1600+900+400+100 = 3000
        self.assertAlmostEqual(
            mh.hhi_from_deposits([400, 300, 200, 100]), 3000.0, places=9)

    def test_monopoly_is_10000(self):
        self.assertAlmostEqual(mh.hhi_from_deposits([123]), 10000.0, places=9)

    def test_decimal_inputs(self):
        # Postgres SUM() returns Decimal — 40%² + 60%² = 1600+3600 = 5200
        self.assertAlmostEqual(
            mh.hhi_from_deposits([Decimal(400), Decimal(600)]),
            5200.0, places=9)

    def test_incomputable_is_none_never_fabricated(self):
        self.assertIsNone(mh.hhi_from_deposits([]))
        self.assertIsNone(mh.hhi_from_deposits([0, 0]))
        self.assertIsNone(mh.hhi_from_deposits([100, None]))
        self.assertIsNone(mh.hhi_from_deposits([100, float("nan")]))


class TestBandsAndScreen(unittest.TestCase):
    def test_concentration_bands(self):
        self.assertEqual(mh.concentration_band(1499.99), "unconcentrated")
        self.assertEqual(mh.concentration_band(1500.0),
                         "moderately concentrated")
        self.assertEqual(mh.concentration_band(2500.0),
                         "moderately concentrated")
        self.assertEqual(mh.concentration_band(2500.01),
                         "highly concentrated")

    def test_classify_screen(self):
        self.assertEqual(mh.classify_screen(5400, 2400),
                         (True, "post-merger HHI > 2,500 with ΔHHI > 200"))
        self.assertEqual(mh.classify_screen(2150, 400),
                         (True, "post-merger HHI > 1,800 with ΔHHI > 100"))
        # Δ exactly 100 — strict inequality, no flag
        self.assertEqual(mh.classify_screen(3850, 100), (False, None))
        self.assertEqual(mh.classify_screen(1800, 500), (False, None))
        self.assertEqual(mh.classify_screen(1801, 101),
                         (True, "post-merger HHI > 1,800 with ΔHHI > 100"))


class TestMergerScreening(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._orig_get_engine = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        with self._eng.begin() as c:
            for i, (cert, tk, nm, fips, msa, dep) in enumerate(_FIXTURES):
                c.execute(text(
                    "INSERT INTO branches (cert, brnum, year, ticker, "
                    "bank_name, branch_name, address, city, state, zip, "
                    "county, stcntybr, msa_code, msa_name, deposits) VALUES "
                    "(:cert, :brnum, 2024, :tk, :nm, 'b', 'a', 'c', 'PA', "
                    "'z', :cty, :fips, :msa, :msan, :dep)"),
                    {"cert": cert, "brnum": i, "tk": tk, "nm": nm,
                     "cty": f"County {fips}", "fips": fips, "msa": msa,
                     "msan": f"MSA {msa}", "dep": dep})

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None

    # ── market_overlap ───────────────────────────────────────────────────
    def test_overlap_markets_and_shares(self):
        res = mh.market_overlap(1, 2, kind="county")
        df = res["markets"]
        self.assertIsNone(res["reason"])
        self.assertEqual(res["year"], 2024)
        # only markets where BOTH banks take deposits; sorted by combined
        # party deposits desc: 11111 (700) > 22222 (300) > 33333 (150)
        self.assertEqual(list(df["market_key"]), ["11111", "22222", "33333"])
        r = df[df["market_key"] == "11111"].iloc[0]
        self.assertEqual(r["deposits_a"], 400.0)      # 250 + 150
        self.assertEqual(int(r["branches_a"]), 2)
        self.assertAlmostEqual(r["share_a_pct"], 40.0, places=9)
        self.assertEqual(r["deposits_b"], 300.0)
        self.assertAlmostEqual(r["share_b_pct"], 30.0, places=9)
        self.assertEqual(r["market_total"], 1000.0)
        self.assertEqual(int(r["n_banks"]), 4)

    def test_overlap_excludes_single_party_market(self):
        res = mh.market_overlap(1, 2, kind="county")
        self.assertNotIn("44444", set(res["markets"]["market_key"]))

    def test_overlap_zero_deposit_market_skipped_with_reason(self):
        res = mh.market_overlap(1, 2, kind="county")
        self.assertNotIn("66666", set(res["markets"]["market_key"]))
        sk = [s for s in res["skipped"] if s["market_key"] == "66666"]
        self.assertEqual(len(sk), 1)
        self.assertIn("zero recorded deposits for cert 2", sk[0]["reason"])

    # ── pro_forma_hhi ────────────────────────────────────────────────────
    def test_pro_forma_hand_computed(self):
        res = mh.pro_forma_hhi(1, 2, kind="county")
        df = res["markets"].set_index("market_key")
        # 11111: pre 3000, post 5400, Δ 2400 (hand, module docstring)
        r = df.loc["11111"]
        self.assertAlmostEqual(r["hhi_pre"], 3000.0, places=9)
        self.assertAlmostEqual(r["hhi_post"], 5400.0, places=9)
        self.assertAlmostEqual(r["hhi_delta"], 2400.0, places=9)
        self.assertAlmostEqual(r["combined_share_pct"], 70.0, places=9)
        self.assertTrue(bool(r["screen_flag"]))
        self.assertEqual(r["screen_reason"],
                         "post-merger HHI > 2,500 with ΔHHI > 200")
        self.assertEqual(r["concentration_post"], "highly concentrated")
        # 22222: pre 1750, post 2150, Δ 400
        r = df.loc["22222"]
        self.assertAlmostEqual(r["hhi_pre"], 1750.0, places=9)
        self.assertAlmostEqual(r["hhi_post"], 2150.0, places=9)
        self.assertAlmostEqual(r["hhi_delta"], 400.0, places=9)
        self.assertTrue(bool(r["screen_flag"]))
        self.assertEqual(r["screen_reason"],
                         "post-merger HHI > 1,800 with ΔHHI > 100")
        self.assertEqual(r["concentration_post"], "moderately concentrated")
        # 33333: pre 3750, post 3850, Δ exactly 100 → not flagged
        r = df.loc["33333"]
        self.assertAlmostEqual(r["hhi_pre"], 3750.0, places=9)
        self.assertAlmostEqual(r["hhi_post"], 3850.0, places=9)
        self.assertAlmostEqual(r["hhi_delta"], 100.0, places=9)
        self.assertFalse(bool(r["screen_flag"]))
        self.assertIsNone(r["screen_reason"])
        self.assertEqual(r["concentration_post"], "highly concentrated")

    def test_pro_forma_sorted_by_delta_desc(self):
        res = mh.pro_forma_hhi(1, 2, kind="county")
        self.assertEqual(list(res["markets"]["market_key"]),
                         ["11111", "22222", "33333"])   # Δ 2400 > 400 > 100

    def test_pro_forma_msa_kind(self):
        res = mh.pro_forma_hhi(1, 2, kind="msa")
        df = res["markets"].set_index("market_key")
        self.assertAlmostEqual(df.loc["10001", "hhi_pre"], 3000.0, places=9)
        self.assertAlmostEqual(df.loc["10001", "hhi_post"], 5400.0, places=9)

    # ── honest empties ───────────────────────────────────────────────────
    def test_missing_cert_a(self):
        res = mh.market_overlap(99, 2)
        self.assertTrue(res["markets"].empty)
        self.assertIn("no SOD rows for cert 99", res["reason"])

    def test_missing_cert_b(self):
        res = mh.pro_forma_hhi(1, 98)
        self.assertTrue(res["markets"].empty)
        self.assertIn("no SOD rows for cert 98", res["reason"])

    def test_same_institution(self):
        res = mh.market_overlap(1, 1)
        self.assertTrue(res["markets"].empty)
        self.assertIn("same institution", res["reason"])

    def test_unknown_kind(self):
        res = mh.market_overlap(1, 2, kind="zip")
        self.assertTrue(res["markets"].empty)
        self.assertIn("unknown market kind", res["reason"])

    def test_no_overlap_pair(self):
        # Force the "no overlapping markets" path: leave B only its
        # zero-deposit 66666 rows, so A and B share no market where both
        # take deposits — the reason must say so and the zero-deposit
        # market must still surface in skipped, never silently.
        with self._eng.begin() as c:
            c.execute(text(
                "DELETE FROM branches WHERE cert = 2 AND stcntybr != '66666'"
            ))
        res = mh.market_overlap(1, 2, kind="county")
        self.assertTrue(res["markets"].empty)
        self.assertIn("no overlapping markets", res["reason"])
        # the zero-deposit market is still reported in skipped, not silently
        self.assertEqual(res["skipped"][0]["market_key"], "66666")

    def test_markets_empty_dataframe_has_columns(self):
        res = mh.market_overlap(99, 2)
        self.assertEqual(list(res["markets"].columns), mh.OVERLAP_COLS)
        res2 = mh.pro_forma_hhi(99, 2)
        self.assertEqual(list(res2["markets"].columns), mh.PRO_FORMA_COLS)


class TestEmptyStore(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._orig_get_engine = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None

    def test_empty_store_reason(self):
        res = mh.market_overlap(1, 2)
        self.assertTrue(res["markets"].empty)
        self.assertEqual(res["reason"], "branches store is empty")
        res2 = mh.pro_forma_hhi(1, 2)
        self.assertEqual(res2["reason"], "branches store is empty")


if __name__ == "__main__":
    unittest.main()
