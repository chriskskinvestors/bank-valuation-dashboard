"""
Pins the nightly NAMEHCR wrong-entity guard (data/bank_universe.py:
_names_corroborate / namehcr_flags), added 2026-07-10 after the sweep that
found 18 tickers fuzzy-joined to another bank's FDIC cert.

The guard is OBSERVE-ONLY in jobs/refresh_universe (prints [namehcr-guard]
lines, never fails the job) — harden to a gate only after the nightly logs
show a stable clean baseline. Local baseline 2026-07-16: 537 cert joins
corroborated on all three keys, zero findings.

Four tells, deliberately complementary:
  • name corroboration — catches 16/18 of the original wrong-join corpus;
  • STATE (added 2026-07-16) — the second identity key. Name alone is not an
    identity: CBSH's $88M Louisiana twin and FDBC's Kansas twin both carry a
    high holder with the registrant's OWN legal name, so every name rule
    affirms them. Holdco state disagreement is what separates them;
  • SIZE (added 2026-07-16) — a single-bank holdco many times larger than its
    claimed sub is a wrong join (JXN's registrant is 443x its cert);
  • duplicate-cert claims — catches the two name-tell leaks (MBIN-class
    shared-token lookalikes, and FNB-class where the wrong cert's holdco is
    LITERALLY named the same), whenever the rightful owner is also tracked.

Offline — pure functions, fixture FDIC records, no network.
"""
import sys
import types
import unittest

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.bank_universe import (  # noqa: E402
    _names_corroborate, namehcr_flags, _NAMEHCR_VERIFIED_OK,
    _STATE_VERIFIED_OK, _SIZE_GAP_X,
)


class TestNamesCorroborate(unittest.TestCase):
    def test_legit_pairs_pass(self):
        # (SEC registrant, NAMEHCR, bank NAME) — every one is a real, correct
        # join that a naive token comparison got wrong in the first sweep.
        for sec, hcr, bank in [
            # FDIC abbreviation style
            ("M&T Bank", "M&T BANK CORP", "Manufacturers and Traders Trust Company"),
            ("U.S. Bancorp", "U S BCORP", "U.S. Bank National Association"),
            ("First Bancorp (NC)", "FIRST BCORP", "First Bank"),
            ("Citizens Financial Services", "CITIZENS FINL SERVICES INC",
             "First Citizens Community Bank"),
            ("First Business Financial Services", "FIRST BUS FINL SERVICES INC",
             "First Business Bank"),
            # registry-truncated tail (prefix rule)
            ("Home Federal Bancorp, Inc. of Louisiana",
             "HOME FEDERAL BCORP INC OF LA", "Home Federal Bank"),
            # SEC /STATE/ suffix
            ("Citizens Holding Co /Ms/", "CITIZENS HOLDING CO",
             "The Citizens Bank of Philadelphia, Mississippi"),
            # high holder above the listed entity: HCR is a trust/LLC, but the
            # bank's own name strictly contains/equals the SEC name
            ("Exchange Bank (Santa Rosa, CA)", "FRANK P DOYLE TRUST ARTICLE IX",
             "Exchange Bank"),
            ("Farmers & Merchants Bank of Long Beach", "PALOMAR ENTERPRISES LLC",
             "Farmers and Merchants Bank of Long Beach"),
            # HCR empty (bank is its own holdco / de novo)
            ("First Bank (NJ)", "", "First Bank"),
            # subsidiary brand differs but HCR matches the registrant
            ("Financial Institutions", "FINANCIAL INSTITUTIONS INC", "Five Star Bank"),
            ("National Bank Holdings", "NATIONAL BANK HOLDINGS CORP", "NBH Bank"),
        ]:
            with self.subTest(sec=sec):
                self.assertTrue(_names_corroborate(sec, hcr, bank))

    def test_wrong_joins_flag(self):
        # The real 2026-07-09 corpus — each served ANOTHER bank's data.
        for sec, hcr, bank in [
            ("First Financial Bankshares",
             "FIRSTFED BCORP EMPLOYEE STK OWNERSHIP PLAN", "First Financial Bank"),
            ("Farmers National Banc Corp.", "PROPHETSTOWN BANKING CO",
             "Farmers National Bank"),
            ("Citizens Financial Group", "FIRST CITIZENS BANCSHARES INC",
             "First-Citizens Bank & Trust Company"),
            ("Bank of New York Mellon Corp", "BANK OF AMERICA CORP",
             "Bank of America, National Association"),
            ("Main Street Capital CORP", "", "The First National Bank of Germantown"),
            ("United Bancorp", "UNITED BANKSHARES INC", "United Bank"),
            ("Community Trust Bancorp", "COMMUNITY FINANCIAL SYSTEM INC",
             "Community Bank, National Association"),
            ("Home Bancorp", "HOME BANCSHARES INC", "Centennial Bank"),
        ]:
            with self.subTest(sec=sec):
                self.assertFalse(_names_corroborate(sec, hcr, bank))

    def test_known_limitations_documented(self):
        # Two wrong joins the NAME tell cannot catch — pinned so a future
        # "improvement" that silently changes these is noticed:
        #   • FNB: the wrong cert's holdco is LITERALLY named "FNB Corp" —
        #     indistinguishable by name (caught 2026-07-09 by assets + manual);
        #   • MBIN: shared distinctive token MERCHANTS.
        # BOTH are caught by the duplicate-cert tell whenever the rightful
        # owner is tracked (FRME claims 4365; FCNCA claims 11063), which is
        # how they were caught in practice.
        self.assertTrue(_names_corroborate(
            "FNB Corp.", "FNB CORP", "First National Bank of South Carolina"))
        self.assertTrue(_names_corroborate(
            "Merchants Bancorp", "FIRST MERCHANTS CORP", "First Merchants Bank"))


