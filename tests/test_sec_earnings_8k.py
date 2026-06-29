"""Earnings-8K (EX-99.1) headline extractor — data/sec_earnings_8k.

Pins the cardinal-rule behavior deterministically (no network) on synthetic
press-release HTML:
  • the scale (thousands vs millions) is detected from the prior-10Q anchor and
    applied to every dollar figure;
  • a segment subtotal grabbed by a naive first-match is REJECTED by the
    balance-sheet anchor band (the KEY case — $37B Consumer-Bank "Total assets"
    must never surface as the $189B consolidated total);
  • a bare "Diluted" row (the diluted SHARE COUNT) is excluded from EPS;
  • ratios out of band and unmatched labels render n/a, never a guess.

Run: python -m unittest tests.test_sec_earnings_8k
"""
import unittest

from data.sec_earnings_8k import (
    extract_earnings_figures, _num, _clean_label, _table_rows, _detect_scale,
)


def _html(rows_html: str) -> bytes:
    return (f"<html><body><table>{rows_html}</table></body></html>").encode("utf-8")


def _row(label, *cells):
    tds = "".join(f"<td>{c}</td>" for c in (label, *cells))
    return f"<tr>{tds}</tr>"


class TestCellParsing(unittest.TestCase):
    def test_num_handles_parens_dollar_percent_commas(self):
        self.assertEqual(_num("1,234"), 1234.0)
        self.assertEqual(_num("(105,536)"), -105536.0)
        self.assertEqual(_num("$1.63"), 1.63)
        self.assertEqual(_num("3.88%"), 3.88)
        self.assertIsNone(_num("Net income"))
        self.assertIsNone(_num(""))

    def test_clean_label_strips_footnotes(self):
        self.assertEqual(_clean_label("Net interest margin (TE)"), "net interest margin (te)")
        self.assertEqual(_clean_label("Return on average assets *"), "return on average assets")
        self.assertEqual(_clean_label("Diluted shares8"), "diluted shares")


class TestScaleDetection(unittest.TestCase):
    """The dollar scale is found by matching the release's total-assets/deposits
    cell to the prior-10Q tagged value (raw dollars)."""

    def test_thousands_release(self):
        rows = _table_rows(_html(_row("Total assets", "28,109,935")))
        scale, fld = _detect_scale(rows, {"total_assets": 28_109_935_000.0})
        self.assertEqual(scale, 1e3)
        self.assertEqual(fld, "total_assets")

    def test_millions_release(self):
        rows = _table_rows(_html(_row("Total assets", "122,766")))
        scale, _ = _detect_scale(rows, {"total_assets": 122_766_000_000.0})
        self.assertEqual(scale, 1e6)

    def test_no_anchor_no_scale(self):
        rows = _table_rows(_html(_row("Total assets", "28,109,935")))
        self.assertEqual(_detect_scale(rows, {}), (None, None))


class TestExtraction(unittest.TestCase):
    def test_clean_thousands_release(self):
        html = _html(
            _row("Total assets", "28,109,935")
            + _row("Total deposits", "22,636,740")
            + _row("Net income", "110,492")
            + _row("Net interest income", "244,436")
            + _row("Diluted earnings per share", "1.63")
            + _row("Net interest margin (TE)", "3.88")
            + _row("Return on average assets", "1.62")
            + _row("Return on average common equity", "10.91")
        )
        anchor = {"total_assets": 28_109_935_000.0, "total_deposits": 22_636_740_000.0}
        out = extract_earnings_figures(html, anchor)
        self.assertAlmostEqual(out["total_assets"], 28_109_935_000.0)
        self.assertAlmostEqual(out["total_deposits"], 22_636_740_000.0)
        self.assertAlmostEqual(out["net_income"], 110_492_000.0)
        self.assertAlmostEqual(out["net_interest_income"], 244_436_000.0)
        self.assertAlmostEqual(out["diluted_eps"], 1.63)
        self.assertAlmostEqual(out["nim"], 3.88)
        self.assertAlmostEqual(out["roaa"], 1.62)
        self.assertAlmostEqual(out["roae"], 10.91)

    def test_segment_subtotal_rejected(self):
        """CARDINAL RULE: a segment 'Total assets' ($37B Consumer Bank) appearing
        BEFORE the consolidated total must be rejected by the anchor band, not
        shipped as the company's total. (KEY 1Q26.)"""
        html = _html(
            _row("Total assets", "37,341")           # segment, millions → $37.3B
            + _row("Total deposits", "147,815")      # consolidated, millions
        )
        anchor = {"total_assets": 188_663_000_000.0, "total_deposits": 147_815_000_000.0}
        out = extract_earnings_figures(html, anchor)
        # $37.3B is < 70% of the $188.7B anchor at every scale → n/a, never wrong.
        self.assertIsNone(out["total_assets"])
        # The real consolidated deposits anchors cleanly and is kept.
        self.assertAlmostEqual(out["total_deposits"], 147_815_000_000.0)

    def test_bare_diluted_is_not_eps(self):
        """A bare 'Diluted' row is the diluted SHARE COUNT, not EPS — excluded."""
        html = _html(
            _row("Total assets", "73,002,651")       # thousands anchor
            + _row("Diluted", "388,054")             # share count, NOT eps
        )
        anchor = {"total_assets": 73_002_651_000.0}
        out = extract_earnings_figures(html, anchor)
        self.assertIsNone(out["diluted_eps"])

    def test_unmatched_and_out_of_band_are_na(self):
        html = _html(
            _row("Total assets", "15,446,476")       # thousands anchor
            + _row("Net interest margin", "385.0")   # absurd ratio → out of band
        )
        anchor = {"total_assets": 15_446_476_000.0}
        out = extract_earnings_figures(html, anchor)
        self.assertIsNone(out["nim"])                # out of 0..60 band
        self.assertIsNone(out["net_income"])         # label absent
        self.assertIsNone(out["roae"])               # label absent

    def test_no_scale_drops_all_dollars(self):
        """If neither balance-sheet anchor resolves a scale, NO dollar figure is
        trustworthy (could be thousands or millions) → all dollars n/a; ratios
        (scale-free) still extract."""
        html = _html(
            _row("Net income", "110,492")
            + _row("Net interest income", "244,436")
            + _row("Return on average assets", "1.62")
        )
        out = extract_earnings_figures(html, {})     # no anchor → no scale
        self.assertIsNone(out["net_income"])
        self.assertIsNone(out["net_interest_income"])
        self.assertAlmostEqual(out["roaa"], 1.62)


if __name__ == "__main__":
    unittest.main()
