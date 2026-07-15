"""
Tests for data/ma_history.py — the completed-deal assembly behind the
Transactions section's Detailed M&A History (docs/SNL-BUILD-PLAN.md §14).
All HTTP is mocked; no live network. Pins (row shapes verified live
2026-07-13 against Umpqua 17266 / Banner 28489 / Columbia State Bank 33826):

  • whole-company acquisition rows carry target assets at the last
    REPDTE ≤ completion, converted $thousands -> RAW DOLLARS (hand-computed:
    Columbia State Bank ASSET 20,258,988 [k$] -> 20,258,988,000)
  • the cert's own terminal 2xx event -> a whole_company SALE row whose
    target assets are the bank's OWN last-filed assets
  • 712 header + same-date 722 office rows -> one branch purchase with an
    exact count; two same-date headers -> counts None (split unknowable);
    a headerless office group -> purchase with counterparty None
  • branch sales via the OUT_CERT reverse query, count recovered from the
    BUYER's cert (sellers record nothing on their own cert)
  • no SDI financials for a target (trust affiliate) -> assets None, cached
  • any history-fetch failure -> [] and nothing cached; an assets-lookup
    failure -> row kept with assets None but the assembly is NOT cached
"""
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Stub streamlit before importing data modules that may touch st decorators.
# Full house stub (see tests/test_audit_regressions.py): a minimal stub that
# wins the sys.modules setdefault race would break later suites needing
# st.fragment / streamlit.components.v1 at module load (the stub-rot trap,
# memory 2026-07-02).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)

UMPQUA = 17266
BANNER = 28489
COLUMBIA = 33826

# The four announcement fields every deal dict now carries (None unless the
# EFTS leg resolves them — patched out in these tests unless stated).
ANN_NONE = {"announce_date": None, "value_usd": None, "value_basis": None,
            "value_note": None, "target_cik": None, "announce_url": None,
            "status": "completed", "termination_date": None}


class _AnnPatched(unittest.TestCase):
    """Base: announcement + termination resolution patched to cacheable n/a."""

    def setUp(self):
        self._ann = patch("data.ma_announcements.resolve_announcement",
                          return_value=(None, True))
        self.mock_ann = self._ann.start()
        self.addCleanup(self._ann.stop)
        self._term = patch("data.ma_announcements.find_terminated_deals",
                           return_value=([], True))
        self.mock_term = self._term.start()
        self.addCleanup(self._term.stop)


def _resp(rows):
    r = MagicMock()
    r.json.return_value = {"meta": {"total": len(rows)},
                           "data": [{"data": d, "score": 0} for d in rows]}
    return r


def _merger_row(effdate, out_cert, out_name, cert=UMPQUA, code=810):
    return {"CERT": cert, "INSTNAME": "Umpqua Bank",
            "EFFDATE": f"{effdate}T00:00:00", "CHANGECODE": code,
            "CHANGECODE_DESC": "Participated in Absorbtion/Consolidation/Merger",
            "OUT_CERT": out_cert, "OUT_INSTNAME": out_name,
            "ACQ_CERT": cert, "ACQ_INSTNAME": "Umpqua Bank",
            "SUR_CERT": cert, "SUR_INSTNAME": "Umpqua Bank"}


def _terminal_row(cert, effdate, sur_cert, sur_name):
    return {"CERT": cert, "INSTNAME": None,
            "EFFDATE": f"{effdate}T00:00:00", "CHANGECODE": 223,
            "CHANGECODE_DESC": "Merger -Without Assistance",
            "OUT_CERT": cert, "OUT_INSTNAME": "Columbia State Bank",
            "ACQ_CERT": sur_cert, "ACQ_INSTNAME": sur_name,
            "SUR_CERT": sur_cert, "SUR_INSTNAME": sur_name}


