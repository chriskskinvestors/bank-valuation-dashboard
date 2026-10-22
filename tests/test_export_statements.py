"""Offline tests for data/export/tables_statements.py (agent-dataset export).

Every test runs against a private SQLite store (isolate_store) populated
through the real data.cache API under the keys the seams themselves write, so
the exporter is exercised exactly as in the job: cache-only, no fetch (the
package network guard turns any live call into LiveNetworkBlocked).

Run: PYTHONIOENCODING=utf-8 python -m unittest tests.test_export_statements -v
"""
import unittest
from datetime import date
from unittest import mock

import pandas as pd

from tests._apptest_store import isolate_store

from data import cache
from data.export import tables_statements as ts

HFWA_CIK = 1046025          # data/bank_map_resolved.json (curated)
ACC_Q2 = "000104602526000050"
ACC_Q1 = "000104602526000030"
ACC_K = "000104602526000010"


def _meta(acc, filed, form="10-Q", cik=HFWA_CIK):
    return {"accession": acc, "doc": "hfwa-20260630.htm", "date": filed,
            "form": form, "cik": cik}


def _balance_q(acc=ACC_Q2, filed="2026-08-05"):
    """A stitched quarterly balance sheet in the seam's cached shape
    (HFWA Q2-2026 release figures: goodwill $279,029K, other intangibles
    $47,269K, total stockholders' equity $1,109,692K)."""
    return {
        "meta": _meta(acc, filed),
        "filings": [_meta(acc, filed), _meta(ACC_Q1, "2026-05-06")],
        "statement": {
            "periods": ["Q2'26", "Q1'26"],
            "units_scale": 1000.0,
            "rows": [
                {"label": "ASSETS", "header": True, "values": []},
                {"label": "Goodwill", "header": False, "kind": "monetary",
                 "element_id": "us-gaap_Goodwill",
                 "values": [279029000.0, 279029000.0]},
                {"label": "Other intangible assets, net", "header": False,
                 "kind": "monetary",
                 "element_id": "us-gaap_IntangibleAssetsNetExcludingGoodwill",
                 "values": [47269000.0, None]},
                {"label": "STOCKHOLDERS’ EQUITY", "header": True, "values": []},
                {"label": "Total stockholders’ equity", "header": False,
                 "kind": "monetary", "element_id": "us-gaap_StockholdersEquity",
                 "values": [1109692000.0, 1101000000.0],
                 "restated": [None, {"original": 1100000000.0,
                                     "original_source": "10-Q filed 2026-05-06",
                                     "restated_in": "10-Q filed 2026-08-05",
                                     "not_re_reported": False}]},
                {"label": "Common stock, shares outstanding (in shares)",
                 "header": False, "kind": "shares", "element_id": "",
                 "values": [33900000.0, 34000000.0]},
                {"label": "Book value per share", "header": False,
                 "kind": "pershare", "element_id": "x_BookValuePerShare",
                 "values": [27.13, 26.90]},
            ],
        },
    }


def _income_annual(filed="2026-02-20"):
    return {
        "meta": _meta(ACC_K, filed, form="10-K"),
        "filings": [_meta(ACC_K, filed, form="10-K")],
        "statement": {
            "periods": ["Dec. 31, 2025", "Dec. 31, 2024"],
            "rows": [
                {"label": "Net income", "header": False, "kind": "monetary",
                 "element_id": "us-gaap_NetIncomeLoss",
                 "values": [70000000.0, 60000000.0]},
                {"label": "Diluted (in dollars per share)", "header": False,
                 "kind": "pershare", "element_id": "us-gaap_EarningsPerShareDiluted",
                 "values": [2.05, 1.75]},
            ],
            "period_entity": [None, "Legacy Bancorp"],
        },
    }


def _key(ptype, stype, acc, n):
    prefix, ver = ts._statement_keys()[ptype]
    return f"{prefix}:{ver}:{stype}:{acc}:{n}"


class _Base(unittest.TestCase):
    def setUp(self):
        isolate_store(self)


