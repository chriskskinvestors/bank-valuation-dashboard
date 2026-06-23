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
    is_material_regulatory, phrase_in_text,
)


def _pad(s: str) -> str:
    """Space-normalize + pad as match_tickers/_is_subject do before matching."""
    import re
    return " " + re.sub(r"[^A-Za-z0-9]+", " ", s.upper()).strip() + " "


class TestProperNounTrap(unittest.TestCase):
    """A brand core ending in a greedy word ("United") must not match when the
    text continues into a larger proper noun — the FUNC/"First United Arab
    Emirates" mis-tag. Genuine bank mentions still match."""

    def test_swallowed_by_country_rejected(self):
        self.assertFalse(phrase_in_text(
            _pad("Century 21 Opens First United Arab Emirates Office"), "FIRST UNITED"))
        self.assertFalse(phrase_in_text(
            _pad("Acme Opens Its First United States Center"), "FIRST UNITED"))
        self.assertFalse(phrase_in_text(
            _pad("Charity partners with United Kingdom office"), "UNITED"))

    def test_genuine_brand_matches(self):
        self.assertTrue(phrase_in_text(
            _pad("First United Corporation Declares Dividend"), "FIRST UNITED"))
        self.assertTrue(phrase_in_text(
            _pad("First United Bank Reports Q2 Results"), "FIRST UNITED"))

    def test_non_trap_brand_unaffected(self):
        self.assertTrue(phrase_in_text(
            _pad("Eastern Bankshares Declares Dividend"), "EASTERN"))
        self.assertFalse(phrase_in_text(_pad("No mention here"), "EASTERN"))


