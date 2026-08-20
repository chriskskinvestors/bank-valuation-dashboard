"""Geo helpers in data.branches_store (Branch Proximity / Competitors).

haversine_miles is pinned against hand-computed great-circle values; the
nearest/competitor queries run against an isolated in-memory SQLite branches
table (same SQL the live Postgres store runs) with branches at hand-picked
coordinates whose distances are computed by hand in the comments.

Hand computations (R = 3958.7613 mi, the module's mean Earth radius):
  1° of latitude        = R·π/180                 = 69.0934 mi
  (0,0) → (1,0)         = 69.0934 mi (same meridian, exact)
  (0,0) → (0,1)         = 69.0934 mi (equator)
  (60,0) → (60,1)       = 2R·asin(cos60°·sin0.5°)
                        = 7917.5226 · asin(0.5·0.0087265355)
                        = 7917.5226 · 0.0043632816 = 34.5464 mi
  (40,-75) → (40.1,-75) = 0.1·69.0934             =  6.9093 mi
  (40,-75) → (40,-75.1) = 2R·asin(cos40°·sin0.05°)
                        = 7917.5226 · 6.684997e-4 =  5.2929 mi
  (40,-75) → (40.14,-75)= 0.14·69.0934            =  9.6731 mi
  (41.5,-75) → (41.5,-75.05) = 2R·asin(cos41.5°·sin0.025°)
                        = 7917.5226 · 3.267936e-4 =  2.5874 mi
  (40,-75) → (40.13,-75.18) ≈ √(8.98² + 9.53²)    ≈ 13.1 mi
      (inside the 10-mile bounding box on both axes — lat 0.13 < 0.1455,
       lng 0.18 < 0.1899 — but OUTSIDE the 10-mile radius: the exact
       haversine cut must drop it after the SQL prefilter passes it.)
"""
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs


def _ins(conn, cert, brnum, lat, lng, dep=100, year=2024, name="Bank"):
    conn.execute(text(
        "INSERT INTO branches (cert, brnum, year, ticker, bank_name, "
        "branch_name, address, city, state, zip, county, stcntybr, "
        "msa_code, msa_name, deposits, lat, lng, serv_type) VALUES "
        "(:cert, :brnum, :year, NULL, :nm, 'br', 'addr', 'city', 'PA', "
        "'19000', 'Cty', '42101', '10900', 'MSA', :dep, :lat, :lng, '12')"),
        {"cert": cert, "brnum": brnum, "year": year, "nm": name,
         "dep": dep, "lat": lat, "lng": lng})


class TestHaversine(unittest.TestCase):
    def test_one_degree_latitude(self):
        # R·π/180 = 3958.7613 × 0.0174532925 = 69.0934 (hand, exact formula)
        self.assertAlmostEqual(bs.haversine_miles(0, 0, 1, 0), 69.0934,
                               delta=0.001)

    def test_one_degree_longitude_at_equator(self):
        self.assertAlmostEqual(bs.haversine_miles(0, 0, 0, 1), 69.0934,
                               delta=0.001)

    def test_one_degree_longitude_at_60N(self):
        # 2R·asin(cos60°·sin0.5°) = 34.5464 (hand; ≈ 69.0934·cos60° = 34.55)
        self.assertAlmostEqual(bs.haversine_miles(60, 0, 60, 1), 34.5464,
                               delta=0.005)

    def test_zero_distance(self):
        self.assertEqual(bs.haversine_miles(40.0, -75.0, 40.0, -75.0), 0.0)

    def test_symmetry(self):
        self.assertAlmostEqual(
            bs.haversine_miles(40.0, -75.0, 41.5, -75.05),
            bs.haversine_miles(41.5, -75.05, 40.0, -75.0), places=9)

    def test_lax_jfk_sanity(self):
        # Published LAX (33.9425,-118.4081) – JFK (40.6398,-73.7789)
        # great-circle ≈ 2,469–2,475 mi depending on Earth model.
        d = bs.haversine_miles(33.9425, -118.4081, 40.6398, -73.7789)
        self.assertGreater(d, 2460)
        self.assertLess(d, 2480)


