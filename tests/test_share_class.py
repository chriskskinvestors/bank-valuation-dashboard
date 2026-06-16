"""Regression tests for share-class exclusion (data/share_class.py).

Pins the bug where non-common share-class tickers polluted the valuation
screens and the Home leaderboard: First Citizens preferred series FCNCN/
FCNCO/FCNCP share CIK 798941 / FDIC cert 11063 with the common FCNCA, so the
pipeline joined the common's ~$1,600 TBVPS to their ~$25 price and rendered a
~0.01x P/TBV and ~+99% "discount to fair value".

The fix excludes non-common classes at data.bank_universe.get_universe_tickers
— the single scope feeding screens + leaderboard — identified structurally by
shared CIK (not a hardcoded ticker blocklist), so it generalizes across the
26 multi-ticker registrants in the universe.
"""
import sys
import types
import unittest

from data.share_class import (
    noncommon_tickers,
    annotate_share_classes,
    _pick_primary,
    _name_flags_noncommon,
)


# First Citizens cluster as it appears in the universe (CIK/cert shared across
# all five tickers — the exact shape that produced the garbage screen rows).
FCNC = {
    "FCNCA": {"cik": 798941, "fdic_cert": 11063, "name": "First Citizens"},
    "FCNCB": {"cik": 798941, "fdic_cert": 11063, "name": "First Citizens"},
    "FCNCN": {"cik": 798941, "fdic_cert": 11063, "name": "First Citizens"},
    "FCNCO": {"cik": 798941, "fdic_cert": 11063, "name": "First Citizens"},
    "FCNCP": {"cik": 798941, "fdic_cert": 11063, "name": "First Citizens"},
}


class TestNonCommonExclusion(unittest.TestCase):
    def test_fcncp_excluded_fcnca_kept_structural(self):
        """The required pin: a known preferred (FCNCP) is excluded, the common
        (FCNCA) is kept — from CIK structure alone, no persisted field."""
        nc = noncommon_tickers(dict(FCNC))
        self.assertIn("FCNCP", nc)
        self.assertIn("FCNCN", nc)
        self.assertIn("FCNCO", nc)
        self.assertNotIn("FCNCA", nc)

    def test_keeps_exactly_one_common_per_registrant(self):
        """FCNCB is a redundant second common class — only FCNCA survives."""
        nc = noncommon_tickers(dict(FCNC))
        survivors = set(FCNC) - nc
        self.assertEqual(survivors, {"FCNCA"})

    def test_persisted_share_class_field_is_trusted(self):
        """When the nightly FMP-backed field is present it drives exclusion;
        an extra labelled-common sibling is still demoted to one row."""
        uni = {t: {**v} for t, v in FCNC.items()}
        uni["FCNCA"]["share_class"] = "common"
        uni["FCNCB"]["share_class"] = "common"
        for t in ("FCNCN", "FCNCO", "FCNCP"):
            uni[t]["share_class"] = "preferred"
        nc = noncommon_tickers(uni)
        self.assertEqual(set(FCNC) - nc, {"FCNCA"})

    def test_single_ticker_registrant_never_excluded(self):
        """An ordinary bank (one ticker per CIK) is untouched — including a
        common ticker that happens to end in a preferred-looking letter."""
        uni = {
            "CFFN": {"cik": 1466026111, "name": "Capitol Federal"},  # ends in N
            "FCNCA": FCNC["FCNCA"], "FCNCP": FCNC["FCNCP"],
            "FCNCN": FCNC["FCNCN"], "FCNCO": FCNC["FCNCO"],
            "FCNCB": FCNC["FCNCB"],
        }
        nc = noncommon_tickers(uni)
        self.assertNotIn("CFFN", nc)

    def test_base_ticker_cluster_keeps_base(self):
        """A cluster with a unique shortest base ticker keeps it (FITB over
        FITBI/M/O/P) without any curated entry."""
        uni = {t: {"cik": 35527} for t in
               ("FITB", "FITBI", "FITBM", "FITBO", "FITBP")}
        nc = noncommon_tickers(uni)
        self.assertEqual(set(uni) - nc, {"FITB"})

    def test_ambiguous_clusters_resolve_to_curated_common(self):
        """Equal-length clusters with no base resolve to the curated common."""
        dcom = {"DCOM": {"cik": 846617}, "DCBG": {"cik": 846617}}
        cub = {"CUBI": {"cik": 1488813}, "CUBB": {"cik": 1488813}}
        self.assertEqual(set(dcom) - noncommon_tickers(dcom), {"DCOM"})
        self.assertEqual(set(cub) - noncommon_tickers(cub), {"CUBI"})

    def test_rebranded_ticker_prefers_major_exchange(self):
        """BNY Mellon changed its ticker BK→BNY; BK is now OTC/stale. The live
        common (BNY, NYSE) must win over the shorter, stale BK (OTC)."""
        uni = {
            "BK":  {"cik": 1390777, "exchange": "OTC"},
            "BNY": {"cik": 1390777, "exchange": "NYSE"},
        }
        nc = noncommon_tickers(uni)
        self.assertIn("BK", nc)
        self.assertNotIn("BNY", nc)

    def test_unknown_ambiguous_cluster_fails_safe(self):
        """An uncurated equal-length cluster with no base drops ALL members
        (n/a) rather than risk showing a preferred as common."""
        uni = {"XXXA": {"cik": 99999999}, "XXXB": {"cik": 99999999}}
        self.assertEqual(noncommon_tickers(uni), {"XXXA", "XXXB"})


