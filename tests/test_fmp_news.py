"""
Pins the FMP press-release adapter (data/events/fmp_news.py), the primary
replacement for the dead Business Wire direct feed.

Invariants:
  • subject confirmation — FMP's symbol index is polluted for short/ambiguous
    tickers (symbols=CMA returns "...CMA Fest" / a mining "CMA Underground",
    neither about Comerica). The adapter requires the bank's NAME in the
    title/body, so those are dropped — no wrong-company mis-tagging.
  • junk discipline — the single is_junk_news filter still drops structured-note
    / ETN-coupon filler FMP carries; is_safe_news_url drops spam links.
  • dedup — the same release collapses across polls (stable external_id).
  • primary source — the emitted url is the BW/PRN/IR link, never an FMP url.
  • no key — returns [] cleanly (dev / unconfigured).

FMP is mocked; no network or key.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.fmp_client as fmp  # noqa: E402
import data.bank_mapping as bank_mapping  # noqa: E402
from data.events.fmp_news import FMPPressReleaseAdapter, _is_subject  # noqa: E402

NOW = datetime.now(timezone.utc)


def _row(title, url="https://www.businesswire.com/news/home/x/en/y/",
         publisher="Business Wire", age_days=1, text=""):
    ts = (NOW - timedelta(days=age_days)).strftime("%Y-%m-%d %H:%M:%S")
    return {"title": title, "publisher": publisher, "url": url,
            "published_at": ts, "text": text or title}


def _poll(ticker, rows):
    """Run the adapter for one ticker with get_press_releases mocked."""
    with patch.object(fmp, "_has_key", return_value=True), \
         patch.object(fmp, "get_press_releases", return_value=rows):
        return FMPPressReleaseAdapter().poll([ticker])


class TestSubjectConfirmation(unittest.TestCase):
    def test_real_release_kept(self):
        evs = _poll("JPM", [_row("JPMorgan Chase Declares Dividend on Preferred Stock")])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "JPM")
        self.assertTrue(evs[0].url.startswith("https://www.businesswire.com/"),
                        "must link to the PRIMARY source, not FMP")

    def test_wrong_company_polluted_symbol_dropped(self):
        # The live CMA failure: FMP tagged Country-Music / mining releases to CMA.
        evs = _poll("CMA", [
            _row("Tractor Supply Celebrates Country Music's Rising Stars at CMA Fest"),
            _row("First Stoping Operations at Perseus Mining's CMA Underground"),
        ])
        self.assertEqual(evs, [], "releases not about the bank must be dropped")

    def test_subject_confirmed_via_body_when_title_is_terse(self):
        # Title alone lacks the legal name, but the body names the issuer.
        evs = _poll("JPM", [_row(
            "Chase Launches New Small-Business Tool",
            text="NEW YORK--Chase, a business of JPMorgan Chase & Co. (NYSE: JPM), today...")])
        self.assertEqual(len(evs), 1)


class TestIsSubjectGuard(unittest.TestCase):
    """The (a) index OR (b) resolved-legal-name confirmation, isolated."""

    def test_kept_via_resolved_name_when_index_misses_bank(self):
        # ABCB/CFBK aren't in the universe-snapshot index, but get_name resolves
        # them — the (b) path must keep their real releases (the live over-drop).
        with patch.object(bank_mapping, "get_name", return_value="Ameris Bancorp"):
            self.assertTrue(_is_subject(
                "ABCB", "Ameris Bancorp Announces First Quarter 2026 Financial Results."))

    def test_wrong_company_dropped_even_with_resolved_name(self):
        # symbols=CMA pollution: name resolves to Comerica, absent from the text.
        with patch.object(bank_mapping, "get_name", return_value="Comerica Incorporated"):
            self.assertFalse(_is_subject(
                "CMA", "Tractor Supply Celebrates Country Music's Rising Stars at CMA Fest."))
            self.assertTrue(_is_subject(
                "CMA", "Comerica Incorporated Reports Second Quarter 2026 Results."))

    def test_unresolvable_name_is_not_confirmed(self):
        # Placeholder name (== ticker): can't confirm → reject, never guess.
        with patch.object(bank_mapping, "get_name", return_value="CMA"):
            self.assertFalse(_is_subject(
                "CMA", "Tractor Supply Celebrates Rising Stars at CMA Fest."))


class TestJunkAndDedup(unittest.TestCase):
    def test_structured_note_filler_dropped(self):
        evs = _poll("JPM", [
            _row("JPMorgan Chase Reports Second Quarter 2026 Results"),
            _row("JPMorgan Chase Issues Buffered Return Enhanced Notes Linked to the S&P 500"),
            # ETN coupon filler (live JPM item) — structured-product noise.
            _row("JPMorgan Chase Financial Company LLC Declares Quarterly Coupon on Alerian MLP Index ETN"),
        ])
        self.assertEqual([e.headline for e in evs],
                         ["JPMorgan Chase Reports Second Quarter 2026 Results"])

    def test_unsafe_url_dropped(self):
        evs = _poll("JPM", [_row("JPMorgan Chase Declares Dividend",
                                  url="https://chat.whatsapp.com/abc")])
        self.assertEqual(evs, [])

    def test_dedup_same_release_within_poll(self):
        evs = _poll("JPM", [
            _row("JPMorgan Chase Declares Dividend on Preferred Stock"),
            _row("JPMorgan Chase Declares Dividend on Preferred Stock",
                 url="https://www.businesswire.com/news/home/other/"),
        ])
        self.assertEqual(len(evs), 1)

    def test_stale_release_dropped_by_cutoff(self):
        evs = _poll("JPM", [_row("JPMorgan Chase Declares Dividend", age_days=60)])
        self.assertEqual(evs, [])


class TestNoKey(unittest.TestCase):
    def test_no_key_returns_empty(self):
        with patch.object(fmp, "_has_key", return_value=False):
            self.assertEqual(FMPPressReleaseAdapter().poll(["JPM"]), [])


if __name__ == "__main__":
    unittest.main()
