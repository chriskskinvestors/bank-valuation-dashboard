"""
Unit tests for Schedule RC-N past-due/nonaccrual extraction + storage
(data/ffiec_client.py: get_rcn_detail; data/call_report_store.py:
upsert_rcn_detail / get_stored_rcn_detail).

Synthetic fixtures use Banner Bank's real 12/31/2025 RC-N values, whose
extracted totals were verified against the SNL FY-2025 screenshot
(30-89 = 26,767 / 90+ = 4,114 / nonaccrual = 41,525, all $000).

The store tests point at an isolated in-memory SQLite engine — no test
ever touches the real cache.db or Postgres.

The live Banner verification (network + FFIEC creds) runs only when the
gitignored tools/verify_ffiec_e2e.py creds file is present.

Run:  python -m unittest tests.test_rcn_detail
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from data.ffiec_client import get_rcn_detail  # noqa: E402


def _df(values: dict[str, float | int]) -> pd.DataFrame:
    """Long-form Call Report DF in the ffiec-data-connect v3 schema."""
    rows = []
    for code, v in values.items():
        rows.append({
            "mdrm": code,
            "rssd": "352772",
            "quarter": "12/31/2025",
            "data_type": "int",
            "int_data": int(v),
            "float_data": None,
            "bool_data": None,
            "str_data": None,
        })
    return pd.DataFrame(rows)


# Banner Bank 12/31/2025 RC-N as filed (RCON, $000). Agricultural rides
# inside "all other loans" (RCON5459-series) on the FFIEC 041, so the filed
# item-9 totals count it once — the residual "other" must net it out.
BANNER_RCN = {
    # construction: 1-4 family + other
    "RCONF172": 289, "RCONF174": 1268, "RCONF176": 737,
    "RCONF173": 739, "RCONF175": 0, "RCONF177": 4437,
    # farmland
    "RCON3493": 291, "RCON3494": 0, "RCON3495": 3117,
    # heloc
    "RCON5398": 4189, "RCON5399": 114, "RCON5400": 4582,
    # closed-end 1-4: first + junior liens
    "RCONC236": 12468, "RCONC237": 2698, "RCONC229": 19173,
    "RCONC238": 867, "RCONC239": 0, "RCONC230": 800,
    # multifamily (true zeros, filed explicitly)
    "RCON3499": 0, "RCON3500": 0, "RCON3501": 0,
    # nonfarm nonresidential: owner-occupied + other
    "RCONF178": 1724, "RCONF180": 0, "RCONF182": 1280,
    "RCONF179": 2892, "RCONF181": 0, "RCONF183": 934,
    # loans to depository institutions (part of residual "other")
    "RCONB834": 0, "RCONB835": 0, "RCONB836": 0,
    # agricultural
    "RCON1594": 317, "RCON1597": 0, "RCON1583": 1491,
    # C&I
    "RCON1606": 1860, "RCON1607": 0, "RCON1608": 4923,
    # credit cards
    "RCONB575": 228, "RCONB576": 0, "RCONB577": 0,
    # auto + other consumer
    "RCONK213": 39, "RCONK214": 0, "RCONK215": 11,
    "RCONK216": 186, "RCONK217": 34, "RCONK218": 40,
    # all other loans (includes agricultural — part of residual "other")
    "RCON5459": 995, "RCON5460": 0, "RCON5461": 1491,
    # leases (part of residual "other")
    "RCON1226": 0, "RCON1227": 0, "RCON1228": 0,
    # item 9 totals — match the SNL screenshot exactly
    "RCON1406": 26767, "RCON1407": 4114, "RCON1403": 41525,
}


class TestRcnExtraction(unittest.TestCase):

    def setUp(self):
        self.detail = get_rcn_detail(
            352772, "12/31/2025", call_report_df=_df(BANNER_RCN))
        self.assertIsNotNone(self.detail)

    def test_totals_match_snl(self):
        d = self.detail
        self.assertEqual(d["total_pd30_89"], 26767.0)
        self.assertEqual(d["total_pd90_plus"], 4114.0)
        self.assertEqual(d["total_nonaccrual"], 41525.0)
        # USD scaling (FFIEC files $thousands)
        self.assertEqual(d["total_pd30_89_usd"], 26_767_000.0)
        self.assertEqual(d["total_pd90_plus_usd"], 4_114_000.0)
        self.assertEqual(d["total_nonaccrual_usd"], 41_525_000.0)

    def test_each_category_extracted(self):
        """Hand-computed per-category values (sub-items summed)."""
        expected = {
            "construction":   (289 + 739, 1268 + 0, 737 + 4437),
            "farmland":       (291, 0, 3117),
            "heloc":          (4189, 114, 4582),
            "resi_1to4":      (12468 + 867, 2698 + 0, 19173 + 800),
            "multifamily":    (0, 0, 0),
            "nonfarm_nonres": (1724 + 2892, 0, 1280 + 934),
            "ci":             (1860, 0, 4923),
            "agricultural":   (317, 0, 1491),
            "credit_cards":   (228, 0, 0),
            "other_consumer": (39 + 186, 0 + 34, 11 + 40),
        }
        cats = self.detail["categories"]
        for cat, (a, b, c) in expected.items():
            self.assertEqual(cats[cat]["pd30_89"], float(a), cat)
            self.assertEqual(cats[cat]["pd90_plus"], float(b), cat)
            self.assertEqual(cats[cat]["nonaccrual"], float(c), cat)

    def test_other_is_residual_and_matrix_reconciles(self):
        """'other' = filed total − named categories. For Banner that nets
        agricultural out of the all-other-loans line (678 / 0 / 0), and the
        full matrix re-sums to the filed totals in every column."""
        cats = self.detail["categories"]
        self.assertEqual(cats["other"],
                         {"pd30_89": 678.0, "pd90_plus": 0.0,
                          "nonaccrual": 0.0})
        for col, total_key in (("pd30_89", "total_pd30_89"),
                               ("pd90_plus", "total_pd90_plus"),
                               ("nonaccrual", "total_nonaccrual")):
            self.assertEqual(sum(c[col] for c in cats.values()),
                             self.detail[total_key], col)

    def test_categories_usd_scaled(self):
        usd = self.detail["categories_usd"]
        self.assertEqual(usd["heloc"]["pd30_89"], 4_189_000.0)
        self.assertEqual(usd["multifamily"]["nonaccrual"], 0.0)
        self.assertEqual(usd["other"]["pd30_89"], 678_000.0)

    # ── $0 vs None discipline ───────────────────────────────────────────

    def test_true_zero_stays_zero(self):
        """Multifamily filed explicit zeros — must read 0.0, not None."""
        mf = self.detail["categories"]["multifamily"]
        self.assertEqual(mf, {"pd30_89": 0.0, "pd90_plus": 0.0,
                              "nonaccrual": 0.0})

    def test_absent_category_is_none(self):
        """Codes absent from the filing map to None, never $0."""
        partial = {k: v for k, v in BANNER_RCN.items()
                   if not k.startswith(("RCONB575", "RCONB576", "RCONB577"))}
        d = get_rcn_detail(352772, "12/31/2025", call_report_df=_df(partial))
        cc = d["categories"]["credit_cards"]
        self.assertEqual(cc, {"pd30_89": None, "pd90_plus": None,
                              "nonaccrual": None})
        self.assertEqual(d["categories_usd"]["credit_cards"]["pd30_89"], None)
        # The filed total is unchanged, so the residual silently absorbs the
        # dropped category (blank == $0 in the filing).
        self.assertEqual(d["categories"]["other"]["pd30_89"], 678.0 + 228.0)

    def test_partial_subitems_sum_only_present(self):
        """One sub-item present, the other absent → sum of the present one
        (not None, not double)."""
        partial = dict(BANNER_RCN)
        del partial["RCONC238"], partial["RCONC239"], partial["RCONC230"]
        d = get_rcn_detail(352772, "12/31/2025", call_report_df=_df(partial))
        r = d["categories"]["resi_1to4"]
        self.assertEqual(r, {"pd30_89": 12468.0, "pd90_plus": 2698.0,
                             "nonaccrual": 19173.0})

    def test_missing_total_means_other_is_none(self):
        partial = {k: v for k, v in BANNER_RCN.items() if k != "RCON1406"}
        d = get_rcn_detail(352772, "12/31/2025", call_report_df=_df(partial))
        self.assertIsNone(d["total_pd30_89"])
        self.assertIsNone(d["total_pd30_89_usd"])
        self.assertIsNone(d["categories"]["other"]["pd30_89"])
        # Other columns unaffected
        self.assertEqual(d["categories"]["other"]["pd90_plus"], 0.0)

    def test_negative_residual_renders_none_not_negative(self):
        """If named categories overshoot the filed total (mapping violation),
        'other' must be n/a — never a negative balance."""
        broken = dict(BANNER_RCN)
        broken["RCON1406"] = 1000  # implausibly small filed total
        d = get_rcn_detail(352772, "12/31/2025", call_report_df=_df(broken))
        self.assertIsNone(d["categories"]["other"]["pd30_89"])

    def test_no_rcn_content_returns_none(self):
        """A call report with zero RC-N concepts must not yield an all-None
        matrix posing as data."""
        d = get_rcn_detail(352772, "12/31/2025",
                           call_report_df=_df({"RCONA549": 123}))
        self.assertIsNone(d)
        self.assertIsNone(get_rcn_detail(
            352772, "12/31/2025", call_report_df=pd.DataFrame()))

    def test_rcfd_preferred_for_consolidated_filers(self):
        """Banks with foreign offices file RCFD — _lookup_concept takes the
        larger of RCFD/RCON, so consolidated values win."""
        both = dict(BANNER_RCN)
        both["RCFD1606"] = 2500  # consolidated C&I 30-89 > domestic 1,860
        d = get_rcn_detail(352772, "12/31/2025", call_report_df=_df(both))
        self.assertEqual(d["categories"]["ci"]["pd30_89"], 2500.0)


class TestRcnDetailStore(unittest.TestCase):

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        import data.db as db
        import data.call_report_store as store

        self._db = db
        self._store = store
        self._saved_db_engine = db._engine
        self._saved_store_engine = store._engine

        # One shared in-memory SQLite DB for the whole test, isolated from
        # the real cache.db.
        db._engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        store._engine = None  # force re-init (schema create) on first use

    def tearDown(self):
        self._db._engine.dispose()
        self._db._engine = self._saved_db_engine
        self._store._engine = self._saved_store_engine

    @staticmethod
    def _detail(period: str, rssd: int = 352772, **overrides) -> dict:
        d = get_rcn_detail(rssd, period, call_report_df=_df(BANNER_RCN))
        d.update(overrides)
        return d

    def test_round_trip_newest_first(self):
        """Write 3 quarters out of order; read back newest-first, intact."""
        s = self._store
        self.assertEqual(s.upsert_rcn_detail(
            28489, 352772, self._detail("06/30/2025", total_pd30_89=1.0)), 1)
        self.assertEqual(s.upsert_rcn_detail(
            28489, 352772, self._detail("12/31/2025")), 1)
        self.assertEqual(s.upsert_rcn_detail(
            28489, 352772, self._detail("09/30/2025", total_pd30_89=2.0)), 1)

        rows = s.get_stored_rcn_detail(28489)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["reporting_period"] for r in rows],
                         ["12/31/2025", "09/30/2025", "06/30/2025"])
        self.assertEqual([r["total_pd30_89"] for r in rows],
                         [26767.0, 2.0, 1.0])
        # Full nested matrix survives the round-trip, including the derived
        # residual and the USD mirror.
        newest = rows[0]
        self.assertEqual(newest["rssd_id"], 352772)
        self.assertEqual(newest["categories"]["heloc"]["nonaccrual"], 4582.0)
        self.assertEqual(newest["categories"]["other"]["pd30_89"], 678.0)
        self.assertEqual(newest["categories_usd"]["ci"]["nonaccrual"],
                         4_923_000.0)
        self.assertEqual(newest["total_nonaccrual_usd"], 41_525_000.0)

    def test_quarters_limit(self):
        s = self._store
        for period in ["03/31/2025", "06/30/2025", "09/30/2025", "12/31/2025"]:
            s.upsert_rcn_detail(28489, 352772, self._detail(period))
        rows = s.get_stored_rcn_detail(28489, quarters=2)
        self.assertEqual([r["reporting_period"] for r in rows],
                         ["12/31/2025", "09/30/2025"])

    def test_rewrite_same_quarter_is_idempotent(self):
        s = self._store
        s.upsert_rcn_detail(28489, 352772,
                            self._detail("12/31/2025", total_pd90_plus=7.0))
        s.upsert_rcn_detail(28489, 352772, self._detail("12/31/2025"))
        rows = s.get_stored_rcn_detail(28489)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_pd90_plus"], 4114.0)

    def test_banks_are_isolated_by_cert(self):
        s = self._store
        s.upsert_rcn_detail(28489, 352772, self._detail("12/31/2025"))
        s.upsert_rcn_detail(6000, 654321,
                            self._detail("12/31/2025", rssd=654321))
        self.assertEqual(len(s.get_stored_rcn_detail(28489)), 1)
        self.assertEqual(s.get_stored_rcn_detail(6000)[0]["rssd_id"], 654321)

    def test_reader_returns_empty_list_when_nothing_stored(self):
        self.assertEqual(self._store.get_stored_rcn_detail(99999), [])

    def test_upsert_rejects_empty_or_periodless_detail(self):
        s = self._store
        self.assertEqual(s.upsert_rcn_detail(28489, 352772, {}), 0)
        self.assertEqual(s.upsert_rcn_detail(28489, 352772, None), 0)
        self.assertEqual(s.upsert_rcn_detail(
            28489, 352772, {"total_pd30_89": 1.0}), 0)
        self.assertEqual(s.get_stored_rcn_detail(28489), [])

    def test_iso_period_normalized_to_mmddyyyy_on_read(self):
        s = self._store
        s.upsert_rcn_detail(28489, 352772, self._detail("2025-12-31"))
        rows = s.get_stored_rcn_detail(28489)
        self.assertEqual(rows[0]["reporting_period"], "12/31/2025")


_CREDS_FILE = REPO_ROOT / "tools" / "verify_ffiec_e2e.py"


@unittest.skipUnless(_CREDS_FILE.exists(),
                     "gitignored FFIEC creds tool not present")
class TestRcnLiveBannerVerification(unittest.TestCase):
    """LIVE: fetch Banner Bank (RSSD 352772) 12/31/2025 and reconcile the
    extracted RC-N totals against the SNL FY-2025 screenshot."""

    SNL = {"total_pd30_89": 26767.0,
           "total_pd90_plus": 4114.0,
           "total_nonaccrual": 41525.0}

    def test_banner_totals_reconcile_to_snl(self):
        import os
        import re
        src = _CREDS_FILE.read_text(encoding="utf-8")
        user = re.search(r'FFIEC_USERNAME", "([^"]+)"', src).group(1)
        m = re.search(r'FFIEC_JWT_TOKEN",\n((?:\s+"[^"]+"\n)+)', src)
        jwt = "".join(re.findall(r'"([^"]+)"', m.group(1)))
        os.environ.setdefault("FFIEC_USERNAME", user)
        os.environ.setdefault("FFIEC_JWT_TOKEN", jwt)

        d = get_rcn_detail(352772, "12/31/2025")
        self.assertIsNotNone(d, "live fetch returned no RC-N data")

        print("\nBanner Bank 12/31/2025 RC-N vs SNL ($000):")
        print(f"  {'metric':18} {'extracted':>12} {'SNL':>12}")
        for key, snl in self.SNL.items():
            got = d[key]
            print(f"  {key:18} {got:>12,.0f} {snl:>12,.0f}")
            self.assertEqual(got, snl, key)

        # The full matrix (named categories + residual other) re-sums to the
        # filed totals in every column.
        for col, total_key in (("pd30_89", "total_pd30_89"),
                               ("pd90_plus", "total_pd90_plus"),
                               ("nonaccrual", "total_nonaccrual")):
            vals = [c[col] for c in d["categories"].values()]
            self.assertTrue(all(v is not None for v in vals), col)
            self.assertEqual(sum(vals), d[total_key], col)


if __name__ == "__main__":
    unittest.main()
