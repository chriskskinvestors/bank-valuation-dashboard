"""UX review P1 (2026-09-30): Branch Competitors ran one owner-aggregate query
+ owner canonicalisation PER footprint county (635 for JPM, >40 s warm on
prod). It now reads every footprint county from ONE
get_market_participants query. Pins:

  * the one-query table is identical to the per-county table (same banks,
    counties, branches, deposits, shares) on a store where the subject runs
    two charters and the page's cert is NOT the lead charter — the case where
    keying rows by cert would split the subject into two rows;
  * the per-county query is not called at all when the one-query path covers
    the footprint;
  * a blank / '0' county code is not a county (the old loop queried
    stcntybr = '' and swept in every blank-coded branch nationwide);
  * strict sums survive: a competitor with an unreported county's deposits
    has unknown footprint deposits (n/a), never a partial total.
Hermetic: in-memory SQLite store, patched Streamlit surface.
"""
import math
import types
import unittest
from unittest import mock

import pandas as pd
from pandas.testing import assert_frame_equal
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from tests import _streamlit_stub

_streamlit_stub.install()

import data.db as db  # noqa: E402
import data.branches_store as bs  # noqa: E402
import ui.branch_analytics as ba  # noqa: E402

Y = 2025
LEAD, WILM = 588, 32826          # subject: two charters under ticker MTB
RIVAL, PRIV_A, PRIV_B, FAR = 700, 900, 901, 950

_BASE = [
    # subject — lead charter is the bigger one; the page resolves to WILM
    dict(cert=LEAD, brnum=0, ticker="MTB", bank_name="M&T Bank",
         stcntybr="36029", deposits=900),
    dict(cert=LEAD, brnum=1, ticker="MTB", bank_name="M&T Bank",
         stcntybr="36029", deposits=100),
    dict(cert=WILM, brnum=0, ticker="MTB", bank_name="Wilmington Trust",
         stcntybr="36029", deposits=50),
    dict(cert=WILM, brnum=1, ticker="MTB", bank_name="Wilmington Trust",
         stcntybr="10003", deposits=70),
    # a blank-coded subject branch: not a county
    dict(cert=WILM, brnum=2, ticker="MTB", bank_name="Wilmington Trust",
         stcntybr="", deposits=5),
    # public rival in both footprint counties
    dict(cert=RIVAL, brnum=0, ticker="RVL", bank_name="Rival Bank",
         stcntybr="36029", deposits=400),
    dict(cert=RIVAL, brnum=1, ticker="RVL", bank_name="Rival Bank",
         stcntybr="10003", deposits=30),
    # two DIFFERENT private banks sharing a name
    dict(cert=PRIV_A, brnum=0, ticker=None, bank_name="First National Bank",
         stcntybr="36029", deposits=200),
    dict(cert=PRIV_B, brnum=0, ticker=None, bank_name="First National Bank",
         stcntybr="10003", deposits=150),
    # outside the footprint, and blank-coded: must never appear
    dict(cert=FAR, brnum=0, ticker="FAR", bank_name="Faraway Bank",
         stcntybr="06001", deposits=9999),
    dict(cert=FAR, brnum=1, ticker="FAR", bank_name="Faraway Bank",
         stcntybr="", deposits=8888),
]


def _insert(eng, rows):
    with eng.begin() as c:
        for r in rows:
            c.execute(text(
                "INSERT INTO branches (cert, brnum, year, ticker, bank_name, "
                "branch_name, address, city, state, zip, county, stcntybr, "
                "msa_code, msa_name, deposits, lat, lng) VALUES (:cert,:brnum,"
                ":year,:ticker,:bank_name,'b','a','c','NY','z','cty',"
                ":stcntybr,'1','m',:deposits,NULL,NULL)"),
                {"year": Y, **r})


class _Store(unittest.TestCase):
    extra: list = []

    def setUp(self):
        self._eng = create_engine("sqlite://", poolclass=StaticPool,
                                  connect_args={"check_same_thread": False})
        self._orig = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        _insert(self._eng, _BASE + self.extra)

    def tearDown(self):
        db.get_engine = self._orig
        bs._engine = None

    def _render(self, one_query: bool):
        """(export frame, provenance, per-county call count)."""
        out, calls = {}, []

        def county(fips, year):
            calls.append(fips)
            return bs.get_banks_by_county(fips, year=year)

        def export(df, *a, **k):
            out["df"], out["prov"] = df.reset_index(drop=True), k["provenance"]

        fp = (ba._footprint_participants if one_query
              else (lambda c, y: {}))
        fake_st = types.SimpleNamespace(markdown=lambda *a, **k: None,
                                        caption=lambda *a, **k: None)
        with mock.patch.object(ba, "st", fake_st), \
                mock.patch.object(ba, "_roster", bs.get_bank_footprint), \
                mock.patch.object(ba, "_county_banks", county), \
                mock.patch.object(ba, "_footprint_participants", fp), \
                mock.patch.object(ba, "get_fdic_cert", lambda t: WILM), \
                mock.patch.object(ba, "get_bank_info", lambda t: {"name": "M&T"}), \
                mock.patch.object(ba, "ksk_table", lambda *a, **k: None), \
                mock.patch.object(ba, "table_export", export):
            ba.render_branch_competitors("MTB")
        return out["df"], out["prov"], calls


