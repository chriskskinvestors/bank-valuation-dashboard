"""tools/valuation_coverage.py — each bank lands under the FIRST failed
precondition, in the engine's order; present values are "ok"."""
import io
import unittest
from contextlib import redirect_stdout

from tools import valuation_coverage as vc


def _row(**k):
    base = {"ticker": "X", "price": 20.0, "tbvps": 10.0, "ptbv_ratio": 2.0,
            "eps": 1.0, "pe_ratio": 20.0, "dividend_yield": 2.0}
    base.update(k)
    return base


class TestClassify(unittest.TestCase):
    def test_present_is_ok(self):
        self.assertEqual(vc.classify(_row(), 1, {}), {"ptbv": "ok", "pe": "ok", "divyield": "ok"})

    def test_no_price_blanks_everything(self):
        c = vc.classify(_row(price=None, ptbv_ratio=None, pe_ratio=None, dividend_yield=None), 1, {})
        self.assertTrue(all(v.startswith("no price") for v in c.values()))

    def test_non_sec_filer(self):
        c = vc.classify(_row(tbvps=None, ptbv_ratio=None, eps=None, pe_ratio=None,
                             dividend_yield=None), None, None)
        self.assertEqual(c["ptbv"], "non-SEC filer and no TBV in its wire release")
        self.assertEqual(c["pe"], "non-SEC filer (no XBRL EPS)")
        self.assertEqual(c["divyield"], "non-SEC filer (no XBRL dividends)")

    def test_sec_preconditions_in_order(self):
        r = _row(tbvps=None, ptbv_ratio=None)
        self.assertEqual(vc.classify(r, 1, None)["ptbv"], "SEC filer but companyfacts empty/unavailable")
        self.assertEqual(vc.classify(r, 1, {"preferred_present": True, "preferred_stock": None})["ptbv"],
                         "preferred outstanding, value unresolved (n/a by rule)")
        self.assertEqual(vc.classify(r, 1, {"shares_outstanding": None, "book_value_total": 1.0})["ptbv"],
                         "share count unresolved (share/equity incoherent)")
        self.assertEqual(vc.classify(r, 1, {"shares_outstanding": 5.0, "book_value_total": None})["ptbv"],
                         "equity total not tagged (or NCI unseparated)")

    def test_deliberate_na_read_from_the_rows_note(self):
        """BAFN (2026-10-12): TBV blanked by the share-basis mismatch rule;
        CFR: by the lapsed goodwill tag. The row's _notes says which — the
        audit must report that, never guess 'intangibles untagged'."""
        sec = {"preferred_present": True, "preferred_stock": 9e7,
               "shares_outstanding": 4_106_905, "book_value_total": 115_901_000}
        bafn = _row(tbvps=None, ptbv_ratio=None, _notes={"tbvps": (
            "share-basis mismatch — the latest cover-page share count differs "
            "85% from the quarter-end count behind book value per share "
            "(post-quarter issuance/conversion), so per-share book and today's "
            "price are on different bases")})
        self.assertEqual(vc.classify(bafn, 1, sec)["ptbv"],
                         "share-basis mismatch — post-quarter issuance/conversion (n/a by rule)")
        cfr = _row(tbvps=None, ptbv_ratio=None, _notes={"tbvps": (
            "tangible book n/a — the holding company stopped tagging goodwill in "
            "its XBRL filings (its bank subsidiary still reports $652.7M of "
            "goodwill), so equity minus intangibles can't be reconstructed")})
        self.assertEqual(vc.classify(cfr, 1, sec)["ptbv"],
                         "holdco goodwill tag lapsed — intangibles unknown (n/a by rule)")
        self.assertEqual(vc.classify(_row(tbvps=None, ptbv_ratio=None), 1, sec)["ptbv"],
                         "TBV n/a — other (no note on the row)")
        self.assertEqual(vc.classify(_row(tbvps=None, ptbv_ratio=None, _notes={}), 1, sec)["ptbv"],
                         "TBV n/a — other (no note on the row)")

    def test_loss_and_negative_tbv(self):
        self.assertEqual(vc.classify(_row(eps=-0.5, pe_ratio=None), 1, {})["pe"], "trailing loss (EPS <= 0)")
        self.assertEqual(vc.classify(_row(tbvps=-1.0, ptbv_ratio=None), 1, {})["ptbv"], "negative tangible book")

    def test_report_counts_and_examples(self):
        rows = [_row(ticker="A"), _row(ticker="B", price=None, ptbv_ratio=None, pe_ratio=None, dividend_yield=None),
                _row(ticker="C", eps=-1, pe_ratio=None)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = vc.run(rows, lambda t: 1, lambda t: {})
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("P/TBV: 2/3 have a value", out)
        self.assertIn("P/E: 1/3 have a value", out)
        self.assertIn("1  trailing loss (EPS <= 0)", out)
        self.assertIn("e.g. C", out)


if __name__ == "__main__":
    unittest.main()