class TestNamehcrFlags(unittest.TestCase):
    RECORDS = {
        4365:  {"NAME": "First Merchants Bank", "NAMEHCR": "FIRST MERCHANTS CORP"},
        8056:  {"NAME": "Merchants Bank of Indiana", "NAMEHCR": "MERCHANTS BCORP"},
        28349: {"NAME": "Home Federal Savings and Loan Association of Niles",
                "NAMEHCR": "FIRST NILES FINANCIAL INC"},
        30012: {"NAME": "Third Federal Savings and Loan Association of Cleveland",
                "NAMEHCR": "THIRD FS&LA OF CLEVELAND MHC"},
    }

    def test_dup_cert_flags_distinct_registrants(self):
        snap = {
            "FRME": {"name": "First Merchants Corp.", "cik": 712534,
                     "fdic_cert": 4365, "share_class": "common"},
            "MBIN": {"name": "Merchants Bancorp", "cik": 1629019,
                     "fdic_cert": 4365, "share_class": "common"},
        }
        flags = namehcr_flags(snap, self.RECORDS)
        self.assertEqual(flags["dup_cert"], [(4365, ["FRME", "MBIN"])])

    def test_same_cik_share_classes_not_flagged(self):
        snap = {
            "FRME":  {"name": "First Merchants Corp.", "cik": 712534,
                      "fdic_cert": 4365, "share_class": "common"},
            "FRMEP": {"name": "First Merchants Corp.", "cik": 712534,
                      "fdic_cert": 4365, "share_class": "preferred"},
        }
        self.assertEqual(namehcr_flags(snap, self.RECORDS)["dup_cert"], [])

    def test_cikless_same_name_siblings_not_flagged(self):
        # FNFI/FNFPA: no CIK, but the identical registrant name marks them as
        # share-class siblings, not two companies claiming one cert.
        snap = {
            "FNFI":  {"name": "First Niles Financial", "cik": None,
                      "fdic_cert": 28349, "share_class": "common"},
            "FNFPA": {"name": "First Niles Financial", "cik": None,
                      "fdic_cert": 28349, "share_class": "common"},
        }
        self.assertEqual(namehcr_flags(snap, self.RECORDS)["dup_cert"], [])

    def test_noncommon_entries_skipped_entirely(self):
        # A preferred series still carrying a stale wrong cert (MBINL-class)
        # is display-excluded — it must produce neither mismatch nor dup noise.
        snap = {
            "FRME":  {"name": "First Merchants Corp.", "cik": 712534,
                      "fdic_cert": 4365, "share_class": "common"},
            "MBINL": {"name": "Merchants Bancorp", "cik": 1629019,
                      "fdic_cert": 4365, "share_class": "preferred"},
        }
        flags = namehcr_flags(snap, self.RECORDS)
        self.assertEqual(flags["dup_cert"], [])
        self.assertEqual(flags["mismatch"], [])

    def test_mismatch_and_missing_reported(self):
        snap = {
            # wrong join: Merchants Bancorp on First Merchants' cert
            "MBIN": {"name": "Merchants Bancorp", "cik": 1629019,
                     "fdic_cert": 4365, "share_class": "common"},
            # cert absent from ACTIVE records (acquired/closed)
            "DEAD": {"name": "Gone Bancorp", "cik": 1, "fdic_cert": 99999,
                     "share_class": "common"},
        }
        flags = namehcr_flags(snap, self.RECORDS)
        # NOTE: MBIN-vs-FIRST MERCHANTS corroborates by name (documented
        # limitation above) — so no mismatch row; the dup tell catches it when
        # FRME is present. The missing cert IS reported.
        self.assertEqual(flags["missing"], [("DEAD", 99999)])

    def test_allowlist_is_cert_keyed(self):
        # TFSL's MHC-named HCR is allowlisted for cert 30012 ONLY — a changed
        # cert must flag again.
        self.assertEqual(_NAMEHCR_VERIFIED_OK.get("TFSL"), 30012)
        snap = {"TFSL": {"name": "TFS Financial Corp.", "cik": 1381668,
                         "fdic_cert": 30012, "share_class": "common"}}
        self.assertEqual(namehcr_flags(snap, self.RECORDS)["mismatch"], [])
        snap["TFSL"]["fdic_cert"] = 4365  # moved to someone else's cert
        self.assertEqual(len(namehcr_flags(snap, self.RECORDS)["mismatch"]), 1)