class TestAnnotateAndVerify(unittest.TestCase):
    def test_annotate_sets_common_and_preferred(self):
        uni = {t: {**v} for t, v in FCNC.items()}
        uni["CFFN"] = {"cik": 1466026111}
        annotate_share_classes(uni)
        self.assertEqual(uni["FCNCA"]["share_class"], "common")
        self.assertEqual(uni["FCNCP"]["share_class"], "preferred")
        self.assertEqual(uni["CFFN"]["share_class"], "common")

    def test_name_lookup_verification_runs_without_overriding(self):
        """An FMP name on the structural common logs a warning but does not
        flip the decision (structural pick stands)."""
        uni = {t: {**v} for t, v in FCNC.items()}
        names = {"FCNCN": "First Citizens Depositary Shares",
                 "FCNCP": "First Citizens 5.30% Series"}
        annotate_share_classes(uni, name_lookup=lambda t: names.get(t))
        self.assertEqual(uni["FCNCA"]["share_class"], "common")

    def test_name_marker_detection(self):
        self.assertTrue(_name_flags_noncommon("Customers Bancorp, Inc 5.375% S"))
        self.assertTrue(_name_flags_noncommon("Dime Community Bancshares 9 % Notes"))
        self.assertTrue(_name_flags_noncommon("... Depositary Shares"))
        self.assertFalse(_name_flags_noncommon("First Citizens BancShares, Inc."))
        self.assertFalse(_name_flags_noncommon("Fifth Third Bancorp"))

    def test_pick_primary_prefers_curated_over_morphology(self):
        self.assertEqual(_pick_primary(sorted(FCNC), 798941), "FCNCA")


class TestUniverseTickerFilter(unittest.TestCase):
    """End-to-end: get_universe_tickers drops the preferred series."""

    def test_get_universe_tickers_excludes_preferred(self):
        # Stub streamlit so bank_universe's @st.cache_data decorators no-op.
        st = types.ModuleType("streamlit")
        st.cache_data = lambda *a, **k: (
            a[0] if a and callable(a[0]) else (lambda f: f))
        st.cache_resource = st.cache_data
        sys.modules.setdefault("streamlit", st)

        import data.bank_universe as bu
        import data.bank_mapping as bm

        uni = {t: {**v} for t, v in FCNC.items()}
        uni["JPM"] = {"cik": 19617, "fdic_cert": 628, "name": "JPMorgan"}

        orig_universe = bu.get_universe
        orig_get_cik = bm.get_cik
        bu.get_universe = lambda: uni
        bm.get_cik = lambda t: uni.get(t, {}).get("cik")
        try:
            tickers = bu.get_universe_tickers()
        finally:
            bu.get_universe = orig_universe
            bm.get_cik = orig_get_cik

        self.assertIn("FCNCA", tickers)
        self.assertIn("JPM", tickers)
        for pref in ("FCNCB", "FCNCN", "FCNCO", "FCNCP"):
            self.assertNotIn(pref, tickers)


if __name__ == "__main__":
    unittest.main()