class TestColumnsContract(_Base):
    def test_columns_match_dict_empty_and_populated(self):
        for build, cols in ((ts.build_statements_as_reported,
                             ts.COLUMNS_STATEMENTS_AS_REPORTED),
                            (ts.build_company_reported,
                             ts.COLUMNS_COMPANY_REPORTED)):
            self.assertEqual(list(build([]).columns), list(cols))
        cache.put(_key("quarter", "balance", ACC_Q2, 12), _balance_q())
        df = ts.build_statements_as_reported(["HFWA"])
        self.assertGreater(len(df), 0)
        self.assertEqual(list(df.columns), list(ts.COLUMNS_STATEMENTS_AS_REPORTED))
        self.assertEqual(df.columns[0], "ticker")
        self.assertEqual(list(ts.COLUMNS_COMPANY_REPORTED)[0], "ticker")


class TestStatementsAsReported(_Base):
    def test_labels_units_periods_preserved(self):
        cache.put(_key("quarter", "balance", ACC_Q2, 12), _balance_q())
        df = ts.build_statements_as_reported(["hfwa"])
        q2 = df[df.period_end == date(2026, 6, 30)]
        got = {r.line_label: r for r in q2.itertuples()}
        # Labels verbatim (curly apostrophe included), never re-mapped.
        self.assertIn("Total stockholders’ equity", got)
        self.assertEqual(got["Goodwill"].value, 279029000.0)
        self.assertEqual(got["Other intangible assets, net"].value, 47269000.0)
        self.assertEqual(got["Total stockholders’ equity"].value, 1109692000.0)
        self.assertEqual(got["Goodwill"].unit, "USD")
        self.assertEqual(got["Book value per share"].unit, "USD/share")
        self.assertEqual(got["Book value per share"].value, 27.13)
        self.assertEqual(
            got["Common stock, shares outstanding (in shares)"].unit, "shares")
        self.assertEqual(got["Goodwill"].section, "ASSETS")
        self.assertEqual(got["Total stockholders’ equity"].section,
                         "STOCKHOLDERS’ EQUITY")
        self.assertEqual(got["Goodwill"].line_order, 1)
        self.assertEqual(got["Goodwill"].period_label, "Q2'26")
        self.assertEqual(got["Goodwill"].period_type, "quarter")
        self.assertEqual(got["Goodwill"].xbrl_element, "us-gaap_Goodwill")
        self.assertEqual(got["Goodwill"].source_accession, ACC_Q2)
        self.assertEqual(got["Goodwill"].source_filed, date(2026, 8, 5))
        self.assertEqual(got["Goodwill"].stitched_accessions, f"{ACC_Q2};{ACC_Q1}")
        self.assertTrue(pd.isna(   # empty element id is absent, not ""
            got["Common stock, shares outstanding (in shares)"].xbrl_element))
        # Header rows are structure, not values.
        self.assertNotIn("ASSETS", set(df.line_label))

    def test_blank_cell_absent_never_zero(self):
        cache.put(_key("quarter", "balance", ACC_Q2, 12), _balance_q())
        df = ts.build_statements_as_reported(["HFWA"])
        oth = df[df.line_label == "Other intangible assets, net"]
        self.assertEqual(list(oth.period_end), [date(2026, 6, 30)])  # Q1 blank
        self.assertFalse((df.value == 0).any())

    def test_restated_cell_carries_original(self):
        cache.put(_key("quarter", "balance", ACC_Q2, 12), _balance_q())
        df = ts.build_statements_as_reported(["HFWA"])
        te = df[(df.line_label == "Total stockholders’ equity")
                & (df.period_end == date(2026, 3, 31))].iloc[0]
        self.assertTrue(te.restated)
        self.assertEqual(te.value, 1101000000.0)
        self.assertEqual(te.as_originally_reported, 1100000000.0)
        self.assertEqual(te.originally_reported_in, "10-Q filed 2026-05-06")

    def test_newest_cached_filing_wins_and_8q_window_fallback(self):
        # Older filing cached under the 12-quarter key, newer only under the
        # nightly 8-quarter key: the newer filing is exported.
        cache.put(_key("quarter", "balance", ACC_Q1, 12),
                  _balance_q(ACC_Q1, "2026-05-06"))
        cache.put(_key("quarter", "balance", ACC_Q2, 8), _balance_q())
        df = ts.build_statements_as_reported(["HFWA"])
        self.assertEqual(set(df.source_accession), {ACC_Q2})

    def test_other_cik_entries_ignored(self):
        other = _balance_q("000001961726000001", "2026-09-01")
        other["meta"]["cik"] = 19617
        cache.put(_key("quarter", "balance", "000001961726000001", 12), other)
        df = ts.build_statements_as_reported(["HFWA"])
        self.assertEqual(len(df), 0)
        self.assertEqual(df.attrs["misses"]["not_cached:balance_quarter"], 1)

    def test_annual_dates_labels_and_legacy_column(self):
        cache.put(_key("annual", "income", ACC_K, 5), _income_annual())
        df = ts.build_statements_as_reported(["HFWA"], today=date(2026, 10, 22))
        ni = df[df.line_label == "Net income"].set_index("period_end")
        self.assertEqual(ni.loc[date(2025, 12, 31), "value"], 70000000.0)
        self.assertEqual(ni.loc[date(2025, 12, 31), "period_label"], "FY2025")
        self.assertTrue(pd.isna(ni.loc[date(2025, 12, 31), "legacy_registrant"]))
        self.assertEqual(ni.loc[date(2024, 12, 31), "legacy_registrant"],
                         "Legacy Bancorp")
        self.assertEqual(set(df.period_type), {"annual"})
        eps = df[df.line_label == "Diluted (in dollars per share)"]
        self.assertEqual(set(eps.unit), {"USD/share"})

    def test_annual_stale_filer_gated_like_dashboard(self):
        cache.put(_key("annual", "income", ACC_K, 5), _income_annual("2024-02-20"))
        df = ts.build_statements_as_reported(["HFWA"], today=date(2026, 10, 22))
        self.assertEqual(len(df), 0)
        self.assertEqual(df.attrs["misses"]["stale_filer:income_annual"], 1)

    def test_misses_counted_not_fabricated(self):
        df = ts.build_statements_as_reported(["HFWA", "ZZZQX"])
        self.assertEqual(len(df), 0)
        m = df.attrs["misses"]
        self.assertEqual(m["no_cik"], 1)
        for tag in ("income_quarter", "balance_quarter", "income_annual",
                    "balance_annual"):
            self.assertEqual(m[f"not_cached:{tag}"], 1)
        self.assertEqual(df.attrs["miss_tickers"]["no_cik"], ["ZZZQX"])

    def test_unknown_kind_skipped_and_counted(self):
        res = _balance_q()
        res["statement"]["rows"][1]["kind"] = "mystery"
        cache.put(_key("quarter", "balance", ACC_Q2, 12), res)
        df = ts.build_statements_as_reported(["HFWA"])
        self.assertNotIn("Goodwill", set(df.line_label))
        self.assertEqual(df.attrs["misses"]["unknown_kind:balance_quarter"], 1)


