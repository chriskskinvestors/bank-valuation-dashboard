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
    extract_reported_tbvps, extract_reported_tbvps_status, _match_tbvps_label,
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

    def test_ocfc_end_of_period_rows(self):
        """OCFC Q2-2026 release (8-K 0001004702-26-000116 EX-99.1) prints
        "… per common share at end of period (4) (6)" — $24.50 / $18.19 on
        98,416,195 common + NVCE shares. Missed before, so the reconstruction
        (cover count omits the 1.812M NVCE shares, ~1.9% high) served."""
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(
            _row("Book value per common share at end of period (6)",
                 "24.50", "28.98", "28.97")
            + _row("Tangible book value per common share at end of period (4) (6)",
                   "18.19", "19.86", "19.79"))
        # Anchors = the post-fix reconstruction (TBVPS 18.53, BVPS 24.95).
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=18.53, bvps=24.95), (18.19, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=24.95, tbvps=18.19), (24.50, "ok"))

    def test_egbn_at_period_end_rows(self):
        """EGBN Q2-2026 release (image pages, text layer) prints "Book value
        per common share at period end $ 37.73" and "Tangible book value per
        common share at period end(1) $ 37.73" (no intangibles)."""
        from data.sec_earnings_8k import (
            _match_bvps_label, extract_reported_bvps_status)
        self.assertTrue(_match_tbvps_label(_clean_label(
            "Tangible book value per common share at period end(1)")))
        self.assertTrue(_match_bvps_label(_clean_label(
            "Book value per common share at period end")))
        html = _html(
            _row("Book value per common share at period end",
                 "$", "37.73", "$", "37.56", "$", "39.03"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=37.73), (37.73, "ok"))

    def test_sweep_label_variants_match(self):
        """2026-09-30 universe sweep: tangible-book rows each release prints,
        verbatim, that were missed. Each was checked against its release's
        own reconciliation to be per-COMMON-share tangible book (see
        TestSweepReleaseRows for the values)."""
        for lbl in (
            "Tangible stockholders' equity (book value) per common share (3)(4)",  # AMTB
            "Common shareholders’ tangible equity per share (1) (2)",              # BANR
            "Tangible common shareholders’ equity (tangible book value) per "
            "share (non-GAAP)",                                                    # BANR
            "Tangible book value per share, period-end (2)",                      # BHB
            "Tangible book value per common share (non-GAAP1)",                   # BHRB
            "Tangible book value per common share – Non-GAAP (b)",                # BNY
            "Tangible book value per common share - non-GAAP",                    # FBP
            "Tangible common equity per total common share outstanding "
            "(non-GAAP)",                                                          # FRBT
            "Common shareholders' equity (tangible), per share",                  # FULT
            "Non-GAAP tangible book value per share",                             # HBCP
            "Tangible common equity (“TCE”) per share (1)",                       # HOPE
            "Tangible common equity per share of common stock (2)",               # IBCP
            "Tangible equity per common share",                                   # MTB
            "TCE per common share (2)",                                           # PCB
            "Tangible book value per share (total tangible stockholders' "
            "equity/shares outstanding)",                                          # PFS
            "Tangible book value per common share at period end – non-GAAP(1)",   # SHBI
            "Tangible book value per common share – non-GAAP (m)/(n)",            # SHBI
            "Tangible common equity book value per share (1)",                    # UVSP
            "Tangible book value per common share, net of tax (3)",               # WAL
        ):
            self.assertTrue(_match_tbvps_label(_clean_label(lbl)), lbl)

    def test_fully_diluted_variants_do_not_match(self):
        """Fully-diluted TBVPS (BWFG, CMTV, OPHC) and per-diluted-share (CCBG)
        divide by a different share count — a different measure."""
        for lbl in (
            "Fully diluted tangible book value per common share(1)(2)",   # BWFG
            "Fully diluted tangible book value per common share (1)",     # CMTV
            "Fully diluted tangible book value per share",                # OPHC
            "Tangible book value per share - diluted",                    # OPHC
            "Tangible Book Value per Diluted Share (non-GAAP)",            # CCBG
        ):
            self.assertFalse(_match_tbvps_label(_clean_label(lbl)), lbl)

    def test_non_tbvps_labels_do_not_match(self):
        for lbl in (
            "Book value per share",            # NOT tangible → reconstruction
            "Book value per common share",
            "Diluted earnings per share",
            "Dividends declared per share",
            "Tangible common equity",          # not per-share
        ):
            self.assertFalse(_match_tbvps_label(_clean_label(lbl)), lbl)


