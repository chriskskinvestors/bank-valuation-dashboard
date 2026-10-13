"""Branch Proximity nightly snapshots (owner decision 2026-10-13: "store what
the page shows").

analysis.branch_proximity builds the page's view — stat pills, top-50
rollup, 25 x 10 per-branch rows, map points — without materialising the full
pair frame; jobs/refresh_sod stores one per universe bank x radius; the page
serves it only while the branches store is unchanged since it was built
(data.branches_store.store_version, bumped by every writer). Pins:

  * the view is exactly what the old page derived from the full pair frame
    (every radius, caps forced small so every cap path runs);
  * one fetch at the largest radius == a live fetch per radius;
  * the snapshot round-trip keeps absent deposits absent (never 0);
  * a stale / missing / unversioned snapshot is never served;
  * every branches writer moves the version; a write during a build leaves
    that build stale;
  * the job builds after its writes; the page renders a snapshot without
    computing; the Export builds the full pair list on click.
Hermetic: one in-memory SQLite engine for both the branches store and the
cache.
"""
import inspect
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from tests import _streamlit_stub

_streamlit_stub.install()

import data.db as db  # noqa: E402
import data.cache as cache  # noqa: E402
import data.branches_store as bs  # noqa: E402
import analysis.branch_proximity as ap  # noqa: E402

Y = 2025


def _rows(rng):
    """Subject 'AAA' runs two charters (certs 1, 2) around Philadelphia;
    ~600 competitor branches from 40 banks; some deposits unreported; three
    competitors co-located (equal distances — order must be deterministic)."""
    out = []

    def add(cert, brnum, ticker, name, lat, lng, dep):
        out.append(dict(cert=cert, brnum=brnum, year=Y, ticker=ticker,
                        bank_name=name, branch_name=f"{name} {brnum}",
                        address=f"{brnum} Main", city="Town", state="PA",
                        zip="19000", county="c", stcntybr="42101",
                        msa_code="1", msa_name="m", deposits=dep,
                        lat=lat, lng=lng, serv_type="11"))
    for i in range(30):
        add(1 if i % 3 else 2, i, "AAA", "Alpha Bank",
            39.95 + rng.normal(0, 0.12), -75.16 + rng.normal(0, 0.15),
            float(rng.integers(1_000, 900_000)) if i % 7 else None)
    add(1, 99, "AAA", "Alpha Bank", None, None, 5_000.0)     # no coords
    for j in range(600):
        k = j % 40
        add(100 + k, j, f"T{k}" if k % 4 == 0 else None, f"Bank {k}",
            39.95 + rng.normal(0, 0.2), -75.16 + rng.normal(0, 0.25),
            None if j % 11 == 0 else float(rng.integers(500, 500_000)))
    for k in range(3):                                         # co-located
        add(200 + k, 0, None, f"Twin {k}", 39.951, -75.161, 10_000.0)
    add(300, 0, None, "NoGeo", None, None, 1.0)                # no coords
    return out