class TestBbox(unittest.TestCase):
    """The SQL prefilter box must CONTAIN the radius circle (never drops an
    in-radius point) while staying tight (≤ 2% oversize per axis)."""

    def test_box_contains_circle_but_stays_tight(self):
        lat_min, lat_max, lng_min, lng_max = bs._bbox(40.0, -75.0, 10.0)
        dlat_10 = 10.0 / bs._MILES_PER_DEG_LAT               # exact 10 mi N/S
        import math
        dlng_10 = 10.0 / (bs._MILES_PER_DEG_LAT
                          * math.cos(math.radians(40.0)))    # ≈ 10 mi E/W
        # circle extremes strictly inside the box
        self.assertLess(40.0 + dlat_10, lat_max)
        self.assertGreater(40.0 - dlat_10, lat_min)
        self.assertLess(-75.0 + dlng_10, lng_max)
        self.assertGreater(-75.0 - dlng_10, lng_min)
        # a point 10.2 mi out on each axis is OUTSIDE the box (tightness)
        self.assertGreater(40.0 + dlat_10 * 1.02, lat_max)
        self.assertLess(40.0 - dlat_10 * 1.02, lat_min)
        self.assertGreater(-75.0 + dlng_10 * 1.02, lng_max)
        self.assertLess(-75.0 - dlng_10 * 1.02, lng_min)


