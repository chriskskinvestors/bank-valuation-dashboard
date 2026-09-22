"""
Pins the verify-metrics oracle to the HOUSE conventions (2026-07-09).

The 2026-07-09 18:00 ET run failed with 231 divergences led by roatce_holdco
(116) and fmp:tbvps (88) — the oracle still computed the OLD preferred-inclusive
ROATCE after the display path moved to the common basis (audit #24b), and the
FMP tangible-book cross-check compared FMP's total-basis figure against our
common-basis TBVPS. Pure convention skew, not a regression — but it failed the
job and paged the owner. The oracle must (1) re-derive ROATCE on the common
basis, (2) keep cross-source/cross-convention checks in a WARN tier that never
fails the job.
"""
import unittest
from pathlib import Path

# Order-independent streamlit stub (shared helper).
from tests import _streamlit_stub

_streamlit_stub.install()

from tools.verify_metrics import _oracle, _split_pe_basis  # noqa: E402


def _sec(**over):
    base = {
        "book_value_total": 1000.0, "goodwill": 60.0, "intangibles": 40.0,
        "intangible_adjustment": 100.0, "shares_outstanding": 100.0,
        "net_income": 72.0, "net_income_to_common_ttm": 60.0,
        "eps": 0.72, "dividends_per_share": 0.30,
        "preferred_present": True, "preferred_stock": 200.0,
        "tangible_book_value_per_share": 7.0,
    }
    base.update(over)
    return base


class TestOracleCommonBasisRoatce(unittest.TestCase):
    def test_preferred_bank_common_basis(self):
        # 60 / (1000 − 200 − 100) = 60/700 = 8.571% — NOT 72/900 = 8.0% (old).
        hard, _ = _oracle({}, _sec(), price=None)
        self.assertAlmostEqual(hard["roatce_holdco"], 60 / 700 * 100, places=3)
        self.assertNotAlmostEqual(hard["roatce_holdco"], 72 / 900 * 100, places=3)

    def test_unresolved_preferred_is_none_matching_dashboard(self):
        hard, _ = _oracle({}, _sec(preferred_stock=None), price=None)
        self.assertIsNone(hard["roatce_holdco"])

    def test_no_preferred_falls_back_to_total_ni(self):
        hard, _ = _oracle({}, _sec(preferred_present=False, preferred_stock=0.0,
                                   net_income_to_common_ttm=None), price=None)
        # 72 / (1000 − 0 − 100) = 8.0%
        self.assertAlmostEqual(hard["roatce_holdco"], 72 / 900 * 100, places=3)

    def test_preferred_without_to_common_is_none(self):
        hard, _ = _oracle({}, _sec(net_income_to_common_ttm=None), price=None)
        self.assertIsNone(hard["roatce_holdco"])


class TestWarnTierNeverFailsJob(unittest.TestCase):
    def test_ptbv_is_warn_tier_not_hard(self):
        hard, warn = _oracle({}, _sec(), price=14.0)
        self.assertNotIn("ptbv_ratio", hard)
        self.assertAlmostEqual(warn["ptbv_ratio"], 2.0, places=6)  # 14 / 7

    def test_source_structure_warn_never_in_exit(self):
        src = (Path(__file__).parent.parent / "tools" /
               "verify_metrics.py").read_text(encoding="utf-8")
        # fmp cross-checks + ptbv go through _warn(...), not divergences
        self.assertIn('_warn("fmp:tbvps"', src)
        self.assertIn('_warn("fmp:pe_ratio"', src)
        self.assertIn('_warn("ptbv_ratio"', src)
        self.assertIn('_warn("pe_ratio:xbrl_basis"', src)
        # exit is decided by hard divergences only
        self.assertIn("return 1 if diverged else 0", src)
        self.assertNotIn("warnings else 1", src)
        # FMP tbvps compares TOTAL-basis reconstruction (FMP's convention)
        self.assertIn("tbvps_total", src)


class TestDeclaredZeroDividend(unittest.TestCase):
    """KFFB (2026-07-13 nightly alert): every recent 10-Q explicitly tags
    $0 declared dividends, so the dashboard's 0.0% yield is evidence-backed.
    The oracle's truthiness check (`dps and ...`) collapsed declared-zero
    into None and the hard gate flagged the honest 0.0 as a divergence."""

    def test_declared_zero_dividend_is_zero_not_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=0.0), price=4.0)
        self.assertEqual(hard["dividend_yield"], 0.0)

    def test_missing_dps_is_still_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=None), price=4.0)
        self.assertIsNone(hard["dividend_yield"])

    def test_missing_price_is_still_none(self):
        hard, _ = _oracle({}, _sec(dividends_per_share=0.0), price=None)
        self.assertIsNone(hard["dividend_yield"])


class TestPeOnServedEpsBasis(unittest.TestCase):
    """Every weekday run from ~2026-09-08 failed on pe_ratio for ONB, FRME,
    HBAN, CCBG: the dashboard served the release-anchored composite TTM EPS
    (SEC companyfacts still held only Q1-2026 for them) while the oracle
    priced P/E off the stale XBRL TTM. ONB: price/2.26 = 11.17 vs
    price/1.95 = 12.95. Convention skew must be WARN, and the hard check
    must verify the identity on the basis actually served."""

    def test_composite_basis_moves_xbrl_pe_to_warn_and_checks_identity(self):
        price = 25.25
        hard, warn = _oracle({}, _sec(eps=1.95), price=price)
        self.assertAlmostEqual(hard["pe_ratio"], 25.25 / 1.95, places=9)
        dash = {"eps": 2.26, "eps_source": "release_ttm",
                "pe_ratio": 25.25 / 2.26}
        _split_pe_basis(dash, hard, warn, price)
        self.assertAlmostEqual(hard["pe_ratio"], 25.25 / 2.26, places=9)
        self.assertAlmostEqual(warn["pe_ratio:xbrl_basis"], 25.25 / 1.95,
                               places=9)

    def test_composite_basis_still_catches_wiring_bug(self):
        # The dashboard displays EPS 2.26 but priced P/E off something else.
        hard, warn = _oracle({}, _sec(eps=1.95), price=25.25)
        dash = {"eps": 2.26, "eps_source": "release_ttm", "pe_ratio": 9.0}
        _split_pe_basis(dash, hard, warn, 25.25)
        self.assertNotAlmostEqual(hard["pe_ratio"], dash["pe_ratio"], places=2)

    def test_xbrl_basis_is_untouched(self):
        hard, warn = _oracle({}, _sec(eps=3.53), price=20.0)
        dash = {"eps": 3.53, "eps_source": "reconstructed", "pe_ratio": 20 / 3.53}
        _split_pe_basis(dash, hard, warn, 20.0)
        self.assertAlmostEqual(hard["pe_ratio"], 20 / 3.53, places=9)
        self.assertNotIn("pe_ratio:xbrl_basis", warn)

    def test_composite_with_no_price_is_none(self):
        hard, warn = _oracle({}, _sec(eps=1.95), price=None)
        dash = {"eps": 2.26, "eps_source": "release_ttm", "pe_ratio": None}
        _split_pe_basis(dash, hard, warn, None)
        self.assertIsNone(hard["pe_ratio"])
        self.assertIsNone(warn["pe_ratio:xbrl_basis"])


if __name__ == "__main__":
    unittest.main()