class TestSweepReleaseRows(unittest.TestCase):
    """The newly matched rows, in their release's real row structure, extract
    the figure the release's own reconciliation reproduces. Anchors are the
    SEC reconstruction as of 2026-09-30 (data.sec_client), exactly as
    analysis.valuation passes them."""

    def test_banr_tangible_equity_per_share(self):
        """BANR Q2-2026 (8-K 0000946673-26-000163): tangible common
        shareholders' equity 1,625,163K ÷ 33,984,909 shares = 47.821;
        + goodwill/intangibles 374,100K → 1,999,263K ÷ 33,984,909 = 58.829."""
        html = _html(
            _row("Common shareholders&#8217; equity per share (1)",
                 "58.83", "58.06", "57.08", "53.95")
            + _row("Common shareholders&#8217; tangible equity per share (1) (2)",
                   "47.82", "47.00", "46.09", "43.09"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=47.82, bvps=58.83), (47.82, "ok"))
        from data.sec_earnings_8k import extract_reported_bvps_status
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=58.83, tbvps=47.82), (58.83, "ok"))

    def test_mtb_tangible_equity_per_common_share(self):
        """MTB Q2-2026: 'Tangible equity per common share' $117.41 is total
        tangible COMMON equity ($17,016M, preferred $2,434M deducted) per
        common share; the percent-change cells follow each period pair."""
        html = _html(
            _row("Common shareholders' equity per share", "$", "176.03", "$",
                 "173.82", "$", "166.94")
            + _row("Tangible equity per common share", "117.41", "115.96", "1",
                   "112.48", "4"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=117.71, bvps=176.66), (117.41, "ok"))

    def test_wal_net_of_tax(self):
        """WAL Q2-2026: total tangible common equity, net of tax $6,906M ÷
        109.2M common shares = 63.24."""
        html = _html(
            _row("Book value per common share", "$", "69.11", "", "$", "61.77", "11.9")
            + _row("Tangible book value per common share, net of tax (3)",
                   "63.24", "", "55.87", "13.2"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=63.23, bvps=69.17), (63.24, "ok"))

    def test_bny_dash_non_gaap(self):
        """BNY Q2-2026: TBVPS 'excludes goodwill and intangible assets, net of
        deferred tax liabilities' (footnote b). The SEC reconstruction (30.04)
        omits the DTL add-back: (1,225 + 659)$M ÷ 678.504M shares = 2.78, and
        30.04 + 2.78 = 32.82 ≈ the printed 32.81 (within the 15% band)."""
        html = _html(
            _row("Book value per common share", "$", "58.82", "$", "57.48", "$", "57.36")
            + _row("Tangible book value per common share &#8211; Non-GAAP (b)",
                   "$", "32.81", "$", "31.75", "$", "31.64"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=30.04, bvps=58.82), (32.81, "ok"))

    def test_key_bare_period_end_rows(self):
        """KEY Q2-2026 prints its per-share book values in a 'Per common
        share' block as bare 'Book value at period end' $16.19 / 'Tangible
        book value at period end' $13.62 (TCE $14,597M ÷ 1,072,035K shares =
        13.616). Bare tier: admitted only when tied to the reconstruction."""
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(
            _row("Book value at period end", "16.19", "", "16.13", "", "15.32")
            + _row("Tangible book value at period end", "13.62", "", "13.60",
                   "", "12.83"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=13.63, bvps=16.21), (13.62, "ok"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=16.21, tbvps=13.62), (16.19, "ok"))
        # No reconstruction: a bare row has nothing to tie to → n/a.
        self.assertEqual(extract_reported_tbvps_status(html),
                         (None, "not_disclosed"))

    def test_npb_total_equity_bvps_needs_a_reconstruction(self):
        """NPB Q2-2026 prints 'Book value per share (GAAP)' $17.10 = total
        equity $589,993K INCLUDING $24,979K preferred ÷ 34.49M common shares;
        per-common book is (589,993 − 24,979) ÷ 34,494 = 16.38. NPB has no SEC
        reconstruction (unresolvable preferred), so the only anchor is the
        in-release TBVPS — too weak for a label that doesn't say COMMON when
        the release itself deducts preferred ("Less: preferred stock").
        The TBVPS itself (TCE $563,985K, preferred deducted, ÷ 34,494K =
        16.35) is correct and still extracts."""
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(
            _row("Tangible book value per share", "16.35", "15.74", "14.17")
            + _row("Book value per share (GAAP)", "17.10", "16.50", "17.09")
            + _row("Less: preferred stock", "24,979", "", "24,979"))
        self.assertEqual(extract_reported_bvps_status(html),
                         (None, "not_disclosed"))
        self.assertEqual(extract_reported_tbvps_status(html), (16.35, "ok"))

    def test_no_preferred_equity_bare_label_bvps_still_served(self):
        """The NPB rule needs preferred EQUITY in the release. SFBS Q2-2026
        prints only preferred DIVIDENDS ($31K, subsidiary REIT preferred);
        its 'Book value per share' $36.19 = total common stockholders' equity
        1,978,418K ÷ 54,671,023 shares — common, and still served (as are
        MS $67.80 and RJF $66.11, same shape)."""
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(
            _row("Book value per share", "36.19", "34.99", "33.87")
            + _row("Tangible book value per share (1)", "35.94", "34.74", "33.62")
            + _row("Dividends on preferred stock", "31", "", "31"))
        self.assertEqual(extract_reported_bvps_status(html), (36.19, "ok"))

    def test_tangible_above_book_never_admitted(self):
        """The no-intangibles waiver (tangible == book, USCB) covers cent
        rounding only. BCTF Q2-2026 prints 'Tangible book value per share'
        $12.94 (total stockholders' equity 60,721K ÷ 4,694,010 shares) against
        a reconstruction of $11.56 for BOTH tangible and book: 12% above book
        is a disagreement, not rounding → n/a."""
        html = _html(
            _row("Total stockholders' equity", "60,721", "49,238")
            + _row("Shares outstanding", "4,694,010", "3,554,455")
            + _row("Tangible book value per share", "12.94", "13.85"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=11.56, bvps=11.56), (None, "not_disclosed"))
        # FSBC (no intangibles): $22.14 printed, reconstruction 473,771K ÷
        # 21,402,864 = 22.136 for both → within rounding → served.
        html = _html(_row("Tangible book value per share(1)", "22.14", "21.45"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=22.136, bvps=22.136), (22.14, "ok"))

    def test_gaap_qualifier_bvps_with_reconstruction(self):
        """'(GAAP)' is a presentational suffix: CFG Q2-2026 'Book value per
        common share (GAAP)' $56.95 = common stockholders' equity $24,072M ÷
        422.7M shares (TCE $16,185M ÷ 38.29)."""
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value per common share (GAAP)", "$", "56.95",
                          "$", "56.48", "$", "53.43"))
        self.assertEqual(extract_reported_bvps_status(
            html, reconstructed=56.95, tbvps=38.29), (56.95, "ok"))


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


