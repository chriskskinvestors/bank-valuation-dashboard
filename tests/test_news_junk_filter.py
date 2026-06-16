"""
Pins data/events/wire_base.is_junk_news — the single junk filter for the
news/events feed (ingest AND display).

Built from a 2026-06-15 live-feed audit (dashboard.kskinvestor.com) where the
Home Alert Inbox and per-bank Press Releases were crowded with junk that the
filter let through. The two invariants this suite enforces:

  1. KNOWN JUNK is dropped — 13F institutional-ownership churn ("X Acquires N
     Shares of Y", "Has $N Position in Z", "New Stake in W"), Form-4 insider
     tax-withholding trivia, broker price-target commentary, content-farm
     editorializing, and headlines tagged with ANOTHER company's ticker (both
     "(NYSE:XXX)" and bare "$XXX").
  2. KNOWN-GOOD press releases ALWAYS pass — dividends, M&A, earnings, buybacks,
     officer changes, notes offerings. The filter must never silently drop
     legitimate news; risky verbs ("Boosts Dividend", "Takes Stake in a
     fintech", "Raises Guidance") stay in the feed.

No network or DB — pure function tests over the regex filter.
"""
import sys
import types
import unittest

# House pattern: stub streamlit before importing data modules (some import
# chains decorate with st.cache_data at module load).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.events.wire_base import (  # noqa: E402
    is_junk_news, is_company_press_release, is_routine_noise, is_safe_news_url,
)


# (headline, ticker) pairs pulled from the live feed (or close paraphrases of
# the same generator templates) that MUST be filtered as junk.
KNOWN_JUNK = [
    # ── 13F / institutional-ownership SEO spam (the dominant live-feed junk) ──
    ("66,617 Shares in SoFi Technologies, Inc. $SOFI Acquired by Blue Jean Financial LLC", "SOFI"),
    ("10,000 Shares in Kimberly-Clark Corporation $KMB Acquired by Ally Financial Inc.", "ALLY"),
    ("State Street Corp Acquires 394,198 Shares of The Goldman Sachs Group, Inc. $GS", "GS"),
    ("State Street Corp Acquires 124,468 Shares of Target Corporation $TGT", "STT"),
    ("State Street Corp Acquires 1,245,457 Shares of CVS Health Corporation $CVS", "STT"),
    ("Victory Capital Management Inc. Acquires 21,856 Shares of Raymond James Financial, Inc. $RJF", "RJF"),
    ("Vistica Wealth Advisors LLC Acquires Shares of 65,452 Columbia Banking System, Inc. $COLB", "COLB"),
    ("JPMorgan Chase & Co. Has $2.1 Billion Position in Apple Inc.", "JPM"),
    ("Hedge Fund Trims Position in Wells Fargo", "WFC"),
    ("Bank of America Boosts Holdings in Tesla", "BAC"),
    ("Blue Jean Financial LLC Takes Position in SoFi Technologies", "SOFI"),
    ("New Stake in Zions Bancorporation Established by Vanguard", "ZION"),
    ("State Street Sells 50,000 Shares of CVS Health Corporation", "STT"),
    # ── Form-4 / insider tax-withholding & share-vesting trivia ──
    ("CF Bankshares (NASDAQ: CFBK) CEO reports 1,932-share tax withholding event", "CFBK"),
    ("Comerica EVP sells 4,000 shares", "CMA"),
    # ── broker price-target / forecast commentary ──
    ("Morgan Stanley, BMO Capital Increase Jefferies Financial (JEF) Price Targets before Q2 Results", "MS"),
    ("Wedbush Lowers Price Target on Comerica", "CMA"),
    # ── content-farm editorializing ──
    ("Provident Financial Holdings Inc. (PROV) Q1 2026 Earnings: EPS Falls Short of "
     "Estimates Amid Challenging Environment - Earnings Manipulation Risk", "PROV"),
    # ── structured-note issuance (is_routine_noise) ──
    ("JPMorgan Chase Issues Buffered Return Enhanced Notes Linked to the S&P 500", "JPM"),
]

# Wrong-company ticker tags — story is about another company; must be junk when
# the bank's ticker is supplied (and is NOT the tagged one).
KNOWN_WRONG_COMPANY = [
    ("Some headline mentioning Ally that is really about (NASDAQ:KMB)", "ALLY"),
    ("Story tagged $CVS but matched to State Street", "STT"),
]