class _Store(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine("sqlite://", poolclass=StaticPool,
                                  connect_args={"check_same_thread": False})
        self._orig_get_engine = db.get_engine
        self._orig_cache_engine = cache._engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        cache._engine = None
        bs._canon_memo.clear()
        bs.init_branches_schema()
        pd.DataFrame(_rows(np.random.default_rng(42))).to_sql(
            "branches", self._eng, if_exists="append", index=False)
        # The cert map is a network resolver — pin it (hermetic).
        self._certs = mock.patch("data.bank_mapping.get_fdic_cert",
                                 lambda t: {"AAA": 1}.get(t))
        self._certs.start()
        self.addCleanup(self._certs.stop)

    def tearDown(self):
        db.get_engine = self._orig_get_engine
        bs._engine = None
        cache._engine = self._orig_cache_engine
        bs._canon_memo.clear()


def _old_page_view(cert, radius):
    """What the page derived before 2026-10-13: the full pair frame from
    get_branch_competitors, then the page's own dedupe / rollup / caps."""
    res = bs.get_branch_competitors(cert, radius_miles=float(radius))
    pairs = res["pairs"]
    uniq = pairs.drop_duplicates(subset=["cert", "brnum"])
    ro = ap.bank_rollup(uniq)
    shown = ap.select_branch_rows(pairs, ap.TABLE_MAX_BRANCHES,
                                  ap.TABLE_MAX_COMPETITORS)
    n_in = pairs.groupby("subj_brnum").size()
    return {
        "n_pairs": len(pairs), "n_comp_branches": len(uniq),
        "n_comp_banks": uniq["cert"].nunique(),
        "comp_deposits": (float(uniq["deposits"].astype(float).sum())
                          if len(uniq) else None),
        "rollup": ro.head(ap.ROLLUP_MAX_BANKS)[ap.ROLLUP_COLS].reset_index(drop=True),
        "n_rollup": len(ro),
        "comp_points": ap.map_competitors(uniq, ap.MAP_MAX_COMP_BRANCHES,
                                          ap.ROLLUP_MAX_BANKS)[ap.MAP_COLS]
                       .reset_index(drop=True),
        "shown": shown[ap.SHOWN_COLS].reset_index(drop=True),
        "shown_n_in_range": {int(k): int(n_in[k])
                             for k in shown["subj_brnum"].unique()},
        "n_branches_with_comp": len(n_in),
    }


# Caps forced small so the rollup, map and per-branch cap paths all run.
_SMALL_CAPS = dict(TABLE_MAX_BRANCHES=4, TABLE_MAX_COMPETITORS=3,
                   ROLLUP_MAX_BANKS=5, MAP_MAX_COMP_BRANCHES=20)


class TestViewMatchesOldPage(_Store):
    def _assert_same(self, got, want):
        for k in ("n_pairs", "n_comp_branches", "n_comp_banks", "n_rollup",
                  "n_branches_with_comp", "shown_n_in_range"):
            self.assertEqual(got[k], want[k], k)
        self.assertAlmostEqual(got["comp_deposits"], want["comp_deposits"])
        for k in ("rollup", "comp_points", "shown"):
            assert_frame_equal(got[k].reset_index(drop=True), want[k],
                               check_dtype=False, check_exact=False,
                               rtol=1e-12, obj=k)

    def test_every_radius_with_caps_engaged(self):
        with mock.patch.multiple(ap, **_SMALL_CAPS):
            built = ap.build_views(1)                  # one max-radius fetch
            for r in ap.RADII:
                with self.subTest(radius=r):
                    want = _old_page_view(1, r)
                    self.assertGreater(want["n_pairs"], 0)
                    live = ap.view_from_inputs(
                        bs.proximity_inputs(1, float(r)), r)
                    self._assert_same(live, want)
                    self._assert_same(built[float(r)], want)
                    self.assertLess(len(live["shown"]), want["n_pairs"])

    def test_counts_and_coverage_fields(self):
        v = ap.build_views(1)[5.0]
        self.assertEqual(v["n_subject_branches"], 31)      # both charters
        self.assertEqual(v["n_subject_missing_coords"], 1)
        self.assertEqual(v["n_competitor_missing_coords"], 1)
        self.assertEqual(len(v["subj_points"]), 30)
        self.assertEqual(v["year"], Y)

    def test_nothing_in_range_is_an_empty_view(self):
        v = ap.build_views(1, radii=(0.0001,))[0.0001]
        self.assertEqual(v["n_pairs"], 0)
        self.assertIsNone(v["comp_deposits"])
        self.assertIn("no competitor branches within", v["reason"])


class TestSerialisation(_Store):
    def test_round_trip_keeps_absent_absent(self):
        v = ap.build_views(1)[10.0]
        back = ap.deserialize_view(ap.serialize_view(v))
        import json
        json.dumps(ap.serialize_view(v))                    # JSON-clean
        for k in ("subj_points", "comp_points", "rollup", "shown"):
            assert_frame_equal(back[k], v[k].reset_index(drop=True),
                               check_dtype=False, obj=k)
        self.assertTrue(back["subj_points"]["deposits"].isna().any())
        self.assertFalse((back["subj_points"]["deposits"] == 0).any())
        self.assertEqual(back["shown_n_in_range"], v["shown_n_in_range"])


class TestServeOnlyWhenCurrent(_Store):
    def test_live_until_built_then_snapshot(self):
        _, src = ap.load_view(1, 5)
        self.assertEqual(src, "live")
        ap.build_snapshots(["AAA"], log=lambda m: None)
        view, src = ap.load_view(1, 5)
        self.assertEqual(src, "snapshot")
        live = ap.view_from_inputs(bs.proximity_inputs(1, 5.0), 5)
        self.assertEqual(view["n_pairs"], live["n_pairs"])

    def test_any_store_write_makes_it_stale(self):
        ap.build_snapshots(["AAA"], log=lambda m: None)
        self.assertEqual(ap.load_view(1, 5)[1], "snapshot")
        bs.retag_tickers(Y, {100: "NEWT"})                  # a real change
        self.assertEqual(ap.load_view(1, 5)[1], "live")

    def test_retag_without_change_keeps_it(self):
        ap.build_snapshots(["AAA"], log=lambda m: None)
        bs.retag_tickers(Y, {1: "AAA"})                     # already tagged
        self.assertEqual(ap.load_view(1, 5)[1], "snapshot")

    def test_unknown_version_is_never_served(self):
        ap.build_snapshots(["AAA"], log=lambda m: None)
        with mock.patch.object(bs, "store_version", lambda: None):
            self.assertEqual(ap.load_view(1, 5)[1], "live")

    def test_write_during_build_leaves_it_stale(self):
        real = ap.build_views

        def racing(*a, **k):
            out = real(*a, **k)
            bs._bump_store_version()                         # concurrent write
            return out
        with mock.patch.object(ap, "build_views", racing):
            ap.build_snapshots(["AAA"], log=lambda m: None)
        self.assertEqual(ap.load_view(1, 5)[1], "live")

    def test_one_build_per_owner(self):
        with mock.patch("data.bank_mapping.get_fdic_cert",
                        lambda t: {"AAA": 1, "AAA.B": 2}.get(t)):
            stats = ap.build_snapshots(["AAA", "AAA.B", "ZZZ"],
                                       log=lambda m: None)
        self.assertEqual((stats["built"], stats["skipped"], stats["failed"]),
                         (1, 2, 0))


class TestWritersBumpVersion(_Store):
    def test_upsert_bumps(self):
        before = bs.store_version()
        bs.upsert_branches("ZZZ", 999, pd.DataFrame([{
            "CERT": 999, "YEAR": Y, "NAMEFULL": "Zed", "BRNUM": 0,
            "NAMEBR": "Zed 0", "ADDRESBR": "a", "CITYBR": "c", "STALPBR": "PA",
            "ZIPBR": "19000", "CNTYNAMB": "c", "STCNTYBR": "42101",
            "MSABR": 1, "MSANAMB": "m", "DEPSUMBR": 1.0,
            "SIMS_LATITUDE": 40.0, "SIMS_LONGITUDE": -75.0, "BRSERTYP": "11"}]))
        self.assertNotEqual(bs.store_version(), before)

    def test_every_writer_calls_the_bump(self):
        for fn in (bs.upsert_branches, bs.reattribute_absorbed_branches,
                   bs.retag_tickers):
            self.assertIn("_bump_store_version()", inspect.getsource(fn),
                          fn.__name__)


class TestJobBuildsAfterWrites(unittest.TestCase):
    def test_proximity_mode_and_ownership_pass(self):
        import jobs.refresh_sod as job
        calls = []
        with mock.patch.object(job, "build_proximity_snapshots",
                               lambda: calls.append("prox") or True), \
                mock.patch("data.branches_store.init_branches_schema"), \
                mock.patch.object(job.sys, "argv", ["x", "proximity"]):
            self.assertEqual(job.main(), 0)
        self.assertEqual(calls, ["prox"])
        src = inspect.getsource(job.main)
        own = src[src.index('if "ownership" in sys.argv'):]
        self.assertLess(own.index("apply_post_survey_ownership("),
                        own.index("build_proximity_snapshots()"))
        tail = src[src.index("apply_post_survey_ownership(\n        sod_year"):]
        self.assertIn("build_proximity_snapshots()", tail)


class TestPageServesSnapshotWithoutComputing(_Store):
    def test_render_from_snapshot(self):
        import ui.branch_proximity as bp
        import ui.geo_view as gv
        ap.build_snapshots(["AAA"], log=lambda m: None)
        md, exports = [], []
        fake = types.SimpleNamespace(
            selectbox=lambda *a, **k: 5, markdown=lambda x, **k: md.append(x),
            caption=lambda x, **k: md.append(x), info=lambda x, **k: md.append(x),
            spinner=lambda *a, **k: mock.MagicMock())

        def boom(*a, **k):
            raise AssertionError("page computed proximity live")
        with mock.patch.object(bp, "st", fake), \
                mock.patch.object(bp, "title_bar", lambda *a, **k: None), \
                mock.patch.object(bp, "pill_row", lambda p, **k: md.extend(p)), \
                mock.patch.object(bp, "stat_pill", lambda l, v, **k: f"{l}: {v}"), \
                mock.patch.object(bp, "get_fdic_cert", lambda t: 1), \
                mock.patch.object(bp, "get_name", lambda t: "Alpha"), \
                mock.patch.object(bp, "table_export",
                                  lambda df, *a, **k: exports.append(df)), \
                mock.patch.object(gv, "_render_map", lambda df, **k: None), \
                mock.patch.object(bs, "proximity_inputs", boom):
            bp.render_branch_proximity("AAA")
        blob = "\n".join(map(str, md))
        want = ap.build_views(1)[5.0]
        self.assertIn(f"COMPETITOR BRANCHES IN RANGE: {want['n_comp_branches']:,}",
                      blob)
        self.assertIn("Who competes within 5 miles", blob)
        # The Export is a callable: the full pair list is built on click.
        self.assertTrue(callable(exports[0]))
        pairs = exports[0]()
        self.assertEqual(len(pairs), want["n_pairs"])
        self.assertIn("Distance (mi)", pairs.columns)


class TestTableExportAcceptsCallable(unittest.TestCase):
    def test_callable_built_on_click(self):
        import ui.export as ex
        built, captured = [], {}

        class Ctx:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        fake = types.SimpleNamespace(
            container=lambda **k: Ctx(),
            download_button=lambda label, data, **k: captured.update(data=data))

        def make():
            built.append(1)
            return pd.DataFrame({"a": [1, 2]})
        with mock.patch.object(ex, "st", fake):
            ex.table_export(make, "f", "k")
        self.assertEqual(built, [])                        # not at render
        self.assertTrue(captured["data"]()[:2] == b"PK")   # xlsx on click
        self.assertEqual(built, [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