class TestStateKey(unittest.TestCase):
    """The identical-NAMEHCR twins. Every record here is the real FDIC row."""

    # The wrong cert each ticker was actually serving, and the right one.
    TWINS = {
        # CBSH: BOTH high holders are literally "COMMERCE BANCSHARES INC"
        1374:  {"NAME": "The Bank of Commerce", "NAMEHCR": "COMMERCE BANCSHARES INC",
                "STALP": "LA", "STALPHCR": "LA", "ASSET": 88_000},
        24998: {"NAME": "Commerce Bank", "NAMEHCR": "COMMERCE BANCSHARES INC",
                "STALP": "MO", "STALPHCR": "MO", "ASSET": 35_540_239},
        # FDBC: wrong bank is BIGGER than the registrant — size is blind here
        30895: {"NAME": "Fidelity Bank, National Association",
                "NAMEHCR": "FIDELITY FINANCIAL CORP", "STALP": "KS",
                "STALPHCR": "KS", "ASSET": 3_350_000},
        11868: {"NAME": "The Fidelity Deposit and Discount Bank",
                "NAMEHCR": "FIDELITY D&D BCORP INC", "STALP": "PA",
                "STALPHCR": "PA", "ASSET": 2_860_000},
    }
    CBSH = {"name": "Commerce Bancshares", "cik": 22356, "share_class": "common"}
    FDBC = {"name": "Fidelity D & D Bancorp", "cik": 1098151,
            "share_class": "common"}

    def _flags(self, snap, profiles):
        return namehcr_flags(snap, self.TWINS, profiles)

    def test_cbsh_louisiana_twin_flags_on_state(self):
        # The live 2026-07-16 failure: every FDIC number app-wide was an $88M
        # Louisiana bank's. The name tell affirms this join — state must not.
        snap = {"CBSH": {**self.CBSH, "fdic_cert": 1374}}
        profiles = {22356: {"hq_state": "MO", "state_of_incorp": "MO",
                            "assets": 35_500_000_000}}
        flags = self._flags(snap, profiles)
        self.assertEqual(flags["mismatch"], [])          # name AFFIRMS the twin
        self.assertEqual(len(flags["state"]), 1)
        self.assertEqual(flags["state"][0][0], "CBSH")

    def test_cbsh_correct_cert_clean(self):
        snap = {"CBSH": {**self.CBSH, "fdic_cert": 24998}}
        profiles = {22356: {"hq_state": "MO", "state_of_incorp": "MO",
                            "assets": 35_500_000_000}}
        flags = self._flags(snap, profiles)
        self.assertEqual(sum(len(v) for v in flags.values()), 0)

    def test_fdbc_same_size_twin_flags_on_state_only(self):
        # 1.17x apart: only the state key can see this one.
        snap = {"FDBC": {**self.FDBC, "fdic_cert": 30895}}
        profiles = {1098151: {"hq_state": "PA", "state_of_incorp": "PA",
                              "assets": 2_860_000_000}}
        flags = self._flags(snap, profiles)
        self.assertEqual(flags["size"], [])
        self.assertEqual(len(flags["state"]), 1)

    def test_fdbc_correct_cert_clean(self):
        snap = {"FDBC": {**self.FDBC, "fdic_cert": 11868}}
        profiles = {1098151: {"hq_state": "PA", "state_of_incorp": "PA",
                              "assets": 2_860_000_000}}
        self.assertEqual(sum(len(v) for v in self._flags(snap, profiles).values()), 0)

    def test_high_holder_above_registrant_escapes_via_bank_state(self):
        # AMAL: the high holder is WORKERS UNITED (a PA union) but Amalgamated
        # Bank sits in NY where the registrant does — corroborated, not flagged.
        rec = {622: {"NAME": "Amalgamated Bank", "NAMEHCR": "WORKERS UNITED",
                     "STALP": "NY", "STALPHCR": "PA", "ASSET": 9_170_000}}
        snap = {"AMAL": {"name": "Amalgamated Financial", "cik": 1823608,
                         "fdic_cert": 622, "share_class": "common"}}
        profiles = {1823608: {"hq_state": "NY", "state_of_incorp": "DE",
                              "assets": 9_170_000_000}}
        self.assertEqual(namehcr_flags(snap, rec, profiles)["state"], [])

    def test_charter_state_away_from_holdco_is_not_a_finding(self):
        # JPMorgan Chase Bank NA is chartered in OHIO. Comparing the registrant
        # to the BANK's state would flag 23 legitimate joins; holdco-to-holdco
        # is the comparison that holds.
        rec = {628: {"NAME": "JPMorgan Chase Bank, National Association",
                     "NAMEHCR": "JPMORGAN CHASE & CO", "STALP": "OH",
                     "STALPHCR": "NY", "ASSET": 3_400_000_000}}
        snap = {"JPM": {"name": "JPMorgan Chase & Co.", "cik": 19617,
                        "fdic_cert": 628, "share_class": "common"}}
        profiles = {19617: {"hq_state": "NY", "state_of_incorp": "DE",
                            "assets": 4_000_000_000_000}}
        self.assertEqual(namehcr_flags(snap, rec, profiles)["state"], [])

    def test_missing_state_never_manufactures_a_finding(self):
        snap = {"CBSH": {**self.CBSH, "fdic_cert": 1374}}
        for profile in ({"hq_state": "", "state_of_incorp": "", "assets": None},
                        {"assets": None}):
            with self.subTest(profile=profile):
                self.assertEqual(self._flags(snap, {22356: profile})["state"], [])

    def test_no_sec_profile_runs_name_tell_only(self):
        # Every FDIC-only §12(i) bank (cik=None) lands here.
        snap = {"CBSH": {**self.CBSH, "cik": None, "fdic_cert": 1374}}
        flags = namehcr_flags(snap, self.TWINS, {})
        self.assertEqual(flags["state"], [])
        self.assertEqual(flags["size"], [])

    def test_state_allowlist_is_cert_keyed(self):
        self.assertEqual(_STATE_VERIFIED_OK.get("BPRN"), 58513)
        rec = {58513: {"NAME": "The Bank of Princeton",
                       "NAMEHCR": "PRINCETON BCORP INC", "STALP": "NJ",
                       "STALPHCR": "NJ", "ASSET": 2_250_000},
               1374: self.TWINS[1374]}
        # SEC address is the registrant's law firm in PA — hand-verified OK.
        snap = {"BPRN": {"name": "Princeton Bancorp", "cik": 1913971,
                         "fdic_cert": 58513, "share_class": "common"}}
        profiles = {1913971: {"hq_state": "PA", "state_of_incorp": "PA",
                              "assets": 2_250_000_000}}
        self.assertEqual(namehcr_flags(snap, rec, profiles)["state"], [])
        snap["BPRN"]["fdic_cert"] = 1374   # moved onto someone else's cert
        self.assertEqual(len(namehcr_flags(snap, rec, profiles)["state"]), 1)


