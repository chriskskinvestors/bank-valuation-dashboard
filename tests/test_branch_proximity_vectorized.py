"""Branch Proximity speed (UX review P1-05 follow-up, 2026-10-12).

get_branch_competitors re-filtered the whole candidate frame in pandas once
per subject branch and built one dict per pair: JPM (5,142 branches, ~national
candidate set) took 35 s on prod before anything rendered. _pairs_within does
the same bbox + exact-haversine cut with a latitude-sorted binary search and
numpy. These tests pin that it is the SAME result as the old loop (kept here
verbatim as the oracle), including the boundary semantics.
"""
import math
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

import data.branches_store as bs


def _oracle(with_coords: pd.DataFrame, cand: pd.DataFrame,
            radius_miles: float) -> pd.DataFrame:
    """The pre-2026-10-12 loop from get_branch_competitors, unchanged."""
    rows: list[dict] = []
    if not cand.empty:
        for s in with_coords.itertuples(index=False):
            s_lat, s_lng = float(s.lat), float(s.lng)
            b_lat_min, b_lat_max, b_lng_min, b_lng_max = bs._bbox(
                s_lat, s_lng, radius_miles)
            near = cand[cand["lat"].between(b_lat_min, b_lat_max)
                        & cand["lng"].between(b_lng_min, b_lng_max)]
            for c in near.itertuples(index=False):
                d = bs.haversine_miles(s_lat, s_lng, float(c.lat), float(c.lng))
                if d > radius_miles:
                    continue
                rows.append({
                    "subj_brnum": int(s.brnum), "subj_branch_name": s.branch_name,
                    "subj_address": s.address, "subj_city": s.city,
                    "subj_state": s.state, "subj_lat": s_lat, "subj_lng": s_lng,
                    "subj_deposits": s.deposits, "cert": int(c.cert),
                    "brnum": int(c.brnum), "ticker": c.ticker,
                    "bank_name": c.bank_name, "branch_name": c.branch_name,
                    "address": c.address, "city": c.city, "state": c.state,
                    "zip": c.zip, "deposits": c.deposits, "lat": float(c.lat),
                    "lng": float(c.lng), "serv_type": c.serv_type,
                    "distance_miles": d,
                })
    return (pd.DataFrame(rows, columns=bs._COMPETITOR_PAIR_COLS)
            .sort_values(["subj_brnum", "distance_miles"])
            .reset_index(drop=True))


def _frame(n, rng, lat0, lng0, spread, cert0, prefix, deposits_none=False):
    lat = lat0 + rng.normal(0, spread, n)
    lng = lng0 + rng.normal(0, spread, n)
    return pd.DataFrame({
        "cert": cert0 + rng.integers(0, 40, n), "brnum": np.arange(n),
        "ticker": [None if i % 3 else f"T{i % 7}" for i in range(n)],
        "bank_name": [f"Bank {i % 40}" for i in range(n)],
        "branch_name": [f"{prefix}{i}" for i in range(n)],
        "address": "1 Main", "city": "Town", "state": "NY", "zip": "10001",
        "deposits": [None if deposits_none and i % 5 == 0 else float(1000 + i)
                     for i in range(n)],
        "lat": lat, "lng": lng, "serv_type": 11,
    })


class TestSameAsOldLoop(unittest.TestCase):
    def _check(self, subj, cand, radius):
        want = _oracle(subj, cand, radius)
        got = bs._pairs_within(subj, cand, radius)
        self.assertEqual(list(got.columns), bs._COMPETITOR_PAIR_COLS)
        self.assertEqual(len(got), len(want))
        if len(want):
            # Distances may differ in the last ulp (numpy vs libm sin/cos);
            # every other cell — and the row order — is identical.
            assert_frame_equal(got, want, check_dtype=False,
                               check_exact=False, rtol=1e-12, atol=1e-12)
            self.assertTrue((got.dtypes == want.dtypes).all(),
                            pd.concat([got.dtypes, want.dtypes], axis=1))
        return got

    def test_dense_metro_many_radii(self):
        rng = np.random.default_rng(11)
        subj = _frame(300, rng, 40.75, -73.98, 0.15, 628, "s")
        cand = _frame(4000, rng, 40.75, -73.98, 0.3, 1000, "c",
                      deposits_none=True)
        for radius in (0.5, 1.0, 5.0, 10.0):
            with self.subTest(radius=radius):
                got = self._check(subj, cand, radius)
                self.assertGreater(len(got), 0)

    def test_spread_out_and_high_latitude(self):
        rng = np.random.default_rng(3)
        subj = pd.concat([_frame(50, rng, 61.2, -149.9, 0.4, 628, "a"),   # Anchorage
                          _frame(50, rng, 25.8, -80.2, 0.4, 628, "m")],   # Miami
                         ignore_index=True)
        subj["brnum"] = np.arange(len(subj))
        cand = pd.concat([_frame(800, rng, 61.2, -149.9, 0.6, 1000, "c"),
                          _frame(800, rng, 25.8, -80.2, 0.6, 1000, "d")],
                         ignore_index=True)
        self._check(subj, cand, 5.0)

    def test_radius_cut_and_bbox_edges_match(self):
        # Due north: one candidate a hair inside the radius (kept), one a
        # hair outside (dropped) — both inside the 0.5%-inflated bbox, so the
        # exact haversine cut, not the box, decides.
        subj = _frame(1, np.random.default_rng(0), 40.0, -75.0, 0.0, 628, "s")
        r = 2.0
        deg = r / (bs._EARTH_RADIUS_MILES * math.pi / 180)
        lat0 = subj["lat"].iloc[0]
        cand = _frame(2, np.random.default_rng(1), 0, 0, 0, 1000, "c")
        cand["lat"] = [lat0 + deg * 0.9999, lat0 + deg * 1.001]
        cand["lng"] = float(subj["lng"].iloc[0])
        got = self._check(subj, cand, r)
        self.assertEqual(len(got), 1)

    def test_empty_inputs(self):
        rng = np.random.default_rng(5)
        subj = _frame(5, rng, 40, -75, 0.1, 628, "s")
        cand = _frame(5, rng, 10, 10, 0.1, 1000, "c")       # nothing in range
        self.assertTrue(self._check(subj, cand, 5.0).empty)
        self.assertTrue(bs._pairs_within(subj, cand.iloc[0:0], 5.0).empty)
        self.assertEqual(list(bs._pairs_within(subj.iloc[0:0], cand, 5.0).columns),
                         bs._COMPETITOR_PAIR_COLS)


class TestCandidateFetchIsColumnLimited(unittest.TestCase):
    def test_pair_columns_come_from_the_fetched_set(self):
        cand_side = [c for c in bs._COMPETITOR_PAIR_COLS
                     if not c.startswith("subj_") and c != "distance_miles"]
        self.assertEqual(sorted(cand_side), sorted(bs._CAND_COLS))

    def test_query_does_not_select_star(self):
        import inspect
        src = inspect.getsource(bs.proximity_inputs)
        q = src[src.index('out["cand"] = _q_to_df('):]
        self.assertNotIn("SELECT *", q)
        self.assertIn("_CAND_COLS", q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