# Legitimate company press releases — MUST pass (is_junk_news == False). These
# carry the risky verbs the junk patterns key on, so they guard against
# over-filtering.
KNOWN_GOOD = [
    ("ServisFirst Bancshares, Inc. Declares Second Quarter Cash Dividend", "SFBS"),
    ("Truist names Fiserv CEO as next leader of Charlotte bank", "TFC"),
    ("ODNB and National Capital Bancorp Announce Merger of Equals to Create $2.4 Billion Community Bank", "CBNK"),
    ("Ameris Bancorp Reports Second Quarter 2026 Results", "ABCB"),
    ("JPMorgan Chase Declares Dividend on Preferred Stock", "JPM"),
    ("Capital One Completes Acquisition of Discover", "COF"),
    ("First Horizon Announces Share Repurchase Program of $500 Million", "FHN"),
    ("Zions Bancorporation Prices $500 Million Senior Notes Offering", "ZION"),
    ("Huntington Bancshares Completes Acquisition of Veritex", "HBAN"),
    ("PNC Financial Increases Quarterly Dividend to $1.60 Per Share", "PNC"),
    ("Regions Financial Appoints New Chief Risk Officer", "RF"),
    ("KeyCorp to Acquire First Niagara in $4.1 Billion Deal", "KEY"),
    ("Wells Fargo Boosts Community Investment Commitment to $20 Billion", "WFC"),
    ("Fifth Third Boosts Quarterly Dividend by 6%", "FITB"),
    ("Synovus Raises Full-Year Guidance", "SNV"),
    ("Citizens Financial Group Increases Buyback Authorization", "CFG"),
    ("Old National Acquires Bremer Financial in $1.4 Billion Deal", "ONB"),
    # A bank taking an equity stake in a fintech IS material — must NOT be
    # confused with "Takes Position in" 13F-spam.
    ("Webster Financial Takes Stake in Digital Banking Startup", "WBS"),
    ("Comerica Announces New Position of Chief Digital Officer", "CMA"),
]


class TestIsJunkNews(unittest.TestCase):
    def test_known_junk_filtered(self):
        for headline, ticker in KNOWN_JUNK:
            with self.subTest(headline=headline):
                self.assertTrue(
                    is_junk_news(headline, ticker),
                    f"expected JUNK but passed: {headline!r}")

    def test_wrong_company_ticker_filtered(self):
        for headline, ticker in KNOWN_WRONG_COMPANY:
            with self.subTest(headline=headline):
                self.assertTrue(
                    is_junk_news(headline, ticker),
                    f"wrong-company tag should be junk: {headline!r}")

    def test_known_good_passes(self):
        for headline, ticker in KNOWN_GOOD:
            with self.subTest(headline=headline):
                self.assertFalse(
                    is_junk_news(headline, ticker),
                    f"legit press release wrongly dropped: {headline!r}")

    def test_price_targets_plural_caught(self):
        # Regression: the old pattern's trailing \b after "target" missed the
        # plural "Price Targets" form, which is how the live MS/JEF item read.
        self.assertTrue(is_junk_news(
            "Analysts Raise Bank of America Price Targets", "BAC"))

    def test_correct_own_ticker_not_filtered_on_tag_alone(self):
        # A headline that tags THIS bank's own ticker (and is otherwise clean)
        # must NOT be junked just for carrying the cashtag.
        self.assertFalse(is_junk_news(
            "Ameris Bancorp $ABCB Declares Quarterly Dividend", "ABCB"))

    def test_no_ticker_still_catches_phrasing_junk(self):
        # Without a ticker the cross-ticker guard can't run, but the phrasing
        # patterns still fire (used by topic feeds with ticker=None).
        self.assertTrue(is_junk_news(
            "Hedge Fund Boosts Holdings in Some Company"))
        self.assertFalse(is_junk_news("Fed holds rates steady"))


class TestSupportingFilters(unittest.TestCase):
    def test_routine_noise_structured_notes(self):
        self.assertTrue(is_routine_noise(
            "Autocallable Contingent Interest Notes Linked to the Russell 2000"))
        self.assertFalse(is_routine_noise(
            "Bank of America Declares Quarterly Dividend"))

    def test_company_pr_gate_passes_company_voiced_news(self):
        # The first-party gate (Google/Yahoo only) keys on PR verbs. Headlines
        # using its recognized verbs — Announces/Reports/Declares/Completes/
        # Prices/Appoints/Acquire — must pass. (Headlines worded "Boosts" or
        # "Takes Stake" aren't gated by this — they reach the feed via the wires
        # / SEC, which don't apply this gate — so they're not asserted here.)
        company_voiced = [
            "ServisFirst Bancshares, Inc. Declares Second Quarter Cash Dividend",
            "Ameris Bancorp Reports Second Quarter 2026 Results",
            "Capital One Completes Acquisition of Discover",
            "First Horizon Announces Share Repurchase Program of $500 Million",
            "Zions Bancorporation Prices $500 Million Senior Notes Offering",
            "Regions Financial Appoints New Chief Risk Officer",
            "KeyCorp to Acquire First Niagara in $4.1 Billion Deal",
        ]
        for headline in company_voiced:
            with self.subTest(headline=headline):
                self.assertTrue(
                    is_company_press_release(headline),
                    f"first-party gate dropped material news: {headline!r}")

    def test_unsafe_urls_blocked(self):
        self.assertFalse(is_safe_news_url("https://chat.whatsapp.com/abc"))
        self.assertFalse(is_safe_news_url("https://t.me/somechannel"))
        self.assertTrue(is_safe_news_url("https://www.businesswire.com/news/x"))
        self.assertTrue(is_safe_news_url(""))  # no link is fine


if __name__ == "__main__":
    unittest.main()
