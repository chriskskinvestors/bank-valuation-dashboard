"""
(AUDIT-2026-07-27, P3 batch) Render-layer fixes that each put a wrong or
misleading value on screen. Behavioural where the code is reachable without a
Streamlit runtime, structural where the fix lives inside a render function.

Pins:
  1. ticker_company_url returns "" (never None) — an all-None LinkColumn renders
     the literal string "None" in every row (all-private deposit-share market,
     private-only peer set). One fix at the source covers all six call sites.
  2. tbvps_source is not format "flag" — the flag formatter renders ANY non-empty
     value as "⚠️", so the selectable "TBV Src" column showed a false
     data-quality warning on every bank and never showed the provenance string.
  3. Comparable-deals size buckets exclude unknown-asset deals instead of
     defaulting them to 0 (which put every unsourceable deal in "<$500M").
  4. The proxy-bio sink neutralizes "$" — two dollar amounts in one bio render
     as inline LaTeX and swallow the text between them.
  5. The FFIEC call-report PDF path raises a miss sentinel instead of returning
     None into @st.cache_data, so one transient failure can't be cached a day.

Run: python -m unittest tests.test_audit_20260727_p3
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()


class TestLinkColumnBlankCells(unittest.TestCase):
    def test_missing_ticker_yields_empty_string_not_none(self):
        from ui.chrome import ticker_company_url
        for missing in (None, "", "  ", "—", "nan", "NaN"):
            self.assertEqual(ticker_company_url(missing), "",
                             f"{missing!r} must map to '' (an all-None "
                             f"LinkColumn renders the literal 'None')")

    def test_real_ticker_still_deep_links(self):
        from ui.chrome import ticker_company_url
        self.assertEqual(ticker_company_url("JPM"), "/?s=Company&bank=JPM")
        self.assertEqual(ticker_company_url(" jpm "), "/?s=Company&bank=jpm")

    def test_never_returns_none_for_any_input(self):
        from ui.chrome import ticker_company_url
        for v in (None, 0, "", float("nan"), "ABC"):
            self.assertIsNotNone(ticker_company_url(v))


class TestTbvpsSourceNotAFlag(unittest.TestCase):
    def test_provenance_column_is_not_flag_formatted(self):
        from config import METRICS
        m = next(m for m in METRICS if m["key"] == "tbvps_source")
        self.assertNotEqual(
            m.get("format"), "flag",
            'tbvps_source holds provenance STRINGS; "flag" renders any '
            'non-empty value as the warning glyph on every bank')

    def test_provenance_string_renders_as_itself(self):
        from config import METRICS
        from utils.formatting import format_value
        m = next(m for m in METRICS if m["key"] == "tbvps_source")
        for val in ("reported_8k", "reconstructed", "company_release"):
            self.assertEqual(format_value(val, m["format"]), val)

    def test_flag_formatter_still_flags_real_booleans(self):
        """Regression guard: the genuine one-time-earnings flag is untouched."""
        from utils.formatting import format_value
        self.assertEqual(format_value(True, "flag"), "⚠️")
        self.assertEqual(format_value(False, "flag"), "")


class TestCompsSizeBucketsExcludeUnknownAssets(unittest.TestCase):
    """The bucket filter is inline in a render function, so pin it structurally:
    an unknown-size deal must be excluded, never coerced to 0 (which satisfies
    0 <= 0 < 5e8 and lands it in the smallest bucket)."""

    def test_bucket_filter_has_no_or_zero_default(self):
        src = (REPO / "ui/transactions.py").read_text(encoding="utf-8")
        i = src.index("_SIZE_BUCKETS:")
        window = src[i:i + 900]
        self.assertNotIn('or 0) < hi', window,
                         "unknown assets must be excluded, not defaulted to 0")
        self.assertIn('d.get("comp_assets") or d.get("target_assets"))',
                      window,
                      "bucket filter must require a resolvable asset figure")

    def test_bucket_math_matches_the_intended_predicate(self):
        """The predicate itself, exercised directly on representative deals."""
        buckets = [("<$500M", 0, 5e8), ("$500M-$2B", 5e8, 2e9)]
        deals = [
            {"comp_assets": 3e8},                       # small, known
            {"comp_assets": None, "target_assets": 1e9},  # mid, via fallback
            {"comp_assets": None, "target_assets": None},  # unknown -> excluded
        ]
        placed = {}
        for label, lo, hi in buckets:
            placed[label] = [
                d for d in deals
                if (d.get("comp_assets") or d.get("target_assets"))
                and lo <= (d.get("comp_assets") or d.get("target_assets")) < hi]
        self.assertEqual(len(placed["<$500M"]), 1)
        self.assertEqual(len(placed["$500M-$2B"]), 1)
        self.assertEqual(sum(len(v) for v in placed.values()), 2,
                         "the unknown-asset deal must appear in NO bucket")


class TestProxyBioDollarEscaping(unittest.TestCase):
    def test_bio_sink_neutralizes_dollar_signs(self):
        src = (REPO / "ui/people_summary.py").read_text(encoding="utf-8")
        i = src.index("One-line bios")
        window = src[i:i + 700]
        self.assertIn('replace("$"', window,
                      "two $ amounts in one bio render as inline LaTeX")

    def test_escape_shape_matches_the_house_pattern(self):
        """A bio with two amounts must come out with both $ escaped."""
        bio = "oversaw $500 million and $2 billion portfolios"
        self.assertEqual(bio.replace("$", "\\$"),
                         "oversaw \\$500 million and \\$2 billion portfolios")


class TestCallReportMissNeverCached(unittest.TestCase):
    def test_sentinel_exists_and_wraps_the_cached_reader(self):
        import ui.recent_documents as rd
        self.assertTrue(hasattr(rd, "_CallReportUnavailable"))
        self.assertTrue(issubclass(rd._CallReportUnavailable, Exception))
        self.assertTrue(hasattr(rd, "_pdf_from_call_report_cached"))

    def test_cached_reader_raises_instead_of_returning_none(self):
        src = (REPO / "ui/recent_documents.py").read_text(encoding="utf-8")
        i = src.index("def _pdf_from_call_report_cached")
        body = src[i:i + 900]
        self.assertIn("raise _CallReportUnavailable()", body)
        # …and the public wrapper converts it back to None for callers.
        j = src.index("def _pdf_from_call_report(")
        self.assertIn("except _CallReportUnavailable", src[j:j + 400])

    def test_miss_surfaces_as_none_to_callers(self):
        from unittest.mock import patch
        import ui.recent_documents as rd
        with patch.object(rd, "_pdf_from_call_report_cached",
                          side_effect=rd._CallReportUnavailable()):
            self.assertIsNone(rd._pdf_from_call_report("AAA", "06302026"))

    def test_hit_passes_through(self):
        from unittest.mock import patch
        import ui.recent_documents as rd
        with patch.object(rd, "_pdf_from_call_report_cached",
                          return_value=b"%PDF-1.4"):
            self.assertEqual(rd._pdf_from_call_report("AAA", "06302026"),
                             b"%PDF-1.4")


if __name__ == "__main__":
    unittest.main()