class TestGeoQueries(unittest.TestCase):
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
            # subject bank, cert 1
            _ins(c, 1, 1, 40.0, -75.0, name="Subject")     # search center S1
            _ins(c, 1, 2, None, None, name="Subject")      # missing coords
            _ins(c, 1, 3, 41.5, -75.0, name="Subject")     # second center S3
            _ins(c, 1, 4, 40.01, -75.0, name="Subject")    # own-bank, near S1
            # competitors (distances from S1 hand-computed in module docstring)
            _ins(c, 2, 1, 40.1, -75.0, name="CompA")       # 6.9093 mi
            _ins(c, 3, 1, 40.0, -75.1, name="CompB")       # 5.2929 mi
            _ins(c, 4, 1, 41.0, -75.0, name="Far")         # 69.09 mi
            _ins(c, 5, 1, None, None, name="NoGeo")        # no coords
            _ins(c, 6, 1, 40.13, -75.18, name="Corner")    # in box, 13.1 mi
            _ins(c, 7, 1, 41.5, -75.05, name="Near3")      # 2.5874 mi from S3
            _ins(c, 8, 1, 40.14, -75.0, name="Edge")       # 9.6731 mi
            # stale survey year — must be ignored
            _ins(c, 9, 1, 40.0, -75.001, year=2023, name="OldYear")

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None

    # ── get_nearest_branches ─────────────────────────────────────────────
    def test_nearest_order_and_distances(self):
        res = bs.get_nearest_branches(1, 40.0, -75.0, limit=10, max_miles=10)
        df = res["branches"]
        self.assertEqual(list(df["cert"]), [3, 2, 8])   # 5.29 < 6.91 < 9.67
        self.assertAlmostEqual(df["distance_miles"].iloc[0], 5.2929,
                               delta=0.002)
        self.assertAlmostEqual(df["distance_miles"].iloc[1], 6.9093,
                               delta=0.002)
        self.assertAlmostEqual(df["distance_miles"].iloc[2], 9.6731,
                               delta=0.002)
        self.assertEqual(res["year"], 2024)

    def test_nearest_excludes_own_bank_and_stale_year(self):
        res = bs.get_nearest_branches(1, 40.0, -75.0, limit=10, max_miles=10)
        self.assertNotIn(1, set(res["branches"]["cert"]))   # own branches out
        self.assertNotIn(9, set(res["branches"]["cert"]))   # 2023 row out

    def test_nearest_in_box_but_outside_radius_excluded(self):
        # cert 6 (40.13, -75.18) passes the SQL bounding box for a 10-mile
        # radius (0.13° lat < 0.1455, 0.18° lng < 0.1899) but sits ~13.1 mi
        # away — the exact haversine cut must drop it.
        res = bs.get_nearest_branches(1, 40.0, -75.0, limit=10, max_miles=10)
        self.assertNotIn(6, set(res["branches"]["cert"]))

    def test_nearest_limit_and_radius(self):
        res = bs.get_nearest_branches(1, 40.0, -75.0, limit=2, max_miles=10)
        self.assertEqual(list(res["branches"]["cert"]), [3, 2])
        res6 = bs.get_nearest_branches(1, 40.0, -75.0, limit=10, max_miles=6)
        self.assertEqual(list(res6["branches"]["cert"]), [3])  # only 5.29 mi

    def test_nearest_missing_coords_counted_not_guessed(self):
        res = bs.get_nearest_branches(1, 40.0, -75.0, limit=10, max_miles=10)
        # cert 5's coordinate-less row is excluded AND counted — never
        # silently treated as far away. Subject's own no-coord branch is not
        # in the competitor count.
        self.assertEqual(res["n_missing_coords"], 1)
        self.assertNotIn(5, set(res["branches"]["cert"]))

    def test_nearest_empty_store(self):
        with self._eng.begin() as c:
            c.execute(text("DELETE FROM branches"))
        res = bs.get_nearest_branches(1, 40.0, -75.0)
        self.assertIsNone(res["year"])
        self.assertTrue(res["branches"].empty)

    # ── get_branch_competitors ───────────────────────────────────────────
    def test_competitor_pairs_grouped_per_subject_branch(self):
        res = bs.get_branch_competitors(1, radius_miles=10)
        pairs = res["pairs"]
        by_subj = {int(k): set(g["cert"]) for k, g in
                   pairs.groupby("subj_brnum")}
        # S1 (40.0,-75.0) and S4 (40.01,-75.0) each see certs 2, 3, 8;
        # S3 (41.5,-75.0) sees only cert 7. Subject brnum 2 has no coords.
        self.assertEqual(by_subj, {1: {2, 3, 8}, 3: {7}, 4: {2, 3, 8}})
        self.assertEqual(len(pairs), 7)
        self.assertIsNone(res["reason"])

    def test_competitor_counts_and_exclusions(self):
        res = bs.get_branch_competitors(1, radius_miles=10)
        self.assertEqual(res["n_subject_branches"], 4)
        self.assertEqual(res["n_subject_missing_coords"], 1)   # brnum 2
        self.assertEqual(res["n_competitor_missing_coords"], 1)  # cert 5
        certs = set(res["pairs"]["cert"])
        self.assertNotIn(1, certs)   # never pairs with own bank
        self.assertNotIn(6, certs)   # in-box/out-of-radius dropped
        self.assertNotIn(9, certs)   # stale year dropped

    def test_competitor_distance_hand_value(self):
        res = bs.get_branch_competitors(1, radius_miles=10)
        pairs = res["pairs"]
        row = pairs[(pairs["subj_brnum"] == 3) & (pairs["cert"] == 7)]
        # (41.5,-75) → (41.5,-75.05) = 2.5874 mi (hand, module docstring)
        self.assertAlmostEqual(row["distance_miles"].iloc[0], 2.5874,
                               delta=0.003)

    def test_competitor_sorted_by_branch_then_distance(self):
        res = bs.get_branch_competitors(1, radius_miles=10)
        pairs = res["pairs"]
        self.assertTrue(pairs["subj_brnum"].is_monotonic_increasing)
        for _, g in pairs.groupby("subj_brnum"):
            self.assertTrue(g["distance_miles"].is_monotonic_increasing)

    def test_competitor_missing_bank(self):
        res = bs.get_branch_competitors(99, radius_miles=10)
        self.assertTrue(res["pairs"].empty)
        self.assertIn("no SOD branches for cert 99", res["reason"])
        self.assertEqual(list(res["pairs"].columns),
                         bs._COMPETITOR_PAIR_COLS)

    def test_competitor_empty_store(self):
        with self._eng.begin() as c:
            c.execute(text("DELETE FROM branches"))
        res = bs.get_branch_competitors(1)
        self.assertEqual(res["reason"], "branches store is empty")
        self.assertIsNone(res["year"])

    # ── has_branches ─────────────────────────────────────────────────────
    def test_has_branches(self):
        self.assertTrue(bs.has_branches(1))
        self.assertTrue(bs.has_branches(9, year=2023))
        self.assertFalse(bs.has_branches(9, year=2024))
        self.assertFalse(bs.has_branches(99))


if __name__ == "__main__":
    unittest.main()