class TestInternalTieOutAnchor(unittest.TestCase):
    """Release-internal tie-out anchor (the MBIN case, 2Q26 8-K acc
    0001104659-26-087529): reconstruction n/a (preferred present but carrying
    value untagged since 2018) AND no book-value-per-share line anywhere in the
    release — but the reconciliation table prints TBVPS's own inputs, tangible
    common shareholders' equity ($1,834,473K) and ending common shares
    (45,938,075), whose ratio reproduces the printed $39.93. That arithmetic
    tie IS an anchor; without it the figure stays untied → not_disclosed."""

    # The real MBIN reconciliation rows (values as printed, $ in thousands).
    def _mbin(self, tbvps="39.93", te="1,834,473", shares="45,938,075"):
        return _html(
            _row("Tangible common shareholders' equity", te)
            + _row("Ending common shares", shares)
            + _row("Tangible book value per common share (1)", tbvps)
        )

    def test_mbin_tie_out_accepts(self):
        v, status = extract_reported_tbvps_status(
            self._mbin(), reconstructed=None, bvps=None)
        self.assertAlmostEqual(v, 39.93)
        self.assertEqual(status, "ok")

    def test_same_scale_inputs_tie_out(self):
        """Equity in $M and shares in millions (ratio at scale ×1)."""
        html = self._mbin(te="1,834.5", shares="45.938")
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertAlmostEqual(v, 39.93)
        self.assertEqual(status, "ok")

    def test_mismatched_ratio_refuses(self):
        """Inputs that do NOT reproduce the figure (wrong row grabbed into the
        shares slot) are no anchor — the figure stays untied → n/a."""
        html = self._mbin(shares="52,000,000")   # → $35.28, 11.6% off 39.93
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_missing_shares_row_refuses(self):
        html = _html(
            _row("Tangible common shareholders' equity", "1,834,473")
            + _row("Tangible book value per common share", "39.93")
        )
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_non_common_equity_label_does_not_anchor(self):
        """A bare 'tangible shareholders' equity' can include preferred — it must
        never anchor a per-COMMON-share figure, even when the ratio happens to
        land (a no-preferred bank would print the same number under a common
        label)."""
        html = _html(
            _row("Tangible shareholders' equity", "1,834,473")
            + _row("Ending common shares", "45,938,075")
            + _row("Tangible book value per common share", "39.93")
        )
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")

    def test_blank_latest_quarter_input_skipped_to_next_table(self):
        """A summary-table variant with a blank latest-quarter cell must not
        block the anchor: the dense row (wherever it appears) supplies it."""
        html = _html(
            _row("Ending common shares", "", "45,938,075")   # blank latest col
            + _row("Tangible common shareholders' equity", "1,834,473")
            + _row("Ending common shares", "45,938,075")
            + _row("Tangible book value per common share", "39.93")
        )
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertAlmostEqual(v, 39.93)
        self.assertEqual(status, "ok")

    def test_in_release_bvps_still_wins_over_tie_out(self):
        """When the release DOES print book value per common share, the
        tangible<book anchor path is unchanged (PNC shape): a v ≥ bvps is
        rejected even if the reconciliation inputs would tie out."""
        html = _html(
            _row("Book value per common share", "39.00")     # below the 39.93
            + _row("Tangible common shareholders' equity", "1,834,473")
            + _row("Ending common shares", "45,938,075")
            + _row("Tangible book value per common share", "39.93")
        )
        v, status = extract_reported_tbvps_status(
            html, reconstructed=None, bvps=None)
        self.assertIsNone(v)
        self.assertEqual(status, "not_disclosed")


