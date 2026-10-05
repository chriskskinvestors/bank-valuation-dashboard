"""UX-review P2-28 follow-up (docs/REVIEW-2026-09-24-ux.md): counts that
can pass 999 must carry a thousands separator. #226 fixed the headline
branch / county counts; three count sites it missed still printed a bare
int ("1234", "of 1500", "1200-county"):

  * Deposit Market Share table — the subject's Branches cell and the
    "#rank of N" Rank cell (ui/deposit_market_share._render_market_table);
  * Market Share & Branches — the "ranks #r of N in <market>" caption
    (ui/deposit_lookup._render_market_share);
  * Branch Competitors — the "<name>'s N-county footprint" heading
    (ui/branch_analytics.render_branch_competitors).

Share columns already carry "%" (checked here too, so a regression shows).
Hermetic: the Streamlit surface and data seams are patched per module.

Run: python -m unittest tests.test_ux_p2_docs_branch
"""
from __future__ import annotations

import contextlib
import types
import unittest
from unittest import mock

import pandas as pd

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.branch_analytics as ba  # noqa: E402
import ui.deposit_lookup as dl  # noqa: E402
import ui.deposit_market_share as dms  # noqa: E402


def _capture_st():
    out: list[str] = []
    fake = types.SimpleNamespace(
        markdown=lambda s, *a, **k: out.append(s),
        caption=lambda s, *a, **k: out.append(s))
    return fake, out


class TestDepositMarketShareTable(unittest.TestCase):
    def _html(self, **over) -> str:
        row = {"market_key": "36061", "market": "New York County, NY",
               "subj_branches": 1234, "subj_deposits_k": 5.0e8,
               "market_total_k": 2.0e9, "share_pct": 25.0, "rank": 1,
               "n_banks": 1500, "hhi": 1800.0, "top_competitor": None,
               "top_competitor_share_pct": None, "top_competitor_cert": None}
        row.update(over)
        fake, out = _capture_st()
        with mock.patch.object(dms, "st", fake), \
                mock.patch.object(dms, "table_export", lambda *a, **k: None), \
                mock.patch("data.bank_universe.cert_ticker_map", lambda: {}):
            dms._render_market_table([row], "By County", "county_X")
        return "".join(out)

    def test_branches_cell_has_thousands_separator(self):
        html = self._html()
        self.assertIn('text-align:right;">1,234</td>', html)
        self.assertNotIn(">1234<", html)

    def test_rank_cell_has_thousands_separator(self):
        self.assertIn("#1 of 1,500</td>", self._html())

    def test_small_counts_unchanged(self):
        html = self._html(subj_branches=7, n_banks=12, rank=3)
        self.assertIn('text-align:right;">7</td>', html)
        self.assertIn("#3 of 12</td>", html)

    def test_share_cell_carries_percent(self):
        self.assertIn('text-align:right;">25.0%</td>', self._html())


class TestMarketShareRankCaption(unittest.TestCase):
    def test_rank_of_n_has_thousands_separator(self):
        n = 1200
        ms = pd.DataFrame({
            "owner_key": [f"o{i}" for i in range(n)],
            "CERT": list(range(n)), "TICKER": [None] * n,
            "NAMEFULL": [f"Bank {i}" for i in range(n)],
            "branches": [1] * n, "deposits": [10.0] * n,
            "market_share": [100.0 / n] * n, "rank": list(range(1, n + 1))})
        fake, out = _capture_st()
        with mock.patch.object(dl, "st", fake), \
                mock.patch.object(dl, "_market_share", lambda *a: ms), \
                mock.patch.object(dl, "_skeleton", contextlib.nullcontext), \
                mock.patch.object(dl, "ksk_table", lambda *a, **k: None), \
                mock.patch.object(dl, "table_export", lambda *a, **k: None), \
                mock.patch.object(dl, "_linked_tickers", lambda s: list(s)):
            dl._render_market_share("msa", "35620", "New York MSA", 2025,
                                    "o0", "Subject Bank", lambda v: "x")
        cap = "".join(out)
        self.assertIn("of 1,200 in New York MSA", cap)
        self.assertIn("**0.1%** share", cap)


class TestCompetitorFootprintHeading(unittest.TestCase):
    def test_county_count_has_thousands_separator(self):
        n = 1100
        fips = [f"{10000 + i}" for i in range(n)]
        roster = pd.DataFrame({"year": [2025] * n, "stcntybr": fips})
        cb = pd.DataFrame({"owner_key": ["s"], "bank_name": ["Subject"],
                           "ticker": ["SUBJ"], "n_branches": [1],
                           "total_deposits": [10.0]})
        fake, out = _capture_st()
        with mock.patch.object(ba, "st", fake), \
                mock.patch.object(ba, "_cert", lambda t: 1), \
                mock.patch.object(ba, "_roster", lambda c: (roster, [])), \
                mock.patch.object(ba, "_footprint_participants",
                                  lambda c, y: {f: cb for f in fips}), \
                mock.patch.object(ba, "get_bank_info",
                                  lambda t: {"name": "Subject"}), \
                mock.patch.object(ba, "ksk_table", lambda *a, **k: None), \
                mock.patch.object(ba, "table_export", lambda *a, **k: None):
            ba.render_branch_competitors("SUBJ")
        heading = "".join(out)
        self.assertIn("Subject's 1,100-county footprint", heading)


if __name__ == "__main__":
    unittest.main()
