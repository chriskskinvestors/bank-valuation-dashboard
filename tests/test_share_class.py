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
import unittest

# Order-independent streamlit stub (shared helper) — bank_universe's
# @st.cache_data decorators must no-op when tests import it lazily.
from tests import _streamlit_stub

_streamlit_stub.install()

from data.share_class import (  # noqa: E402
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


class TestDisplaySurfaces(unittest.TestCase):
    """Honest count + cleaner search: non-common classes are hidden from the
    covered count and from browse/prefix/name search, but get_universe() stays
    the raw resolution store and exact-ticker lookup still works."""

    def _stub_universe(self):
        import data.bank_universe as bu
        uni = {t: {**v, "name": "First Citizens BancShares"}
               for t, v in FCNC.items()}
        uni["JPM"] = {"cik": 19617, "fdic_cert": 628, "name": "JPMorgan Chase"}
        bu._UNIVERSE_CACHE = uni
        bu._NONCOMMON_CACHE = None  # force recompute against the stub
        return bu, uni

    def test_count_excludes_non_common(self):
        bu, uni = self._stub_universe()
        try:
            # 6 raw tickers (5 FCNC + JPM); 4 non-common (FCNCB/N/O/P) → 2 covered
            self.assertEqual(bu.get_universe_count(), 2)
            self.assertEqual(bu.get_universe_count_fast(), "2")
        finally:
            bu._UNIVERSE_CACHE = None
            bu._NONCOMMON_CACHE = None

    def test_search_hides_non_common_from_discovery(self):
        bu, uni = self._stub_universe()
        try:
            hits = {r["ticker"] for r in bu.search_universe("FCNC")}
            self.assertIn("FCNCA", hits)
            self.assertFalse(hits & {"FCNCB", "FCNCN", "FCNCO", "FCNCP"})
            # Name search likewise excludes the preferred series.
            name_hits = {r["ticker"] for r in bu.search_universe("First Citizens")}
            self.assertEqual(name_hits, {"FCNCA"})
        finally:
            bu._UNIVERSE_CACHE = None
            bu._NONCOMMON_CACHE = None

    def test_search_exact_lookup_of_preferred_still_resolves(self):
        bu, uni = self._stub_universe()
        try:
            hits = bu.search_universe("FCNCP")
            self.assertEqual([r["ticker"] for r in hits], ["FCNCP"])
        finally:
            bu._UNIVERSE_CACHE = None
            bu._NONCOMMON_CACHE = None


class TestForeignTwinExclusion(unittest.TestCase):
    """A skipped foreign ADR (MFG) leaves its OTC ordinary twin (MZHOF) alone
    under one CIK, so the share-class CIK rule sees a lone 'common'. The skip
    list must be enforced at runtime so MZHOF is dropped from screens, search,
    and the count without waiting for a snapshot rebuild."""

    def _stub(self):
        import data.bank_universe as bu
        import data.bank_mapping as bm
        uni = {
            "JPM":   {"cik": 19617, "fdic_cert": 628, "name": "JPMorgan Chase"},
            "MZHOF": {"cik": 1335730, "fdic_cert": 21843,
                      "name": "MIZUHO FINANCIAL GROUP INC", "exchange": "OTC"},
        }
        bu._UNIVERSE_CACHE = uni
        bu._NONCOMMON_CACHE = None
        return bu, bm, uni

    def test_mzhof_in_skip_list(self):
        import data.bank_universe as bu
        self.assertIn("MZHOF", bu._SKIP_TICKERS)

    def test_mzhof_excluded_everywhere(self):
        bu, bm, uni = self._stub()
        orig = bm.get_cik
        bm.get_cik = lambda t: uni.get(t, {}).get("cik")
        try:
            self.assertIn("MZHOF", bu.coverage_excluded())
            self.assertNotIn("MZHOF", bu.get_universe_tickers())
            self.assertIn("JPM", bu.get_universe_tickers())
            self.assertEqual(bu.get_universe_count(), 1)  # only JPM covered
            hits = {r["ticker"] for r in bu.search_universe("MIZUHO")}
            self.assertNotIn("MZHOF", hits)
        finally:
            bm.get_cik = orig
            bu._UNIVERSE_CACHE = None
            bu._NONCOMMON_CACHE = None


class TestCertTickerMap(unittest.TestCase):
    """cert -> ticker deep links resolve to the registrant's primary common,
    never a shared-cert ETN/preferred sibling. Prod regression 2026-09-16:
    Norfolk County, MA deposit market-share table linked "JPMorgan Chase Bank,
    National Association" to AMJB (a JPMorgan-issued ETN under the same cert)
    instead of JPM — first-seen setdefault over the RAW universe."""

    def setUp(self):
        import data.bank_universe as bu
        self.bu = bu
        self._saved = (bu._UNIVERSE_CACHE, bu._NONCOMMON_CACHE,
                       bu._NONCOMMON_PRIMARY_CACHE)
        bu._NONCOMMON_CACHE = None
        bu._NONCOMMON_PRIMARY_CACHE = None

    def tearDown(self):
        (self.bu._UNIVERSE_CACHE, self.bu._NONCOMMON_CACHE,
         self.bu._NONCOMMON_PRIMARY_CACHE) = self._saved

    def test_shared_cert_etn_sibling_never_wins(self):
        # AMJB deliberately FIRST in insertion order — the old first-wins
        # setdefault handed it the cert.
        self.bu._UNIVERSE_CACHE = {
            "AMJB": {"cik": 19617, "fdic_cert": 628,
                     "name": "JPMorgan Chase", "exchange": "NYSE Arca"},
            "JPM":  {"cik": 19617, "fdic_cert": 628,
                     "name": "JPMorgan Chase", "exchange": "NYSE"},
        }
        self.assertEqual(self.bu.cert_ticker_map().get(628), "JPM")

    def test_skip_listed_twin_stays_unlinked(self):
        # MZHOF (skip-listed OTC twin, no Company page) must not claim its
        # cert — better an unlinked name than a link to an uncovered page.
        self.bu._UNIVERSE_CACHE = {
            "MZHOF": {"cik": 1335730, "fdic_cert": 21843,
                      "name": "MIZUHO FINANCIAL GROUP INC", "exchange": "OTC"},
        }
        self.assertNotIn(21843, self.bu.cert_ticker_map())


# First Niles Financial (Niles, OH) as it sits in bank_map_resolved.json: an
# FDIC-only bank (deregistered 2006, no CIK) whose common FNFI and Series A
# preferred FNFPA share cert 28349. The preferred exists because the 2006
# going-private reclassification turned holdings of <=300 common shares into
# Series A Preferred (SEC SC 13E3/A, CIK 1065823, filed 2006-11-16) — so FNFI
# is the common. Keyed on CIK alone, neither was clustered and every Screen
# listed the bank twice (prod finding 2026-09-30).
FNF = {
    "FNFI":  {"cik": None, "fdic_cert": 28349, "name": "First Niles Financial",
              "exchange": "OTC"},
    "FNFPA": {"cik": None, "fdic_cert": 28349, "name": "First Niles Financial",
              "exchange": "OTC"},
}


class TestCertlessSiblings(unittest.TestCase):
    def test_fnfpa_excluded_fnfi_kept(self):
        self.assertEqual(noncommon_tickers({t: {**v} for t, v in FNF.items()}),
                         {"FNFPA"})

    def test_served_snapshot_labels_both_common(self):
        """The snapshot built before this fix annotated both as singleton
        'common'; the persisted-label path must still keep only FNFI."""
        uni = {t: {**v, "share_class": "common"} for t, v in FNF.items()}
        self.assertEqual(noncommon_tickers(uni), {"FNFPA"})

    def test_annotate_and_canonicalize(self):
        from data.share_class import noncommon_to_primary
        uni = {t: {**v} for t, v in FNF.items()}
        self.assertEqual(noncommon_to_primary(uni), {"FNFPA": "FNFI"})
        annotate_share_classes(uni)
        self.assertEqual(uni["FNFI"]["share_class"], "common")
        self.assertEqual(uni["FNFPA"]["share_class"], "preferred")

    def test_certless_banks_on_distinct_certs_untouched(self):
        uni = {"FNFI": {**FNF["FNFI"]},
               "OAKC": {"cik": None, "fdic_cert": 99991, "name": "Oak"}}
        self.assertEqual(noncommon_tickers(uni), set())

    def test_cik_and_certless_on_one_cert_not_silently_merged(self):
        """A CIK'd ticker and a CIK-less one on the same cert are different
        registrant keys — a wrong-entity join, not siblings. The classifier
        leaves both; shared_cert_claims (the deploy gate) reports it."""
        from data.bank_universe import shared_cert_claims
        uni = {"AAA": {"cik": 111, "fdic_cert": 500},
               "BBB": {"cik": None, "fdic_cert": 500}}
        self.assertEqual(noncommon_tickers(uni), set())
        self.assertEqual(shared_cert_claims({t: v["fdic_cert"]
                                             for t, v in uni.items()}),
                         {500: ["AAA", "BBB"]})


class TestSharedCertClaims(unittest.TestCase):
    def test_shape(self):
        from data.bank_universe import shared_cert_claims
        self.assertEqual(
            shared_cert_claims({"FNFPA": 28349, "FNFI": 28349, "JPM": 628,
                                "X": None, "Y": 0}),
            {28349: ["FNFI", "FNFPA"]})

    def test_curated_maps_one_covered_ticker_per_cert(self):
        """Every cert in the committed curated maps (BANK_MAP over
        bank_map_resolved.json, get_cik/get_fdic_cert precedence) belongs to
        exactly one covered ticker once share-class siblings and skip-listed
        tickers are dropped. The live-universe twin of this check runs in the
        deploy gate (tests/test_universe_coverage.py)."""
        from data.bank_mapping import BANK_MAP, _RESOLVED_FROM_JSON
        from data.bank_universe import _SKIP_TICKERS, shared_cert_claims
        uni = {t: dict(v) for t, v in {**_RESOLVED_FROM_JSON, **BANK_MAP}.items()}
        excluded = noncommon_tickers(uni) | _SKIP_TICKERS
        self.assertEqual(
            shared_cert_claims({t: v.get("fdic_cert") for t, v in uni.items()
                                if t not in excluded}),
            {})


if __name__ == "__main__":
    unittest.main()