def _hdr712(effdate, seller_cert, seller_name, cert=BANNER):
    return {"CERT": cert, "EFFDATE": f"{effdate}T00:00:00", "CHANGECODE": 712,
            "CHANGECODE_DESC": "Branch Purchased",
            "OUT_CERT": seller_cert, "OUT_INSTNAME": seller_name,
            "ACQ_CERT": cert, "ACQ_INSTNAME": "Banner Bank",
            "OFF_NUM": 0, "OFF_NAME": None}


def _off722(effdate, off_num, off_name, cert=BANNER):
    return {"CERT": cert, "EFFDATE": f"{effdate}T00:00:00", "CHANGECODE": 722,
            "CHANGECODE_DESC": "Branch Sold",
            "OUT_CERT": None, "OUT_INSTNAME": None,
            "ACQ_CERT": None, "ACQ_INSTNAME": None,
            "OFF_NUM": off_num, "OFF_NAME": off_name}


def _fin_row(cert, repdte, asset_thousands):
    return {"CERT": cert, "REPDTE": repdte, "ASSET": asset_thousands,
            "ID": f"{cert}_{repdte}"}


def _route(structure=(), branch=(), sold=(), buyers=None, fin=None,
           fin_fail=None, hist_fail=()):
    """side_effect for data.http.get_with_retry routing on URL + filters.

    buyers: {buyer_cert: rows} for the sale-count lookups on buyer certs.
    fin: {cert: [financial rows]}; fin_fail: set of certs whose financials
    call raises. hist_fail: substrings of history filters that should raise.
    """
    buyers = buyers or {}
    fin = fin or {}
    fin_fail = fin_fail or set()

    def side_effect(url, params, timeout=30):
        f = params.get("filters", "")
        for frag in hist_fail:
            if frag in f:
                raise Exception("connection reset")
        if "financials" in url:
            cert = int(f.split("CERT:")[1].split(" ")[0])
            if cert in fin_fail:
                raise Exception("financials down")
            return _resp(fin.get(cert, []))
        if f.startswith("OUT_CERT:"):
            return _resp(list(sold))
        if "CHANGECODE:712 OR CHANGECODE:722" in f:
            cert = int(f.split("CERT:")[1].split(" ")[0])
            for bc, rows in buyers.items():
                if cert == bc:
                    return _resp(list(rows))
            return _resp(list(branch))
        return _resp(list(structure))

    return side_effect


class TestWholeCompanyDeals(_AnnPatched):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_acquisition_with_target_assets(self, mock_get, _cg, mock_cput):
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0], {
            "completion_date": "2023-03-01",
            "deal_kind": "whole_company",
            "direction": "acquisition",
            "counterparty": {"name": "Columbia State Bank", "cert": COLUMBIA},
            "branch_count": None,
            "event_code": 810,
            "event_desc": "Participated in Absorbtion/Consolidation/Merger",
            # $20,258,988 thousand -> raw dollars (units contract at boundary)
            "target_assets": 20_258_988_000,
            "target_assets_repdte": "2022-12-31",
            **ANN_NONE,
        })
        # Assets query bounded at the completion date, newest-first, limit 1.
        fin_call = next(c for c in mock_get.call_args_list
                        if "financials" in c[0][0])
        self.assertIn("REPDTE:[19000101 TO 20230301]", fin_call[0][1]["filters"])
        self.assertEqual(fin_call[0][1]["limit"], 1)
        # Cached under the documented key.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, f"ma_history:v6:{UMPQUA}:0")
        self.assertEqual(payload["deals"], deals)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_terminal_event_is_sale_with_own_assets(self, mock_get, _cg, _cp):
        # Queried from the absorbed bank's cert: its own last-filed assets.
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_terminal_row(COLUMBIA, "2023-03-01", UMPQUA, "Umpqua Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(COLUMBIA)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["deal_kind"], "whole_company")
        self.assertEqual(deals[0]["direction"], "sale")
        self.assertEqual(deals[0]["counterparty"],
                         {"name": "Umpqua Bank", "cert": UMPQUA})
        self.assertEqual(deals[0]["target_assets"], 20_258_988_000)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_non_sdi_target_assets_na_but_cached(self, mock_get, _cg, mock_cput):
        # Trust-affiliate target with no financials: honest n/a, still cached.
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2024-01-01", 34227, "Columbia Trust Company")],
            fin={})  # cert 34227 returns no rows
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertIsNone(deals[0]["target_assets"])
        self.assertIsNone(deals[0]["target_assets_repdte"])
        mock_cput.assert_called_once()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_assets_failure_keeps_row_but_skips_cache(self, mock_get, _cg, mock_cput):
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank")],
            fin_fail={COLUMBIA})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0]["target_assets"])
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_charter_and_phantom_rows_excluded(self, mock_get, _cg, _cp):
        from data import ma_history
        charter = {"CERT": UMPQUA, "INSTNAME": "Umpqua Bank",
                   "EFFDATE": "1991-10-01T00:00:00", "CHANGECODE": 420,
                   "CHANGECODE_DESC": "Change in Chartering Agency",
                   "OUT_CERT": None, "OUT_INSTNAME": None,
                   "ACQ_CERT": None, "ACQ_INSTNAME": None,
                   "SUR_CERT": None, "SUR_INSTNAME": None}
        mock_get.side_effect = _route(structure=[charter])
        self.assertEqual(ma_history.get_ma_history(UMPQUA), [])