class TestSeamKeyCoupling(_Base):
    """The exporter's keys are the keys the seams read — proven by making the
    real seam serve a value this module's key builder put in the cache."""

    def test_multiquarter_key_matches_seam(self):
        from data import sec_statements as ss
        cache.put(_key("quarter", "balance", ACC_Q2, 12), _balance_q())
        with mock.patch.object(ss, "_recent_10q_metas",
                               return_value=[_meta(ACC_Q2, "2026-08-05")]), \
                mock.patch.object(ss, "_recent_10k_metas", return_value=[]):
            got = ss.as_reported_statement_multiquarter(HFWA_CIK, "balance", 12)
        self.assertEqual(got["meta"]["accession"], ACC_Q2)

    def test_multiyear_key_matches_seam(self):
        from data import sec_statements as ss
        filed = date.today().isoformat()
        cache.put(_key("annual", "income", ACC_K, 5), _income_annual(filed))
        with mock.patch.object(ss, "_recent_10k_metas",
                               return_value=[_meta(ACC_K, filed, "10-K")]):
            got = ss.as_reported_statement_multiyear(HFWA_CIK, "income", 5)
        self.assertEqual(got["meta"]["accession"], ACC_K)

    def test_earnings_8k_key_matches_seam(self):
        from data import sec_earnings_8k as e8
        ver = ts._seam_version(e8.latest_earnings_8k_figures, "earnings_8k")
        payload = {"filed": "2026-07-24", "accession": "0001046025-26-000040",
                   "doc": "ex991.htm", "figures": {"nim": 3.5},
                   "_preliminary": True, "period": "2026-03-31"}
        cache.put(f"earnings_8k:{ver}:000104602526000040", payload)
        f8k = {"accession": "000104602526000040",
               "accession_dash": "0001046025-26-000040", "date": "2026-07-24",
               "cik": HFWA_CIK}
        with mock.patch.object(e8, "_latest_earnings_8k", return_value=f8k), \
                mock.patch.object(e8, "_release_superseded", return_value=False):
            got = e8.latest_earnings_8k_figures(HFWA_CIK)
        self.assertEqual(got["figures"], {"nim": 3.5})

    def test_version_read_from_module_not_hardcoded(self):
        from data import sec_statements as ss
        import inspect
        for ptype, fn in (("quarter", ss.as_reported_statement_multiquarter),
                          ("annual", ss.as_reported_statement_multiyear)):
            prefix, ver = ts._statement_keys()[ptype]
            self.assertIn(f'f"{prefix}:{ver}:', inspect.getsource(fn))

    def test_ambiguous_or_missing_version_raises(self):
        def _two():
            return f"x:v1:{1}", f"x:v2:{2}"
        with self.assertRaises(RuntimeError):
            ts._seam_version(_two, "x")
        with self.assertRaises(RuntimeError):
            ts._seam_version(_two, "nope")


