"""OTC (non-SEC filer) P/E and market cap — owner spec 2026-10-06.

Pins, on cell-faithful copies of four REAL releases (tests/fixtures_otc_releases,
every value hand-read from the published pages on 2026-10-06):

  • data/release_metrics.extract_table_series — the discrete-quarter diluted
    EPS series a five-quarter table prints, period-proven column by column;
    YTD/twin/unproven layouts refused, loss quarters kept with their sign.
  • data/release_shares.extract_shares_outstanding — common shares at the
    quarter-end (table row + balance-sheet caption, common classes summed),
    served ONLY when BV/share × shares ties to the release's own equity (±1%).
  • data/otc_release quarter history — write-once per quarter, conflict on
    disagreement, bounded one-time backfill from prior releases.
  • analysis/release_eps.otc_composite_ttm_eps — four consecutive quarters
    from the bank's own releases or (None, None, None) (audit A21: a single
    quarter is never a TTM).
  • analysis/valuation — eps_source "release_ttm_otc" for a no-CIK bank;
    market cap = price × tied-out release shares, labeled "(co. release)".

FDVA 2Q26 (https://investors.freedom.bank/2026-07-31-…): diluted EPS by
quarter 0.04 | 0.16 | (0.50) | 0.16 | 0.11 (Jun-26 … Jun-25); 6,978,754 voting
+ 0 non-voting shares; total stockholders' equity $85,147,801; TBV/share
$12.20 → TTM thru 2026-06-30 = 0.04 + 0.16 − 0.50 + 0.16 = −0.14.
BKSC 2Q26: 0.42 | 0.36 | 0.36 | 0.38 | 0.35; 5,338,872 shares; BV/share $11.43;
equity $61,371,224 → TTM 1.52.  PTBS 1Q26: 0.73 | 0.57 | 0.56 | 0.50 | 0.53
(split month/year header); 4,144,561 shares; TBV $20.42; equity $84,637K →
TTM 2.36.  TMAK 1Q26: 0.02 | 0.10 | 0.13 | 0.08 | 0.16 → TTM 0.33; its share
caption ("…as of the periods presented") states no dates → shares n/a.

Run: python -m unittest tests.test_otc_eps_mcap
"""
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from tests import fixtures_otc_releases as fx  # noqa: E402
from data.release_metrics import (_prior_quarter_end, _table_rows,  # noqa: E402
                                  extract_release_metrics,
                                  extract_table_series)
from data.release_shares import _caption_shares, extract_shares_outstanding  # noqa: E402