class TestBranchDeals(_AnnPatched):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_purchase_header_plus_offices(self, mock_get, _cg, _cp):
        # Banner 2014-06-20: one 712 header (seller Umpqua) + six 722 offices.
        from data import ma_history
        rows = [_hdr712("2014-06-20", UMPQUA, "Umpqua Bank")] + [
            _off722("2014-06-20", 205 + i, f"BRANCH {i}") for i in range(6)]
        mock_get.side_effect = _route(branch=rows)
        deals = ma_history.get_ma_history(BANNER)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0], {
            "completion_date": "2014-06-20",
            "deal_kind": "branch",
            "direction": "acquisition",
            "counterparty": {"name": "Umpqua Bank", "cert": UMPQUA},
            "branch_count": 6,
            "event_code": 712,
            "event_desc": "Branch Purchased",
            "target_assets": None,
            "target_assets_repdte": None,
            **ANN_NONE,
        })

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_two_same_date_headers_counts_unattributable(self, mock_get, _cg, _cp):
        from data import ma_history
        rows = [_hdr712("2014-06-20", UMPQUA, "Umpqua Bank"),
                _hdr712("2014-06-20", 12345, "Other Bank"),
                _off722("2014-06-20", 205, "A"), _off722("2014-06-20", 206, "B")]
        mock_get.side_effect = _route(branch=rows)
        deals = ma_history.get_ma_history(BANNER)
        self.assertEqual(len(deals), 2)
        self.assertTrue(all(d["branch_count"] is None for d in deals))

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_headerless_office_group_is_unknown_seller_purchase(self, mock_get, _cg, _cp):
        # Banner's 1992 Dayton branch: 722 office row with no 712 header.
        from data import ma_history
        mock_get.side_effect = _route(
            branch=[_off722("1992-08-31", 109, "DAYTON BRANCH")])
        deals = ma_history.get_ma_history(BANNER)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["deal_kind"], "branch")
        self.assertEqual(deals[0]["direction"], "acquisition")
        self.assertIsNone(deals[0]["counterparty"])
        self.assertEqual(deals[0]["branch_count"], 1)
        self.assertEqual(deals[0]["event_code"], 722)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_self_header_dropped(self, mock_get, _cg, _cp):
        from data import ma_history
        mock_get.side_effect = _route(
            branch=[_hdr712("2000-01-01", BANNER, "Banner Bank")])
        self.assertEqual(ma_history.get_ma_history(BANNER), [])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_sale_via_reverse_query_count_from_buyer(self, mock_get, _cg, _cp):
        # Umpqua's view of the 2014 divestiture: header lives on Banner's cert.
        from data import ma_history
        hdr = _hdr712("2014-06-20", UMPQUA, "Umpqua Bank")
        buyer_rows = [hdr] + [_off722("2014-06-20", 205 + i, f"BR {i}")
                              for i in range(6)]
        mock_get.side_effect = _route(sold=[hdr], buyers={BANNER: buyer_rows})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["deal_kind"], "branch")
        self.assertEqual(deals[0]["direction"], "sale")
        self.assertEqual(deals[0]["counterparty"],
                         {"name": "Banner Bank", "cert": BANNER})
        self.assertEqual(deals[0]["branch_count"], 6)

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_sale_count_lookup_failure_keeps_row_skips_cache(self, mock_get, _cg, mock_cput):
        from data import ma_history
        hdr = _hdr712("2014-06-20", UMPQUA, "Umpqua Bank")

        base = _route(sold=[hdr])

        def side_effect(url, params, timeout=30):
            f = params.get("filters", "")
            if f.startswith(f"CERT:{BANNER} AND (CHANGECODE:712"):
                raise Exception("buyer fetch down")
            return base(url, params, timeout)

        mock_get.side_effect = side_effect
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0]["branch_count"])
        mock_cput.assert_not_called()


