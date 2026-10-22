"""Agent dataset export — history tables (data/export/tables_history.py).

bank_quarterly: deep-store rows per charter, ×1000 at the boundary, the
store's read-time literal-zero fixes inherited, multi-charter rows NOT
summed, former charters only while held.
holdco_quarterly: hand-computed companyfacts fixtures — MCHB carried
goodwill (TCE 1,540,004,000 at 2025-06-30), Q4 = FY − 9M (and a 10-Q's
12-month tag never used as the FY), EPS never differenced, the MCHB
reverse-merger basis break, JPM's zero-share-count preferred, pre-coverage
dates, the bank-goodwill guard, same-date share rules. Offline: every
identity / cache seam is the store or a patch.
"""
import json
import unittest
from datetime import date
from unittest.mock import patch

from tests import _streamlit_stub  # noqa: F401

from data.export import tables_history as th


def _usd(*rows):
    """companyfacts USD entries: (end, val, form, filed[, start])."""
    out = []
    for r in rows:
        e = {"end": r[0], "val": r[1], "form": r[2], "filed": r[3],
             "accn": f"acc-{r[3]}"}
        if len(r) > 4:
            e["start"] = r[4]
        out.append(e)
    return {"units": {"USD": out}}


def _per_share(*rows):
    node = _usd(*rows)
    return {"units": {"USD/shares": node["units"]["USD"]}}


def _shares(*rows):
    node = _usd(*rows)
    return {"units": {"shares": node["units"]["USD"]}}


def _facts(us_gaap: dict, dei: dict | None = None) -> dict:
    return {"facts": {"us-gaap": us_gaap, "dei": dei or {}}}


def _rows_by_end(rows):
    return {r["period_end"].isoformat(): r for r in rows}


