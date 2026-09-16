"""Branch OWNERSHIP model (data/branches_store, jobs/refresh_sod) — pins the
two owner reports of 2026-09-16:

  • Beacon Financial (BBT) showed 28 branches site-wide: the 2025 SOD survey
    predates the 2025-09-02 merger that folded Berkshire Bank / Bank Rhode
    Island / PCSB Bank into cert 17798, so their branches sat under dead certs
    (or were never ingested — the sweep only walks ACTIVE institutions). The
    first fix patched two pages; every other view (Geographic By Bank(s),
    rankings, HHI, proximity) kept the wrong count.
  • MTB listed twice in the Geographic picker: M&T runs two charters (M&T
    Bank + Wilmington Trust NA) and every bank-level view keyed on cert.

The fix lives in the STORE: absorbed charters' branches are re-attributed to
their current owner (brnum = -UNINUMBR, provenance kept), and bank-level
queries group by OWNER (public company = ticker, private bank = cert), so no
page can count one company as several banks. Hermetic: in-memory SQLite +
patched FDIC calls.
"""
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs

Y = 2025
BBT, BERK, RI = 17798, 23621, 34147
MTB_LEAD, MTB_WILM = 588, 32826
PRIV_A, PRIV_B = 900, 901


def _insert(eng, rows):
    with eng.begin() as c:
        for r in rows:
            c.execute(text(
                "INSERT INTO branches (cert, brnum, year, ticker, bank_name, "
                "branch_name, address, city, state, zip, county, stcntybr, "
                "msa_code, msa_name, deposits, lat, lng) VALUES (:cert,:brnum,"
                ":year,:ticker,:bank_name,:branch_name,'a','c',:state,'z',"
                ":county,:stcntybr,:msa_code,'m',:deposits,:lat,:lng)"),
                {"year": Y, "state": "MA", "county": "cty", "msa_code": "1",
                 "branch_name": "b", "lat": None, "lng": None, **r})


def _sod_frame(rows):
    """sod_client.fetch_branches-shaped frame for an absorbed charter."""
    return pd.DataFrame([{
        "CERT": BERK, "YEAR": Y, "NAMEFULL": "Berkshire Bank",
        "NAMEBR": f"Berkshire {i}", "ADDRESBR": "a", "CITYBR": "Pittsfield",
        "STALPBR": "MA", "ZIPBR": "01201", "CNTYNAMB": "Berkshire",
        "STCNTYBR": "25003", "MSANAMB": "Pittsfield, MA", "MSABR": 38340,
        "BRSERTYP": "12", "SIMS_LATITUDE": 42.45, "SIMS_LONGITUDE": -73.25,
        **r} for i, r in enumerate(rows)])


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._eng = create_engine("sqlite://", poolclass=StaticPool,
                                  connect_args={"check_same_thread": False})
        self._orig = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        _insert(self._eng, [
            # Beacon survivor's own survey rows (as filed: "Brookline  Bank")
            dict(cert=BBT, brnum=0, ticker="BBT", bank_name="Brookline  Bank",
                 stcntybr="25021", deposits=500),
            dict(cert=BBT, brnum=1, ticker="BBT", bank_name="Brookline  Bank",
                 stcntybr="25021", deposits=300),
            # a stale as-filed row of the absorbed charter (the render-path
            # backfill of PR #113 left exactly these in prod)
            dict(cert=BERK, brnum=0, ticker=None, bank_name="Berkshire Bank",
                 stcntybr="25003", deposits=400),
            # M&T: two live charters under one ticker
            dict(cert=MTB_LEAD, brnum=0, ticker="MTB",
                 bank_name="Manufacturers and Traders Trust Company",
                 stcntybr="36029", deposits=900, lat=42.886, lng=-78.878),
            dict(cert=MTB_LEAD, brnum=1, ticker="MTB",
                 bank_name="Manufacturers and Traders Trust Company",
                 stcntybr="36029", deposits=100),
            dict(cert=MTB_WILM, brnum=0, ticker="MTB",
                 bank_name="Wilmington Trust, National Association",
                 stcntybr="36029", deposits=50, lat=42.887, lng=-78.879),
            dict(cert=MTB_WILM, brnum=1, ticker="MTB",
                 bank_name="Wilmington Trust, National Association",
                 stcntybr="10003", deposits=70),
            # two DIFFERENT private banks sharing a name, same county
            dict(cert=PRIV_A, brnum=0, ticker=None,
                 bank_name="First National Bank", stcntybr="25021",
                 deposits=200, lat=42.888, lng=-78.880),
            dict(cert=PRIV_B, brnum=0, ticker=None,
                 bank_name="First National Bank", stcntybr="25021",
                 deposits=150),
        ])

    def tearDown(self):
        db.get_engine = self._orig
        bs._engine = None

    def _rows(self, where="1=1", params=None):
        return bs._q_to_df(f"SELECT * FROM branches WHERE {where}", params or {})

    def _reattribute_berkshire(self, frame=None):
        return bs.reattribute_absorbed_branches(
            BERK, BBT, "BBT", "Beacon Bank and Trust", "2025-09-02",
            frame if frame is not None else _sod_frame(
                [{"UNINUMBR": 16536, "DEPSUMBR": 900},
                 {"UNINUMBR": 225603, "DEPSUMBR": 100},
                 {"UNINUMBR": None, "DEPSUMBR": 5}]), Y)