class TestAnnouncementEnrichment(_AnnPatched):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_announcement_merged_with_deal_time_names(self, mock_get, _cg, mock_cput):
        from data import ma_history
        self.mock_ann.return_value = ({
            "announce_date": "2021-10-12", "value_usd": 5_100_000_000,
            "value_basis": "computed", "value_note": "computed: 0.5958 x ...",
            "url": "https://sec.gov/x", "accession": "0001-21-1"}, True)
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(deals[0]["announce_date"], "2021-10-12")
        # Assets re-anchored at the ANNOUNCE date (spec: at announcement) —
        # the second financials call is bounded by it.
        fin_bounds = [c[0][1]["filters"] for c in mock_get.call_args_list
                      if "financials" in c[0][0]]
        self.assertIn(f"CERT:{COLUMBIA} AND REPDTE:[19000101 TO 20211012]",
                      fin_bounds)
        self.assertEqual(deals[0]["value_usd"], 5_100_000_000)
        self.assertEqual(deals[0]["value_basis"], "computed")
        self.assertEqual(deals[0]["value_note"], "computed: 0.5958 x ...")
        self.assertEqual(deals[0]["announce_url"], "https://sec.gov/x")
        # Called with the names AT DEAL TIME from the structure row.
        self.mock_ann.assert_called_once_with(
            "Columbia State Bank", "Umpqua Bank", "2023-03-01")
        mock_cput.assert_called_once()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_announcement_fetch_failure_keeps_row_skips_cache(
            self, mock_get, _cg, mock_cput):
        from data import ma_history
        self.mock_ann.return_value = (None, False)
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0]["announce_date"])
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_non_sdi_target_not_enriched(self, mock_get, _cg, _cp):
        # Affiliate consolidations (no SDI financials) must never reach the
        # announcement resolver — the Columbia Trust mislinkage guard.
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2024-01-01", 34227, "Columbia Trust Company")],
            fin={})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0]["announce_date"])
        self.mock_ann.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_branch_deals_not_enriched(self, mock_get, _cg, _cp):
        from data import ma_history
        mock_get.side_effect = _route(
            branch=[_hdr712("2014-06-20", UMPQUA, "Umpqua Bank")] +
                   [_off722("2014-06-20", 205 + i, f"BR {i}") for i in range(6)])
        deals = ma_history.get_ma_history(BANNER)
        self.assertEqual(len(deals), 1)
        self.mock_ann.assert_not_called()