class TestReleaseTbvpsShapes(unittest.TestCase):
    """Review P1-3 (docs/REVIEW-2026-09-24-numbers.md): two releases that state
    TBVPS in shapes the exact per-share label set missed, so the
    reconstruction served instead of the company's figure. Fixtures reproduce
    the real EX-99.1 markup (fonts, superscript footnote runs, colspans).

      JPM 2Q26 (8-K 0001628280-26-048078, a2q26erfexhibit991narrative.htm):
        prose only — "…; tangible book value per share² of $113.35, up 10% YoY".
        TCE 301,314 / 2,658.2M shares = 113.35. Reconstruction 112.69.
      ONB 2Q26 (8-K 0001628280-26-049108, onb_exhibit991er2q26.htm):
        Table 5 "Tangible book value³" and Table 14 "Tangible common book
        value" = 14.32; 5,477,697 / 382,537 = 14.3194. Reconstruction 14.35."""

    _FONT = "<font style=\"font-family:'Times New Roman',serif;font-size:8.95pt\">"
    _SUP = ("<font style=\"font-size:5.81pt;position:relative;top:-3.13pt;"
            "vertical-align:baseline\">")

    def _jpm(self, bullet_tail=""):
        return (
            "<html><body>"
            "<table><tr><td>Return on tangible common equity</td>"
            "<td>29</td><td>%</td><td>23</td><td>%</td></tr></table>"
            "<div><div style=\"margin-top:6pt\"><font>FORTRESS PRINCIPLES</font></div>"
            "<div style=\"padding-left:11.25pt;text-indent:-9pt\">"
            "<font style=\"font-family:'Wingdings',sans-serif\">n</font>"
            f"{self._FONT} &#160;&#160;&#160;&#160;Book value per share of $133.01,"
            " up 9% YoY&#59; tangible book value per share</font>"
            f"{self._SUP}2</font>"
            f"{self._FONT} of $113.35, up 10% YoY</font></div>{bullet_tail}</div>"
            "<div>b. Tangible common equity (&#8220;TCE&#8221;), return on tangible"
            " common equity (&#8220;ROTCE&#8221;) and tangible book value per share"
            " (&#8220;TBVPS&#8221;) are each non-GAAP financial measures. Book value"
            " per share was $133.01, $128.38 and $122.51 at June 30, 2026, March 31,"
            " 2026 and June 30, 2025, respectively.</div>"
            "</body></html>").encode("utf-8")

    def _onb_row(self, label, sup, *vals, dollar=False):
        lab = ("<td colspan=\"3\"><div><font style=\"font-size:9pt\">"
               f"{label}</font>"
               + (f"<font style=\"font-size:5.85pt;top:-3.15pt\">{sup}</font>"
                  if sup else "") + "</div></td>")
        if dollar:
            cells = "".join(f"<td>$</td><td>{v}</td><td></td>" for v in vals)
        else:
            cells = "".join(f"<td colspan=\"2\">{v}</td><td></td>" for v in vals)
        return f"<tr>{lab}{cells}</tr>"

    def _onb_table5(self):
        return ("<table>"
                + self._onb_row("Book value", None, "21.80", "21.40", "21.17",
                                dollar=True)
                + self._onb_row("Stock price", None, "25.90", "22.10", "22.31")
                + self._onb_row("Tangible book value", "3", "14.32", "13.93",
                                "13.71")
                + "</table>")

    def _onb_table14(self):
        return ("<table>"
                + self._onb_row("Tangible shareholders' common equity", None,
                                "5,477,697", "5,380,515", "5,343,083", dollar=True)
                + "<tr><td colspan=\"3\">Tangible Common Book Value:</td></tr>"
                + self._onb_row("Common shares outstanding", None,
                                "382,537", "386,315", "389,662")
                + self._onb_row("Tangible common book value", None,
                                "14.32", "13.93", "13.71", dollar=True)
                + "</table>")

    def test_hand_verified_inputs(self):
        self.assertAlmostEqual(301_314 / 2_658.2, 113.35, places=2)
        self.assertAlmostEqual(5_477_697 / 382_537, 14.32, places=2)

    def test_jpm_prose_statement_extracted(self):
        self.assertEqual(extract_reported_tbvps_status(
            self._jpm(), reconstructed=112.69, bvps=133.01), (113.35, "ok"))

    def test_onb_bare_tangible_book_value_row(self):
        html = (f"<html><body>{self._onb_table5()}{self._onb_table14()}"
                "</body></html>").encode("utf-8")
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=14.35, bvps=21.84), (14.32, "ok"))

    def test_onb_table14_tangible_common_book_value_alone(self):
        html = f"<html><body>{self._onb_table14()}</body></html>".encode("utf-8")
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=14.35, bvps=21.84), (14.32, "ok"))

    def test_explicit_per_share_row_outranks_bare_row(self):
        """A bare 'Tangible book value' $-total row ahead of an explicit
        per-share row must not displace it (no regression for C/HBAN/…)."""
        html = _html(_row("Tangible book value", "5,477")          # $M total
                     + _row("Tangible book value per share", "42.68"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=42.68, bvps=52.10), (42.68, "ok"))

    def test_bare_row_dollar_total_is_not_tbvps(self):
        """The same words labelling a DOLLAR total fail the per-share gates."""
        for cell in ("5,477,697", "5,477"):                  # $K, $M
            html = _html(_row("Tangible book value", cell))
            self.assertEqual(extract_reported_tbvps_status(
                html, reconstructed=14.35, bvps=21.84), (None, "not_disclosed"),
                cell)

    def test_bare_row_growth_percent_not_tbvps(self):
        """A bare 'Tangible book value' % -change row: tangible<book alone
        must not admit it (no reconstruction), and against a reconstruction it
        is a mis-grab, not a release-vs-pipeline conflict."""
        html = _html(_row("Tangible book value", "4.5%"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=None, bvps=21.84), (None, "not_disclosed"))
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=14.35, bvps=21.84), (None, "not_disclosed"))

    def test_prose_non_tbvps_numbers_not_picked(self):
        """Book value per share, ROTCE and a qualified (ex-AOCI) or comparison
        TBVPS clause in the same prose are never taken as TBVPS."""
        html = (b"<html><body><p>Book value per share of $133.01; return on"
                b" tangible common equity of 29%. Excluding AOCI, tangible book"
                b" value per share of $120.00. Compared with tangible book value"
                b" per share of $103.07 a year ago.</p></body></html>")
        self.assertEqual(extract_reported_tbvps_status(
            html, reconstructed=112.69, bvps=133.01), (None, "not_disclosed"))

    def test_prose_two_different_values_is_ambiguous(self):
        """Two clause-start statements with different figures (current vs a
        prior period) → n/a, never a pick."""
        tail = ("<div>A year earlier: tangible book value per share was"
                " $103.07 at June 30, 2025.</div>")
        self.assertEqual(extract_reported_tbvps_status(
            self._jpm(bullet_tail=tail), reconstructed=112.69, bvps=133.01),
            (None, "not_disclosed"))