class TestSEOAndForm144(unittest.TestCase):
    """Auto-generated stock-analysis profile pages (simplywall.st / marketbeat /
    'Risk Zones' trade-signal sites) and Form 144 restricted-stock notices name a
    bank but carry no news — must be filtered (live PFS feed, 2026-06-23)."""

    def test_seo_profile_pages_are_junk(self):
        for h in [
            "Provident Financial Services Inc (PFS) Shareholder Structure: Major Shareholders & Institutional Holdings",
            "Provident Financial Services Inc (PFS) Valuation: PE, PB & Fair Value Analysis",
            "Precision Trading with Provident Financial Services Inc (PFS) Risk Zones",
            "Acme Bancorp Fair Value Analysis and Intrinsic Value Estimate",
        ]:
            with self.subTest(h=h):
                self.assertTrue(is_junk_news(h, "PFS"))

    def test_form_144_is_junk(self):
        self.assertTrue(is_junk_news(
            "Provident Financial Services (PFS) files Form 144 for restricted stock awards", "PFS"))

    def test_real_releases_survive(self):
        # The SEO regex must not catch legitimate releases that mention value/
        # shareholders in a normal way.
        for h in [
            "Provident Bank Appoints Adriano Duarte EVP and Chief Financial Officer",
            "Ameris Bancorp Announces Second Quarter 2026 Results",
            "PNC Declares Quarterly Dividend; Approves Share Repurchase",
            "First Horizon to Hold Annual Meeting of Shareholders on May 21",
        ]:
            with self.subTest(h=h):
                self.assertFalse(is_junk_news(h))


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
    # Compact "N-share stake" form, tagged to the bank's OWN ticker (cross-ticker
    # guard can't catch it) — pulled from the live ABCB feed 2026-06-15.
    ("North Reef Capital reports 3.42M-share stake in Ameris Bancorp (ABCB)", "ABCB"),
    ("Vanguard reports 1,250,000-share position in Zions Bancorporation", "ZION"),
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
    # ── promotional / listicle / SEO-spam (_PROMO_RE) ──
    ("Top 5 Bank Stocks to Buy in 2026", "JPM"),
    ("3 Reasons to Buy Bank of America Now", "BAC"),
    ("7 Best Dividend Stocks for 2026", "WFC"),
    ("5 Cheap Bank Stocks to Watch", "RF"),
    ("Zions Bancorporation Stock: Should Be On Your Radar", "ZION"),
    ("JPMorgan Chase & Co. (JPM) Stock Moves -1.12%: What You Should Know", "JPM"),
    ("Bank Stocks: Midday Movers", "JPM"),
    ("Wells Fargo Hits New 52-Week High", "WFC"),
    # ── structured-product prospectus boilerplate (_PROSPECTUS_RE) ──
    ("[424B2] JPMORGAN CHASE & CO Prospectus Supplement", "JPM"),
    ("Guarantor: JPMorgan Chase & Co.", "JPM"),
    # ── dividend-CALENDAR filler (_DIV_CALENDAR_RE) — NOT a real dividend action ──
    ("Comerica to Trade Ex-Dividend Beginning June 14th", "CMA"),
    ("Ex-Dividend Reminder: Citizens Financial Group", "CFG"),
    # ── off-subject / marketing (_OFFSUBJECT_RE) — bank named only peripherally,
    #    or sponsorship/sports/"study finds" fluff. From the live Home feed
    #    2026-06-17 (owner-flagged): a transparency filing that names BAC as
    #    custodian, a stock offering where GS is just the underwriter, a UFC
    #    sponsorship PR, and a BofA marketing study. ──
    ("Umicore - Transparency notification by Bank of America Corporation", "BAC"),
    ("Macerich Announces Commencement of Public Offering of Common Stock; "
     "BofA Securities and Goldman Sachs Acting as Joint Book-Running Managers", "GS"),
    ("Acme Corp Prices $1.2 Billion Notes Offering; J.P. Morgan Acting as "
     "Sole Book-Running Manager", "JPM"),
    ("Monster Energy's Justin Gaethje Defeats Ilia Topuria to Claim UFC "
     "Lightweight Championship", "GS"),
    ("Bank of America Study Finds Longevity and Accelerating Wealth Transfer "
     "Are Reshaping Family Finances", "BAC"),
    ("Citizens Financial Group Named Exclusive Financial Advisor to Acme on "
     "Its $900 Million Sale", "CFG"),
    # ── EU major-shareholding / transparency notices (_SHAREHOLDER_NOTICE_RE):
    #    plural + non-English forms the _OFFSUBJECT_RE pass missed (live feed) ──
    ("Umicore - Transparency notifications by Bank of America Corporation", "BAC"),
    ("Umicore - Transparantieverklaringen van Bank of America Corporation", "BAC"),
    ("REG - Permanent TSB Group JPMorgan Chase & Co - Holding(s) in Company", "JPM"),
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
    # Real corporate dividend actions must survive the ex-dividend CALENDAR
    # filter (which only targets scheduling chaff, not declarations).
    ("Truist Declares Quarterly Cash Dividend", "TFC"),
    ("Regions Financial Increases Quarterly Dividend", "RF"),
]

# Material regulatory / enforcement events — third-party-voiced (the regulator
# or press writes them, so no company PR verb). They must be recognized as
# material (so the Google/Yahoo first-party gate lets them through) AND must NOT
# be flagged as junk.
KNOWN_REGULATORY = [
    "OCC fines Wells Fargo $250 million over compliance failures",
    "CFPB orders Citizens Financial to pay penalty over overdraft practices",
    "Bank of America charged by SEC over disclosure failures",
    "Zions enters written agreement with the Fed",
    "Federal Reserve issues consent order against Comerica",
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


class TestRegulatoryCoverage(unittest.TestCase):
    def test_material_regulatory_recognized(self):
        for headline in KNOWN_REGULATORY:
            with self.subTest(headline=headline):
                self.assertTrue(
                    is_material_regulatory(headline),
                    f"regulatory event not recognized as material: {headline!r}")

    def test_regulatory_not_flagged_junk(self):
        for headline in KNOWN_REGULATORY:
            with self.subTest(headline=headline):
                self.assertFalse(
                    is_junk_news(headline),
                    f"regulatory event wrongly dropped as junk: {headline!r}")

    def test_ordinary_press_release_is_not_regulatory(self):
        # The regulatory branch must be tight — a routine dividend/earnings
        # release must NOT be misclassified as a regulatory event.
        for headline in ("Truist Declares Quarterly Cash Dividend",
                         "Ameris Bancorp Reports Second Quarter 2026 Results"):
            with self.subTest(headline=headline):
                self.assertFalse(is_material_regulatory(headline))


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
