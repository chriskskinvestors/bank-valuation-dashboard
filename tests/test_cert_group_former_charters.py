"""
(2026-10-05) Former charters merged into a holdco's surviving bank count
toward its history for the quarters the holdco held them.

The charter group was ACTIVE charters only, so history dropped every charter
that later merged away. TMP folded The Bank of Castile, Mahopac Bank and VIST
Bank into Tompkins Trust on 2022-01-01: Financial Highlights FY2021 showed
total assets of $2.45B (Tompkins Trust alone) for a $7.82B banking operation.
Measured 2026-10-05: 48 such charters (ended 2021+) under 20 universe
holdcos — CBC (12 charters, 2021), MS (E*TRADE Bank, $79B), PNC (BBVA USA
Q2-Q3 2021, FirstBank Q1-2026), USB (MUFG Union Bank Q4-22/Q1-23), ...

A former charter's record counts only when its own RSSDHCR (FDIC stamps the
high holder per REPDTE) is the holdco: BBVA USA reads 1391237 (BBVA) through
Q1-2021 and 1069778 (PNC) from Q2-2021, so PNC's pre-acquisition history is
not inflated by a bank it did not own yet.

Values are FDIC financials ($K), pulled live 2026-10-05; the group totals
equal Σ ASSET over every charter FDIC lists under the holdco's RSSDHCR at
that REPDTE (the independent check run across TMP, CBC, MS, PNC, C).

Run: python -m unittest tests.test_cert_group_former_charters
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

import data.cert_group as cg  # noqa: E402

TMP_HC = "2367921"
# 2021-12-31 call reports under Tompkins Financial (RSSDHCR 2367921).
TMP_ROWS = {
    609: [{"CERT": 609, "REPDTE": "20220331", "ASSET": 7_842_153, "RSSDHCR": TMP_HC},
          {"CERT": 609, "REPDTE": "20211231", "ASSET": 2_453_893, "RSSDHCR": TMP_HC}],
    13292: [{"CERT": 13292, "REPDTE": "20211231", "ASSET": 1_865_267, "RSSDHCR": TMP_HC}],
    7153: [{"CERT": 7153, "REPDTE": "20211231", "ASSET": 1_551_206, "RSSDHCR": TMP_HC}],
    7748: [{"CERT": 7748, "REPDTE": "20211231", "ASSET": 1_945_388, "RSSDHCR": TMP_HC}],
}

PNC_HC = "1069778"
# BBVA USA (cert 19048): held by BBVA (1391237) through Q1-21, by PNC from Q2-21.
PNC_ROWS = {
    6384: [{"CERT": 6384, "REPDTE": "20210630", "ASSET": 453_973_605, "RSSDHCR": PNC_HC},
           {"CERT": 6384, "REPDTE": "20210331", "ASSET": 469_299_477, "RSSDHCR": PNC_HC}],
    19048: [{"CERT": 19048, "REPDTE": "20210630", "ASSET": 95_848_996, "RSSDHCR": PNC_HC},
            {"CERT": 19048, "REPDTE": "20210331", "ASSET": 101_925_551, "RSSDHCR": "1391237"}],
}


def _fetch(rows):
    return lambda c, limit=20: pd.DataFrame(rows.get(c, []))


class TestBuildAbsorbedMap(unittest.TestCase):
    def test_every_active_cert_of_the_holdco_is_keyed(self):
        active = [{"cert": 609, "rssdhcr": TMP_HC}, {"cert": 1, "rssdhcr": "999"},
                  {"cert": 2, "rssdhcr": ""}]
        inactive = [{"cert": 13292, "rssdhcr": TMP_HC, "end": "01/01/2022"},
                    {"cert": 7153, "rssdhcr": TMP_HC, "end": "01/01/2022"},
                    {"cert": 7748, "rssdhcr": TMP_HC, "end": "01/01/2022"},
                    {"cert": 5, "rssdhcr": None, "end": "01/01/2022"}]
        m = cg.build_absorbed_map(active, inactive)
        self.assertEqual(m, {"609": {
            "hc": TMP_HC, "certs": [7153, 7748, 13292],
            "ends": {"13292": "20220101", "7153": "20220101", "7748": "20220101"}}})

    def test_get_absorbed_charters_reads_the_map_only(self):
        m = {"609": {"hc": TMP_HC, "certs": [7153, 7748, 13292],
                     "ends": {"7153": "20220101", "7748": "20220101", "13292": "19990630"}}}
        with patch("data.cache.get", return_value=m):
            self.assertEqual(cg.get_absorbed_charters(609), (TMP_HC, [7153, 7748, 13292]))
            self.assertEqual(cg.get_absorbed_charters(4), (None, []))
            # A window starting 2021-06 cannot hold a charter that ended in 1999.
            self.assertEqual(cg.get_absorbed_charters(609, since="20210601"),
                             (TMP_HC, [7153, 7748]))
            self.assertEqual(cg.get_absorbed_charters(609, since="20230101"), (None, []))
        with patch("data.cache.get", return_value=None):
            self.assertEqual(cg.get_absorbed_charters(609), (None, []))


class TestGroupHistoryIncludesFormerCharters(unittest.TestCase):
    def _hist(self, rows, group, absorbed):
        with patch("data.fdic_client.fetch_financials", side_effect=_fetch(rows)), \
                patch.object(cg, "get_cert_group", return_value=group), \
                patch.object(cg, "get_absorbed_charters", return_value=absorbed):
            return cg.fetch_group_history("T", limit=20)

    def test_tmp_fy2021_sums_the_four_charters(self):
        h = self._hist(TMP_ROWS, [609], (TMP_HC, [7153, 7748, 13292]))
        by = {r["REPDTE"]: r for r in h}
        # 2,453,893 + 1,865,267 + 1,551,206 + 1,945,388 = 7,815,754
        self.assertEqual(by["20211231"]["ASSET"], 7_815_754)
        self.assertEqual(by["20211231"]["_charter_count"], 4)
        self.assertEqual(by["20211231"]["CERT"], 609)            # lead identity
        # After the merger the survivor alone — passthrough, unaggregated.
        self.assertEqual(by["20220331"]["ASSET"], 7_842_153)
        self.assertNotIn("_aggregated", by["20220331"])

    def test_without_the_map_only_the_survivor_counted(self):
        h = self._hist(TMP_ROWS, [609], (None, []))
        self.assertEqual({r["REPDTE"]: r["ASSET"] for r in h}["20211231"], 2_453_893)

    def test_pre_acquisition_quarters_stay_out(self):
        h = self._hist(PNC_ROWS, [6384], (PNC_HC, [19048]))
        by = {r["REPDTE"]: r["ASSET"] for r in h}
        self.assertEqual(by["20210630"], 453_973_605 + 95_848_996)   # 549,822,601
        self.assertEqual(by["20210331"], 469_299_477)                # BBVA not PNC's yet

    def test_holder_stored_as_float_still_counts(self):
        # Mahopac/VIST: pre-2006/2012 quarters have no holder, so pandas makes
        # the column float64 and the deep store keeps 2367921.0.
        rows = {609: TMP_ROWS[609],
                7153: [{"CERT": 7153, "REPDTE": "20211231", "ASSET": 1_551_206,
                        "RSSDHCR": 2367921.0},
                       {"CERT": 7153, "REPDTE": "19921231", "ASSET": 300_000,
                        "RSSDHCR": float("nan")}]}
        h = self._hist(rows, [609], (TMP_HC, [7153]))
        by = {r["REPDTE"]: r["ASSET"] for r in h}
        self.assertEqual(by["20211231"], 2_453_893 + 1_551_206)
        self.assertNotIn("19921231", by)                # NaN holder: not ours

    def test_record_without_a_holder_is_not_counted(self):
        rows = {609: TMP_ROWS[609],
                13292: [{"CERT": 13292, "REPDTE": "20211231", "ASSET": 1_865_267}]}
        h = self._hist(rows, [609], (TMP_HC, [13292]))
        self.assertEqual({r["REPDTE"]: r["ASSET"] for r in h}["20211231"], 2_453_893)

    def test_failed_former_fetch_withholds_not_a_short_fy2021(self):
        # REVIEW 2026-10-05 P0-4 applied to former charters: losing Mahopac
        # (13292) would print FY2021 as 5,950,487 for a 7,815,754 operation.
        rows = {c: r for c, r in TMP_ROWS.items() if c != 13292}
        h = self._hist(rows, [609], (TMP_HC, [7153, 7748, 13292]))
        self.assertEqual(h, [])


class TestDeepStoreIncludesFormerCharters(unittest.TestCase):
    def test_deep_read_applies_the_same_rule(self):
        import data.fdic_history_store as s
        stored = {c: rows for c, rows in PNC_ROWS.items()}
        with patch.object(s, "get_cert_history",
                          side_effect=lambda c, limit=None: stored.get(c, [])), \
                patch.object(s, "get_certs_history",
                             side_effect=lambda cs: {c: stored[c] for c in cs if c in stored}), \
                patch.object(cg, "get_cert_group", return_value=[6384]), \
                patch.object(cg, "get_absorbed_charters", return_value=(PNC_HC, [19048])):
            out = s.deep_group_history("PNC")
        by = {r["REPDTE"]: r["ASSET"] for r in out}
        self.assertEqual(by["20210630"], 549_822_601)
        self.assertEqual(by["20210331"], 469_299_477)


if __name__ == "__main__":
    unittest.main()
