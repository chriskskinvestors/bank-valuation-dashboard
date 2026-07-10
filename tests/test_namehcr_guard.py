"""
Pins the nightly NAMEHCR wrong-entity guard (data/bank_universe.py:
_names_corroborate / namehcr_flags), added 2026-07-10 after the sweep that
found 18 tickers fuzzy-joined to another bank's FDIC cert.

The guard is OBSERVE-ONLY in jobs/refresh_universe (prints [namehcr-guard]
lines, never fails the job) — harden to a gate only after the nightly logs
show a stable clean baseline. Local baseline 2026-07-10: 447 cert joins
corroborated, zero findings.

Two tells, deliberately complementary:
  • name corroboration — catches 16/18 of the real wrong-join corpus;
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


if __name__ == "__main__":
    unittest.main()