class TestResolveTbvpsFallback(unittest.TestCase):
    """analysis.valuation._resolve_tbvps returns (value, source, conflict):
    prefers the reported figure, falls back to the reconstruction, and never
    regresses on error. conflict fires ONLY on the extractor's gate_rejected
    status (release vs reconstruction ≥15% apart — pinned in
    tests/test_tbvps_conflict_signal.py); every scenario here is conflict-free.
    The SEC seam is reported_tbvps_status → (value, status)."""

    def test_prefers_reported_when_available(self):
        import analysis.valuation as val
        called = {}

        def fake_reported(cik, reconstructed=None, bvps=None):
            called["args"] = (cik, reconstructed, bvps)
            return 42.68, "ok"

        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=1521951), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps_status", fake_reported):
            value, source, conflict = val._resolve_tbvps("FBIZ", reconstructed=42.68, bvps=52.10)
        self.assertAlmostEqual(value, 42.68)
        self.assertEqual(source, "reported_8k")
        self.assertIs(conflict, False)
        self.assertEqual(called["args"], (1521951, 42.68, 52.10))

    def test_falls_back_to_reconstruction_when_reported_none(self):
        import analysis.valuation as val
        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=1521951), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps_status",
                                 return_value=(None, "not_disclosed")):
            value, source, conflict = val._resolve_tbvps("ABCB", reconstructed=37.50, bvps=50.0)
        self.assertAlmostEqual(value, 37.50)
        self.assertEqual(source, "reconstructed")
        self.assertIs(conflict, False)   # not_disclosed is noise, not a conflict

    def test_error_does_not_regress(self):
        import analysis.valuation as val

        def boom(cik, reconstructed=None, bvps=None):
            raise RuntimeError("network")

        with unittest.mock.patch("data.bank_mapping.get_cik", return_value=999), \
             unittest.mock.patch("data.sec_earnings_8k.reported_tbvps_status", boom):
            value, source, conflict = val._resolve_tbvps("XXXX", reconstructed=30.0, bvps=45.0)
        self.assertAlmostEqual(value, 30.0)   # reconstruction stands
        self.assertEqual(source, "reconstructed")
        self.assertIs(conflict, False)   # an extractor error is not a conflict

    def test_no_ticker_returns_reconstruction(self):
        import analysis.valuation as val
        value, source, conflict = val._resolve_tbvps(None, reconstructed=30.0, bvps=45.0)
        self.assertAlmostEqual(value, 30.0)
        self.assertEqual(source, "reconstructed")
        self.assertIs(conflict, False)


