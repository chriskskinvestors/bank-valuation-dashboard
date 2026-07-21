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
import unittest.mock

from data.sec_earnings_8k import (
    extract_earnings_figures, _num, _clean_label, _table_rows, _detect_scale,
    extract_reported_tbvps, _match_tbvps_label,
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


class TestReportedTbvpsLabelMatch(unittest.TestCase):
    """The non-GAAP TBVPS label variants match; a plain book-value or per-share
    EPS label does NOT (that's the reconstruction's job / a wrong figure)."""

    def test_label_variants_match(self):
        for lbl in (
            "Tangible book value per share",
            "Tangible book value per common share",
            "Tangible common book value per share",
            "Tangible common equity per share",
            "Tangible book value per share (non-GAAP)",
            "Tangible book value per common share (Non-GAAP)",
        ):
            self.assertTrue(_match_tbvps_label(_clean_label(lbl)), lbl)

    def test_non_tbvps_labels_do_not_match(self):
        for lbl in (
            "Book value per share",            # NOT tangible → reconstruction
            "Book value per common share",
            "Diluted earnings per share",
            "Dividends declared per share",
            "Tangible common equity",          # not per-share
        ):
            self.assertFalse(_match_tbvps_label(_clean_label(lbl)), lbl)


class TestReportedTbvpsExtraction(unittest.TestCase):
    """extract_reported_tbvps — Company-Reported number, gated by the cardinal
    rule (positive per-share, tangible < book, within 15% of the reconstruction).
    Deterministic, no network."""

    def _rel(self, tbvps_cell, bvps_cell="52.10"):
        # A realistic non-GAAP reconciliation snippet: book, then tangible.
        return _html(
            _row("Book value per common share", bvps_cell)
            + _row("Tangible book value per common share", tbvps_cell)
        )

    def test_clean_reported_value_ties_reconstruction(self):
        """FBIZ-style: reported $42.68 ties the reconstruction ($42.68) and is
        < book ($52.10) → taken as reported."""
        html = self._rel("42.68", bvps_cell="52.10")
        v = extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10)
        self.assertAlmostEqual(v, 42.68)

    def test_within_gate_band_accepted(self):
        # 41.00 vs reconstruction 42.68 → |Δ|/recon ≈ 3.9% < 15% → accepted.
        html = self._rel("41.00")
        v = extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10)
        self.assertAlmostEqual(v, 41.00)

    def test_too_high_vs_reconstruction_rejected(self):
        # A preferred-inflated / wrong-column value 60.00 vs recon 42.68 → >15%
        # AND it would also fail tangible<book → None (fallback to reconstruction).
        html = self._rel("60.00")
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10))

    def test_eps_value_in_slot_rejected(self):
        """An EPS-magnitude value (1.63) mis-grabbed into the TBVPS row is >15%
        off the reconstruction → None, never shipped as tangible book."""
        html = self._rel("1.63")
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10))

    def test_tangible_not_less_than_book_rejected(self):
        """Tangible must be < book. A value ≥ bvps is a mis-aligned row → None,
        even when no reconstruction anchor is present."""
        html = self._rel("55.00", bvps_cell="52.10")
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=None, bvps=52.10))

    def test_negative_or_zero_rejected(self):
        html = self._rel("(3.20)")
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10))

    def test_no_anchor_and_no_bvps_anywhere_not_trusted(self):
        """Nothing to tie the raw match to — no reconstruction, no passed bvps,
        and the release itself has NO book-value line → None. The label matched,
        but the cardinal rule forbids an unanchored guess."""
        html = _html(_row("Tangible book value per common share", "42.68"))
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=None, bvps=None))

    def test_in_release_bvps_anchors_when_caller_has_none(self):
        """PNC case: no reconstruction and no passed bvps, but the release
        discloses BOTH book ($143.65) and tangible-book ($109.42) per common
        share → the in-release book value anchors the tangible < book check and
        the reported figure is taken."""
        html = _html(
            _row("Book value per common share", "143.65")
            + _row("Tangible book value per common share (non-GAAP)", "109.42")
        )
        v = extract_reported_tbvps(html, reconstructed=None, bvps=None)
        self.assertAlmostEqual(v, 109.42)

    def test_period_end_footnote_label_matches(self):
        """USB/ABCB style: a '(period end)(a)' footnote suffix must not defeat the
        label match."""
        html = _html(
            _row("Book value per common share", "36.86")
            + _row("Tangible book value per common share (period end)(a)", "29.56")
        )
        v = extract_reported_tbvps(html, reconstructed=25.90, bvps=36.86)
        self.assertAlmostEqual(v, 29.56)

    def test_bvps_only_cross_check_accepts(self):
        """No reconstruction (bank the reconstruction couldn't resolve, e.g.
        unresolvable preferred) but bvps IS disclosed and tangible < book → the
        reported figure is taken. This is the PNC-style win."""
        html = self._rel("48.00", bvps_cell="52.10")
        v = extract_reported_tbvps(html, reconstructed=None, bvps=52.10)
        self.assertAlmostEqual(v, 48.00)

    def test_not_disclosed_returns_none(self):
        """A release with NO tangible-book line → None → caller falls back to the
        reconstruction (no regression)."""
        html = _html(
            _row("Book value per common share", "52.10")
            + _row("Diluted earnings per share", "1.63")
        )
        self.assertIsNone(
            extract_reported_tbvps(html, reconstructed=42.68, bvps=52.10))


class TestResolveTbvpsFallback(unittest.TestCase):
    """analysis.valuation._resolve_tbvps prefers the reported figure, falls back
    to the reconstruction, and never regresses on error."""

    def test_prefers_reported_when_available(self):
        import analysis.valuation as val
        called = {}

        def fake_reported(cik, reconstructed=None, bvps=None):
            called["args"] = (cik, reconstructed, bvps)
            return 42.68

        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=1521951), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps", fake_reported):
            value, source = val._resolve_tbvps("FBIZ", reconstructed=42.68, bvps=52.10)
        self.assertAlmostEqual(value, 42.68)
        self.assertEqual(source, "reported_8k")
        self.assertEqual(called["args"], (1521951, 42.68, 52.10))

    def test_falls_back_to_reconstruction_when_reported_none(self):
        import analysis.valuation as val
        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=1521951), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps", return_value=None):
            value, source = val._resolve_tbvps("ABCB", reconstructed=37.50, bvps=50.0)
        self.assertAlmostEqual(value, 37.50)
        self.assertEqual(source, "reconstructed")

    def test_error_does_not_regress(self):
        import analysis.valuation as val

        def boom(cik, reconstructed=None, bvps=None):
            raise RuntimeError("network")

        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=999), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps", boom):
            value, source = val._resolve_tbvps("XXXX", reconstructed=30.0, bvps=45.0)
        self.assertAlmostEqual(value, 30.0)   # reconstruction stands
        self.assertEqual(source, "reconstructed")

    def test_no_ticker_returns_reconstruction(self):
        import analysis.valuation as val
        value, source = val._resolve_tbvps(None, reconstructed=30.0, bvps=45.0)
        self.assertAlmostEqual(value, 30.0)
        self.assertEqual(source, "reconstructed")


if __name__ == "__main__":
    unittest.main()