class TestSizeKey(unittest.TestCase):
    RECORDS = {
        # JXN: an annuity registrant on a Kentucky community bank's cert
        2759:  {"NAME": "FNB Bank, Inc.", "NAMEHCR": "JACKSON FINANCIAL CORP",
                "STALP": "KY", "STALPHCR": "KY", "ASSET": 766_000},
        # Wintrust: 15 charters, so one sub is legitimately a fraction
        33935: {"NAME": "Wintrust Bank, National Association",
                "NAMEHCR": "WINTRUST FINANCIAL CORP", "STALP": "IL",
                "STALPHCR": "IL", "ASSET": 9_280_000, "HCTMULT": "1"},
    }

    def test_holdco_hundreds_of_times_its_sub_flags(self):
        snap = {"JXN": {"name": "Jackson Financial Inc.", "cik": 1822993,
                        "fdic_cert": 2759, "share_class": "common"}}
        profiles = {1822993: {"hq_state": "KY", "state_of_incorp": "KY",
                              "assets": 339_540_000_000}}
        flags = namehcr_flags(snap, self.RECORDS, profiles)
        # states deliberately agree here: the SIZE key stands on its own.
        self.assertEqual(flags["state"], [])
        self.assertEqual(len(flags["size"]), 1)
        self.assertGreater(flags["size"][0][2], 400)

    def test_multibank_holdco_escapes_via_hctmult(self):
        # WTFC's holdco is 7.8x this charter — legitimate, and FDIC says so.
        snap = {"WTFC": {"name": "Wintrust Financial", "cik": 1015328,
                         "fdic_cert": 33935, "share_class": "common"}}
        profiles = {1015328: {"hq_state": "IL", "state_of_incorp": "IL",
                              "assets": 72_160_000_000}}
        self.assertEqual(namehcr_flags(snap, self.RECORDS, profiles)["size"], [])

    def test_single_bank_holdco_matches_its_sub(self):
        snap = {"OK": {"name": "Fine Bancorp", "cik": 5, "fdic_cert": 2759,
                       "share_class": "common"}}
        # ~1.0x — a single-bank holdco carries its assets in its one charter.
        profiles = {5: {"hq_state": "KY", "state_of_incorp": "KY",
                        "assets": 766_000 * 1000}}
        self.assertEqual(namehcr_flags(snap, self.RECORDS, profiles)["size"], [])

    def test_threshold_catches_the_smallest_real_gap(self):
        # The 14 wrong entities swept on 2026-07-16 bottomed out at ~4x; the
        # ~100x an $88M-vs-$35B case suggests would have caught only 2 of them.
        self.assertLessEqual(_SIZE_GAP_X, 4.0)

    def test_absent_assets_are_not_comparable(self):
        snap = {"X": {"name": "Whoever", "cik": 7, "fdic_cert": 2759,
                      "share_class": "common"}}
        for assets in (None, 0):
            with self.subTest(assets=assets):
                profiles = {7: {"hq_state": "KY", "state_of_incorp": "KY",
                                "assets": assets}}
                self.assertEqual(
                    namehcr_flags(snap, self.RECORDS, profiles)["size"], [])


if __name__ == "__main__":
    unittest.main()
