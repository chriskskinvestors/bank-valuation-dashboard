"""
Pins the FMP press-release adapter (data/events/fmp_news.py), the primary
replacement for the dead Business Wire direct feed.

Invariants:
  • subject confirmation — FMP's symbol index is polluted for short/ambiguous
    tickers (symbols=CMA -> "...CMA Fest"; symbols=CHCO -> "Kansas City" stories,
    none about the bank). The DETERMINISTIC _is_subject guard (resolved-name
    phrase core OR curated brand alias) drops those — no wrong-company mis-tag —
    while keeping real releases that use the brand form ("Eastern Bank" for legal
    "Eastern Bankshares", "UMB" for "UMB Financial").
  • junk discipline — the single is_junk_news filter drops structured-note /
    ETN-coupon filler; is_safe_news_url drops spam links.
  • dedup — the same release collapses across polls (stable external_id).
  • primary source — the emitted url is the BW/PRN/IR link, never an FMP url.
  • no key — returns [] cleanly (dev / unconfigured).

FMP / get_name are mocked; no network or key.
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
from data.events.fmp_news import (  # noqa: E402
    FMPPressReleaseAdapter, _is_subject, _subject_phrase,
)

NOW = datetime.now(timezone.utc)


def _row(title, url="https://www.businesswire.com/news/home/x/en/y/",
         publisher="Business Wire", age_days=1, text=""):
    ts = (NOW - timedelta(days=age_days)).strftime("%Y-%m-%d %H:%M:%S")
    return {"title": title, "publisher": publisher, "url": url,
            "published_at": ts, "text": text or title}


def _poll(ticker, rows):
    """Run the adapter for one ticker with the batched FMP call mocked. FMP
    tags each row with a `symbol`; the batch endpoint returns it, so inject
    `ticker` as the symbol on each fixture row."""
    tagged = [{**r, "symbol": ticker} for r in rows]
    with patch.object(fmp, "_has_key", return_value=True), \
         patch.object(fmp, "get_press_releases_multi", return_value=tagged):
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
    """The deterministic resolved-name-phrase OR brand-alias confirmation."""

    def test_kept_via_resolved_name(self):
        with patch.object(bank_mapping, "get_name", return_value="Ameris Bancorp"):
            self.assertTrue(_is_subject(
                "ABCB", "Ameris Bancorp Announces First Quarter 2026 Financial Results."))

    def test_wrong_company_dropped_even_with_resolved_name(self):
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

    def test_phrase_strips_state_suffix(self):
        # SEC "/MS/" state suffix must not leak into the phrase (a recall bug).
        self.assertNotIn("MS", _subject_phrase("PEOPLES FINANCIAL CORP /MS/").split())
        with patch.object(bank_mapping, "get_name",
                          return_value="PEOPLES FINANCIAL CORP /MS/"):
            self.assertTrue(_is_subject(
                "PFBX", "Peoples Financial Corporation Announces a Cash Dividend."))

    def test_ampersand_canonicalized_to_and(self):
        # Name uses "&"; the wire headline uses "AND" — must still match.
        self.assertEqual(_subject_phrase("Farmers & Merchants Bancshares, Inc."),
                         "FARMERS AND MERCHANTS")
        with patch.object(bank_mapping, "get_name",
                          return_value="Farmers & Merchants Bancshares, Inc."):
            self.assertTrue(_is_subject(
                "FMFG", "FARMERS AND MERCHANTS BANCSHARES, INC. Increases Its Dividend."))

    def test_brand_core_matches_short_entity_variant(self):
        # Legal name vs the brand used in headlines differ only in the trailing
        # entity word; the core must match both (the big recall recovery).
        for legal, ticker, headline in [
            ("Eastern Bankshares, Inc.", "EBC", "Eastern Bank Announces Leadership Appointment."),
            ("UMB FINANCIAL CORP", "UMBF", "UMB Institutional Custody Business Grows to $250B."),
            ("FNB Corp.", "FNB", "FNB Invests in Future Talent and Welcomes Interns."),
            ("FIRST CITIZENS BANCSHARES INC /DE/", "FCNCA", "First Citizens Bank to Expand Commercial Solutions."),
            ("Independent Bank Corp. (MI)", "IBCP", "Independent Bank Corporation Announces Quarterly Dividend."),
        ]:
            with self.subTest(ticker=ticker):
                with patch.object(bank_mapping, "get_name", return_value=legal):
                    self.assertTrue(_is_subject(ticker, headline),
                                    f"{ticker}: {_subject_phrase(legal, ticker)!r} should match")

    def test_common_word_name_requires_full_phrase(self):
        # FMP-live regression: a bank whose core is a common English word
        # ("Freedom Holding"->FREEDOM, "Popular Inc"->POPULAR) must NOT match an
        # unrelated PR that merely contains the word — keep the full name.
        for legal, ticker, junk, real in [
            ("Freedom Holding Corp", "FRHC",
             "Ridgeline Roofing Acquires Freedom Roofing & Construction.",
             "Freedom Holding Corp Reports Second Quarter 2026 Results."),
            ("Popular, Inc.", "BPOP",
             "Joy Organics Enhances Popular CBD Salve with Added Arnica.",
             "Popular, Inc. Declares Quarterly Cash Dividend."),
            ("Citizens, Inc.", "CIA",
             "APEX Capital Survey: A Majority of US Citizens Want Lower Fees.",
             "Citizens, Inc. Reports First Quarter 2026 Financial Results."),
        ]:
            with self.subTest(ticker=ticker):
                with patch.object(bank_mapping, "get_name", return_value=legal):
                    self.assertFalse(_is_subject(ticker, junk), f"{ticker}: junk must drop")
                    self.assertTrue(_is_subject(ticker, real), f"{ticker}: real must keep")

    def test_meridian_requires_suffix_ticker_or_alias(self):
        # Live 2026-07-09 mis-tag: get_name display-normalizes "Meridian
        # Corporation" to the bare common word "Meridian" (format_bank_name
        # strips the suffix), which matched Centene's "MERIDIAN HEALTH PLAN
        # OF ILLINOIS" Medicaid PR. A single-common-word phrase now needs a
        # corporate suffix right after it or the issuer's exchange ticker;
        # the "Meridian Bank" subsidiary-brand alias keeps recall.
        with patch.object(bank_mapping, "get_name", return_value="Meridian"):
            self.assertFalse(_is_subject(
                "MRBK", "CENTENE SUBSIDIARY MERIDIAN HEALTH PLAN OF ILLINOIS "
                        "AWARDED ILLINOIS MEDICAID CONTRACT."))
            self.assertTrue(_is_subject(
                "MRBK", "Meridian Corporation Reports Second Quarter 2026 Results."))
            self.assertTrue(_is_subject(
                "MRBK", "Meridian Bank Announces New Chief Lending Officer."))
            self.assertTrue(_is_subject(
                "MRBK", "Meridian Declares Quarterly Dividend. Meridian "
                        "(NASDAQ: MRBK) today announced a cash dividend."))
        # Were the resolver ever to serve the full legal name, the phrase must
        # NOT collapse back to the bare common word.
        self.assertEqual(_subject_phrase("Meridian Corporation", "MRBK"),
                         "MERIDIAN CORPORATION")

    def test_meridian_poll_end_to_end(self):
        # Unmocked get_name — reads the updated bank_map_resolved.json entry.
        evs = _poll("MRBK", [
            _row("CENTENE SUBSIDIARY MERIDIAN HEALTH PLAN OF ILLINOIS "
                 "AWARDED ILLINOIS MEDICAID CONTRACT"),
            _row("Meridian Corporation Reports Second Quarter 2026 Results"),
        ])
        self.assertEqual([e.headline for e in evs],
                         ["Meridian Corporation Reports Second Quarter 2026 Results"])

    def test_short_core_kept_only_when_ticker_related(self):
        # "UMB" (ticker UMBF) is a trustworthy short core; "CITY" (vs CHCO) is not.
        self.assertEqual(_subject_phrase("UMB FINANCIAL CORP", "UMBF"), "UMB")
        self.assertEqual(_subject_phrase("CITY HOLDING CO", "CHCO"), "CITY HOLDING")

    def test_common_word_name_not_overstripped(self):
        # "CITY HOLDING CO" must stay "CITY HOLDING" — never collapse to "CITY",
        # which FMP mis-tags onto "Kansas City"/"Québec City" stories.
        with patch.object(bank_mapping, "get_name", return_value="CITY HOLDING CO"):
            self.assertFalse(_is_subject(
                "CHCO", "Kansas City Current, BofA Announce Multi-Year Partnership."))
            self.assertFalse(_is_subject(
                "CHCO", "Edible Garden Advances Development of Prairie Hills Facility."))
            self.assertTrue(_is_subject(
                "CHCO", "City Holding Company Reports First Quarter 2026 Results."))

    def test_brand_not_swallowed_by_proper_noun(self):
        # FMP-live regression: "First United" (FUNC) was tagged onto a Century 21
        # PR because the brand core "FIRST UNITED" is a substring of the country
        # in "...first United Arab Emirates office". The trap must drop it while
        # keeping the bank's own releases ("First United <entity word>").
        with patch.object(bank_mapping, "get_name", return_value="First United"):
            self.assertFalse(_is_subject(
                "FUNC",
                "Century 21 Brand Expands Global Footprint With Opening of First "
                "United Arab Emirates Office."))
            self.assertFalse(_is_subject(
                "FUNC", "Acme Opens Its First United States Distribution Center."))
            self.assertTrue(_is_subject(
                "FUNC", "First United Corporation Declares Quarterly Cash Dividend."))
            self.assertTrue(_is_subject(
                "FUNC", "First United Bank Reports Second Quarter 2026 Results."))


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