class TestReattribution(_StoreCase):
    def test_absorbed_branches_move_to_owner_with_provenance(self):
        res = self._reattribute_berkshire()
        self.assertEqual(res["written"], 2)
        self.assertEqual(res["skipped_no_uninumbr"], 1)   # never a guessed key
        moved = self._rows("filed_cert = :c", {"c": BERK})
        self.assertEqual(sorted(moved["brnum"]), [-225603, -16536])
        self.assertTrue((moved["cert"] == BBT).all())
        self.assertTrue((moved["ticker"] == "BBT").all())
        self.assertTrue((moved["bank_name"] == "Beacon Bank and Trust").all())
        self.assertTrue((moved["filed_bank_name"] == "Berkshire Bank").all())
        self.assertTrue((moved["owner_since"] == "2025-09-02").all())

    def test_dead_cert_rows_are_removed(self):
        self._reattribute_berkshire()
        self.assertTrue(self._rows("cert = :c", {"c": BERK}).empty)

    def test_owner_rows_take_current_name_keeping_filed_name(self):
        res = self._reattribute_berkshire()
        self.assertEqual(res["renamed"], 2)
        own = self._rows("cert = :c AND filed_cert IS NULL", {"c": BBT})
        self.assertTrue((own["bank_name"] == "Beacon Bank and Trust").all())
        self.assertTrue((own["filed_bank_name"] == "Brookline  Bank").all())

    def test_idempotent(self):
        self._reattribute_berkshire()
        before = len(self._rows())
        res = self._reattribute_berkshire()
        self.assertEqual(len(self._rows()), before)
        self.assertEqual(res["renamed"], 0)

    def test_self_and_empty_are_noops(self):
        n = len(self._rows())
        self.assertEqual(bs.reattribute_absorbed_branches(
            BBT, BBT, "BBT", "x", "2025-09-02", _sod_frame([{"UNINUMBR": 1}]),
            Y)["written"], 0)
        self.assertEqual(bs.reattribute_absorbed_branches(
            BERK, BBT, "BBT", "x", "2025-09-02", pd.DataFrame(), Y)["written"], 0)
        self.assertEqual(len(self._rows()), n)

    def test_reattributed_map(self):
        self._reattribute_berkshire()
        self.assertEqual(bs.get_reattributed_certs(Y), {BERK: BBT})