def _release_env(**over):
    env = {
        "qend": "2026-06-30",
        "metrics": {"tbv_ps": 19.15, "bv_ps": 27.13, "nim": 3.61,
                    "eps_diluted": None, "efficiency": None,
                    "total_revenue": 70500000.0, "dil_shares": 34100.0},
        "capital": {"cet1_ratio": 12.4, "t1_ratio": None},
        "prior_metrics": {"tbv_ps": 18.90, "nim": None},
        "prior_qend": "2026-03-31",
        "yoy_metrics": {}, "yoy_qend": "2025-06-30",
        "basis_split": ["efficiency"],
        "url": "https://www.sec.gov/Archives/edgar/data/1046025/x/ex991.htm",
        "filed_date": "2026-07-23", "accession": "000104602526000040",
        "ai_state": "ok",
    }
    env.update(over)
    return env


class TestCompanyReported(_Base):
    def _put_release(self, env):
        from data import release_metrics as rm
        ver = ts._seam_version(rm.cached_release_metrics, "release_metrics")
        cache.put(f"release_metrics:{ver}:{HFWA_CIK}", {"value": env})

    def test_release_metrics_long_rows(self):
        self._put_release(_release_env())
        df = ts.build_company_reported(["HFWA"])
        cur = df[(df.period_role == "current") & (df.status == "ok")]
        vals = dict(zip(cur.metric, cur.value))
        self.assertEqual(vals["tbv_ps"], 19.15)
        self.assertEqual(vals["bv_ps"], 27.13)
        self.assertEqual(vals["cet1_ratio"], 12.4)
        units = dict(zip(cur.metric, cur.unit))
        self.assertEqual(units["tbv_ps"], "USD/share")
        self.assertEqual(units["nim"], "percent")
        self.assertEqual(units["total_revenue"], "USD")
        self.assertEqual(set(cur.quarter_end), {date(2026, 6, 30)})
        self.assertEqual(set(cur.extractor), {"release_metrics"})
        self.assertEqual(set(cur.release_date), {date(2026, 7, 23)})
        self.assertEqual(set(cur.detail), {"ai_state=ok"})
        # Absent stays absent: None metrics produce no row, never 0.
        self.assertNotIn("eps_diluted", set(df.metric))
        self.assertNotIn("t1_ratio", set(df.metric))
        self.assertFalse((df.value == 0).any())
        prior = df[df.period_role == "prior_quarter"]
        self.assertEqual(list(prior.metric), ["tbv_ps"])
        self.assertEqual(list(prior.quarter_end), [date(2026, 3, 31)])

    def test_unknown_scale_metric_excluded_and_counted(self):
        self._put_release(_release_env())
        df = ts.build_company_reported(["HFWA"])
        self.assertNotIn("dil_shares", set(df.metric))
        self.assertEqual(df.attrs["misses"]["unknown_metric:dil_shares"], 1)

    def test_basis_split_refusal_is_a_status_not_a_value(self):
        self._put_release(_release_env())
        df = ts.build_company_reported(["HFWA"])
        eff = df[df.metric == "efficiency"].iloc[0]
        self.assertEqual(eff.status, "refused_basis_split")
        self.assertTrue(eff.value is None or eff.value != eff.value)

    def test_call_notice_envelope_skipped(self):
        self._put_release(_release_env(call_notice=True))
        df = ts.build_company_reported(["HFWA"])
        self.assertEqual(len(df), 0)
        self.assertEqual(df.attrs["misses"]["call_notice:release_metrics"], 1)
        self.assertEqual(df.attrs["misses"]["no_release_data"], 1)

    def test_otc_envelope_and_history(self):
        from data import otc_release as otc
        ver = ts._seam_version(otc._env_record, "otc_release")
        env = _release_env(accession=None, ai_state=None, transport="wire",
                           source="company_release", shares_outstanding=4100000.0,
                           url="https://www.globenewswire.com/x")
        cache.put(f"otc_release:{ver}:PBAM", {"value": env})
        cache.put(otc._hist_key("PBAM"), {"value": {"quarters": {
            "2026-06-30": {"eps_diluted": 0.61, "url": "u1", "via": "release"},
            "2026-03-31": {"eps_diluted": 0.55, "url": "u0", "via": "table",
                           "conflict": True, "alt": {"eps_diluted": 0.57}},
        }}})
        df = ts.build_company_reported(["PBAM"])
        o = df[df.extractor == "otc_release"]
        self.assertEqual(dict(zip(o.metric, o.value))["shares_outstanding"],
                         4100000.0)
        self.assertEqual(set(o.detail), {"transport=wire"})
        h = df[df.extractor == "otc_eps_history"].set_index("quarter_end")
        self.assertEqual(h.loc[date(2026, 6, 30), "value"], 0.61)
        self.assertEqual(h.loc[date(2026, 6, 30), "status"], "ok")
        self.assertEqual(h.loc[date(2026, 3, 31), "status"], "conflict")
        v = h.loc[date(2026, 3, 31), "value"]
        self.assertTrue(v is None or v != v)          # refused, not 0.55

    def test_earnings_8k_headline_rows(self):
        from data import sec_earnings_8k as e8
        ver = ts._seam_version(e8.latest_earnings_8k_figures, "earnings_8k")
        cache.put(e8._submissions_ckey(HFWA_CIK), {
            "f8k": {"accession": "000104602526000040",
                    "accession_dash": "0001046025-26-000040",
                    "date": "2026-07-23", "cik": HFWA_CIK},
            "f8ks": [], "periodic": None})
        cache.put(f"earnings_8k:{ver}:000104602526000040", {
            "period": "2026-03-31", "filed": "2026-07-23",
            "accession": "0001046025-26-000040", "doc": "ex991.htm",
            "figures": {"total_assets": 7.1e9, "net_income": None,
                        "diluted_eps": 0.71, "nim": 3.61},
            "_preliminary": True})
        df = ts.build_company_reported(["HFWA"])
        e = df[df.extractor == "earnings_8k"]
        self.assertEqual(dict(zip(e.metric, e.unit)),
                         {"total_assets": "USD", "diluted_eps": "USD/share",
                          "nim": "percent"})
        self.assertEqual(set(e.quarter_end), {date(2026, 6, 30)})
        self.assertEqual(set(e.source_accession), {"000104602526000040"})
        self.assertNotIn("net_income", set(e.metric))

    def test_nothing_cached_is_a_miss(self):
        df = ts.build_company_reported(["HFWA", "ZZZQX"])
        self.assertEqual(len(df), 0)
        self.assertEqual(df.attrs["misses"]["no_release_data"], 2)


if __name__ == "__main__":
    unittest.main()
