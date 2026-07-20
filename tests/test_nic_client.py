"""
Tests for data/nic_client.py — the Fed NIC organizational-hierarchy client
behind the Corporate Structure sub-tab (docs/SNL-BUILD-PLAN.md §12). All
bulk-file access is via small synthetic CSV fixtures written to a temp dir
(_bulk_path is patched); no live downloads. Pins:

  • tree assembly: holdco → bank + trust, bank → sub-LLC, sorted by stake
  • ownership propagation: PCT_EQUITY parsed per edge; 0 → None (other-basis)
  • inactive relationship rows (DT_END != 99991231) excluded
  • depth limit: generation MAX_DEPTH included, MAX_DEPTH+1 cut
  • cycle guard: A↔B ownership loop terminates, no infinite recursion
  • missing rssd → None (tree and parent); top-of-chain parent → None
  • fresh cache entry short-circuits the bulk files entirely
  • fetch_y9c_pdf ladder: GCS mirror hit serves whatever its age; Cloud
    Run NEVER falls through to NPW direct; a dev direct fetch heals the
    mirror under the shared y9c_mirror_name contract
  • tools/refresh_y9c_mirror target building: RSSDHCR grouping with
    summed multi-bank assets, $3B floor, largest-first order
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Stub streamlit before importing data modules (config may pull it in).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

ATTR_HEADER = ("#ID_RSSD,NM_LGL,NM_SHORT,ENTITY_TYPE,CITY,"
               "STATE_ABBR_NM,CNTRY_NM")
REL_HEADER = ("#ID_RSSD_PARENT,ID_RSSD_OFFSPRING,PCT_EQUITY,"
              "CTRL_IND,DT_END")

# Synthetic org: 100 HOLDCO CORP (BHC)
#   ├─ 200 FIRST BANK (NMB, 100%, controlled)
#   │    └─ 400 BANK SUB LLC (DEO, 60%, controlled)
#   ├─ 300 CAPITAL TRUST I (DEO, 100%, controlled)
#   └─ 500 OLD SUB (DEO) — relationship CLOSED in 2019 (must be excluded)
# 999 exists in RELATIONSHIPS history only (closed), not in ATTRIBUTES.
ATTR_ROWS = [
    '100,"HOLDCO CORP          ",HOLDCO,BHC,"WALLA WALLA",WA,"UNITED STATES  "',
    '200,"FIRST BANK",FIRST BK,NMB,"WALLA WALLA",WA,"UNITED STATES"',
    '300,"CAPITAL TRUST I",CAP TR,DEO,"SACRAMENTO",CA,"UNITED STATES"',
    '400,"BANK SUB LLC",BANK SUB,DEO,"BOISE",ID,"UNITED STATES"',
    '500,"OLD SUB",OLD SUB,DEO,"PORTLAND",OR,"UNITED STATES"',
]
REL_ROWS = [
    "100,200,100.00,1,99991231",
    "100,300,100.00,1,99991231",
    "200,400,60.00,1,99991231",
    "100,500,100.00,1,20190630",   # closed — excluded
    "999,100,0.00,0,20150101",     # closed historical parent of the holdco
]


def _write_fixture(dirpath: Path, attr_rows, rel_rows) -> dict:
    attr = dirpath / "attributes_active.csv"
    rel = dirpath / "relationships.csv"
    attr.write_text("\n".join([ATTR_HEADER] + list(attr_rows)) + "\n",
                    encoding="utf-8")
    rel.write_text("\n".join([REL_HEADER] + list(rel_rows)) + "\n",
                   encoding="utf-8")
    return {"attributes_active": attr, "relationships": rel}


class _Base(unittest.TestCase):
    """Fixture CSVs on disk + _bulk_path/data.cache patched."""

    attr_rows = ATTR_ROWS
    rel_rows = REL_ROWS

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nic_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        paths = _write_fixture(self.tmp, self.attr_rows, self.rel_rows)

        p_bulk = patch("data.nic_client._bulk_path",
                       side_effect=lambda name: paths[name])
        self.mock_bulk = p_bulk.start()
        self.addCleanup(p_bulk.stop)

        p_get = patch("data.cache.get", return_value=None)
        self.mock_cget = p_get.start()
        self.addCleanup(p_get.stop)

        p_put = patch("data.cache.put")
        self.mock_cput = p_put.start()
        self.addCleanup(p_put.stop)


class TestTreeAssembly(_Base):

    def test_holdco_tree_shape_and_attributes(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(100)
        self.assertIsNotNone(tree)
        self.assertEqual(tree["entity"]["name"], "HOLDCO CORP")  # stripped
        self.assertEqual(tree["entity"]["rssd"], 100)
        self.assertEqual(tree["entity"]["type"], "Bank Holding Company")
        self.assertEqual(tree["entity"]["type_code"], "BHC")
        self.assertEqual(tree["entity"]["location"], "Walla Walla, WA")

        # Two ACTIVE children (closed 500 excluded), sorted by stake/name.
        kids = tree["children"]
        self.assertEqual([k["entity"]["rssd"] for k in kids], [200, 300])
        bank = kids[0]
        self.assertEqual(bank["entity"]["name"], "FIRST BANK")
        self.assertEqual(bank["entity"]["type"], "State Non-member Bank")
        self.assertEqual(bank["ownership_pct"], 100.0)
        self.assertEqual(bank["relationship"], "Controlled")

        # Grandchild under the bank.
        self.assertEqual(len(bank["children"]), 1)
        llc = bank["children"][0]
        self.assertEqual(llc["entity"]["rssd"], 400)
        self.assertEqual(llc["entity"]["location"], "Boise, ID")
        self.assertEqual(llc["children"], [])

        # Trust is a leaf.
        self.assertEqual(kids[1]["entity"]["rssd"], 300)
        self.assertEqual(kids[1]["children"], [])

        # Result cached under the documented key with a freshness stamp.
        key, payload = self.mock_cput.call_args[0]
        self.assertEqual(key, "nic:tree:100")
        self.assertIn("cached_at", payload)

    def test_ownership_propagates_per_edge(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(100)
        bank = tree["children"][0]
        self.assertEqual(bank["ownership_pct"], 100.0)       # holdco → bank
        self.assertEqual(bank["children"][0]["ownership_pct"], 60.0)  # bank → llc

    def test_closed_relationship_excluded(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(100)
        rssds = {k["entity"]["rssd"] for k in tree["children"]}
        self.assertNotIn(500, rssds)

    def test_missing_rssd_returns_none(self):
        from data import nic_client
        self.assertIsNone(nic_client.get_org_hierarchy(777777))
        self.mock_cput.assert_not_called()  # never cache a failure

    def test_rssd_in_relationships_but_not_attributes_returns_none(self):
        # 999 appears in RELATIONSHIPS history but not ATTRIBUTES-ACTIVE.
        from data import nic_client
        self.assertIsNone(nic_client.get_org_hierarchy(999))


class TestOwnershipParsing(_Base):

    rel_rows = [
        "100,200,0.00,1,99991231",    # control via other basis — pct 0
        "100,300,49.50,0,99991231",   # minority, non-controlled
    ]

    def test_zero_pct_is_none_never_zero(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(100)
        by_rssd = {k["entity"]["rssd"]: k for k in tree["children"]}
        self.assertIsNone(by_rssd[200]["ownership_pct"])
        self.assertEqual(by_rssd[200]["relationship"], "Controlled")
        self.assertEqual(by_rssd[300]["ownership_pct"], 49.5)
        self.assertEqual(by_rssd[300]["relationship"], "Non-controlled")

    def test_sort_puts_known_stake_first(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(100)
        # 49.5% known stake sorts ahead of None (treated as 0).
        self.assertEqual([k["entity"]["rssd"] for k in tree["children"]],
                         [300, 200])


class TestDepthLimit(_Base):

    # Chain 1→2→3→4→5→6: generations 1..5 below root 1.
    attr_rows = [f'{i},"ENTITY {i}",E{i},DEO,"BOISE",ID,"UNITED STATES"'
                 for i in range(1, 7)]
    rel_rows = [f"{i},{i + 1},100.00,1,99991231" for i in range(1, 6)]

    def test_tree_cut_at_max_depth(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(1)
        node, depth = tree, 0
        while node["children"]:
            node = node["children"][0]
            depth += 1
        # MAX_DEPTH (4) generations included: 2,3,4,5 — entity 6 cut.
        self.assertEqual(depth, nic_client.MAX_DEPTH)
        self.assertEqual(node["entity"]["rssd"], 5)


class TestCycleGuard(_Base):

    attr_rows = [
        '10,"LOOP PARENT",LP,BHC,"BOISE",ID,"UNITED STATES"',
        '20,"LOOP CHILD",LC,DEO,"BOISE",ID,"UNITED STATES"',
    ]
    rel_rows = [
        "10,20,100.00,1,99991231",
        "20,10,5.00,0,99991231",   # child owns a sliver of parent — cycle
    ]

    def test_cycle_terminates(self):
        from data import nic_client
        tree = nic_client.get_org_hierarchy(10)
        child = tree["children"][0]
        self.assertEqual(child["entity"]["rssd"], 20)
        # The back-edge to the ancestor is attached but NOT recursed into.
        self.assertEqual(len(child["children"]), 1)
        self.assertEqual(child["children"][0]["entity"]["rssd"], 10)
        self.assertEqual(child["children"][0]["children"], [])


class TestGetParent(_Base):

    def test_parent_of_bank_is_holdco(self):
        from data import nic_client
        parent = nic_client.get_parent(200)
        self.assertIsNotNone(parent)
        self.assertEqual(parent["rssd"], 100)
        self.assertEqual(parent["name"], "HOLDCO CORP")
        self.assertEqual(parent["type_code"], "BHC")
        self.assertEqual(parent["ownership_pct"], 100.0)
        self.assertEqual(parent["relationship"], "Controlled")
        key, payload = self.mock_cput.call_args[0]
        self.assertEqual(key, "nic:parent:200")
        self.assertIn("cached_at", payload)

    def test_top_of_chain_returns_none_and_caches(self):
        # Holdco 100's only parent row (999) is CLOSED → no active parent.
        from data import nic_client
        self.assertIsNone(nic_client.get_parent(100))
        key, payload = self.mock_cput.call_args[0]
        self.assertEqual(key, "nic:parent:100")
        self.assertIsNone(payload["parent"])

    def test_missing_rssd_returns_none(self):
        from data import nic_client
        self.assertIsNone(nic_client.get_parent(777777))


class TestBulkSourceLadder(unittest.TestCase):
    """_bulk_path's source ladder (module doc): local → GCS mirror → NPW
    direct → stale copies. NPW 403s Cloud Run egress (2026-07-14), so the
    GCS-mirror leg IS the production path — these pin it with the real
    _bulk_path (no patching of the function under test)."""

    ZIP = b"PK\x03\x04fake-zip-payload"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nic_bulk_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p_dir = patch("data.nic_client._BULK_DIR", self.tmp)
        p_load = patch("data.cloud_storage.load_bytes", return_value=None)
        p_save = patch("data.cloud_storage.save_bytes", return_value=True)
        p_dl = patch("data.nic_client._download", return_value=None)
        p_dir.start()
        self.addCleanup(p_dir.stop)
        self.mock_load = p_load.start()
        self.addCleanup(p_load.stop)
        self.mock_save = p_save.start()
        self.addCleanup(p_save.stop)
        self.mock_dl = p_dl.start()
        self.addCleanup(p_dl.stop)

    def test_fresh_mirror_serves_without_touching_npw(self):
        from data import nic_client
        self.mock_load.return_value = (self.ZIP, 3600.0)  # fresh blob
        path = nic_client._bulk_path("relationships")
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), self.ZIP)
        self.mock_dl.assert_not_called()

    def test_stale_mirror_served_when_npw_fails(self):
        from data import nic_client
        age = nic_client.BULK_TTL_SECONDS + 86400.0
        self.mock_load.return_value = (self.ZIP, age)
        path = nic_client._bulk_path("relationships")
        self.assertEqual(path.read_bytes(), self.ZIP)
        self.mock_dl.assert_called_once()  # NPW tried first for stale blob

    def test_npw_success_heals_the_mirror(self):
        from data import nic_client
        self.mock_dl.return_value = self.ZIP
        path = nic_client._bulk_path("attributes_active")
        self.assertEqual(path.read_bytes(), self.ZIP)
        prefix, filename, data = self.mock_save.call_args[0][:3]
        self.assertEqual((prefix, filename), ("nic_bulk",
                                              "attributes_active.zip"))
        self.assertEqual(data, self.ZIP)

    def test_everything_down_returns_none(self):
        from data import nic_client
        self.assertIsNone(nic_client._bulk_path("relationships"))

    def test_non_zip_mirror_object_ignored(self):
        from data import nic_client
        self.mock_load.return_value = (b"<html>challenge page</html>", 60.0)
        self.assertIsNone(nic_client._bulk_path("relationships"))
        self.mock_dl.assert_called_once()  # fell through to NPW

    def test_fresh_local_file_short_circuits_everything(self):
        from data import nic_client
        (self.tmp / "relationships.zip").write_bytes(self.ZIP)
        path = nic_client._bulk_path("relationships")
        self.assertEqual(path, self.tmp / "relationships.zip")
        self.mock_load.assert_not_called()
        self.mock_dl.assert_not_called()


class TestY9cSourceLadder(unittest.TestCase):
    """fetch_y9c_pdf's ladder: GCS mirror → NPW direct (dev only — NEVER
    on Cloud Run, where a guaranteed 403 would burn per-IP bot score) →
    None. Facsimiles are immutable, so a mirror hit serves at ANY age."""

    PDF = b"%PDF-1.7 fake-facsimile"

    def setUp(self):
        p_load = patch("data.cloud_storage.load_bytes", return_value=None)
        p_save = patch("data.cloud_storage.save_bytes", return_value=True)
        p_curl = patch("data.nic_client._curl_fetch", return_value=None)
        self.mock_load = p_load.start()
        self.addCleanup(p_load.stop)
        self.mock_save = p_save.start()
        self.addCleanup(p_save.stop)
        self.mock_curl = p_curl.start()
        self.addCleanup(p_curl.stop)
        # Tests must behave the same on a dev box and in CI containers.
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("K_SERVICE", None)
        os.environ.pop("CLOUD_RUN_JOB", None)

    def test_mirror_hit_serves_at_any_age_without_touching_npw(self):
        from data import nic_client
        self.mock_load.return_value = (self.PDF, 400 * 86400.0)  # very old
        out = nic_client.fetch_y9c_pdf(1027004, "20260331")
        self.assertEqual(out, self.PDF)
        self.mock_curl.assert_not_called()

    def test_cloud_run_service_never_hits_npw_direct(self):
        from data import nic_client
        os.environ["K_SERVICE"] = "bank-dashboard"
        self.assertIsNone(nic_client.fetch_y9c_pdf(1027004, "20260331"))
        self.mock_curl.assert_not_called()

    def test_cloud_run_job_never_hits_npw_direct(self):
        from data import nic_client
        os.environ["CLOUD_RUN_JOB"] = "nic-mirror-verify"
        self.assertIsNone(nic_client.fetch_y9c_pdf(1027004, "20260331"))
        self.mock_curl.assert_not_called()

    def test_dev_direct_fetch_heals_the_mirror(self):
        from data import nic_client
        self.mock_curl.return_value = self.PDF
        out = nic_client.fetch_y9c_pdf(1027004, "20260331")
        self.assertEqual(out, self.PDF)
        prefix, filename, data = self.mock_save.call_args[0][:3]
        self.assertEqual((prefix, filename), ("y9c", "1027004_20260331.pdf"))
        self.assertEqual(data, self.PDF)

    def test_non_pdf_mirror_object_ignored(self):
        from data import nic_client
        self.mock_load.return_value = (b"<html>challenge page</html>", 60.0)
        self.assertIsNone(nic_client.fetch_y9c_pdf(1027004, "20260331"))
        self.mock_curl.assert_called_once()  # fell through to NPW (dev)

    def test_invalid_args_never_fetch(self):
        from data import nic_client
        for rssd, dt in ((None, "20260331"), (1027004, "2026-03-31"),
                         (1027004, "202603"), (0, "20260331")):
            self.assertIsNone(nic_client.fetch_y9c_pdf(rssd, dt))
        self.mock_load.assert_not_called()
        self.mock_curl.assert_not_called()

    def test_mirror_name_contract(self):
        from data.nic_client import y9c_mirror_name
        self.assertEqual(y9c_mirror_name(1027004, "20260331"),
                         "1027004_20260331.pdf")


class TestY9cMirrorTool(unittest.TestCase):
    """tools/refresh_y9c_mirror pure logic — target building + the
    latest-filed-quarter rule."""

    def test_targets_grouped_summed_floored_and_sorted(self):
        from tools.refresh_y9c_mirror import _holdco_targets_from_records
        records = [
            # multi-bank holdco: neither sub clears $3B alone, sum does
            {"CERT": 1, "RSSDHCR": "100", "ASSET": 2_000_000},
            {"CERT": 2, "RSSDHCR": "100", "ASSET": 1_500_000},
            {"CERT": 3, "RSSDHCR": "200", "ASSET": 50_000_000},
            {"CERT": 4, "RSSDHCR": "", "ASSET": 9_000_000},    # no holdco
            {"CERT": 5, "RSSDHCR": "0", "ASSET": 9_000_000},   # 0 sentinel
            {"CERT": 6, "RSSDHCR": "300", "ASSET": 500_000},   # under floor
            {"CERT": 7, "RSSDHCR": None, "ASSET": 9_000_000},
        ]
        self.assertEqual(_holdco_targets_from_records(records),
                         [(200, 50_000_000), (100, 3_500_000)])

    def test_latest_y9c_period_respects_45_day_window(self):
        import pandas as pd
        from tools.refresh_y9c_mirror import _latest_y9c_period
        # 2026-07-14: Q2 ended 14 days ago (not filed) → Q1.
        self.assertEqual(_latest_y9c_period(pd.Timestamp(2026, 7, 14)),
                         "20260331")
        # 2026-08-20: Q2 deadline (+45d) passed → Q2.
        self.assertEqual(_latest_y9c_period(pd.Timestamp(2026, 8, 20)),
                         "20260630")

    def test_y9c_filer_override(self):
        import json as _json
        from data import nic_client
        body = _json.dumps({"1238565": 3606542, "55": 0}).encode("utf-8")
        with patch("data.cloud_storage.load_bytes",
                   return_value=(body, 10.0)):
            self.assertEqual(nic_client.y9c_filer_override(1238565), 3606542)
            self.assertIsNone(nic_client.y9c_filer_override(55))   # 0 → None
            self.assertIsNone(nic_client.y9c_filer_override(999))  # unmapped
        with patch("data.cloud_storage.load_bytes", return_value=None):
            self.assertIsNone(nic_client.y9c_filer_override(1238565))

    def test_fetch_y9c_classified(self):
        from tools import refresh_y9c_mirror as R

        def run_with(stdout):
            proc = types.SimpleNamespace(stdout=stdout, returncode=0)
            with patch.object(R.shutil, "which", return_value="curl"), \
                 patch.object(R.subprocess, "run", return_value=proc):
                return R._fetch_y9c_classified(123, "20260331")

        pdf = b"%PDF-" + b"x" * R._MIN_PDF_BYTES
        # OK: PDF body, trailer stripped so the returned bytes are clean.
        status, content = run_with(pdf + R._META + b"200 https://x/pdf")
        self.assertEqual(status, R._OK)
        self.assertEqual(content, pdf)
        # ABSENT: NPW redirected to the onError page.
        status, _ = run_with(b"<html>err</html>" + R._META
                             + b"200 https://www.ffiec.gov/npw/Home/onError")
        self.assertEqual(status, R._ABSENT)
        # BLOCKED: Cloudflare 403 (no onError redirect).
        status, _ = run_with(b"" + R._META + b"403 https://x/ReturnFinancialReportPDF")
        self.assertEqual(status, R._BLOCKED)

    def test_foreign_holdco_rssds(self):
        # Foreign parents (FHF/FBH) file FR Y-7 not Y-9C → flagged for filer
        # resolution. US filers (BHC) and NIC-unknown RSSDs are not flagged.
        from tools import refresh_y9c_mirror as R
        from pathlib import Path
        targets = [(1238565, 900), (1025541, 500), (7777777, 100)]
        attrs = {1238565: {"type_code": "FHF"},   # Toronto-Dominion
                 1025541: {"type_code": "BHC"}}    # Westamerica (US)
        with patch.object(R.nic_client, "_bulk_path",
                          return_value=Path("attrs.zip")), \
             patch.object(R.nic_client, "_load_attributes",
                          return_value=attrs):
            self.assertEqual(R._foreign_holdco_rssds(targets), {1238565})

    def test_foreign_holdco_rssds_degrades_when_nic_unavailable(self):
        from tools import refresh_y9c_mirror as R
        targets = [(1238565, 900), (1025541, 500)]
        with patch.object(R.nic_client, "_bulk_path", return_value=None):
            self.assertEqual(R._foreign_holdco_rssds(targets), set())

    def test_resolve_filer_probes_topmost_first_verified_by_fetch(self):
        # Chain bank(10) → P1(20) → P2(30) → stop(99). Candidates probed
        # top-down: P2 absent (e.g. a non-filing midco), P1 serves a real
        # facsimile → P1 is the filer, proven by its own PDF.
        from tools import refresh_y9c_mirror as R
        pdf = b"%PDF-" + b"x" * R._MIN_PDF_BYTES
        edges = {10: [{"rssd": 20, "controlled": True, "ownership_pct": 100}],
                 20: [{"rssd": 30, "controlled": True, "ownership_pct": 100}],
                 30: [{"rssd": 99, "controlled": True, "ownership_pct": 100}]}
        outcome = {30: (R._ABSENT, None), 20: (R._OK, pdf)}
        with patch.object(R.nic_client, "_scan_parent_edges",
                          side_effect=lambda cur, rel: edges.get(cur, [])), \
             patch.object(R, "_fetch_y9c_classified",
                          side_effect=lambda rssd, p: outcome[rssd]), \
             patch.object(R.time, "sleep"):
            filer, content, spent = R._resolve_filer(10, 99, "20260331", "rel")
        self.assertEqual((filer, content, spent), (20, pdf, 2))

    def test_resolve_filer_blocked_records_nothing(self):
        from tools import refresh_y9c_mirror as R
        edges = {10: [{"rssd": 30, "controlled": True, "ownership_pct": 100}],
                 30: [{"rssd": 99, "controlled": True, "ownership_pct": 100}]}
        with patch.object(R.nic_client, "_scan_parent_edges",
                          side_effect=lambda cur, rel: edges.get(cur, [])), \
             patch.object(R, "_fetch_y9c_classified",
                          return_value=(R._BLOCKED, None)), \
             patch.object(R.time, "sleep"):
            filer, content, spent = R._resolve_filer(10, 99, "20260331", "rel")
        self.assertEqual((filer, content), (None, None))  # retry next run

    def test_resolve_filer_exhausted_chain_confirms_no_filer(self):
        from tools import refresh_y9c_mirror as R
        edges = {10: [{"rssd": 30, "controlled": True, "ownership_pct": 100}],
                 30: [{"rssd": 99, "controlled": True, "ownership_pct": 100}]}
        with patch.object(R.nic_client, "_scan_parent_edges",
                          side_effect=lambda cur, rel: edges.get(cur, [])), \
             patch.object(R, "_fetch_y9c_classified",
                          return_value=(R._ABSENT, None)), \
             patch.object(R.time, "sleep"):
            filer, content, spent = R._resolve_filer(10, 99, "20260331", "rel")
        self.assertEqual((filer, content, spent), (0, None, 1))

    def test_resolve_filer_bank_directly_under_stop(self):
        # No intermediate holdco between the bank and the non-filing top
        # holder → nothing can file; zero NPW hits spent.
        from tools import refresh_y9c_mirror as R
        edges = {10: [{"rssd": 99, "controlled": True, "ownership_pct": 100}]}
        with patch.object(R.nic_client, "_scan_parent_edges",
                          side_effect=lambda cur, rel: edges.get(cur, [])), \
             patch.object(R.time, "sleep"):
            filer, content, spent = R._resolve_filer(10, 99, "20260331", "rel")
        self.assertEqual((filer, content, spent), (0, None, 0))

    def test_dead_credential_fails_fast_before_any_fetch(self):
        # cloud_storage swallows auth errors (list_files → []), so a dead ADC
        # makes the mirror look empty and the run would re-fetch everything.
        # The startup probe write must catch it before any NPW hit.
        from tools import refresh_y9c_mirror as R
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "save_bytes", return_value=False), \
             patch.object(R, "_fetch_fdic_records") as mock_fdic, \
             patch.object(R, "_fetch_y9c_classified") as mock_fetch:
            rc = R.main()
        self.assertEqual(rc, 1)
        mock_fdic.assert_not_called()
        mock_fetch.assert_not_called()

    def test_mapped_high_holder_fetches_under_filer_rssd(self):
        # A high holder in the filer map fetches (and mirrors) under the
        # FILER's RSSD — the same name the UI override computes.
        from tools import refresh_y9c_mirror as R
        records = [{"CERT": 1, "RSSDHCR": "1238565", "ASSET": 9_000_000,
                    "FED_RSSD": "497404"}]
        good = (R._OK, b"%PDF-" + b"x" * R._MIN_PDF_BYTES)
        saved = []
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "_latest_y9c_period", return_value="20260331"), \
             patch.object(R, "_fetch_fdic_records", return_value=records), \
             patch.object(R.nic_client, "_bulk_path", return_value=None), \
             patch.object(R, "list_files", return_value=[]), \
             patch.object(R, "_load_manifest", return_value={}), \
             patch.object(R, "_load_filer_map",
                          return_value={"1238565": 3606542}), \
             patch.object(R, "_fetch_y9c_classified", return_value=good), \
             patch.object(R, "save_bytes",
                          side_effect=lambda *a, **k: saved.append(a[1]) or True), \
             patch.object(R, "_save_manifest"), \
             patch.object(R.time, "sleep"):
            rc = R.main()
        self.assertEqual(rc, 0)
        self.assertIn(R.y9c_mirror_name(3606542, "20260331"), saved)

    def test_unmapped_foreign_holdco_resolves_and_persists_map(self):
        from tools import refresh_y9c_mirror as R
        from pathlib import Path
        records = [{"CERT": 1, "RSSDHCR": "1238565", "ASSET": 9_000_000,
                    "FED_RSSD": "497404"}]
        pdf = b"%PDF-" + b"x" * R._MIN_PDF_BYTES
        saved = []
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "_latest_y9c_period", return_value="20260331"), \
             patch.object(R, "_fetch_fdic_records", return_value=records), \
             patch.object(R, "_foreign_holdco_rssds",
                          return_value={1238565}), \
             patch.object(R.nic_client, "_bulk_path",
                          return_value=Path("rel.zip")), \
             patch.object(R, "list_files", return_value=[]), \
             patch.object(R, "_load_manifest", return_value={}), \
             patch.object(R, "_load_filer_map", return_value={}), \
             patch.object(R, "_resolve_filer",
                          return_value=(3606542, pdf, 1)) as mock_resolve, \
             patch.object(R, "save_bytes",
                          side_effect=lambda *a, **k: saved.append(a[1]) or True), \
             patch.object(R, "_save_filer_map") as mock_savefm, \
             patch.object(R, "_save_manifest"), \
             patch.object(R.time, "sleep"):
            rc = R.main()
        self.assertEqual(rc, 0)
        mock_resolve.assert_called_once()
        self.assertEqual(mock_resolve.call_args[0][:2], (497404, 1238565))
        self.assertEqual(mock_savefm.call_args[0][0], {"1238565": 3606542})
        self.assertIn(R.y9c_mirror_name(3606542, "20260331"), saved)

    def test_max_fetches_env_override(self):
        from tools import refresh_y9c_mirror as R
        with patch.dict(os.environ, {"Y9C_MAX_FETCHES": "50"}):
            self.assertEqual(R._max_fetches(), 50)
        with patch.dict(os.environ, {"Y9C_MAX_FETCHES": "0"}):   # invalid
            self.assertEqual(R._max_fetches(), R._MAX_FETCHES_PER_RUN)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(R._max_fetches(), R._MAX_FETCHES_PER_RUN)

    def test_circuit_breaker_aborts_on_consecutive_upload_failures(self):
        # Simulate ADC dying mid-run: every fetch is a good PDF, but every GCS
        # upload fails. The run must abort after _MAX_CONSEC_UPLOAD_FAILS
        # attempts, not hammer NPW for all targets.
        from tools import refresh_y9c_mirror as R
        records = [{"CERT": i, "RSSDHCR": str(1000 + i),
                    "ASSET": 5_000_000} for i in range(20)]
        good = (R._OK, b"%PDF-" + b"x" * R._MIN_PDF_BYTES)
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "_latest_y9c_period", return_value="20260331"), \
             patch.object(R, "_fetch_fdic_records", return_value=records), \
             patch.object(R.nic_client, "_bulk_path", return_value=None), \
             patch.object(R, "list_files", return_value=[]), \
             patch.object(R, "_load_manifest", return_value={}), \
             patch.object(R, "_fetch_y9c_classified", return_value=good), \
             patch.object(R, "save_bytes",
                          side_effect=lambda *a, **k: a[1] == "_health.txt") \
                as mock_save, \
             patch.object(R.time, "sleep"):
            rc = R.main()
        self.assertEqual(rc, 1)  # loud failure
        # Aborted at the breaker (+1 for the startup health probe), not
        # after all 20 targets.
        self.assertEqual(mock_save.call_count,
                         R._MAX_CONSEC_UPLOAD_FAILS + 1)

    def test_successful_upload_resets_consecutive_failure_counter(self):
        # A success between failures resets the breaker — a single flaky
        # upload must not abort a healthy run.
        from tools import refresh_y9c_mirror as R
        records = [{"CERT": i, "RSSDHCR": str(1000 + i),
                    "ASSET": 5_000_000} for i in range(6)]
        good = (R._OK, b"%PDF-" + b"x" * R._MIN_PDF_BYTES)
        # health probe ok, then fail, ok, fail, ok, fail, ok — never 3 in a row.
        saves = [True, False, True, False, True, False, True]
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "_latest_y9c_period", return_value="20260331"), \
             patch.object(R, "_fetch_fdic_records", return_value=records), \
             patch.object(R.nic_client, "_bulk_path", return_value=None), \
             patch.object(R, "list_files", return_value=[]), \
             patch.object(R, "_load_manifest", return_value={}), \
             patch.object(R, "_fetch_y9c_classified", return_value=good), \
             patch.object(R, "save_bytes", side_effect=saves), \
             patch.object(R, "_save_manifest"), \
             patch.object(R.time, "sleep"):
            rc = R.main()
        # All 6 attempted (no early abort); 3 upload failures → rc 1.
        self.assertEqual(rc, 1)

    def test_absent_recorded_blocked_not_recorded(self):
        # The crux of a true-zero miss rate: a non-filer (onError) is recorded
        # to the manifest and counts as done; a Cloudflare block is NOT
        # recorded (so it retries next run) and forces a non-zero exit.
        from tools import refresh_y9c_mirror as R
        # rssd 1000 → real filer, 1001 → absent (non-filer), 1002 → blocked.
        records = [{"CERT": 0, "RSSDHCR": "1000", "ASSET": 9_000_000},
                   {"CERT": 1, "RSSDHCR": "1001", "ASSET": 8_000_000},
                   {"CERT": 2, "RSSDHCR": "1002", "ASSET": 7_000_000}]
        good = (R._OK, b"%PDF-" + b"x" * R._MIN_PDF_BYTES)
        outcomes = {1000: good, 1001: (R._ABSENT, None),
                    1002: (R._BLOCKED, None)}
        manifest = {}
        saved = {}
        with patch.object(R, "is_gcs_enabled", return_value=True), \
             patch.object(R, "_latest_y9c_period", return_value="20260331"), \
             patch.object(R, "_fetch_fdic_records", return_value=records), \
             patch.object(R.nic_client, "_bulk_path", return_value=None), \
             patch.object(R, "list_files", return_value=[]), \
             patch.object(R, "_load_manifest", return_value=manifest), \
             patch.object(R, "_fetch_y9c_classified",
                          side_effect=lambda rssd, period: outcomes[rssd]), \
             patch.object(R, "save_bytes",
                          side_effect=lambda *a, **k: saved.setdefault(
                              a[1], a[2]) or True), \
             patch.object(R, "_save_manifest") as mock_savem, \
             patch.object(R.time, "sleep"):
            rc = R.main()
        self.assertEqual(rc, 1)  # a block → re-run signal
        # Only the real filer was uploaded (ignoring the health probe).
        self.assertEqual([n for n in saved if n != "_health.txt"],
                         [R.y9c_mirror_name(1000, "20260331")])
        # The persisted manifest holds the non-filer, NOT the blocked filer.
        persisted = mock_savem.call_args[0][0]
        self.assertIn(R.y9c_mirror_name(1001, "20260331"), persisted)
        self.assertNotIn(R.y9c_mirror_name(1002, "20260331"), persisted)


class TestRefreshToolValidation(unittest.TestCase):
    """tools/refresh_nic_bulk._validate — the gate that keeps a Cloudflare
    challenge page or truncated fetch from clobbering the good mirror."""

    def _zip_with_header(self, header: str, pad: int = 1_100_000) -> bytes:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("data.csv", header + "\n" + "x" * pad)
        return buf.getvalue()

    def test_good_relationships_zip_passes(self):
        from tools.refresh_nic_bulk import _validate
        header = ("#ID_RSSD_PARENT,ID_RSSD_OFFSPRING,PCT_EQUITY,"
                  "CTRL_IND,DT_END,OTHER")
        self.assertIsNone(_validate("relationships",
                                    self._zip_with_header(header)))

    def test_missing_column_rejected(self):
        from tools.refresh_nic_bulk import _validate
        header = "#ID_RSSD_PARENT,ID_RSSD_OFFSPRING,DT_END"  # no PCT_EQUITY
        problem = _validate("relationships", self._zip_with_header(header))
        self.assertIn("PCT_EQUITY", problem or "")

    def test_small_or_non_zip_rejected(self):
        from tools.refresh_nic_bulk import _validate
        self.assertIsNotNone(_validate("relationships", b"PK tiny"))
        self.assertIsNotNone(_validate("relationships",
                                       b"<html>" + b"x" * 1_100_000))


class TestCaching(_Base):

    def test_fresh_tree_cache_short_circuits_bulk_files(self):
        from data import nic_client
        cached_tree = {"entity": {"rssd": 100}, "children": [],
                       "cached_at": datetime.now().isoformat()}
        self.mock_cget.return_value = cached_tree
        out = nic_client.get_org_hierarchy(100)
        self.assertEqual(out, cached_tree)
        self.mock_bulk.assert_not_called()

    def test_stale_tree_cache_reparses(self):
        from data import nic_client
        stale = (datetime.now() - timedelta(days=31)).isoformat()
        self.mock_cget.return_value = {"entity": {"rssd": 100},
                                       "children": [], "cached_at": stale}
        out = nic_client.get_org_hierarchy(100)
        self.assertEqual(len(out["children"]), 2)  # rebuilt from fixtures
        self.assertGreater(self.mock_bulk.call_count, 0)

    def test_fresh_no_parent_cache_short_circuits(self):
        from data import nic_client
        self.mock_cget.return_value = {"parent": None,
                                       "cached_at": datetime.now().isoformat()}
        self.assertIsNone(nic_client.get_parent(100))
        self.mock_bulk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