def _tbl(*rows):
    return ("<table>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        + "</table>")


Q5 = ["June 30, 2026", "March 31, 2026", "December 31, 2025",
      "September 30, 2025", "June 30, 2025"]
FDVA_SERIES = {"2026-06-30": 0.04, "2026-03-31": 0.16, "2025-12-31": -0.5,
               "2025-09-30": 0.16, "2025-06-30": 0.11}
BKSC_SERIES = {"2026-06-30": 0.42, "2026-03-31": 0.36, "2025-12-31": 0.36,
               "2025-09-30": 0.38, "2025-06-30": 0.35}
PTBS_SERIES = {"2026-03-31": 0.73, "2025-12-31": 0.57, "2025-09-30": 0.56,
               "2025-06-30": 0.5, "2025-03-31": 0.53}
TMAK_SERIES = {"2026-03-31": 0.02, "2025-12-31": 0.1, "2025-09-30": 0.13,
               "2025-06-30": 0.08, "2025-03-31": 0.16}


class TestSeriesReader(unittest.TestCase):
    def test_fdva_five_quarters_incl_loss_quarter(self):
        self.assertEqual(extract_table_series(fx.FDVA_2Q26), FDVA_SERIES)

    def test_bksc_single_table(self):
        self.assertEqual(extract_table_series(fx.BKSC_2Q26), BKSC_SERIES)

    def test_ptbs_split_month_year_header(self):
        self.assertEqual(extract_table_series(fx.PTBS_1Q26), PTBS_SERIES)

    def test_tmak_dollar_sign_cells(self):
        self.assertEqual(extract_table_series(fx.TMAK_1Q26), TMAK_SERIES)

    def test_ytd_only_table_refused(self):
        h = _tbl(["", "Six Months Ended"], ["", "June 30, 2026", "June 30, 2025"],
                 ["Diluted earnings per share", "$ 0.20", "$ 0.39"])
        self.assertEqual(extract_table_series(h), {})

    def test_quarter_and_ytd_twin_columns_refused(self):
        h = _tbl(["", "Three Months Ended", "", "Six Months Ended", ""],
                 ["", "June 30, 2026", "June 30, 2025", "June 30, 2026",
                  "June 30, 2025"],
                 ["Diluted earnings per share", "$ 0.04", "$ 0.11", "$ 0.20",
                  "$ 0.39"])
        self.assertEqual(extract_table_series(h), {})

    def test_header_without_quarter_proof_refused(self):
        h = _tbl(["Selected Financial Data", *Q5],
                 ["Diluted earnings per share", "$ 0.42", "$ 0.36", "$ 0.36",
                  "$ 0.38", "$ 0.35"])
        self.assertEqual(extract_table_series(h), {})

    def test_bare_year_columns_refused(self):
        # CTUY 1Q26 shape: "Three Months Ended March 31" over "2026 | 2025".
        h = _tbl(["", "Three Months Ended March 31"], ["", "2026", "2025"],
                 ["Diluted", "$ 1.89", "$ 1.88"])
        self.assertEqual(extract_table_series(h), {})

    def test_value_count_mismatch_row_skipped(self):
        h = _tbl(["", "For the Three Months Ended"], ["", *Q5],
                 ["Diluted earnings per share", "$ 0.42", "$ 0.36", "$ 0.36",
                  "$ 0.38"])
        self.assertEqual(extract_table_series(h), {})

    def test_adjusted_row_never_read(self):
        h = _tbl(["", "For the Three Months Ended"], ["", *Q5],
                 ["Adjusted diluted earnings per share", "$ 0.42", "$ 0.36",
                  "$ 0.36", "$ 0.38", "$ 0.35"])
        self.assertEqual(extract_table_series(h), {})

    def test_disagreeing_tables_drop_only_that_quarter(self):
        a = _tbl(["", "For the Three Months Ended"], ["", *Q5],
                 ["Diluted earnings per share", "$ 0.42", "$ 0.36", "$ 0.36",
                  "$ 0.38", "$ 0.35"])
        b = _tbl(["", "For the Three Months Ended"], ["", *Q5],
                 ["Earnings per share - diluted", "$ 0.45", "$ 0.36", "$ 0.36",
                  "$ 0.38", "$ 0.35"])
        got = extract_table_series(a + b)
        self.assertNotIn("2026-06-30", got)
        self.assertEqual(got["2026-03-31"], 0.36)
        self.assertEqual(len(got), 4)

    def test_percent_row_never_read(self):
        h = _tbl(["", "For the Three Months Ended"], ["", *Q5],
                 ["Diluted earnings per share", "0.42 %", "0.36 %", "0.36 %",
                  "0.38 %", "0.35 %"])
        self.assertEqual(extract_table_series(h), {})


class TestSharesOutstanding(unittest.TestCase):
    def _shares(self, html, qend):
        return extract_shares_outstanding(html, qend,
                                          extract_release_metrics(html, qend))

    def test_fdva_row_ties_on_tbv(self):
        got = self._shares(fx.FDVA_2Q26, "2026-06-30")
        self.assertEqual(got["shares"], 6_978_754)
        self.assertEqual(got["tie"]["basis"], "tbv_ps")
        self.assertEqual(got["tie"]["per_share"], 12.2)
        self.assertEqual(got["tie"]["equity"], 85_147_801)
        self.assertLess(got["tie"]["diff_pct"], 0.01)

    def test_fdva_caption_sums_voting_and_nonvoting(self):
        # The balance sheet's equity caption, read on its own: 6,978,754 voting
        # + 0 non-voting; the preferred caption ("0 Shares Issued and
        # Outstanding") is NOT a common class and is skipped.
        tables = re.findall(r"(?is)<table[^>]*>(.*?)</table>", fx.FDVA_2Q26)
        bs = next(t for t in tables if "Voting Common Stock" in t)
        self.assertEqual(_caption_shares(_table_rows(bs), "2026-06-30"),
                         6_978_754)
        self.assertEqual(_caption_shares(_table_rows(bs), "2026-03-31"),
                         6_973_747)
        self.assertIsNone(_caption_shares(_table_rows(bs), "2025-09-30"))

    def test_bksc_row_ties_on_bv(self):
        got = self._shares(fx.BKSC_2Q26, "2026-06-30")
        self.assertEqual(got["shares"], 5_338_872)
        self.assertEqual(got["tie"]["basis"], "bv_ps")
        self.assertEqual(got["tie"]["per_share"], 11.43)
        self.assertEqual(got["tie"]["equity"], 61_371_224)
        # 11.43 × 5,338,872 = 61,023,307 vs 61,371,224: 0.57%, inside ±1%.
        self.assertAlmostEqual(got["tie"]["diff_pct"], 0.567, places=2)

    def test_ptbs_split_header_rows_in_thousands_table(self):
        got = self._shares(fx.PTBS_1Q26, "2026-03-31")
        self.assertEqual(got["shares"], 4_144_561)
        self.assertEqual(got["tie"]["equity"], 84_637_000)   # $84,637K
        self.assertEqual(got["tie"]["per_share"], 20.42)

    def test_tmak_caption_without_dates_is_na(self):
        got = self._shares(fx.TMAK_1Q26, "2026-03-31")
        self.assertIsNone(got["shares"])
        self.assertEqual(got["tie"]["reason"], "no share count found")

    def test_tie_out_failure_refuses(self):
        got = extract_shares_outstanding(fx.BKSC_2Q26, "2026-06-30",
                                         {"bv_ps": 15.00})
        self.assertIsNone(got["shares"])
        self.assertEqual(got["tie"]["reason"], "tie-out failed")
        self.assertEqual(got["tie"]["count"], 5_338_872)

    def test_no_per_share_basis_refuses(self):
        got = extract_shares_outstanding(fx.BKSC_2Q26, "2026-06-30", {})
        self.assertIsNone(got["shares"])
        self.assertEqual(got["tie"]["reason"], "no stated per-share book value")

    def test_preferred_and_treasury_never_summed(self):
        h = _tbl(["", "June 30, 2026", "March 31, 2026"],
                 ["Preferred stock: 1,000,000 shares issued and outstanding at "
                  "June 30, 2026 and March 31, 2026", "10,000", "10,000"],
                 ["Common stock: 5,000,000 and 4,900,000 shares issued and "
                  "outstanding at June 30, 2026 and March 31, 2026",
                  "50,000", "49,000"],
                 ["Treasury stock, 200,000 shares", "(2,000)", "(2,000)"],
                 ["Total stockholders' equity", "$ 60,000,000", "$ 58,000,000"])
        got = extract_shares_outstanding(h, "2026-06-30", {"bv_ps": 12.00})
        self.assertEqual(got["shares"], 5_000_000)        # 12 × 5M = 60M
        self.assertEqual(got["tie"]["source"], "caption")

    def test_treasury_and_weighted_rows_never_match(self):
        h = _tbl(["", "Three Months Ended"], ["", *Q5],
                 ["Weighted average shares outstanding - diluted", "5,100,000",
                  "5,100,000", "5,100,000", "5,100,000", "5,100,000"],
                 ["Treasury shares", "100,000", "100,000", "100,000", "100,000",
                  "100,000"],
                 ["Total stockholders' equity", "$ 60,000,000", "$ 59,000,000",
                  "$ 58,000,000", "$ 57,000,000", "$ 56,000,000"])
        got = extract_shares_outstanding(h, "2026-06-30", {"bv_ps": 12.00})
        self.assertIsNone(got["shares"])
        self.assertEqual(got["tie"]["reason"], "no share count found")

    def test_thousands_table_without_share_exception_refused(self):
        # Shares may be printed in thousands here — never scaled by a guess.
        h = _tbl(["(in thousands, except per share data)", "Three Months Ended"],
                 ["", *Q5],
                 ["Shares outstanding", "5,339", "5,380", "5,400", "5,420", "5,422"],
                 ["Total shareholders' equity", "$ 61,371", "$ 60,909",
                  "$ 60,159", "$ 58,315", "$ 56,244"])
        got = extract_shares_outstanding(h, "2026-06-30", {"bv_ps": 11.43})
        self.assertIsNone(got["shares"])

    def test_row_vs_caption_disagreement_refuses(self):
        h = _tbl(["", "Three Months Ended"], ["", *Q5],
                 ["Shares outstanding at period end", "5,000,000", "5,000,000",
                  "5,000,000", "5,000,000", "5,000,000"],
                 ["Common stock: 5,200,000 shares issued and outstanding at "
                  "June 30, 2026", "52,000", "52,000", "52,000", "52,000",
                  "52,000"],
                 ["Total stockholders' equity", "$ 60,000,000", "$ 59,000,000",
                  "$ 58,000,000", "$ 57,000,000", "$ 56,000,000"])
        got = extract_shares_outstanding(h, "2026-06-30", {"bv_ps": 12.00})
        self.assertIsNone(got["shares"])
        self.assertEqual(got["tie"]["reason"], "row vs caption disagree")


class _StoreBase(unittest.TestCase):
    """In-memory data.cache (max_age_s=None reads, as production)."""

    def setUp(self):
        import data.cache as dc
        import data.otc_release as orl
        self.orl, self.store = orl, {}
        self._orig = (dc.get, dc.put, orl._latest_earnings_pr,
                      orl._fetch_story, orl._latest_ir_release,
                      orl._fetch_document, orl._earnings_prs,
                      orl._ir_release_candidates)
        dc.get = lambda k, max_age_s=None: self.store.get(k)
        dc.put = lambda k, v: self.store.__setitem__(k, v)
        orl._latest_ir_release = lambda t: None

    def tearDown(self):
        import data.cache as dc
        (dc.get, dc.put, self.orl._latest_earnings_pr, self.orl._fetch_story,
         self.orl._latest_ir_release, self.orl._fetch_document,
         self.orl._earnings_prs, self.orl._ir_release_candidates) = self._orig


class TestQuarterHistory(_StoreBase):
    def test_write_once_then_conflict_on_disagreement(self):
        orl = self.orl
        orl._append_eps_history("T", url="u1", qend="2026-03-31", eps=0.16,
                                series={})
        q = orl.get_eps_history("T")["quarters"]["2026-03-31"]
        self.assertEqual((q["eps_diluted"], q["via"], q["url"]),
                         (0.16, "release", "u1"))
        orl._append_eps_history("T", url="u2", qend=None, eps=None,
                                series={"2026-03-31": 0.16})
        q = orl.get_eps_history("T")["quarters"]["2026-03-31"]
        self.assertEqual(q["eps_diluted"], 0.16)
        self.assertNotIn("conflict", q)                  # agreement re-affirms
        orl._append_eps_history("T", url="u3", qend=None, eps=None,
                                series={"2026-03-31": 0.20})
        q = orl.get_eps_history("T")["quarters"]["2026-03-31"]
        self.assertEqual(q["eps_diluted"], 0.16)          # never overwritten
        self.assertTrue(q["conflict"])
        self.assertEqual(q["alt"]["eps_diluted"], 0.20)

    def test_release_entry_outranks_agreeing_table_entry(self):
        orl = self.orl
        orl._append_eps_history("T", url="later", qend="2026-06-30", eps=0.04,
                                series={"2026-03-31": 0.16})
        orl._append_eps_history("T", url="q1", qend="2026-03-31", eps=0.16,
                                series={})
        q = orl.get_eps_history("T")["quarters"]["2026-03-31"]
        self.assertEqual((q["via"], q["url"]), ("release", "q1"))

    def test_non_quarter_end_never_recorded(self):
        # IR-crawl qends can be publish dates (CCNB "2026-07-28" seen live).
        self.orl._append_eps_history("T", url="u", qend="2026-07-28", eps=0.39,
                                     series={"2026-07-28": 0.39})
        self.assertEqual(self.orl.get_eps_history("T").get("quarters", {}), {})

    def test_fdva_extraction_fills_envelope_and_history(self):
        orl = self.orl
        orl._latest_earnings_pr = lambda t: {
            "title": "Freedom Financial Holdings Announces Earnings for Second "
                     "Quarter of 2026",
            "url": "https://investors.freedom.bank/fdva-2q26",
            "published_at": "2026-07-31 08:00:00"}
        orl._fetch_story = lambda u: fx.FDVA_2Q26
        val = orl.otc_release_metrics("FDVA")
        self.assertEqual(val["qend"], "2026-06-30")
        self.assertEqual(val["metrics"]["eps_diluted"], 0.04)
        self.assertEqual(val["metrics"]["tbv_ps"], 12.2)
        self.assertEqual(val["eps_series"], FDVA_SERIES)
        self.assertEqual(val["shares_outstanding"], 6_978_754)
        self.assertEqual(val["shares_tie_out"]["basis"], "tbv_ps")
        hist = orl.get_eps_history("FDVA")["quarters"]
        self.assertEqual({q: e["eps_diluted"] for q, e in hist.items()},
                         FDVA_SERIES)
        self.assertEqual(hist["2026-06-30"]["via"], "release")
        self.assertEqual(hist["2026-03-31"]["via"], "table")
        self.assertIn("otc_release:v15:FDVA", self.store)

        # Composite: four discrete quarters from the bank's own releases.
        from analysis.release_eps import otc_composite_ttm_eps
        v, qend, comps = otc_composite_ttm_eps("FDVA", today=date(2026, 10, 6))
        self.assertEqual((v, qend), (-0.14, "2026-06-30"))
        self.assertEqual(comps["quarters"],
                         {"2026-06-30": 0.04, "2026-03-31": 0.16,
                          "2025-12-31": -0.5, "2025-09-30": 0.16})
        self.assertEqual(comps["quarter_sources"]["2026-06-30"]["via"], "release")
        self.assertEqual(comps["quarter_sources"]["2025-12-31"]["via"], "table")
        # Stale anchor (>200 days) → n/a, never priced against today's quote.
        self.assertEqual(otc_composite_ttm_eps("FDVA", today=date(2027, 3, 1)),
                         (None, None, None))


def _seed_envelope(store, ticker, qend, eps, **extra):
    store[f"otc_release:v15:{ticker}"] = {
        "cached_at": "2026-01-01T00:00:00",
        "value": {"qend": qend, "url": "latest", "metrics": {"eps_diluted": eps},
                  **extra}}


def _seed_history(store, ticker, quarters: dict):
    store[f"otc_eps_history:v1:{ticker}"] = {
        "cached_at": "2026-01-01T00:00:00",
        "value": {"quarters": {q: {"eps_diluted": v, "via": "table", "url": "u"}
                               for q, v in quarters.items()}}}


class TestOtcComposite(_StoreBase):
    Q0 = "2026-06-30"
    TODAY = date(2026, 10, 6)

    def _priors(self, n):
        out, q = [], self.Q0
        for _ in range(n):
            q = _prior_quarter_end(q)
            out.append(q)
        return out

    def test_four_quarters_sum(self):
        from analysis.release_eps import otc_composite_ttm_eps
        _seed_envelope(self.store, "T", self.Q0, 0.42)
        _seed_history(self.store, "T", dict(zip(self._priors(3), (0.36, 0.36, 0.38))))
        v, q, c = otc_composite_ttm_eps("T", today=self.TODAY)
        self.assertEqual((v, q), (1.52, self.Q0))                 # BKSC values

    def test_three_quarters_is_not_a_ttm(self):
        from analysis.release_eps import otc_composite_ttm_eps
        _seed_envelope(self.store, "T", self.Q0, 0.42)
        _seed_history(self.store, "T", dict(zip(self._priors(2), (0.36, 0.36))))
        self.assertEqual(otc_composite_ttm_eps("T", today=self.TODAY),
                         (None, None, None))

    def test_conflicted_quarter_refuses(self):
        from analysis.release_eps import otc_composite_ttm_eps
        _seed_envelope(self.store, "T", self.Q0, 0.42)
        _seed_history(self.store, "T", dict(zip(self._priors(3), (0.36, 0.36, 0.38))))
        self.store["otc_eps_history:v1:T"]["value"]["quarters"][
            self._priors(2)[-1]]["conflict"] = True
        self.assertEqual(otc_composite_ttm_eps("T", today=self.TODAY),
                         (None, None, None))

    def test_anchor_disagreeing_with_history_refuses(self):
        from analysis.release_eps import otc_composite_ttm_eps
        _seed_envelope(self.store, "T", self.Q0, 0.42)
        _seed_history(self.store, "T", {self.Q0: 0.52,
                                        **dict(zip(self._priors(3), (0.36, 0.36, 0.38)))})
        self.assertEqual(otc_composite_ttm_eps("T", today=self.TODAY),
                         (None, None, None))

    def test_non_quarter_end_anchor_refuses(self):
        from analysis.release_eps import otc_composite_ttm_eps
        _seed_envelope(self.store, "T", "2026-07-28", 0.39)
        self.assertEqual(otc_composite_ttm_eps("T", today=self.TODAY),
                         (None, None, None))

    def test_no_envelope_is_na(self):
        from analysis.release_eps import otc_composite_ttm_eps
        self.assertEqual(otc_composite_ttm_eps("NONE", today=self.TODAY),
                         (None, None, None))


class TestBackfill(_StoreBase):
    def _prs(self, n):
        quarters = ["First Quarter 2026", "Fourth Quarter 2025",
                    "Third Quarter 2025", "Second Quarter 2025",
                    "First Quarter 2025", "Fourth Quarter 2024"]
        dates = ["2026-04-20", "2026-01-25", "2025-10-20", "2025-07-21",
                 "2025-04-21", "2025-01-27"]
        return [{"title": f"T Bancorp Reports {q} Results", "url": f"u{i}",
                 "published_at": f"{d} 08:00:00"}
                for i, (q, d) in enumerate(zip(quarters[:n], dates[:n]))]

    def test_wire_backfill_bounded_once(self):
        orl = self.orl
        _seed_envelope(self.store, "T", "2026-06-30", 0.42)
        orl._earnings_prs = lambda t: self._prs(6)
        fetched = []
        orl._fetch_document = lambda u, k: fetched.append(u) or (
            "<p>Diluted earnings per share were $0.36 for the quarter.</p>")
        crawls = []
        orl._ir_release_candidates = lambda t: crawls.append(1) or []
        self.assertTrue(orl.backfill_eps_history("T"))
        self.assertEqual(fetched, ["u0", "u1", "u2", "u3"])      # ≤4 prior
        self.assertEqual(crawls, [])                              # wire sufficed
        hist = orl.get_eps_history("T")
        self.assertEqual({q: e["eps_diluted"] for q, e in hist["quarters"].items()},
                         {"2026-03-31": 0.36, "2025-12-31": 0.36,
                          "2025-09-30": 0.36, "2025-06-30": 0.36})
        self.assertEqual(hist["backfill"]["urls"], ["u0", "u1", "u2", "u3"])
        # Once: the marker stops a second pass before any fetch.
        self.assertFalse(orl.backfill_eps_history("T"))
        self.assertEqual(len(fetched), 4)

    def test_ir_candidates_only_when_wire_has_nothing(self):
        orl = self.orl
        _seed_envelope(self.store, "T", "2026-06-30", 0.42)
        orl._earnings_prs = lambda t: []
        orl._ir_release_candidates = lambda t: [
            {"url": "latest", "title": "Q2 2026 Earnings", "qend": "2026-06-30",
             "kind": "html"},
            {"url": "q1.pdf", "title": "Q1 2026 Earnings", "qend": "2026-03-31",
             "kind": "pdf"},
            {"url": "jul.pdf", "title": "July 28, 2026 release", "qend": "2026-07-28",
             "kind": "pdf"}]
        fetched = []
        orl._fetch_document = lambda u, k: fetched.append(u) or (
            "<p>Diluted earnings per share were $0.16 for the quarter.</p>")
        self.assertTrue(orl.backfill_eps_history("T"))
        self.assertEqual(fetched, ["q1.pdf"])          # latest skipped; no QE skipped
        self.assertEqual(orl.get_eps_history("T")["quarters"]["2026-03-31"]
                         ["eps_diluted"], 0.16)

    def test_bank_without_webaddr_backfills_nothing_and_marks_done(self):
        # _ir_release_candidates must hand the backfill a LIST on its early
        # exits (no FDIC web address / unparseable domain) — never None.
        orl = self.orl
        _seed_envelope(self.store, "T", "2026-06-30", 0.42)
        orl._earnings_prs = lambda t: []
        with patch.object(orl, "_bank_webaddr", return_value=None):
            self.assertTrue(orl.backfill_eps_history("T"))
        self.assertEqual(orl.get_eps_history("T")["backfill"]["urls"], [])

    def test_no_envelope_no_backfill(self):
        self.orl._earnings_prs = lambda t: self._prs(2)
        self.assertFalse(self.orl.backfill_eps_history("NONE"))
        self.assertEqual(self.store, {})

    def test_warm_pass_backfills_no_xbrl_targets_within_budget(self):
        import data.otc_release as otc
        from jobs import refresh_home_snapshot as job
        ran = []
        tickers = [f"T{i:02d}" for i in range(30)] + ["SEC1"]
        with patch.object(otc, "otc_release_metrics",
                          side_effect=lambda t, allow_fetch=True, ir_crawl=True: None), \
                patch.object(otc, "backfill_eps_history",
                             side_effect=lambda t: ran.append(t) or True), \
                patch.object(job, "_no_recent_earnings_8k", return_value=False):
            job._warm_otc_releases(tickers, sec={"SEC1": {"x": 1}})
        self.assertEqual(len(ran), job._EPS_BACKFILL_PER_RUN)
        self.assertNotIn("SEC1", ran)


class TestValuationWiring(unittest.TestCase):
    def test_no_cik_bank_serves_otc_composite(self):
        from analysis import release_eps as re_mod
        from analysis import valuation as va
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(re_mod, "otc_composite_ttm_eps",
                             return_value=(-0.14, "2026-06-30", {})):
            self.assertEqual(va._resolve_eps("FDVA", None, None),
                             (-0.14, "release_ttm_otc", False))

    def test_no_cik_bank_without_four_quarters_is_na(self):
        from analysis import release_eps as re_mod
        from analysis import valuation as va
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(re_mod, "otc_composite_ttm_eps",
                             return_value=(None, None, None)):
            self.assertEqual(va._resolve_eps("FDVA", None, None),
                             (None, None, False))

    def test_sec_filer_never_takes_the_otc_path(self):
        from analysis import release_eps as re_mod
        from analysis import valuation as va
        with patch("data.bank_mapping.get_cik", return_value=19617), \
                patch.object(va, "_release_newer_than_filings", return_value=False), \
                patch.object(re_mod, "otc_composite_ttm_eps") as otc:
            self.assertEqual(va._resolve_eps("JPM", 20.0, "2026-06-30"),
                             (20.0, "reconstructed", False))
            otc.assert_not_called()

    def test_release_shares_only_for_no_cik_and_fresh(self):
        import data.otc_release as otc
        from analysis import valuation as va
        recent = (date.today() - timedelta(days=40)).isoformat()
        env = {"qend": recent, "metrics": {}, "shares_outstanding": 6_978_754}
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(otc, "otc_release_metrics", return_value=env):
            self.assertEqual(va._otc_release_shares("FDVA"), 6_978_754)
        with patch("data.bank_mapping.get_cik", return_value=19617), \
                patch.object(otc, "otc_release_metrics", return_value=env):
            self.assertIsNone(va._otc_release_shares("JPM"))
        stale = {**env, "qend": "2024-06-30"}
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(otc, "otc_release_metrics", return_value=stale):
            self.assertIsNone(va._otc_release_shares("FDVA"))

    def test_market_cap_prices_release_shares_and_labels_source(self):
        from analysis import valuation as va
        with patch("data.bank_mapping.get_cik", return_value=None), \
                patch.object(va, "_otc_release_shares", return_value=6_978_754), \
                patch.object(va, "_resolve_eps", return_value=(-0.14, "release_ttm_otc", False)), \
                patch.object(va, "_resolve_tbvps", return_value=(12.2, "company_release", False)), \
                patch.object(va, "_resolve_bvps", return_value=(None, None, False)), \
                patch.object(va, "_sec_facts_lag", return_value={
                    "lag": None, "facts_as_of": None, "filed_period": None,
                    "filed_date": None, "filed_form": None}), \
                patch.object(va, "_resolve_release_efficiency", return_value=(None, None)):
            out = va.compute_all_valuations({"price": 12.15}, {}, {}, None,
                                            ticker="FDVA")
        self.assertAlmostEqual(out["market_cap"], 12.15 * 6_978_754, places=2)
        self.assertEqual(out["market_cap_source"], "company_release")
        self.assertEqual(out["shares_outstanding"], 6_978_754)
        self.assertEqual(out["eps"], -0.14)
        self.assertEqual(out["eps_source"], "release_ttm_otc")
        self.assertIsNone(out["pe_ratio"])              # negative TTM → n/a

    def test_sec_count_keeps_market_cap_unlabeled(self):
        from analysis import valuation as va
        with patch.object(va, "_otc_release_shares") as otc:
            self.assertEqual(va.compute_market_cap(10.0, 100.0), 1000.0)
            otc.assert_not_called()


class TestCardLabels(unittest.TestCase):
    def test_eps_label_plural_releases(self):
        from ui import bank_detail as bd
        self.assertEqual(bd._eps_label({"eps_source": "release_ttm_otc"}),
                         "EPS (TTM, co. releases)")
        self.assertEqual(bd._eps_label({"eps_source": "release_ttm"}),
                         "EPS (TTM, co. release)")

    def test_market_cap_and_shares_labels(self):
        from ui import bank_detail as bd
        row = {"market_cap_source": "company_release"}
        self.assertEqual(bd._mc_label(row), "Market Cap (co. release)")
        self.assertEqual(bd._mc_label(row, "Shares Outstanding"),
                         "Shares Outstanding (co. release)")
        self.assertEqual(bd._mc_label({"market_cap_source": None}), "Market Cap")

    def test_metrics_row_carries_market_cap_source(self):
        from analysis import metrics as am
        src = Path(am.__file__).read_text(encoding="utf-8")
        self.assertIn('result["market_cap_source"] = computed.get("market_cap_source")', src)
        self.assertIn('result["shares_outstanding"] = computed.get("shares_outstanding")', src)


if __name__ == "__main__":
    unittest.main()
