"""(2026-10-06) Company share basis for the reconstructed TBVPS — the GS shape.

GS divides common equity by "Basic shares" (shares outstanding + RSUs with no
future service requirement): 2Q26 release BVPS $367.67 = common equity
$109,714M ÷ 298.4M, while period-end shares outstanding are 291,442,355.
Release-first served that BVPS beside our reconstructed TBVPS $344.64 on
291.4M — two share conventions on one page. Owner decision 2026-10-06: use
the company's basis. The reconstruction is restated onto it ONLY when the
release proves the gap is the denominator alone (our common equity ÷ the
release's BVPS reproduces the latest share count it prints, within 0.1%).

GS hand values: TCE = 109,714,000,000 − goodwill 7,342,000,000 −
intangibles 1,929,000,000 = 100,443,000,000; basis = 109,714,000,000 ÷ 367.67
= 298,403,459.06; TBVPS = 100,443,000,000 ÷ 298,403,459.06 = 336.6013.

Run: python -m unittest tests.test_company_share_basis
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

import analysis.valuation as val  # noqa: E402
import data.sec_earnings_8k as s8k  # noqa: E402

CE = 109_714_000_000.0
SHARES = 291_442_355
RECON_TBVPS = 100_443_000_000.0 / SHARES          # 344.6410…
RECON_BVPS = CE / SHARES                          # 376.4518…
BASIS = CE / 367.67                               # 298,403,459.06


def _gs_release(basic_cells=("", "298.4", "", "302.0", "", "311.5")):
    """GS 2Q26 'SELECTED DATA AT PERIOD-END' rows as filed (cells copied from
    a2q26gsearningsresults.htm): the BVPS row carries '$' decoration cells,
    the Basic shares row blanks — so the share value sits one parsed column
    right of the BVPS row's latest value."""
    tds = "".join(f"<td>{c}</td>" for c in basic_cells)
    return (
        "<html><body><table>"
        f"<tr><td>Basic shares3</td>{tds}</tr>"
        "<tr><td>Book value per common share</td><td>$</td><td>367.67</td>"
        "<td>$</td><td>361.19</td><td>$</td><td>349.74</td></tr>"
        "</table></body></html>").encode()


class TestExtractShareBasis(unittest.TestCase):
    def test_gs_basic_shares_tie_out(self):
        v, status = s8k.extract_share_basis_status(_gs_release(), CE)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(v, BASIS, places=2)      # 298,403,459.06

    def test_prior_period_counts_never_tie(self):
        """Only last quarter's 302.0 / 311.5 printed → 109,714M ÷ 367.67 =
        298.40M ties neither (1.2% / 4.4% off)."""
        html = _gs_release(("", "", "", "302.0", "", "311.5"))
        self.assertEqual(s8k.extract_share_basis_status(html, CE),
                         (None, "not_disclosed"))

    def test_numerator_mismatch_never_ties(self):
        """Our equity 1% off the release's → not the same numerator → None.
        (Implied 301.39M sits 0.2% from the PRIOR quarter's 302.0 — only the
        row's first printed value may tie, so that must not match.)"""
        self.assertEqual(s8k.extract_share_basis_status(_gs_release(), CE * 1.01),
                         (None, "not_disclosed"))

    def test_raw_printed_count_is_returned_exactly(self):
        """A full (×1) count is more precise than equity ÷ a cents-rounded
        BVPS: EQBK prints 20,567,009 beside BVPS 40.22."""
        html = (b"<html><body><table>"
                b"<tr><td>Book value per common share</td><td>$</td><td>40.22</td></tr>"
                b"<tr><td>Common shares outstanding at period end</td>"
                b"<td>20,567,009</td></tr></table></body></html>")
        v, status = s8k.extract_share_basis_status(html, 40.22 * 20_567_009)
        self.assertEqual((v, status), (20_567_009, "ok"))


def _resolve(basis, shares=SHARES):
    """_resolve_tbvps with the release TBVPS / wire paths disclosing nothing,
    so the reconstruction fallback (and the share-basis step) decides."""
    with patch("data.bank_mapping.get_cik", return_value=886982), \
            patch.object(val, "_earnings_8k_predates", return_value=False), \
            patch.object(val, "_newer_company_source", return_value=None), \
            patch("data.sec_earnings_8k.reported_tbvps_status",
                  return_value=(None, "not_disclosed")), \
            patch.object(val, "_otc_tbvps", return_value=None), \
            patch("data.sec_earnings_8k.reported_share_basis",
                  return_value=basis):
        return val._resolve_tbvps("GS", RECON_TBVPS, RECON_BVPS,
                                  sec_as_of="2026-06-30", shares=shares)


class TestResolveTbvpsOnCompanyBasis(unittest.TestCase):
    def test_gs_restated_onto_basic_shares(self):
        v, src, conflict = _resolve(BASIS)
        self.assertAlmostEqual(v, 336.6013, places=4)
        self.assertEqual(src, "reconstructed_company_shares")
        self.assertFalse(conflict)

    def test_no_basis_keeps_the_reconstruction(self):
        self.assertEqual(_resolve(None)[:2], (RECON_TBVPS, "reconstructed"))

    def test_basis_within_a_tenth_of_a_percent_is_ours(self):
        self.assertEqual(_resolve(SHARES * 1.0005)[:2],
                         (RECON_TBVPS, "reconstructed"))

    def test_without_shares_nothing_is_restated(self):
        self.assertEqual(_resolve(BASIS, shares=None)[:2],
                         (RECON_TBVPS, "reconstructed"))


class TestCardLabel(unittest.TestCase):
    def test_card_says_company_share_basis(self):
        from ui.bank_detail import _ps_label
        self.assertEqual(
            _ps_label({"tbvps_source": "reconstructed_company_shares"},
                      "TBV / Share", "tbvps_source"),
            "TBV / Share (co. share basis)")


if __name__ == "__main__":
    unittest.main()