# ── bank_quarterly ─────────────────────────────────────────────────────────────
class _StoreCase(unittest.TestCase):
    """Private in-memory store for the deep history + cache tables."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.cache as cache
        import data.db as db
        import data.fdic_history_store as store
        saved = (db._engine, db.USE_POSTGRES, store._engine, store._USE_POSTGRES,
                 cache._engine)

        def restore():
            (db._engine, db.USE_POSTGRES, store._engine, store._USE_POSTGRES,
             cache._engine) = saved
        self.addCleanup(restore)
        db._engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                   poolclass=StaticPool, future=True)
        db.USE_POSTGRES = store._USE_POSTGRES = False
        store._engine = cache._engine = None
        store.init_history_schema()
        self.store = store


class TestBankQuarterly(_StoreCase):
    def _seed(self):
        s = self.store
        # Lead charter 101: a 2026 row and a 2014 pre-Basel-III row whose CET1
        # FDIC reports as a literal 0 beside positive Tier 1.
        s.upsert_history(101, [
            {"REPDTE": "20260630", "CERT": 101, "REPNM": "Alpha Bank", "ASSET": 5000,
             "EQTOT": 600, "INTAN": 50, "INTANGW": 40, "NETINC": 40, "ROA": 1.23,
             "RBCT1J": 800, "RBCT1C": 750, "IDT1CER": 18.75, "RWAJ": 4000,
             "RSSDHCR": 555, "NUMEMP": 120},
            {"REPDTE": "20140930", "CERT": 101, "REPNM": "Alpha Bank", "ASSET": 4000,
             "RBCT1J": 700, "RBCT1C": 0, "IDT1CER": 0, "RWAJ": 3500, "RSSDHCR": 555},
        ])
        # Sibling charter 102 under the same holdco, same quarter.
        s.upsert_history(102, [
            {"REPDTE": "20260630", "CERT": 102, "REPNM": "Beta Bank", "ASSET": 3000,
             "ROA": 0.80, "RSSDHCR": 555}])
        # Former charter 103: held by holdco 555 in 2021, by someone else in 2020.
        s.upsert_history(103, [
            {"REPDTE": "20210930", "CERT": 103, "REPNM": "Gamma Bank", "ASSET": 900,
             "RSSDHCR": 555.0},
            {"REPDTE": "20200930", "CERT": 103, "REPNM": "Gamma Bank", "ASSET": 850,
             "RSSDHCR": 999}])

    def _build(self, tickers=("ALPH",)):
        self._seed()
        with patch.object(th, "_cert_for", return_value=101), \
                patch("data.cert_group.get_cert_group_cached", return_value=[101, 102]), \
                patch("data.cert_group.get_absorbed_charters", return_value=("555", [103])):
            return th.build_bank_quarterly(list(tickers))

    def test_columns_match_the_dictionary(self):
        df = self._build()
        self.assertEqual(list(df.columns), list(th.COLUMNS_BANK_QUARTERLY))
        empty = th.build_bank_quarterly([])
        self.assertEqual(list(empty.columns), list(th.COLUMNS_BANK_QUARTERLY))
        self.assertEqual(list(empty.columns)[0], "ticker")

    def test_thousands_to_dollars_and_ratios_as_reported(self):
        df = self._build()
        r = df[(df.cert == 101) & (df.report_date == date(2026, 6, 30))].iloc[0]
        self.assertEqual(r.total_assets, 5_000_000.0)       # 5,000 $K × 1000
        self.assertEqual(r.total_equity, 600_000.0)
        self.assertEqual(r.intangibles_total, 50_000.0)     # INTAN: total intangibles
        self.assertEqual(r.goodwill, 40_000.0)              # INTANGW: goodwill only
        self.assertEqual(r.cet1_capital, 750_000.0)         # RBCT1C, not RBCT1J
        self.assertEqual(r.tier1_capital, 800_000.0)
        self.assertEqual(r.roa_ytd, 1.23)                   # percent, unscaled
        self.assertEqual(r.cet1_ratio, 18.75)
        self.assertEqual(r.employees, 120.0)                # count, unscaled
        self.assertEqual(r.holdco_rssd, 555)
        self.assertEqual(r.charter_name, "Alpha Bank")
        # Raw JSON keeps FDIC's own units ($K).
        self.assertEqual(json.loads(r.fdic_fields_json)["ASSET"], 5000)
        # Absent field → None, never 0.
        self.assertTrue(r.loans_c_and_i is None or r.loans_c_and_i != r.loans_c_and_i)

    def test_pre_adoption_zero_is_none(self):
        df = self._build()
        r = df[(df.cert == 101) & (df.report_date == date(2014, 9, 30))].iloc[0]
        self.assertTrue(r.cet1_ratio is None or r.cet1_ratio != r.cet1_ratio)
        self.assertTrue(r.cet1_capital is None or r.cet1_capital != r.cet1_capital)
        self.assertEqual(r.tier1_capital, 700_000.0)
        self.assertIsNone(json.loads(r.fdic_fields_json)["IDT1CER"])

    def test_multi_charter_rows_are_not_summed(self):
        df = self._build()
        q = df[df.report_date == date(2026, 6, 30)]
        self.assertEqual(sorted(q.cert), [101, 102])
        self.assertEqual(sorted(q.total_assets), [3_000_000.0, 5_000_000.0])
        self.assertNotIn(8_000_000.0, list(df.total_assets))
        self.assertEqual(dict(zip(q.cert, q.is_primary_charter)), {101: True, 102: False})
        self.assertEqual(dict(zip(q.cert, q.roa_ytd)), {101: 1.23, 102: 0.80})

    def test_former_charter_only_while_held(self):
        df = self._build()
        g = df[df.cert == 103]
        self.assertEqual(list(g.report_date), [date(2021, 9, 30)])
        self.assertEqual(g.iloc[0].charter_status, "former")
        self.assertEqual(g.iloc[0].total_assets, 900_000.0)
        self.assertEqual(set(df[df.cert != 103].charter_status), {"active"})

    def test_unknown_ticker_is_skipped_without_a_live_lookup(self):
        with patch("data.bank_mapping.resolve_ticker",
                   side_effect=AssertionError("live resolution attempted")):
            df = th.build_bank_quarterly(["ZZZZQ"])
            hd = th.build_holdco_quarterly(["ZZZZQ"])
        self.assertTrue(df.empty)
        self.assertEqual(df.attrs["skipped"]["no_cert"], ["ZZZZQ"])
        self.assertEqual(hd.attrs["skipped"]["no_cik"], ["ZZZZQ"])

    def test_bank_goodwill_floor_sums_reporting_charters(self):
        self._seed()
        with patch("data.cert_group.get_cert_group_cached", return_value=[101, 102]), \
                patch("data.cert_group.get_absorbed_charters", return_value=(None, [])):
            floor = th._bank_goodwill_floor(101)
        self.assertEqual(floor, {"2026-06-30": 40_000.0})   # 102 reported none


class TestCacheOnlyFacts(unittest.TestCase):
    def test_blob_read_at_any_age(self):
        from data.sec_client import company_facts_cache_key
        with patch("data.cache.get", return_value={"facts": {"us-gaap": {}}}) as get:
            self.assertIsNotNone(th._cached_facts(42))
        get.assert_called_once_with(company_facts_cache_key(42), max_age_s=None)
        with patch("data.cache.get", return_value=None):
            self.assertIsNone(th._cached_facts(42))


# ── holdco_quarterly ───────────────────────────────────────────────────────────
def _mchb_post_merger():
    """MCHB companyfacts as captured 2026-10-20, new-basis facts only."""
    return _facts({
        "StockholdersEquity": _usd(("2025-06-30", 2_416_617_000, "10-Q", "2025-11-17")),
        "Assets": _usd(("2024-12-31", 16_490_112_000, "10-Q", "2025-11-17"),
                       ("2025-09-30", 22_708_820_000, "10-Q", "2025-11-17")),
        "Goodwill": _usd(("2024-12-31", 843_305_000, "10-K", "2026-03-17"),
                         ("2025-09-30", 843_305_000, "10-Q", "2025-11-17")),
        "IntangibleAssetsNetExcludingGoodwill": _usd(
            ("2024-12-31", 38_744_000, "10-K", "2026-03-17"),
            ("2025-06-30", 33_308_000, "10-Q", "2025-11-17")),
    })


class TestHoldcoTce(unittest.TestCase):
    def test_mchb_carried_goodwill_beside_same_date_cdi(self):
        rows = _rows_by_end(th._holdco_rows("MCHB", 1518715, _mchb_post_merger(), {}))
        r = rows["2025-06-30"]
        self.assertEqual(r["total_equity"], 2_416_617_000.0)
        # 2,416,617,000 − 843,305,000 (goodwill carried from 2024-12-31)
        #               − 33,308,000 (CDI tagged at the date) = 1,540,004,000
        self.assertEqual(r["tce"], 1_540_004_000.0)
        self.assertEqual(r["intangible_adjustment"], 876_613_000.0)
        self.assertIsNone(r["goodwill"])          # not tagged AT the date
        self.assertEqual(r["preferred"], 0.0)
        self.assertIs(r["preferred_present"], False)
        self.assertIsNone(r["total_assets"])      # no Assets fact at the date
        self.assertEqual(r["filed_form"], "10-Q")
        self.assertEqual(r["filed_date"], date(2025, 11, 17))
        self.assertIsNone(r["tce_note"])

    def test_tce_equals_the_dashboard_helper(self):
        from analysis.valuation import _holdco_tce_at
        facts = _mchb_post_merger()
        for r in th._holdco_rows("MCHB", 1518715, facts, {}):
            if r["tce"] is not None:
                self.assertEqual(r["tce"], _holdco_tce_at(facts, r["period_end"].isoformat()))

    def test_before_first_balance_sheet_is_unresolved(self):
        # IBOC 2008-12-31: equity only in a later equity statement, before the
        # first tagged balance sheet (2009-12-31) — TARP preferred and goodwill
        # untagged there must not read as "none".
        facts = _facts({
            "StockholdersEquity": _usd(("2008-12-31", 1_257_297_000, "10-K", "2010-03-01"),
                                       ("2009-12-31", 1_407_470_000, "10-K", "2010-03-01")),
            "Assets": _usd(("2009-12-31", 11_762_540_000, "10-K", "2010-03-01")),
        })
        r = _rows_by_end(th._holdco_rows("IBOC", 315709, facts, {}))["2008-12-31"]
        self.assertEqual(r["total_equity"], 1_257_297_000.0)
        for k in ("tce", "preferred", "preferred_present", "intangible_adjustment"):
            self.assertIsNone(r[k], k)
        self.assertIn("first tagged balance sheet", r["tce_note"])

    def test_zero_preferred_share_count_beside_a_value_is_unresolved(self):
        # JPM Q1-2014 10-Q: PreferredStockSharesIssued = 0 beside $15.083B.
        d = "2014-03-31"
        facts = _facts({
            "StockholdersEquity": _usd((d, 219_655_000_000, "10-Q", "2014-05-02")),
            "Assets": _usd((d, 2_476_986_000_000, "10-Q", "2014-05-02")),
            "Goodwill": _usd((d, 48_065_000_000, "10-Q", "2014-05-02")),
            "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount":
                _usd((d, 15_083_000_000, "10-Q", "2014-05-02")),
            "PreferredStockSharesIssued": _shares((d, 0, "10-Q", "2014-05-02")),
        })
        from data import sec_client as sc
        self.assertEqual(sc._resolve_preferred_stock(facts, as_of=d), (0.0, False))
        r = th._holdco_rows("JPM", 19617, facts, {})[0]
        self.assertIsNone(r["tce"])
        self.assertIsNone(r["preferred"])
        self.assertIs(r["preferred_present"], True)
        self.assertIn("zero share count", r["tce_note"])

    def test_untagged_holdco_goodwill_beside_bank_goodwill_is_none(self):
        d = "2025-12-31"
        facts = _facts({
            "StockholdersEquity": _usd((d, 5_000_000_000, "10-K", "2026-02-20")),
            "Assets": _usd((d, 50_000_000_000, "10-K", "2026-02-20")),
        })
        r = th._holdco_rows("CFR", 39263, facts, {d: 652_700_000.0})[0]
        self.assertIsNone(r["tce"])
        self.assertIn("goodwill untagged", r["tce_note"])
        # Immaterial bank goodwill (TRST shape, < 1% of TCE): no intangibles.
        r = th._holdco_rows("TRST", 357301, facts, {d: 553_000.0})[0]
        self.assertEqual(r["tce"], 5_000_000_000.0)
        self.assertEqual(r["intangible_adjustment"], 0.0)


class TestHoldcoFlows(unittest.TestCase):
    def _facts(self):
        y = "2025-01-01"
        return _facts({
            "StockholdersEquity": _usd(*[(e, 1_000, "10-Q", "2025-11-05") for e in
                                         ("2025-03-31", "2025-06-30", "2025-09-30")],
                                       ("2025-12-31", 1_100, "10-K", "2026-02-20")),
            "Assets": _usd(*[(e, 9_000, "10-Q", "2025-11-05") for e in
                             ("2025-03-31", "2025-06-30", "2025-09-30")],
                           ("2025-12-31", 9_500, "10-K", "2026-02-20")),
            "NetIncomeLoss": _usd(
                ("2025-03-31", 100, "10-Q", "2025-05-05", y),
                ("2025-06-30", 210, "10-Q", "2025-08-05", y),
                ("2025-06-30", 110, "10-Q", "2025-08-05", "2025-04-01"),
                ("2025-09-30", 300, "10-Q", "2025-11-05", y),
                ("2025-09-30", 90, "10-Q", "2025-11-05", "2025-07-01"),
                ("2025-12-31", 420, "10-K", "2026-02-20", y),
                # FCBC trap: a LATER 10-Q tags the prior FY at its Q1 value.
                ("2025-12-31", 100, "10-Q", "2026-05-05", y)),
            "EarningsPerShareDiluted": _per_share(
                ("2025-03-31", 1.00, "10-Q", "2025-05-05", y),
                ("2025-06-30", 1.10, "10-Q", "2025-08-05", "2025-04-01"),
                ("2025-09-30", 0.90, "10-Q", "2025-11-05", "2025-07-01"),
                ("2025-09-30", 3.00, "10-Q", "2025-11-05", y),
                ("2025-12-31", 4.21, "10-K", "2026-02-20", y)),
            "CommonStockDividendsPerShareDeclared": _per_share(
                ("2025-03-31", 0.25, "10-Q", "2025-05-05", y),
                ("2025-06-30", 0.50, "10-Q", "2025-08-05", y),
                ("2025-09-30", 0.75, "10-Q", "2025-11-05", y),
                ("2025-12-31", 1.00, "10-K", "2026-02-20", y)),
        })

    def test_q4_is_fy_minus_9m(self):
        rows = _rows_by_end(th._holdco_rows("TEST", 1, self._facts(), {}))
        ni = {k: r["net_income_quarter"] for k, r in rows.items()}
        # Q1 = 100; Q2 = 210 − 100; Q3 = 300 − 210; Q4 = FY 420 − 9M 300.
        self.assertEqual(ni, {"2025-03-31": 100.0, "2025-06-30": 110.0,
                              "2025-09-30": 90.0, "2025-12-31": 120.0})
        self.assertEqual(rows["2025-12-31"]["net_income_concept"], "NetIncomeLoss")

    def test_eps_is_never_differenced(self):
        rows = _rows_by_end(th._holdco_rows("TEST", 1, self._facts(), {}))
        self.assertEqual(rows["2025-03-31"]["diluted_eps_quarter"], 1.00)
        self.assertEqual(rows["2025-06-30"]["diluted_eps_quarter"], 1.10)
        self.assertEqual(rows["2025-09-30"]["diluted_eps_quarter"], 0.90)
        self.assertIsNone(rows["2025-12-31"]["diluted_eps_quarter"])   # not 4.21 − 3.00

    def test_dividends_per_share_by_ytd_difference(self):
        rows = _rows_by_end(th._holdco_rows("TEST", 1, self._facts(), {}))
        self.assertEqual([rows[k]["dividends_per_share_quarter"] for k in sorted(rows)],
                         [0.25, 0.25, 0.25, 0.25])

    def test_build_frame_columns_and_skips(self):
        with patch.object(th, "_cik_for", side_effect=lambda t: {"AAA": 1, "BBB": 2}.get(t)), \
                patch.object(th, "_cached_facts",
                             side_effect=lambda c: self._facts() if c == 1 else None), \
                patch.object(th, "_cert_for", return_value=None):
            df = th.build_holdco_quarterly(["AAA", "BBB", "CCC"])
        self.assertEqual(list(df.columns), list(th.COLUMNS_HOLDCO_QUARTERLY))
        self.assertEqual(len(df), 4)
        self.assertEqual(df.attrs["skipped"], {"no_cik": ["CCC"], "no_cached_facts": ["BBB"]})
        self.assertIsInstance(df.period_end.iloc[0], date)
        empty = th.build_holdco_quarterly([])
        self.assertEqual(list(empty.columns), list(th.COLUMNS_HOLDCO_QUARTERLY))


class TestBasisBreak(unittest.TestCase):
    """MCHB: HomeStreet (legal acquirer) → Mechanics (accounting acquirer).
    The Q3-2025 10-Q (filed 2025-11-17) restated history to Mechanics'."""

    def _facts(self):
        return _facts({
            "StockholdersEquity": _usd(
                ("2024-03-31", 527_333_000, "10-Q", "2024-05-09"),
                ("2024-12-31", 396_997_000, "10-K", "2025-03-07"),
                ("2024-12-31", 2_301_868_000, "10-Q", "2025-11-17"),
                ("2025-06-30", 402_981_000, "10-Q", "2025-08-06"),
                ("2025-06-30", 2_416_617_000, "10-Q", "2025-11-17"),
                ("2025-09-30", 2_774_134_000, "10-Q", "2025-11-17")),
            "Assets": _usd(
                ("2024-03-31", 9_455_182_000, "10-Q", "2024-05-09"),
                ("2024-12-31", 8_123_698_000, "10-K", "2025-03-07"),
                ("2024-12-31", 16_490_112_000, "10-Q", "2025-11-17"),
                ("2025-06-30", 7_609_323_000, "10-Q", "2025-08-06"),
                ("2025-09-30", 22_708_820_000, "10-Q", "2025-11-17")),
            "Goodwill": _usd(("2024-12-31", 843_305_000, "10-Q", "2025-11-17"),
                             ("2025-09-30", 843_305_000, "10-Q", "2025-11-17")),
            "IntangibleAssetsNetIncludingGoodwill": _usd(
                ("2024-12-31", 7_141_000, "10-K", "2025-03-07")),   # HomeStreet's
            "IntangibleAssetsNetExcludingGoodwill": _usd(
                ("2024-12-31", 38_744_000, "10-Q", "2025-11-17"),
                ("2025-06-30", 33_308_000, "10-Q", "2025-11-17"),
                ("2025-09-30", 143_264_000, "10-Q", "2025-11-17")),
            "CommonStockSharesOutstanding": _shares(
                ("2025-06-30", 18_920_808, "10-Q", "2025-08-06")),
        })

    def test_break_detected(self):
        self.assertEqual(th._basis_break(self._facts()), ("2025-11-17", "2025-09-30"))

    def test_rows_never_mix_bases(self):
        rows = _rows_by_end(th._holdco_rows("MCHB", 1518715, self._facts(), {}))
        # 2024-03-31: only HomeStreet's own figures exist → no row.
        self.assertNotIn("2024-03-31", rows)
        r = rows["2024-12-31"]
        self.assertEqual(r["total_equity"], 2_301_868_000.0)
        self.assertEqual(r["total_assets"], 16_490_112_000.0)
        # 2,301,868,000 − 843,305,000 − 38,744,000; HomeStreet's $7.1M rollup
        # is not on the new basis.
        self.assertEqual(r["tce"], 1_419_819_000.0)
        self.assertIsNotNone(r["basis_note"])
        r = rows["2025-06-30"]
        self.assertEqual(r["tce"], 1_540_004_000.0)
        self.assertIsNone(r["total_assets"])          # HomeStreet's $7.6B dropped
        self.assertIsNone(r["shares_outstanding"])    # HomeStreet's 18.9M dropped
        r = rows["2025-09-30"]
        self.assertIsNone(r["basis_note"])
        # 2,774,134,000 − 843,305,000 − 143,264,000
        self.assertEqual(r["tce"], 1_787_565_000.0)

    def test_equity_tag_artifact_alone_is_not_a_break(self):
        # Regions 2020-05-06: the NCI column tagged as the NCI-inclusive total.
        facts = _facts({
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": _usd(
                ("2019-03-31", 15_523_000_000, "10-Q", "2019-05-08"),
                ("2019-03-31", 11_000_000, "10-Q", "2020-05-06")),
            "Assets": _usd(("2019-03-31", 126_000_000_000, "10-Q", "2019-05-08")),
        })
        self.assertIsNone(th._basis_break(facts))