class TestTerminatedDeals(_AnnPatched):

    TERM = {"termination_date": "2023-05-04", "announce_date": "2022-02-28",
            "counterparty_name": "TD Bank Group", "value_usd": 13_400_000_000,
            "value_basis": "stated", "value_note": None, "direction": None,
            "announce_url": "https://sec.gov/a",
            "termination_url": "https://sec.gov/t"}

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_terminated_rows_merged_and_sorted(self, mock_get, _cg, mock_cput):
        from data import ma_history
        self.mock_term.return_value = ([dict(self.TERM)], True)
        mock_get.side_effect = _route(
            structure=[_merger_row("2024-01-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA, cik=36966)
        self.assertEqual(len(deals), 2)
        # Sorted by completion-or-termination date, newest first.
        self.assertEqual([d["status"] for d in deals],
                         ["completed", "terminated"])
        t = deals[1]
        self.assertIsNone(t["completion_date"])
        self.assertEqual(t["termination_date"], "2023-05-04")
        self.assertEqual(t["announce_date"], "2022-02-28")
        self.assertEqual(t["counterparty"],
                         {"name": "TD Bank Group", "cert": None})
        self.assertEqual(t["value_usd"], 13_400_000_000)
        self.assertEqual(t["event_desc"], "Merger agreement terminated")
        self.mock_term.assert_called_once()
        # Subject name derived from the structure rows (deal-time name).
        self.assertEqual(self.mock_term.call_args[0][1], "Umpqua Bank")
        # CIK-scoped cache key.
        self.assertEqual(mock_cput.call_args[0][0],
                         f"ma_history:v6:{UMPQUA}:36966")

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_no_cik_skips_termination_sweep(self, mock_get, _cg, _cp):
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2024-01-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertTrue(all(d["status"] == "completed" for d in deals))
        self.mock_term.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_termination_fetch_failure_skips_cache(self, mock_get, _cg, mock_cput):
        from data import ma_history
        self.mock_term.return_value = ([], False)
        mock_get.side_effect = _route(
            structure=[_merger_row("2024-01-01", COLUMBIA, "Columbia State Bank")],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA, cik=36966)
        self.assertEqual(len(deals), 1)
        mock_cput.assert_not_called()


class TestAssemblyAndCache(_AnnPatched):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_newest_first_across_deal_kinds(self, mock_get, _cg, _cp):
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank",
                                   cert=UMPQUA)],
            branch=[_hdr712("2024-05-01", 999, "Somebank", cert=UMPQUA)],
            fin={COLUMBIA: [_fin_row(COLUMBIA, "20221231", 20_258_988)]})
        deals = ma_history.get_ma_history(UMPQUA)
        self.assertEqual([d["completion_date"] for d in deals],
                         ["2024-05-01", "2023-03-01"])

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_history_fetch_failure_returns_empty_uncached(self, mock_get, _cg, mock_cput):
        from data import ma_history
        mock_get.side_effect = _route(
            structure=[_merger_row("2023-03-01", COLUMBIA, "Columbia State Bank")],
            hist_fail=("CHANGECODE:712 OR CHANGECODE:722",))
        self.assertEqual(ma_history.get_ma_history(UMPQUA), [])
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import ma_history
        cached = [{"completion_date": "2023-03-01", "deal_kind": "whole_company",
                   "direction": "acquisition", "counterparty": None,
                   "branch_count": None, "event_code": 810, "event_desc": "x",
                   "target_assets": None, "target_assets_repdte": None,
                   **ANN_NONE}]
        mock_cget.return_value = {"deals": cached,
                                  "cached_at": datetime.now().isoformat()}
        self.assertEqual(ma_history.get_ma_history(UMPQUA), cached)
        self.assertEqual(mock_get.call_count, 0)

    def test_falsy_cert_returns_empty(self):
        from data import ma_history
        self.assertEqual(ma_history.get_ma_history(None), [])
        self.assertEqual(ma_history.get_ma_history(0), [])


if __name__ == "__main__":
    unittest.main()