class TestOwnerViews(_StoreCase):
    def test_beacon_footprint_everywhere_after_reattribution(self):
        self._reattribute_berkshire()
        roster, notes = bs.get_bank_footprint(BBT)
        self.assertEqual(len(roster), 4)                    # 2 own + 2 merged
        merged = [n for n in notes if n["kind"] == "merged"]
        self.assertEqual(merged, [{"kind": "merged", "name": "Berkshire Bank",
                                   "cert": BERK, "date": "2025-09-02",
                                   "n_branches": 2}])
        counts = bs.get_branch_counts_by_bank()
        bbt = counts[counts["ticker"] == "BBT"]
        self.assertEqual(len(bbt), 1)
        self.assertEqual(int(bbt["n_branches"].iloc[0]), 4)
        self.assertEqual(bbt["bank_name"].iloc[0], "Beacon Bank and Trust")

    def test_multi_charter_company_is_one_bank_in_the_picker(self):
        counts = bs.get_branch_counts_by_bank()
        mtb = counts[counts["ticker"] == "MTB"]
        self.assertEqual(len(mtb), 1)                       # was two "MTB" rows
        self.assertEqual(int(mtb["n_branches"].iloc[0]), 4)
        self.assertEqual(int(mtb["n_charters"].iloc[0]), 2)
        self.assertEqual(int(mtb["cert"].iloc[0]), MTB_LEAD)  # largest charter
        self.assertEqual(mtb["bank_name"].iloc[0],
                         "Manufacturers and Traders Trust Company")

    def test_owner_branches_span_charters(self):
        roster, notes = bs.get_bank_footprint(MTB_LEAD)
        self.assertEqual(len(roster), 4)
        self.assertEqual([n["cert"] for n in notes if n["kind"] == "charter"],
                         [MTB_WILM])
        # opening from the smaller charter reaches the same company
        self.assertEqual(len(bs.get_owner_branches(MTB_WILM)), 4)

    def test_county_ranking_counts_company_once(self):
        df = bs.get_banks_by_county("36029", year=Y)
        mtb = df[df["ticker"] == "MTB"]
        self.assertEqual(len(mtb), 1)
        self.assertEqual(int(mtb["total_deposits"].iloc[0]), 1050)
        self.assertEqual(int(mtb["n_branches"].iloc[0]), 3)

    def test_same_named_private_banks_stay_distinct(self):
        df = bs.get_banks_by_county("25021", year=Y)
        fnb = df[df["bank_name"] == "First National Bank"]
        self.assertEqual(sorted(int(c) for c in fnb["cert"]), [PRIV_A, PRIV_B])

    def test_market_participants_owner_level_subject_cert_preserved(self):
        parts = bs.get_market_participants(MTB_LEAD, kind="county", year=Y)
        # Wilmington-only county is still a subject market (same company)
        self.assertEqual(set(parts["market_key"]), {"36029", "10003"})
        subj = parts[parts["owner_key"] == "MTB"]
        self.assertTrue((subj["cert"] == MTB_LEAD).all())
        self.assertEqual(len(subj[subj["market_key"] == "36029"]), 1)
        self.assertEqual(int(subj[subj["market_key"] == "36029"]["deposits"]
                             .iloc[0]), 1050)

    def test_proximity_never_lists_own_charter_as_competitor(self):
        res = bs.get_nearest_branches(MTB_LEAD, 42.886, -78.878, max_miles=5)
        certs = set(res["branches"]["cert"].astype(int)) if not res["branches"].empty else set()
        self.assertNotIn(MTB_WILM, certs)                   # sibling charter
        self.assertIn(PRIV_A, certs)                        # a real competitor

    def test_retag_fixes_sibling_mistag_without_unlinking(self):
        _insert(self._eng, [dict(cert=628, brnum=0, ticker="AMJB",
                                 bank_name="JPMorgan Chase Bank",
                                 stcntybr="36061", deposits=10)])
        n = bs.retag_tickers(Y, {628: "JPM", MTB_LEAD: "MTB"})
        self.assertEqual(n, 1)                              # only the stale tag
        self.assertEqual(self._rows("cert = 628")["ticker"].iloc[0], "JPM")
        self.assertEqual(bs.retag_tickers(Y, {}), 0)        # hiccup-safe


class TestResolveOwners(unittest.TestCase):
    def test_direct_and_chained(self):
        from data.fdic_structure import resolve_owners
        out = resolve_owners([
            {"absorbed": 1, "survivor": 2, "date": "2025-08-01", "absorbed_name": "A"},
            {"absorbed": 2, "survivor": 3, "date": "2026-01-01", "absorbed_name": "B"},
        ])
        self.assertEqual(out[1]["owner"], 3)                # followed the chain
        self.assertEqual(out[2]["owner"], 3)
        self.assertEqual(out[1]["date"], "2025-08-01")

    def test_cycle_terminates(self):
        from data.fdic_structure import resolve_owners
        out = resolve_owners([
            {"absorbed": 1, "survivor": 2, "date": "2025-08-01", "absorbed_name": ""},
            {"absorbed": 2, "survivor": 1, "date": "2025-09-01", "absorbed_name": ""},
        ])
        self.assertEqual(set(out), {1, 2})                  # returned, no hang


