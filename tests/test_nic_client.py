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
"""
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