if __name__ == "__main__":
    unittest.main()


class TestReleaseBvpsShapes(unittest.TestCase):
    """Release BVPS shapes the explicit label set missed (review P1-3 follow-up;
    the TBVPS twin is TestReleaseTbvpsShapes). The reconstruction served
    instead of the company's own figure:
      BBT 2Q26 EX-99.1: "Book value per share (end of period)" 30.30 on
        83,816,086 shares (2,539,796 / 83,816,086 = 30.30); reconstruction
        30.105 on issued − treasury (84,364,733).
      ONB 2Q26 Table 5: bare "Book value" 21.80 (8,340,124 / 382,537 = 21.80);
        reconstruction 21.837."""

    def test_end_of_period_qualifier_is_stripped(self):
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value per share (end of period)", "30.30", "", "29.88")
                     + _row("Tangible book value per share (end of period) (non-GAAP)",
                            "23.98", "", "23.48"))
        self.assertEqual(extract_reported_bvps_status(html, reconstructed=30.105),
                         (30.30, "ok"))

    def test_end_of_period_qualifier_also_serves_tbvps(self):
        html = _html(_row("Tangible book value per share (end of period) (non-GAAP)",
                          "23.98", "", "23.48"))
        self.assertEqual(extract_reported_tbvps_status(html, reconstructed=23.35, bvps=None),
                         (23.98, "ok"))

    def test_bare_book_value_row_ties_to_reconstruction(self):
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value", "21.80", "21.40", "21.17")
                     + _row("Tangible book value", "14.32", "", "13.93"))
        self.assertEqual(extract_reported_bvps_status(html, reconstructed=21.837),
                         (21.80, "ok"))

    def test_explicit_row_outranks_bare_row(self):
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value", "21.95")
                     + _row("Book value per common share", "21.80"))
        self.assertEqual(extract_reported_bvps_status(html, reconstructed=21.837),
                         (21.80, "ok"))

    def test_bare_row_dollar_total_is_not_bvps(self):
        from data.sec_earnings_8k import extract_reported_bvps_status
        for total in ("8,340,124", "8,340"):          # $K and $M totals
            html = _html(_row("Book value", total))
            self.assertEqual(extract_reported_bvps_status(html, reconstructed=21.837),
                             (None, "not_disclosed"), total)

    def test_bare_row_needs_the_reconstruction(self):
        # With nothing to tie to, a bare "book value" is never accepted — even
        # though it is above the in-release TBVPS (a growth or ratio row can be).
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value", "21.80") + _row("Tangible book value per share", "14.32"))
        self.assertEqual(extract_reported_bvps_status(html, reconstructed=None),
                         (None, "not_disclosed"))

    def test_bare_row_far_from_reconstruction_is_not_a_conflict(self):
        from data.sec_earnings_8k import extract_reported_bvps_status
        html = _html(_row("Book value", "9.5"))        # e.g. a growth % row
        self.assertEqual(extract_reported_bvps_status(html, reconstructed=21.837),
                         (None, "not_disclosed"))