class TestFetchAbsorptionsSince(unittest.TestCase):
    def test_boundary_exclusive_and_self_rows_dropped(self):
        from data import fdic_structure as fs
        raw = [
            {"EFFDATE": "2025-06-30T00:00:00", "OUT_CERT": 5, "SUR_CERT": 6},
            {"EFFDATE": "2025-09-02T00:00:00", "OUT_CERT": BERK,
             "SUR_CERT": BBT, "OUT_INSTNAME": "Berkshire Bank"},
            {"EFFDATE": "2025-10-01T00:00:00", "OUT_CERT": 7, "SUR_CERT": 7},
        ]
        with patch.object(fs, "fetch_history_rows", return_value=raw):
            out = fs.fetch_absorptions_since("2025-06-30")
        self.assertEqual(out, [{"absorbed": BERK, "survivor": BBT,
                                "date": "2025-09-02",
                                "absorbed_name": "Berkshire Bank"}])

    def test_fetch_failure_is_none_not_empty(self):
        from data import fdic_structure as fs
        with patch.object(fs, "fetch_history_rows", return_value=None):
            self.assertIsNone(fs.fetch_absorptions_since("2025-06-30"))


class TestOwnershipPass(_StoreCase):
    def _run(self, absorptions, frames):
        from jobs import refresh_sod
        with patch("data.fdic_structure.fetch_absorptions_since",
                   return_value=absorptions), \
             patch("data.sod_client.fetch_branches",
                   side_effect=lambda c, year=None: frames.get(c, pd.DataFrame())) as fb:
            ok = refresh_sod.apply_post_survey_ownership(
                Y, {BBT: "BBT"}, {BBT: "Beacon Bank and Trust"})
        return ok, fb

    def test_pass_reattributes_then_skips_when_current(self):
        abs_ = [{"absorbed": BERK, "survivor": BBT, "date": "2025-09-02",
                 "absorbed_name": "Berkshire Bank"}]
        frames = {BERK: _sod_frame([{"UNINUMBR": 16536, "DEPSUMBR": 900}])}
        ok, fb = self._run(abs_, frames)
        self.assertTrue(ok)
        self.assertEqual(len(bs.get_owner_branches(BBT)), 3)
        ok, fb = self._run(abs_, frames)                    # second night
        self.assertTrue(ok)
        fb.assert_not_called()                              # already current

    def test_history_outage_leaves_store_untouched(self):
        n = len(self._rows())
        ok, _ = self._run(None, {})
        self.assertFalse(ok)
        self.assertEqual(len(self._rows()), n)


class TestMarketShareSurface(_StoreCase):
    """ui.deposit_lookup reads the owner-resolved store (it used to call FDIC
    live per render, grouped by cert)."""

    def test_footprint_in_sod_field_names(self):
        import ui.deposit_lookup as dl
        self._reattribute_berkshire()
        df, notes, year = dl._fetch_footprint(BBT)
        self.assertEqual(year, Y)
        self.assertEqual(len(df), 4)
        for col in ("NAMEBR", "STALPBR", "STCNTYBR", "DEPSUMBR", "MSABR",
                    "SIMS_LATITUDE", "ticker", "cert"):
            self.assertIn(col, df.columns)
        self.assertEqual([n["kind"] for n in notes], ["merged"])

    def test_market_share_is_owner_level_and_ranked(self):
        import ui.deposit_lookup as dl
        ms = dl._market_share("county", "25021", Y)
        self.assertEqual(list(ms["TICKER"].fillna("")), ["BBT", "", ""])
        self.assertEqual(list(ms["rank"]), [1, 2, 3])
        self.assertEqual(list(ms["deposits"]), [800, 200, 150])
        self.assertAlmostEqual(float(ms["market_share"].sum()), 100.0)
        # the two same-named private banks are ranked separately
        self.assertEqual(sorted(int(c) for c in ms["CERT"][1:]), [PRIV_A, PRIV_B])

    def test_linked_tickers(self):
        import ui.deposit_lookup as dl
        cells = dl._linked_tickers(["MTB", None, float("nan"), ""])
        self.assertIn('href="?s=Company&bank=MTB"', cells[0])
        self.assertEqual(cells[1:], ["", "", ""])


if __name__ == "__main__":
    unittest.main()
