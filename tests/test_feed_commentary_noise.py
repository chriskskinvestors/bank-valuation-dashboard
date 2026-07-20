"""
Pins the 2026-07-20 feed-noise pass.

On the busiest reporting day of Q2, 42 of the 50 rows in Recent Activity were
Google News rewrites and ~21 belonged to four megabanks — almost none of them
news ABOUT those banks. Morgan Stanley alone held 8 slots with items on Texas
Instruments, Micron, DRAM pricing, European diesel and cybersecurity stocks.
That crowding is what buries a community bank's earnings release in a 50-row
window, which reads as "earnings aren't being picked up".

Three leaks, all pinned here with the VERBATIM live headlines:
  • bank-as-COMMENTATOR (sell-side research where the bank is the speaker)
  • fund/ETF daily administrative updates
  • 13F ownership churn in the "stock holdings" / "stake raised by" forms the
    existing _INSTITUTIONAL_RE clauses walked past

The must-pass list is the point of this suite: every one of these filters runs
at INGEST, so an over-broad pattern silently deletes real earnings news. Real
releases — including megabank ones using the same verbs — must survive.
"""
import sys
import types
import unittest

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.events.wire_base import is_junk_news  # noqa: E402

# Verbatim from the live feed, 2026-07-20 — every one tagged to a bank ticker.
COMMENTARY_JUNK = [
    ("MS", "Morgan Stanley raises Texas Instruments estimates on chip demand"),
    ("MS", "Morgan Stanley Names Top Cybersecurity Stocks"),
    ("MS", "Diesel Squeeze in Europe Set to Deepen, Morgan Stanley Says"),
    ("MS", "Europe's diesel inventories set to hit lowest seasonal level in a decade: Morgan Stanley"),
    ("MS", "Morgan Stanley Raises Q3 DRAM ASP Forecast to 21% Amid Surge in Server Memory Demand"),
    ("MS", "Morgan Stanley Just Turned Bullish On This Payments Processor Ahead Of Q2 Earnings"),
    ("BAC", "Bank of America expects SARB to raise repo rate by 25bps in July meeting"),
    ("BAC", "Bank of America Analysis: Raises DRAM Price Increase Forecast to 21%"),
    ("FITB", "Fifth Third Bancorp (FITB) PT Raised to $65 at BofA Securities"),
]

FUND_ADMIN_JUNK = [
    ("STT", "State Street SPDR S&P/ASX 200 ETF Announces Daily Fund Update for July 2026"),
    ("STT", "State Street Global Advisors Reports Daily Fund Update for SPDR S&P/ASX 200 Listed Property ETF"),
    ("STT", "State Street Global Advisors Reports Daily Fund Update for SPDR S&P/ASX 50 ETF"),
]

OWNERSHIP_JUNK = [
    ("ZION", "Illinois Municipal Retirement Fund Raises Stock Holdings in Zions Bancorporation, N.A."),
    ("WFC", "Dimensional Fund Advisors LP Reduces Stock Holdings in Wells Fargo & Company"),
    ("BAC", "Bank of America Corporation Stake Raised by Sound Shore Management Inc. CT"),
    ("JPM", "JPMorgan Chase & Co.'s Long Position In H-Shares Of Zijin Mining Group Increases On July 15"),
]

# MUST SURVIVE. These are the reason the filters are narrow and source-scoped.
REAL_NEWS = [
    # the actual releases this fix exists to stop burying
    ("MNSB", "MainStreet Bancshares, Inc. Delivers Solid Second Quarter 2026 Performance"),
    ("ISTR", "Investar Holding Corporation Announces 2026 Second Quarter Results"),
    ("CBSH", "Commerce Bancshares, Inc. Reports Second Quarter Earnings Per Share of $1.10"),
    ("INDB", "Independent Bank Corp. Reports Second Quarter Net Income of $81.8 Million"),
    ("FFIN", "FIRST FINANCIAL BANKSHARES ANNOUNCES SECOND QUARTER 2026 EARNINGS"),
    ("SFNC", "Simmons First National Corporation Reports Second Quarter Results"),
    # megabanks using the very verbs the commentary filter keys on
    ("MS", "Morgan Stanley Reports Second Quarter 2026 Results"),
    ("BAC", "Bank of America Announces Quarterly Dividend"),
    ("MS", "Morgan Stanley Raises Quarterly Dividend to $1.00 Per Share"),
    ("STT", "State Street Corporation Reports Second-Quarter 2026 Financial Results"),
    # "Names" — officer changes must not trip the stock-picking clause
    ("AVBH", "Avidbank Names 34-Year Banker to Lead New Small-Business Loan Team"),
    ("USB", "U.S. Bancorp Names New Chief Financial Officer"),
    # real bank-business events
    ("CLST", "Catalyst Bancorp Completes Acquisition Of Lakeside Bancshares"),
    ("LKFN", "Lakeland Financial Corporation Declares Quarterly Cash Dividend"),
    ("BANR", "Banner Bank Releases New Corporate Responsibility Report"),
]


class TestCommentaryNoise(unittest.TestCase):
    def test_sell_side_commentary_is_junk_from_aggregators(self):
        for tk, h in COMMENTARY_JUNK:
            with self.subTest(headline=h):
                self.assertTrue(
                    is_junk_news(h, tk, source="google_news"),
                    f"bank-as-commentator must be filtered: {h!r}")

    def test_commentary_filter_is_aggregator_scoped(self):
        # A first-party wire is trusted — the house rule. If the bank itself
        # issues it, we do not second-guess the subject.
        h = "Morgan Stanley Names Top Cybersecurity Stocks"
        self.assertFalse(is_junk_news(h, "MS", source="businesswire"),
                         "first-party wires must stay trusted")

    def test_fund_admin_updates_are_junk_from_any_source(self):
        for tk, h in FUND_ADMIN_JUNK:
            with self.subTest(headline=h):
                self.assertTrue(is_junk_news(h, tk, source="google_news"))
                self.assertTrue(is_junk_news(h, tk, source="prnewswire"),
                                "daily NAV filler is never company news")

    def test_ownership_churn_forms_that_leaked(self):
        for tk, h in OWNERSHIP_JUNK:
            with self.subTest(headline=h):
                self.assertTrue(
                    is_junk_news(h, tk, source="google_news"),
                    f"13F churn must be filtered: {h!r}")


class TestRealNewsSurvives(unittest.TestCase):
    """The safety net. These filters delete at INGEST — a false positive is a
    lost earnings release, which is strictly worse than the noise."""

    def test_real_news_passes_every_source(self):
        for src in ("google_news", "businesswire", "prnewswire",
                    "globenewswire", "fmp_news"):
            for tk, h in REAL_NEWS:
                with self.subTest(source=src, headline=h):
                    self.assertFalse(
                        is_junk_news(h, tk, source=src),
                        f"REAL NEWS was filtered as junk [{src}]: {h!r}")


if __name__ == "__main__":
    unittest.main()