class TestSharesAt(unittest.TestCase):
    D = "2026-06-30"

    def _f(self, cso=None, issued=None, treasury=None, cover=None):
        g = {}
        if cso is not None:
            g["CommonStockSharesOutstanding"] = _shares((self.D, cso, "10-Q", "2026-08-06"))
        if issued is not None:
            g["CommonStockSharesIssued"] = _shares((self.D, issued, "10-Q", "2026-08-06"))
        if treasury is not None:
            g["TreasuryStockCommonShares"] = _shares((self.D, treasury, "10-Q", "2026-08-06"))
        dei = {}
        if cover is not None:
            dei["EntityCommonStockSharesOutstanding"] = _shares(
                ("2026-08-03", cover, "10-Q", "2026-08-06"))
        return _facts(g, dei)

    def test_issued_minus_treasury_when_no_primary(self):
        # IBOC 2026-06-30: 96,680,381 issued − 34,492,192 treasury.
        self.assertEqual(th._shares_at(self._f(issued=96_680_381, treasury=34_492_192),
                                       self.D), 62_188_189.0)

    def test_rounded_placeholder_is_replaced(self):
        # USB tagged exactly 1,600,000,000.
        f = self._f(cso=1_600_000_000, issued=2_125_725_742, treasury=570_000_000)
        self.assertEqual(th._shares_at(f, self.D), 1_555_725_742.0)

    def test_conflict_arbitrated_by_cover(self):
        # FGBI shape: primary is last year's count; cover backs issued − treasury.
        f = self._f(cso=15_793_433, issued=16_539_094, treasury=0, cover=16_540_000)
        self.assertEqual(th._shares_at(f, self.D), 16_539_094.0)
        f = self._f(cso=15_793_433, issued=16_539_094, treasury=0, cover=20_000_000)
        self.assertIsNone(th._shares_at(f, self.D))
        f = self._f(cso=15_793_433, issued=16_539_094, treasury=0)
        self.assertEqual(th._shares_at(f, self.D), 15_793_433.0)   # no witness

    def test_nothing_tagged_is_none(self):
        self.assertIsNone(th._shares_at(self._f(issued=100), self.D))


class TestFieldContract(unittest.TestCase):
    def test_curated_fields_are_fetched_and_unit_kinds_match_the_catalog(self):
        import config
        from data import fdic_client
        from data.call_report_fields import catalog
        fetched = fdic_client._BASE_FINANCIALS_FIELDS | config.get_fdic_fields()
        kinds = {"usd": "level", "pct": "ratio", "count": "count"}
        for col, code, unit, _ in th._FDIC_FIELDS:
            self.assertIn(code, fetched, col)
            self.assertEqual(catalog()[code]["kind"], kinds[unit], col)
            self.assertIn(f"FDIC {code}", th.COLUMNS_BANK_QUARTERLY[col])
        self.assertEqual(len({c for c, *_ in th._FDIC_FIELDS}), len(th._FDIC_FIELDS))
        self.assertIn("×1000", th.COLUMNS_BANK_QUARTERLY["total_assets"])


if __name__ == "__main__":
    unittest.main()