class TestOneQueryMatchesPerCounty(_Store):
    def test_identical_table(self):
        new, new_prov, calls = self._render(one_query=True)
        old, old_prov, _ = self._render(one_query=False)
        assert_frame_equal(new, old)
        self.assertEqual(new_prov, old_prov)
        self.assertEqual(calls, [])          # no per-county round trips

    def test_hand_computed_values(self):
        df, prov, _ = self._render(one_query=True)
        by = {(r["Bank"], r["Ticker"]): r for r in df.to_dict("records")}
        # Subject is ONE row despite two charters + a non-lead page cert:
        # 36029 = 900+100+50, 10003 = 70 (blank-coded 5 excluded).
        subj = by[("M&T Bank", "MTB")]
        self.assertEqual((subj["Shared counties"], subj["Branches"],
                          subj["Deposits ($K)"]), (2, 4, 1120.0))
        rival = by[("Rival Bank", "RVL")]
        self.assertEqual((rival["Shared counties"], rival["Branches"],
                          rival["Deposits ($K)"]), (2, 2, 430.0))
        # Same-name private banks stay two rows.
        fnb = sorted(r["Deposits ($K)"] for r in df.to_dict("records")
                     if r["Bank"] == "First National Bank")
        self.assertEqual(fnb, [150.0, 200.0])
        # Footprint = 36029 (1050+400+200) + 10003 (70+30+150) = 1900.
        self.assertEqual(prov["Footprint counties"], 2)
        self.assertEqual(prov["Footprint deposits, all banks ($K)"], 1900.0)
        self.assertAlmostEqual(subj["% of footprint deposits (%)"],
                               round(1120 / 1900 * 100, 2))
        self.assertNotIn("Faraway Bank", set(df["Bank"]))


class TestStrictSumSurvives(_Store):
    # Rival's second 10003 branch reports no deposits.
    extra = [dict(cert=RIVAL, brnum=2, ticker="RVL", bank_name="Rival Bank",
                  stcntybr="10003", deposits=None)]

    def test_unknown_county_deposits_stay_unknown(self):
        new, new_prov, _ = self._render(one_query=True)
        old, old_prov, _ = self._render(one_query=False)
        assert_frame_equal(new, old)
        rival = new[new["Bank"] == "Rival Bank"].iloc[0]
        self.assertTrue(math.isnan(rival["Deposits ($K)"]))
        self.assertEqual(rival["Branches"], 3)
        self.assertIsNone(new_prov["Footprint deposits, all banks ($K)"])



class TestDepositLookupMapFitted(unittest.TestCase):
    """UX-P1-27: Company > Market Share & Branches used st.map, which fits to
    EVERY point — JPM's map opened on the Atlantic. The fitted figure frames
    the 1st-99th percentile box but still plots every branch."""

    def test_outlier_does_not_drag_the_view(self):
        import ui.deposit_lookup as dl
        rows = [{"SIMS_LATITUDE": 40.70 + i * 0.002,
                 "SIMS_LONGITUDE": -74.00 - i * 0.002, "DEPSUMBR": 100 + i,
                 "NAMEBR": f"b{i}", "CITYBR": "NYC", "STALPBR": "NY"}
                for i in range(300)]
        rows.append({"SIMS_LATITUDE": 0.0, "SIMS_LONGITUDE": 0.0,
                     "DEPSUMBR": None, "NAMEBR": "mis-geocoded",
                     "CITYBR": "?", "STALPBR": "?"})
        fig = dl._branch_map_figure(pd.DataFrame(rows))
        m = fig.layout.map
        self.assertTrue(40.0 < m.center.lat < 41.5, m.center.lat)
        self.assertTrue(-75.0 < m.center.lon < -73.5, m.center.lon)
        self.assertGreater(m.zoom, 5)                     # not continental
        self.assertEqual(len(fig.data[0].lat), 301)       # nothing hidden


if __name__ == "__main__":
    unittest.main(verbosity=2)
